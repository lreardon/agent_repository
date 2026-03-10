"""Scale-to-zero for hosted agents.

Agents that haven't processed a job within their idle_timeout are scaled
down to zero (pod deleted, status → SLEEPING). When a new job arrives,
the platform wakes the agent before delivering the event.

Flow:
  1. Job proposed → seller is a hosted agent in SLEEPING state
  2. Platform calls wake_agent() → scales pod to 1, waits for ready
  3. Agent connects via WebSocket, receives the job event
  4. After idle_timeout with no activity → idle_monitor scales to 0

Cost impact:
  - GKE Autopilot charges per-pod per-second
  - A sleeping agent costs $0/month
  - Wake time: ~5-10s on Autopilot (image pull is cached after first deploy)
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.hosting import DeploymentStatus, HostedAgent

logger = logging.getLogger(__name__)

# How long to wait for a waking agent to come online
WAKE_TIMEOUT_SECONDS = 60


async def wake_agent(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Wake a sleeping hosted agent. Returns True if agent is now running.

    Called by the job delivery path when a job targets a sleeping hosted agent.
    Idempotent — if the agent is already running, returns immediately.
    """
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    hosted = result.scalar_one_or_none()
    if not hosted:
        return False

    if hosted.status == DeploymentStatus.RUNNING:
        # Already awake — just update activity timestamp
        hosted.last_activity_at = datetime.now(UTC)
        await db.flush()
        return True

    if hosted.status != DeploymentStatus.SLEEPING:
        logger.warning(
            "Cannot wake agent %s: status is %s (expected SLEEPING)",
            agent_id, hosted.status.value,
        )
        return False

    logger.info("Waking hosted agent %s", agent_id)

    hosted.status = DeploymentStatus.DEPLOYING
    hosted.last_activity_at = datetime.now(UTC)
    await db.commit()

    # Scale up the container
    try:
        if settings.hosting_gke_cluster:
            await _scale_gke_deployment(hosted, replicas=1)
        else:
            await _start_docker_container(hosted, agent_id)

        # Wait for the agent to connect (it will set is_online=True via WebSocket)
        await _wait_for_online(db, agent_id, timeout=WAKE_TIMEOUT_SECONDS)

        hosted.status = DeploymentStatus.RUNNING
        await db.commit()
        logger.info("Agent %s is awake and online", agent_id)
        return True

    except Exception as e:
        logger.exception("Failed to wake agent %s", agent_id)
        hosted.status = DeploymentStatus.SLEEPING
        hosted.error_message = f"Wake failed: {e}"
        await db.commit()
        return False


async def sleep_agent(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Scale a running hosted agent to zero. Returns True on success."""
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    hosted = result.scalar_one_or_none()
    if not hosted:
        return False

    if hosted.status != DeploymentStatus.RUNNING:
        return False

    logger.info("Scaling agent %s to zero", agent_id)

    try:
        if settings.hosting_gke_cluster:
            await _scale_gke_deployment(hosted, replicas=0)
        else:
            await _stop_docker_container(hosted)

        hosted.status = DeploymentStatus.SLEEPING
        hosted.container_id = hosted.container_id  # Keep the deployment name for wake
        hosted.updated_at = datetime.now(UTC)
        await db.commit()
        return True

    except Exception as e:
        logger.exception("Failed to sleep agent %s", agent_id)
        return False


async def record_activity(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Record that a hosted agent just processed work.

    Called when a job is delivered to or completed by a hosted agent.
    Resets the idle timer.
    """
    await db.execute(
        update(HostedAgent)
        .where(HostedAgent.agent_id == agent_id)
        .values(last_activity_at=datetime.now(UTC))
    )


async def is_hosted_and_sleeping(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Check if an agent is hosted and currently sleeping."""
    result = await db.execute(
        select(HostedAgent.status).where(HostedAgent.agent_id == agent_id)
    )
    row = result.scalar_one_or_none()
    return row == DeploymentStatus.SLEEPING


# ==========================================================================
# Idle Monitor — runs as a background task
# ==========================================================================

async def run_idle_monitor() -> None:
    """Background loop: check for idle hosted agents and scale them to zero.

    Runs every 60 seconds. An agent is considered idle when:
    - status is RUNNING
    - scale_to_zero is True
    - last_activity_at is older than idle_timeout_seconds
    """
    from app.database import async_session_factory

    logger.info("Idle monitor started")

    while True:
        try:
            await asyncio.sleep(60)

            async with async_session_factory() as db:
                now = datetime.now(UTC)

                # Find running agents with scale_to_zero enabled
                result = await db.execute(
                    select(HostedAgent).where(
                        HostedAgent.status == DeploymentStatus.RUNNING,
                        HostedAgent.scale_to_zero.is_(True),
                    )
                )
                agents = list(result.scalars().all())

                for hosted in agents:
                    last = hosted.last_activity_at or hosted.created_at
                    idle_since = (now - last).total_seconds()

                    if idle_since > hosted.idle_timeout_seconds:
                        logger.info(
                            "Agent %s idle for %.0fs (threshold: %ds) — scaling to zero",
                            hosted.agent_id, idle_since, hosted.idle_timeout_seconds,
                        )
                        await sleep_agent(db, hosted.agent_id)

        except asyncio.CancelledError:
            logger.info("Idle monitor shutting down")
            break
        except Exception:
            logger.exception("Idle monitor error")


# ==========================================================================
# Container orchestration helpers
# ==========================================================================

async def _wait_for_online(
    db: AsyncSession,
    agent_id: uuid.UUID,
    timeout: int,
) -> None:
    """Poll until the agent's is_online flag is True."""
    from app.models.agent import Agent

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(2)
        result = await db.execute(
            select(Agent.is_online).where(Agent.agent_id == agent_id)
        )
        await db.expire_all()  # Force fresh read
        is_online = result.scalar_one_or_none()
        if is_online:
            return

    raise TimeoutError(f"Agent {agent_id} did not come online within {timeout}s")


async def _scale_gke_deployment(hosted: HostedAgent, replicas: int) -> None:
    """Scale a GKE deployment up or down."""
    from kubernetes import client as k8s_client

    namespace = settings.hosting_namespace
    deployment_name = hosted.container_id
    if not deployment_name:
        raise ValueError("No deployment name (container_id) set")

    from app.services.hosting.deploy import _get_hosting_k8s_clients

    batch_api, _ = await asyncio.get_event_loop().run_in_executor(
        None, _get_hosting_k8s_clients,
    )
    apps_api = k8s_client.AppsV1Api(batch_api.api_client)

    body = {"spec": {"replicas": replicas}}
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: apps_api.patch_namespaced_deployment_scale(
            deployment_name, namespace, body,
        ),
    )

    if replicas > 0:
        # Wait for pod to be ready
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            dep = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: apps_api.read_namespaced_deployment(
                    deployment_name, namespace,
                ),
            )
            if dep.status.ready_replicas and dep.status.ready_replicas >= 1:
                return

        logger.warning("Deployment %s not ready after 30s", deployment_name)


async def _start_docker_container(hosted: HostedAgent, agent_id: uuid.UUID) -> None:
    """Restart a stopped Docker container."""
    container_name = f"arcoa-agent-{str(agent_id)[:8]}"

    proc = await asyncio.create_subprocess_exec(
        "docker", "start", container_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Docker start failed: {error}")


async def _stop_docker_container(hosted: HostedAgent) -> None:
    """Stop (not remove) a Docker container so it can be restarted."""
    if not hosted.container_id:
        return

    proc = await asyncio.create_subprocess_exec(
        "docker", "stop", hosted.container_id,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
