"""Agent deployment orchestration.

Handles building agent code into containers and deploying them
to GKE Autopilot or local Docker for development.
"""

import asyncio
import hashlib
import io
import logging
import tarfile
import uuid
from datetime import UTC, datetime

import yaml
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.hosting import DeploymentStatus, HostedAgent
from app.services.hosting.manifest import AgentManifest, extract_secret_refs, parse_manifest
from app.services.hosting.secrets import get_all_decrypted

logger = logging.getLogger(__name__)

# Max upload size: 50MB
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# Runtime → base Docker image
RUNTIME_IMAGES = {
    "python:3.13": "python:3.13-slim",
    "python:3.12": "python:3.12-slim",
    "node:20": "node:20-slim",
    "node:22": "node:22-slim",
}


async def deploy_agent(
    db: AsyncSession,
    agent_id: uuid.UUID,
    archive_bytes: bytes,
    deploy_params: dict,
) -> HostedAgent:
    """Deploy agent code.

    1. Validate the upload and parse arcoa.yaml
    2. Create/update HostedAgent record
    3. Kick off async build + deploy
    """
    if len(archive_bytes) > MAX_UPLOAD_SIZE:
        raise ValueError(f"Upload too large: {len(archive_bytes)} bytes (max {MAX_UPLOAD_SIZE})")

    # Extract and parse manifest from archive
    manifest_data = _extract_manifest(archive_bytes)
    manifest = parse_manifest(manifest_data)

    # Check that referenced secrets exist
    secret_refs = extract_secret_refs(manifest.env)
    if secret_refs:
        existing = await get_all_decrypted(db, agent_id)
        missing = [r for r in secret_refs if r not in existing]
        if missing:
            raise ValueError(
                f"Missing secrets: {', '.join(missing)}. "
                f"Set them with: arcoa secrets set <KEY> <VALUE>"
            )

    source_hash = hashlib.sha256(archive_bytes).hexdigest()

    # Check for existing deployment
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    hosted = result.scalar_one_or_none()

    if hosted:
        # Update existing
        hosted.manifest = manifest_data
        hosted.source_hash = source_hash
        hosted.status = DeploymentStatus.BUILDING
        hosted.runtime = manifest.runtime
        hosted.region = deploy_params.get("region", hosted.region)
        hosted.cpu_limit = manifest.cpu
        hosted.memory_limit_mb = manifest.memory_mb
        hosted.build_log = None
        hosted.error_message = None
        hosted.updated_at = datetime.now(UTC)
    else:
        hosted = HostedAgent(
            agent_id=agent_id,
            manifest=manifest_data,
            source_hash=source_hash,
            status=DeploymentStatus.BUILDING,
            runtime=manifest.runtime,
            region=deploy_params.get("region", "us-west1"),
            cpu_limit=manifest.cpu,
            memory_limit_mb=manifest.memory_mb,
        )
        db.add(hosted)

    # Update agent hosting_mode to "hosted"
    await db.execute(
        update(Agent)
        .where(Agent.agent_id == agent_id)
        .values(hosting_mode="hosted")
    )

    await db.flush()

    # Kick off build in background
    hosted_id = hosted.id
    asyncio.create_task(
        _build_and_deploy(hosted_id, agent_id, archive_bytes, manifest)
    )

    return hosted


