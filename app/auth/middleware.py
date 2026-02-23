"""Ed25519 signature verification dependency for FastAPI."""

import uuid

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.agent import Agent, AgentStatus
from app.redis import get_redis
from app.utils.crypto import is_timestamp_valid, verify_signature


class AuthenticatedAgent:
    """Container for the verified agent context."""

    def __init__(self, agent_id: uuid.UUID, agent: Agent) -> None:
        self.agent_id = agent_id
        self.agent = agent


async def verify_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthenticatedAgent:
    """Verify Ed25519 signature on incoming request."""
    # Extract headers
    auth_header = request.headers.get("Authorization")
    timestamp = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")

    if not auth_header or not timestamp:
        raise HTTPException(status_code=403, detail="Missing authentication headers")

    # Parse Authorization: AgentSig <agent_id>:<signature>
    if not auth_header.startswith("AgentSig "):
        raise HTTPException(status_code=403, detail="Invalid authorization scheme")

    try:
        credentials = auth_header[9:]  # strip "AgentSig "
        agent_id_str, signature = credentials.split(":", 1)
        agent_id = uuid.UUID(agent_id_str)
    except (ValueError, IndexError):
        raise HTTPException(status_code=403, detail="Malformed authorization header")

    # Check timestamp freshness
    if not is_timestamp_valid(timestamp, settings.signature_max_age_seconds):
        raise HTTPException(status_code=403, detail="Request timestamp expired")

    # Check nonce (replay protection)
    if nonce:
        nonce_key = f"nonce:{nonce}"
        already_used = await redis.set(nonce_key, "1", nx=True, ex=settings.nonce_ttl_seconds)
        if not already_used:
            raise HTTPException(status_code=403, detail="Nonce already used")

    # Look up agent
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=403, detail="Agent not found")

    if agent.status != AgentStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Agent is not active")

    # Read body for signature verification
    body = await request.body()
    method = request.method.upper()
    path = request.url.path

    # Verify signature
    if not verify_signature(agent.public_key, signature, timestamp, method, path, body):
        raise HTTPException(status_code=403, detail="Invalid signature")

    return AuthenticatedAgent(agent_id=agent_id, agent=agent)
