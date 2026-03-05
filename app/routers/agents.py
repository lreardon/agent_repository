"""Agent CRUD + balance + reputation + agent-card endpoints."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.agent import (
    AgentCreate,
    AgentResponse,
    AgentStatusResponse,
    AgentUpdate,
    BalanceResponse,
    ReputationResponse,
)
from app.schemas.errors import AUTH_ERRORS, OWNER_ERRORS, PUBLIC_ERRORS
from app.services import agent as agent_service
from app.services import review as review_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentResponse, status_code=201, dependencies=[Depends(check_rate_limit)],
             responses={**AUTH_ERRORS, 400: {"description": "Invalid agent card or registration token"}})
async def register_agent(
    data: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Register a new agent. Fetches and validates A2A Agent Card."""
    from app.config import settings
    agent = await agent_service.register_agent(db, data, skip_card_fetch=not settings.require_agent_card)
    return AgentResponse.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(check_rate_limit)],
            responses=PUBLIC_ERRORS)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Get agent profile (includes cached A2A Agent Card)."""
    agent = await agent_service.get_agent(db, agent_id)
    return AgentResponse.model_validate(agent)


@router.get(
    "/{agent_id}/status",
    response_model=AgentStatusResponse,
    dependencies=[Depends(check_rate_limit)],
    responses=PUBLIC_ERRORS,
)
async def get_agent_status(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AgentStatusResponse | HTMLResponse:
    """Public agent readiness status. Returns JSON or HTML based on Accept header."""
    agent = await agent_service.get_agent(db, agent_id)
    status_data = AgentStatusResponse.model_validate(agent)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        is_active = status_data.status == "active"
        indicator = "\u2705" if is_active else "\u274c"
        status_color = "#4ade80" if is_active else "#ef4444"
        caps = ", ".join(status_data.capabilities or []) or "none"
        html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{status_data.display_name} — Arcoa Agent Status</title>
<style>
  body {{ margin:0; background:#111; color:#e0e0e0; font-family:system-ui,sans-serif;
         display:flex; justify-content:center; align-items:center; min-height:100vh; }}
  .card {{ background:#1a1a1a; border:1px solid #333; border-radius:12px;
           max-width:520px; width:90%; padding:2.5rem; }}
  h1 {{ color:#fff; margin:0 0 .5rem; font-size:1.5rem; }}
  .status {{ font-size:1.1rem; margin:.75rem 0; color:{status_color}; }}
  .field {{ margin:.75rem 0; }}
  .label {{ color:#888; font-size:.8rem; text-transform:uppercase; letter-spacing:.05em; }}
  .value {{ margin-top:.25rem; }}
  .mono {{ font-family:monospace; font-size:.85rem; color:#ccc; }}
</style>
</head>
<body>
<div class="card">
  <h1>{status_data.display_name}</h1>
  <div class="status">{indicator} {status_data.status.upper()}</div>
  <div class="field">
    <div class="label">Agent ID</div>
    <div class="value mono">{status_data.agent_id}</div>
  </div>
  <div class="field">
    <div class="label">Capabilities</div>
    <div class="value">{caps}</div>
  </div>
  <div class="field">
    <div class="label">Registered</div>
    <div class="value">{status_data.created_at.strftime("%Y-%m-%d %H:%M UTC")}</div>
  </div>
  <div class="field">
    <div class="label">Last Seen</div>
    <div class="value">{status_data.last_seen.strftime("%Y-%m-%d %H:%M UTC")}</div>
  </div>
</div>
</body>
</html>"""
        return HTMLResponse(content=html)

    return status_data


@router.patch("/{agent_id}", response_model=AgentResponse, dependencies=[Depends(check_rate_limit)],
              responses=OWNER_ERRORS)
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


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(check_rate_limit)],
               responses=OWNER_ERRORS)
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
    responses=PUBLIC_ERRORS,
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
    responses=PUBLIC_ERRORS,
)
async def get_reputation(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReputationResponse:
    """Computed reputation scores + review summary."""
    return await review_service.get_reputation(db, agent_id)


@router.get("/{agent_id}/balance", response_model=BalanceResponse, dependencies=[Depends(check_rate_limit)],
            responses=OWNER_ERRORS)
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


from app.schemas.agent import DepositRequest

@router.post("/{agent_id}/deposit", response_model=BalanceResponse, dependencies=[Depends(check_rate_limit)],
             responses=OWNER_ERRORS)
async def dev_deposit(
    agent_id: uuid.UUID,
    data: DepositRequest,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    """Dev-only: credit agent balance directly. Disabled unless DEV_DEPOSIT_ENABLED=true."""
    from fastapi import HTTPException
    if settings.env == "production" or not settings.dev_deposit_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if auth.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only deposit to own agent")
    agent = await agent_service.deposit(db, agent_id, data.amount)
    return BalanceResponse(agent_id=agent.agent_id, balance=agent.balance)


