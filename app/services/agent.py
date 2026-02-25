"""Agent business logic."""

import logging
import secrets
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent, AgentStatus
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_card import (
    AgentCardError,
    extract_capabilities_from_card,
    fetch_agent_card,
)

logger = logging.getLogger(__name__)


async def register_agent(
    db: AsyncSession, data: AgentCreate, skip_card_fetch: bool = False
) -> Agent:
    """Register a new agent.

    Fetches and validates the A2A Agent Card from the agent's endpoint_url.
    Set skip_card_fetch=True for testing without a live A2A server.
    If the card fetch fails and the agent provides capabilities, we allow
    registration with those capabilities (graceful degradation for v1).
    """
    # Check for duplicate public key
    result = await db.execute(
        select(Agent).where(Agent.public_key == data.public_key)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Public key already registered")

    # Fetch and validate A2A Agent Card
    a2a_card = None
    capabilities = data.capabilities or []

    if not skip_card_fetch:
        try:
            a2a_card = await fetch_agent_card(data.endpoint_url)
            # Derive capabilities from Agent Card skills tags
            capabilities = extract_capabilities_from_card(a2a_card)
        except AgentCardError as e:
            raise HTTPException(status_code=422, detail=f"Agent Card validation failed: {e}")

    # MoltBook identity verification
    moltbook_profile = None
    if data.moltbook_identity_token:
        from app.services.moltbook import verify_identity_token

        moltbook_profile = await verify_identity_token(data.moltbook_identity_token)

        # Check if this MoltBook identity is already registered
        existing = await db.execute(
            select(Agent).where(Agent.moltbook_id == moltbook_profile.moltbook_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail="This MoltBook identity is already linked to an agent",
            )
    elif settings.moltbook_required:
        raise HTTPException(
            status_code=422,
            detail="MoltBook identity token is required for registration. "
                   "Get one at: https://moltbook.com/auth.md?app=agent-registry",
        )

    webhook_secret = secrets.token_hex(32)
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=data.public_key,
        display_name=data.display_name,
        description=data.description,
        endpoint_url=data.endpoint_url,
        capabilities=capabilities,
        a2a_agent_card=a2a_card,
        webhook_secret=webhook_secret,
        moltbook_id=moltbook_profile.moltbook_id if moltbook_profile else None,
        moltbook_username=moltbook_profile.username if moltbook_profile else None,
        moltbook_karma=moltbook_profile.karma if moltbook_profile else None,
        moltbook_verified=moltbook_profile.verified if moltbook_profile else False,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    if moltbook_profile:
        logger.info(
            "Agent %s registered with MoltBook identity: @%s (karma=%d, verified=%s)",
            agent.agent_id, moltbook_profile.username,
            moltbook_profile.karma, moltbook_profile.verified,
        )

    return agent


async def get_agent(db: AsyncSession, agent_id: uuid.UUID) -> Agent:
    """Get agent by ID."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def update_agent(
    db: AsyncSession,
    agent_id: uuid.UUID,
    data: AgentUpdate,
    skip_card_fetch: bool = False,
) -> Agent:
    """Update an agent's mutable fields. Re-fetches Agent Card if endpoint_url changes."""
    agent = await get_agent(db, agent_id)

    update_data = data.model_dump(exclude_unset=True)

    # If endpoint_url is changing, re-fetch the Agent Card
    if "endpoint_url" in update_data and not skip_card_fetch:
        try:
            a2a_card = await fetch_agent_card(update_data["endpoint_url"])
            agent.a2a_agent_card = a2a_card
            agent.capabilities = extract_capabilities_from_card(a2a_card)
        except AgentCardError as e:
            raise HTTPException(status_code=422, detail=f"Agent Card validation failed: {e}")

    for field, value in update_data.items():
        setattr(agent, field, value)

    await db.commit()
    await db.refresh(agent)
    return agent


async def deactivate_agent(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Soft-delete an agent by setting status to deactivated."""
    agent = await get_agent(db, agent_id)
    agent.status = AgentStatus.DEACTIVATED
    await db.commit()


async def get_balance(db: AsyncSession, agent_id: uuid.UUID) -> Agent:
    """Get agent balance."""
    return await get_agent(db, agent_id)


async def deposit(
    db: AsyncSession, agent_id: uuid.UUID, amount: Decimal
) -> Agent:
    """Add credits to an agent's balance. Uses SELECT FOR UPDATE."""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id).with_for_update()
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.balance = agent.balance + amount
    await db.commit()
    await db.refresh(agent)
    return agent
