"""WebSocket gateway for agent connections."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.agent import Agent, AgentStatus
from app.redis import redis_pool
from app.services.connection_manager import manager
from app.utils.crypto import is_timestamp_valid, verify_signature

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

PING_INTERVAL = 30  # seconds
PONG_TIMEOUT = 10  # seconds


async def _authenticate(ws: WebSocket, db: AsyncSession, redis_client: aioredis.Redis) -> Agent | None:
    """Wait for auth message and verify. Returns Agent on success, None on failure."""
    from app.config import settings

    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        return None

    if msg.get("type") != "auth":
        await ws.send_json({"type": "error", "detail": "Expected auth message"})
        return None

    try:
        agent_id = uuid.UUID(msg["agent_id"])
        timestamp = msg["timestamp"]
        signature = msg["signature"]
    except (KeyError, ValueError):
        await ws.send_json({"type": "error", "detail": "Malformed auth message"})
        return None

    # Verify timestamp
    if not is_timestamp_valid(timestamp, settings.signature_max_age_seconds):
        await ws.send_json({"type": "error", "detail": "Timestamp expired"})
        return None

    # Nonce replay protection
    nonce = msg.get("nonce")
    if nonce:
        nonce_key = f"nonce:{nonce}"
        already_used = await redis_client.set(nonce_key, "1", nx=True, ex=settings.nonce_ttl_seconds)
        if not already_used:
            await ws.send_json({"type": "error", "detail": "Nonce already used"})
            return None

    # Look up agent
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None or agent.status != AgentStatus.ACTIVE:
        await ws.send_json({"type": "error", "detail": "Agent not found or not active"})
        return None

    # Verify signature (method=WS, path=/ws/agent)
    if not verify_signature(agent.public_key, signature, timestamp, "WS", "/ws/agent", b""):
        await ws.send_json({"type": "error", "detail": "Invalid signature"})
        return None

    return agent


async def _set_online(db: AsyncSession, redis_client: aioredis.Redis, agent_id: uuid.UUID) -> None:
    """Mark agent as online in DB and Redis."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if agent:
        agent.is_online = True
        agent.last_connected_at = datetime.now(UTC)
        await db.commit()
    await redis_client.sadd("online_agents", str(agent_id))


async def _set_offline(db: AsyncSession, redis_client: aioredis.Redis, agent_id: uuid.UUID) -> None:
    """Mark agent as offline in DB and Redis."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if agent:
        agent.is_online = False
        await db.commit()
    await redis_client.srem("online_agents", str(agent_id))


@router.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    """WebSocket endpoint for agent connections."""
    await ws.accept()

    redis_client = aioredis.Redis(connection_pool=redis_pool)
    try:
        async with async_session_factory() as db:
            agent = await _authenticate(ws, db, redis_client)
            if agent is None:
                await ws.close(code=4001, reason="Authentication failed")
                return

            agent_id = agent.agent_id

            # Mark online
            await _set_online(db, redis_client, agent_id)
            await manager.connect(agent_id, ws)

            await ws.send_json({"type": "auth_ok", "agent_id": str(agent_id)})

            try:
                while True:
                    # Send server ping
                    await ws.send_json({"type": "ping"})

                    # Wait for pong or other message
                    try:
                        raw = await asyncio.wait_for(ws.receive_text(), timeout=PING_INTERVAL + PONG_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.info("Agent %s timed out (no pong)", agent_id)
                        break

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "pong":
                        # Wait for next ping interval
                        try:
                            raw = await asyncio.wait_for(ws.receive_text(), timeout=PING_INTERVAL)
                            # Got another message before next ping cycle
                            try:
                                msg = json.loads(raw)
                                if msg.get("type") == "ping":
                                    await ws.send_json({"type": "pong"})
                            except json.JSONDecodeError:
                                pass
                        except asyncio.TimeoutError:
                            pass  # Time for next ping
                    elif msg_type == "ping":
                        await ws.send_json({"type": "pong"})

            except WebSocketDisconnect:
                pass
            finally:
                # Mark offline
                await manager.disconnect(agent_id)
                await _set_offline(db, redis_client, agent_id)

    finally:
        await redis_client.aclose()
