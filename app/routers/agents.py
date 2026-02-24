"""Agent CRUD + balance + reputation + agent-card endpoints."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    BalanceResponse,
    DepositRequest,
    ReputationResponse,
)
from app.services import agent as agent_service
from app.services import review as review_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentResponse, status_code=201)
async def register_agent(
    data: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Register a new agent. Fetches and validates A2A Agent Card."""
    from app.config import settings
    agent = await agent_service.register_agent(db, data, skip_card_fetch=not settings.require_agent_card)
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(check_rate_limit)])
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Get agent profile (includes cached A2A Agent Card)."""
    agent = await agent_service.get_agent(db, agent_id)
    return AgentResponse.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Update agent profile. Re-fetches Agent Card if endpoint_url changes."""
    if auth.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Can only update own agent")
    from app.config import settings
    agent = await agent_service.update_agent(db, agent_id, data, skip_card_fetch=not settings.require_agent_card)
    return AgentResponse.model_validate(agent)


@router.delete("/{agent_id}", status_code=204)
async def deactivate_agent(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Deactivate agent. Own agent only."""
    if auth.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Can only deactivate own agent")
    await agent_service.deactivate_agent(db, agent_id)
    return Response(status_code=204)


@router.get(
    "/{agent_id}/agent-card",
    dependencies=[Depends(check_rate_limit)],
)
async def get_agent_card(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the agent's cached A2A Agent Card."""
    agent = await agent_service.get_agent(db, agent_id)
    if agent.a2a_agent_card is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent has no cached Agent Card")
    return agent.a2a_agent_card


@router.get(
    "/{agent_id}/reputation",
    response_model=ReputationResponse,
    dependencies=[Depends(check_rate_limit)],
)
async def get_reputation(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReputationResponse:
    """Computed reputation scores + review summary."""
    return await review_service.get_reputation(db, agent_id)


@router.get("/{agent_id}/balance", response_model=BalanceResponse, dependencies=[Depends(check_rate_limit)])
async def get_balance(
    agent_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Check agent balance. Own agent only."""
    if auth.agent_id != agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Can only view own balance")
    agent = await agent_service.get_balance(db, agent_id)
    return BalanceResponse(agent_id=agent.agent_id, balance=agent.balance)


@router.post("/{agent_id}/deposit", response_model=BalanceResponse)
async def deposit(
    agent_id: uuid.UUID,
    data: DepositRequest,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Add credits to agent balance (development/test only).

    In production, deposits happen via USDC transfers to the agent's deposit address.
    See GET /agents/{agent_id}/wallet/deposit-address
    """
    from app.config import settings
    from fastapi import HTTPException

    if settings.env not in ("development", "test"):
        raise HTTPException(
            status_code=403,
            detail="Direct deposits disabled in production. Use USDC deposit via /wallet/deposit-address",
        )
    if auth.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only deposit to own account")
    agent = await agent_service.deposit(db, agent_id, data.amount)
    return BalanceResponse(agent_id=agent.agent_id, balance=agent.balance)
