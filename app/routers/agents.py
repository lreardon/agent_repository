"""Agent CRUD + balance + reputation + agent-card endpoints."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    BalanceResponse,
    ReputationResponse,
)
from app.services import agent as agent_service
from app.services import review as review_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentResponse, status_code=201, dependencies=[Depends(check_rate_limit)])
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


@router.patch("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(check_rate_limit)])
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


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(check_rate_limit)])
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


from pydantic import BaseModel as _BaseModel

class DevDepositRequest(_BaseModel):
    amount: str

@router.post("/{agent_id}/deposit", response_model=BalanceResponse, dependencies=[Depends(check_rate_limit)])
async def dev_deposit(
    agent_id: uuid.UUID,
    data: DevDepositRequest,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Dev-only: credit agent balance directly. Disabled unless DEV_DEPOSIT_ENABLED=true."""
    from fastapi import HTTPException
    if settings.env == "production" or not settings.dev_deposit_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    agent = await agent_service.deposit(db, agent_id, Decimal(data.amount))
    return BalanceResponse(agent_id=agent.agent_id, balance=agent.balance)