async def get_deployment(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> HostedAgent | None:
    """Get the current deployment for an agent."""
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    return result.scalar_one_or_none()


async def undeploy_agent(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> bool:
    """Stop and remove a hosted agent deployment."""
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    hosted = result.scalar_one_or_none()
    if not hosted:
        return False

    # Stop the container
    if hosted.container_id:
        try:
            await _stop_container(hosted)
        except Exception:
            logger.exception("Failed to stop container %s", hosted.container_id)

    hosted.status = DeploymentStatus.STOPPED
    hosted.container_id = None
    hosted.updated_at = datetime.now(UTC)

    # Revert agent hosting mode
    await db.execute(
        update(Agent)
        .where(Agent.agent_id == agent_id)
        .values(hosting_mode="client_only")
    )

    await db.flush()
    return True


async def get_logs(
    db: AsyncSession,
    agent_id: uuid.UUID,
    tail: int = 200,
) -> str:
    """Get logs from a hosted agent container."""
    result = await db.execute(
        select(HostedAgent).where(HostedAgent.agent_id == agent_id)
    )
    hosted = result.scalar_one_or_none()
    if not hosted:
        raise ValueError("No deployment found")

    if not hosted.container_id:
        return hosted.build_log or "(no logs available — agent not running)"

    if settings.hosting_gke_cluster:
        return await _get_gke_logs(hosted, tail)
    else:
        return await _get_docker_logs(hosted, tail)


def _extract_manifest(archive_bytes: bytes) -> dict:
    """Extract arcoa.yaml from a tar.gz archive."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            # Security: check for path traversal
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    raise ValueError(f"Unsafe path in archive: {member.name}")

            # Find arcoa.yaml
            manifest_member = None
            for member in tar.getmembers():
                basename = member.name.split("/")[-1]
                if basename == "arcoa.yaml" or basename == "arcoa.yml":
                    manifest_member = member
                    break

            if manifest_member is None:
                raise ValueError("Archive must contain arcoa.yaml at the root")

            f = tar.extractfile(manifest_member)
            if f is None:
                raise ValueError("Could not read arcoa.yaml")

            return yaml.safe_load(f.read())
    except tarfile.TarError as e:
        raise ValueError(f"Invalid archive: {e}")


# ==========================================================================
# Build & Deploy Pipeline
# ==========================================================================

async def _build_and_deploy(
    hosted_id: uuid.UUID,
    agent_id: uuid.UUID,
    archive_bytes: bytes,
    manifest: AgentManifest,
) -> None:
    """Background task: build container image and deploy it."""
    from app.database import async_session_factory

    async with async_session_factory() as db:
        try:
            result = await db.execute(
                select(HostedAgent).where(HostedAgent.id == hosted_id)
            )
            hosted = result.scalar_one()

            # Step 1: Build
            hosted.build_log = "Building agent container...\n"
            await db.commit()

            if settings.hosting_gke_cluster:
                container_id = await _build_and_deploy_gke(
                    hosted, agent_id, archive_bytes, manifest, db
                )
            else:
                container_id = await _build_and_deploy_docker(
                    hosted, agent_id, archive_bytes, manifest, db
                )

            # Step 2: Mark running
            hosted.status = DeploymentStatus.RUNNING
            hosted.container_id = container_id
            hosted.build_log = (hosted.build_log or "") + "Deploy complete.\n"
            hosted.updated_at = datetime.now(UTC)
            await db.commit()

            logger.info("Agent %s deployed: container=%s", agent_id, container_id)

        except Exception as e:
            logger.exception("Deploy failed for agent %s", agent_id)
            try:
                hosted.status = DeploymentStatus.ERRORED
                hosted.error_message = str(e)
                hosted.build_log = (hosted.build_log or "") + f"\nERROR: {e}\n"
                hosted.updated_at = datetime.now(UTC)
                await db.commit()
            except Exception:
                logger.exception("Failed to update error status")


def _generate_dockerfile(manifest: AgentManifest) -> str:
    """Generate a Dockerfile for the agent."""
    base_image = RUNTIME_IMAGES[manifest.runtime]
    is_python = manifest.runtime.startswith("python")
    is_node = manifest.runtime.startswith("node")

    lines = [f"FROM {base_image}"]
    lines.append("WORKDIR /app")

    # Install system requirements
    if manifest.requirements:
        pkg_list = " ".join(manifest.requirements)
        lines.append(
            f"RUN apt-get update && apt-get install -y --no-install-recommends {pkg_list} "
            "&& rm -rf /var/lib/apt/lists/*"
        )

    # Install dependencies
    if is_python:
        lines.append("COPY requirements.txt* ./")
        lines.append(
            "RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi"
        )
        # Install arcoa SDK
        lines.append("RUN pip install --no-cache-dir arcoa")
    elif is_node:
        lines.append("COPY package*.json ./")
        lines.append(
            "RUN if [ -f package.json ]; then npm ci --production; fi"
        )

    # Copy agent code
    lines.append("COPY . .")

    # Copy the runtime entrypoint
    lines.append("COPY _arcoa_entrypoint.py /app/_arcoa_entrypoint.py")

    # Non-root user
    lines.append("RUN adduser --disabled-password --gecos '' --uid 10001 agent")
    lines.append("USER agent")

    if is_python:
        lines.append(f'CMD ["python", "/app/_arcoa_entrypoint.py"]')
    elif is_node:
        lines.append(f'CMD ["node", "/app/_arcoa_entrypoint.py"]')

    return "\n".join(lines) + "\n"


def _generate_entrypoint(manifest: AgentManifest) -> str:
    """Generate the runtime entrypoint that wraps the developer's handler."""
    return f'''\
"""Arcoa hosted agent runtime entrypoint.

Auto-generated — do not edit. This connects your handler to the Arcoa marketplace.
"""

import asyncio
import importlib.util
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("arcoa.runtime")


def _load_handler(path: str):
    """Import the developer's handler module."""
    spec = importlib.util.spec_from_file_location("handler", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load handler from {{path}}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def main():
    from arcoa import ArcoaAgent

    agent_id = os.environ["ARCOA_AGENT_ID"]
    private_key = os.environ["ARCOA_PRIVATE_KEY"]
    api_url = os.environ.get("ARCOA_API_URL", "https://api.arcoa.ai")

    agent = ArcoaAgent(agent_id=agent_id, private_key=private_key, api_url=api_url)

    # Load the developer's handler
    handler_path = "/app/{manifest.entrypoint}"
    handler_mod = _load_handler(handler_path)

    # Register skill handlers from the module
    if hasattr(handler_mod, "handle"):
        # Simple single-handler mode
        @agent.on("job.funded")
        async def on_job_funded(payload):
            job_id = payload["job_id"]
            logger.info("Job funded: %s — starting work", job_id)

            await agent._client.start_job(job_id)

            # Get job details
            job = await agent._client.get_job(job_id)
            requirements = job.get("requirements", {{}})

            # Call the developer's handler
            try:
                result = handler_mod.handle(requirements)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as e:
                logger.exception("Handler failed for job %s", job_id)
                await agent._client.fail_job(job_id)
                return

            # Deliver the result
            await agent._client.deliver_job(job_id, result)
            logger.info("Job delivered: %s", job_id)

    elif hasattr(handler_mod, "setup"):
        # Advanced mode: developer registers their own handlers
        handler_mod.setup(agent)

    else:
        logger.error("Handler must define either handle(requirements) or setup(agent)")
        sys.exit(1)

    logger.info("Agent online — listening for jobs")
    await agent.connect()


if __name__ == "__main__":
    asyncio.run(main())
'''


# ==========================================================================
# Docker Backend (Local Development)
# ==========================================================================

async def _build_and_deploy_docker(
    hosted: HostedAgent,
    agent_id: uuid.UUID,
    archive_bytes: bytes,
    manifest: AgentManifest,
    db: AsyncSession,
) -> str:
    """Build and run agent as a local Docker container."""
    import tempfile
    from pathlib import Path

    container_name = f"arcoa-agent-{str(agent_id)[:8]}"
    image_tag = f"arcoa-agent-{str(agent_id)[:8]}:{hosted.source_hash[:12]}"

    with tempfile.TemporaryDirectory(prefix="arcoa-build-") as build_dir:
        build_path = Path(build_dir)

        # Extract archive
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            tar.extractall(build_path, filter="data")

        # Find the actual code directory (might be nested one level)
        entries = list(build_path.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            code_dir = entries[0]
        else:
            code_dir = build_path

        # Generate Dockerfile
        dockerfile = _generate_dockerfile(manifest)
        (code_dir / "Dockerfile").write_text(dockerfile)

        # Generate entrypoint
        entrypoint = _generate_entrypoint(manifest)
        (code_dir / "_arcoa_entrypoint.py").write_text(entrypoint)

        hosted.build_log = (hosted.build_log or "") + "Building Docker image...\n"
        await db.commit()

        # Build image
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "-t", image_tag, str(code_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        build_output = stdout.decode("utf-8", errors="replace")
        hosted.build_log = (hosted.build_log or "") + build_output + "\n"

        if proc.returncode != 0:
            raise RuntimeError(f"Docker build failed (exit {proc.returncode})")

        # Stop existing container if running
        await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        hosted.build_log = (hosted.build_log or "") + "Starting container...\n"
        await db.commit()

        # Get secrets for env vars
        decrypted_secrets = await get_all_decrypted(db, agent_id)

        # Build env vars
        env_args = []
        for k, v in manifest.env.items():
            # Resolve secret references
            if v.startswith("${secrets.") and v.endswith("}"):
                secret_name = v[len("${secrets."):-1]
                resolved = decrypted_secrets.get(secret_name, "")
                env_args.extend(["-e", f"{k}={resolved}"])
            else:
                env_args.extend(["-e", f"{k}={v}"])

        # Inject Arcoa credentials
        agent_row = await db.execute(
            select(Agent).where(Agent.agent_id == agent_id)
        )
        agent_obj = agent_row.scalar_one()

        # Run container
        docker_cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            f"--memory={manifest.memory_mb}m",
            f"--cpus={manifest.cpu}",
            "--restart=on-failure:3",
            "-e", f"ARCOA_AGENT_ID={agent_id}",
            "-e", f"ARCOA_API_URL={settings.base_url}",
            *env_args,
            image_tag,
        ]

        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"Docker run failed: {error}")

        container_id = stdout.decode().strip()[:64]
        return container_id


async def _stop_container(hosted: HostedAgent) -> None:
    """Stop a container."""
    if not hosted.container_id:
        return

    if settings.hosting_gke_cluster:
        await _stop_gke_deployment(hosted)
    else:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", hosted.container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


async def _get_docker_logs(hosted: HostedAgent, tail: int) -> str:
    """Get logs from a local Docker container."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "logs", "--tail", str(tail), hosted.container_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8", errors="replace")


# ==========================================================================
# GKE Backend (Production)
# ==========================================================================

async def _build_and_deploy_gke(
    hosted: HostedAgent,
    agent_id: uuid.UUID,
    archive_bytes: bytes,
    manifest: AgentManifest,
    db: AsyncSession,
) -> str:
    """Build image via Cloud Build API, deploy as GKE Deployment."""
    import tempfile
    from pathlib import Path

    project = settings.hosting_gke_project or settings.gcp_project_id
    region = hosted.region
    # Use the hosted-agents Artifact Registry repo
    image_tag = (
        f"{region}-docker.pkg.dev/{project}/hosted-agents-staging/"
        f"hosted-{str(agent_id)[:8]}:{hosted.source_hash[:12]}"
    )
    deployment_name = f"agent-{str(agent_id)[:8]}"

    with tempfile.TemporaryDirectory(prefix="arcoa-build-") as build_dir:
        build_path = Path(build_dir)

        # Extract archive
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            tar.extractall(build_path, filter="data")

        entries = list(build_path.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            code_dir = entries[0]
        else:
            code_dir = build_path

        # Generate Dockerfile and entrypoint
        (code_dir / "Dockerfile").write_text(_generate_dockerfile(manifest))
        (code_dir / "_arcoa_entrypoint.py").write_text(_generate_entrypoint(manifest))

        hosted.build_log = (hosted.build_log or "") + f"Building image: {image_tag}\n"
        await db.commit()

        # Create a tar.gz of the build context for Cloud Build
        source_buf = io.BytesIO()
        with tarfile.open(fileobj=source_buf, mode="w:gz") as tar_out:
            for fpath in code_dir.rglob("*"):
                if fpath.is_file():
                    tar_out.add(str(fpath), arcname=str(fpath.relative_to(code_dir)))
        source_bytes = source_buf.getvalue()

        # Upload source to GCS
        source_object = await _upload_build_source(project, source_bytes, agent_id, hosted.source_hash)

        hosted.build_log = (hosted.build_log or "") + f"Source uploaded, starting Cloud Build...\n"
        await db.commit()

        # Run Cloud Build via API
        build_log = await _run_cloud_build(project, region, source_object, image_tag)
        hosted.build_log = (hosted.build_log or "") + build_log + "\n"

    # Deploy to GKE
    hosted.build_log = (hosted.build_log or "") + "Deploying to GKE...\n"
    await db.commit()

    # Get secrets for env injection
    decrypted_secrets = await get_all_decrypted(db, agent_id)

    env_vars = {}
    for k, v in manifest.env.items():
        if v.startswith("${secrets.") and v.endswith("}"):
            secret_name = v[len("${secrets."):-1]
            env_vars[k] = decrypted_secrets.get(secret_name, "")
        else:
            env_vars[k] = v

    env_vars["ARCOA_AGENT_ID"] = str(agent_id)
    env_vars["ARCOA_API_URL"] = settings.base_url

    deployment_id = await _apply_gke_deployment(
        deployment_name=deployment_name,
        image=image_tag,
        manifest=manifest,
        env_vars=env_vars,
        agent_id=agent_id,
    )

    return deployment_id


async def _upload_build_source(
    project: str,
    source_bytes: bytes,
    agent_id: uuid.UUID,
    source_hash: str,
) -> dict:
    """Upload build source to GCS and return {bucket, object} dict."""
    from google.cloud import storage

    bucket_name = f"{project}_cloudbuild"
    object_name = f"hosted-agents/{agent_id}/{source_hash}.tar.gz"

    def _upload():
        client = storage.Client(project=project)
        bucket = client.bucket(bucket_name)
        # Create bucket if it doesn't exist
        if not bucket.exists():
            bucket.create(location="us-west1")
        blob = bucket.blob(object_name)
        blob.upload_from_string(source_bytes, content_type="application/gzip")
        return {"bucket": bucket_name, "object": object_name}

    return await asyncio.get_event_loop().run_in_executor(None, _upload)


async def _run_cloud_build(
    project: str,
    region: str,
    source_object: dict,
    image_tag: str,
) -> str:
    """Run Cloud Build via the API and wait for completion."""
    from google.cloud.devtools import cloudbuild_v1

    def _build():
        client = cloudbuild_v1.CloudBuildClient()

        build = cloudbuild_v1.Build(
            source=cloudbuild_v1.Source(
                storage_source=cloudbuild_v1.StorageSource(
                    bucket=source_object["bucket"],
                    object_=source_object["object"],
                ),
            ),
            images=[image_tag],
            steps=[
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/docker",
                    args=["build", "-t", image_tag, "."],
                ),
            ],
        )

        operation = client.create_build(project_id=project, build=build)
        # Wait for build to complete (blocking)
        result = operation.result(timeout=600)

        log_lines = []
        if result.status == cloudbuild_v1.Build.Status.SUCCESS:
            log_lines.append(f"Cloud Build succeeded (id={result.id})")
        else:
            status_name = cloudbuild_v1.Build.Status(result.status).name
            log_lines.append(f"Cloud Build {status_name} (id={result.id})")
            if result.status_detail:
                log_lines.append(f"Detail: {result.status_detail}")
            if result.status != cloudbuild_v1.Build.Status.SUCCESS:
                raise RuntimeError(
                    f"Cloud Build failed: {status_name} — {result.status_detail or 'no details'}"
                )

        return "\n".join(log_lines)

    return await asyncio.get_event_loop().run_in_executor(None, _build)


async def _apply_gke_deployment(
    deployment_name: str,
    image: str,
    manifest: AgentManifest,
    env_vars: dict[str, str],
    agent_id: uuid.UUID,
) -> str:
    """Create or update a K8s Deployment for the hosted agent."""
    from kubernetes import client as k8s_client

    namespace = settings.hosting_namespace
    batch_api, core_api = await asyncio.get_event_loop().run_in_executor(
        None, _get_hosting_k8s_clients,
    )
    apps_api = k8s_client.AppsV1Api(batch_api.api_client)

    # Build env var list
    env_list = [
        k8s_client.V1EnvVar(name=k, value=v)
        for k, v in env_vars.items()
    ]

    # The Deployment spec
    deployment = k8s_client.V1Deployment(
        metadata=k8s_client.V1ObjectMeta(
            name=deployment_name,
            namespace=namespace,
            labels={
                "app": "arcoa-hosted-agent",
                "agent-id": str(agent_id)[:8],
            },
        ),
        spec=k8s_client.V1DeploymentSpec(
            replicas=1,
            selector=k8s_client.V1LabelSelector(
                match_labels={"app": deployment_name},
            ),
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(
                    labels={
                        "app": deployment_name,
                        "arcoa-role": "hosted-agent",
                        "agent-id": str(agent_id)[:8],
                    },
                ),
                spec=k8s_client.V1PodSpec(
                    automount_service_account_token=False,
                    enable_service_links=False,
                    containers=[
                        k8s_client.V1Container(
                            name="agent",
                            image=image,
                            env=env_list,
                            resources=k8s_client.V1ResourceRequirements(
                                limits={
                                    "memory": f"{manifest.memory_mb}Mi",
                                    "cpu": manifest.cpu,
                                },
                                requests={
                                    "memory": f"{min(manifest.memory_mb, 256)}Mi",
                                    "cpu": "125m",
                                    "ephemeral-storage": "256Mi",
                                },
                            ),
                            security_context=k8s_client.V1SecurityContext(
                                run_as_non_root=True,
                                run_as_user=10001,
                                allow_privilege_escalation=False,
                                read_only_root_filesystem=True,
                                capabilities=k8s_client.V1Capabilities(
                                    drop=["ALL"],
                                ),
                            ),
                            volume_mounts=[
                                k8s_client.V1VolumeMount(
                                    name="tmp",
                                    mount_path="/tmp",
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        k8s_client.V1Volume(
                            name="tmp",
                            empty_dir=k8s_client.V1EmptyDirVolumeSource(
                                medium="Memory",
                                size_limit="64Mi",
                            ),
                        ),
                    ],
                ),
            ),
        ),
    )

    # Create or update
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: apps_api.read_namespaced_deployment(deployment_name, namespace),
        )
        # Exists — update
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: apps_api.replace_namespaced_deployment(
                deployment_name, namespace, deployment,
            ),
        )
    except Exception:
        # Doesn't exist — create
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: apps_api.create_namespaced_deployment(namespace, deployment),
        )

    return deployment_name


def _get_hosting_k8s_clients():
    """Get K8s clients for the hosting cluster (reuses sandbox pattern)."""
    import google.auth
    import google.auth.transport.requests
    from kubernetes import client as k8s_client
    import base64

    project = settings.hosting_gke_project or settings.gcp_project_id
    cluster = settings.hosting_gke_cluster
    location = settings.hosting_gke_location

    from google.cloud import container_v1
    gke_client = container_v1.ClusterManagerClient()
    cluster_path = f"projects/{project}/locations/{location}/clusters/{cluster}"
    gke_cluster = gke_client.get_cluster(name=cluster_path)

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())

    configuration = k8s_client.Configuration()
    endpoint = gke_cluster.endpoint
    if gke_cluster.private_cluster_config and gke_cluster.private_cluster_config.private_endpoint:
        endpoint = gke_cluster.private_cluster_config.private_endpoint
    configuration.host = f"https://{endpoint}"
    configuration.api_key = {"authorization": f"Bearer {credentials.token}"}

    import tempfile
    ca_cert_bytes = base64.b64decode(gke_cluster.master_auth.cluster_ca_certificate)
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    f.write(ca_cert_bytes)
    f.close()
    configuration.ssl_ca_cert = f.name
    configuration.verify_ssl = True

    api_client = k8s_client.ApiClient(configuration)
    return (
        k8s_client.BatchV1Api(api_client),
        k8s_client.CoreV1Api(api_client),
    )


async def _stop_gke_deployment(hosted: HostedAgent) -> None:
    """Scale down a GKE deployment to 0."""
    from kubernetes import client as k8s_client

    namespace = settings.hosting_namespace
    batch_api, _ = await asyncio.get_event_loop().run_in_executor(
        None, _get_hosting_k8s_clients,
    )
    apps_api = k8s_client.AppsV1Api(batch_api.api_client)

    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: apps_api.delete_namespaced_deployment(
                hosted.container_id, namespace,
            ),
        )
    except Exception:
        logger.exception("Failed to delete GKE deployment %s", hosted.container_id)


async def _get_gke_logs(hosted: HostedAgent, tail: int) -> str:
    """Get logs from a GKE pod."""
    from kubernetes import client as k8s_client

    namespace = settings.hosting_namespace
    _, core_api = await asyncio.get_event_loop().run_in_executor(
        None, _get_hosting_k8s_clients,
    )

    try:
        pods = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: core_api.list_namespaced_pod(
                namespace,
                label_selector=f"app={hosted.container_id}",
            ),
        )
        if not pods.items:
            return "(no pods found)"

        pod_name = pods.items[0].metadata.name
        logs = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: core_api.read_namespaced_pod_log(
                pod_name, namespace,
                container="agent",
                tail_lines=tail,
            ),
        )
        return logs or ""
    except Exception as e:
        return f"(error reading logs: {e})"
