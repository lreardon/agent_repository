"""Admin API endpoints — platform management via API key auth.

All endpoints require a valid X-Admin-Key header.
Admin is disabled when admin_api_keys is empty in config.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.config import settings as app_settings
from app.database import get_db
from app.models.agent import Agent, AgentStatus
from app.models.account import Account
from app.models.escrow import EscrowAccount, EscrowStatus
from app.models.job import Job, JobStatus
from app.models.wallet import (
    DepositTransaction,
    DepositStatus,
    WithdrawalRequest,
    WithdrawalStatus,
)
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.schemas.admin import (
    AdminAccountSummary,
    AdminAgentDetail,
    AdminAgentSummary,
    AdminDepositSummary,
    AdminEscrowSummary,
    AdminJobDetail,
    AdminJobSummary,
    AdminWebhookSummary,
    AdminWithdrawalSummary,
    AgentStatusUpdate,
    EscrowRefundRequest,
    JobStatusUpdate,
    PlatformStats,
)
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix=app_settings.admin_path_prefix,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    include_in_schema=False,  # Admin endpoints are internal — not exposed in public OpenAPI docs
)


# ═══════════════════════════════════════════════════════════════════════════
# Platform stats
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/stats", response_model=PlatformStats)
async def get_platform_stats(db: AsyncSession = Depends(get_db)) -> PlatformStats:
    """Aggregate platform statistics."""

    # Agent counts by status
    agent_q = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(Agent.status == AgentStatus.ACTIVE).label("active"),
            func.count().filter(Agent.status == AgentStatus.SUSPENDED).label("suspended"),
            func.count().filter(Agent.status == AgentStatus.DEACTIVATED).label("deactivated"),
        )
    )
    agent_row = agent_q.one()

    # Account counts
    acct_q = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(Account.email_verified.is_(True)).label("verified"),
        )
    )
    acct_row = acct_q.one()

    # Job counts by status
    job_q = await db.execute(
        select(Job.status, func.count()).group_by(Job.status)
    )
    jobs_by_status = {row[0].value: row[1] for row in job_q.all()}
    total_jobs = sum(jobs_by_status.values())

    # Escrow held (funded only)
    escrow_q = await db.execute(
        select(func.coalesce(func.sum(EscrowAccount.amount), 0)).where(
            EscrowAccount.status == EscrowStatus.FUNDED
        )
    )
    total_escrow_held = escrow_q.scalar() or Decimal("0")

    # Deposit totals (credited only)
    dep_q = await db.execute(
        select(func.coalesce(func.sum(DepositTransaction.amount_credits), 0)).where(
            DepositTransaction.status == DepositStatus.CREDITED
        )
    )
    total_deposits = dep_q.scalar() or Decimal("0")

    # Withdrawal totals (completed only) + pending count
    wd_q = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (WithdrawalRequest.status == WithdrawalStatus.COMPLETED, WithdrawalRequest.net_payout),
                        else_=Decimal("0"),
                    )
                ),
                0,
            ).label("total"),
            func.count().filter(
                WithdrawalRequest.status.in_([WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING])
            ).label("pending"),
        )
    )
    wd_row = wd_q.one()

    # Webhook stats
    wh_q = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(WebhookDelivery.status == WebhookStatus.FAILED).label("failed"),
        )
    )
    wh_row = wh_q.one()

    return PlatformStats(
        total_agents=agent_row.total,
        active_agents=agent_row.active,
        suspended_agents=agent_row.suspended,
        deactivated_agents=agent_row.deactivated,
        total_accounts=acct_row.total,
        verified_accounts=acct_row.verified,
        total_jobs=total_jobs,
        jobs_by_status=jobs_by_status,
        total_escrow_held=total_escrow_held,
        total_deposits=total_deposits,
        total_withdrawals=wd_row.total,
        pending_withdrawals=wd_row.pending,
        total_webhook_deliveries=wh_row.total,
        failed_webhook_deliveries=wh_row.failed,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Agents
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/agents", response_model=PaginatedResponse[AdminAgentSummary])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter: active, suspended, deactivated"),
    search: str | None = Query(None, description="Search display_name (case-insensitive contains)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminAgentSummary]:
    """List all agents with optional filters."""
    query = select(Agent)
    count_query = select(func.count()).select_from(Agent)

    if status:
        try:
            status_enum = AgentStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(Agent.status == status_enum)
        count_query = count_query.where(Agent.status == status_enum)

    if search:
        query = query.where(Agent.display_name.ilike(f"%{search}%"))
        count_query = count_query.where(Agent.display_name.ilike(f"%{search}%"))

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(Agent.created_at.desc()).offset(offset).limit(limit)
    )
    agents = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminAgentSummary(
                agent_id=a.agent_id,
                display_name=a.display_name,
                status=a.status.value,
                balance=a.balance,
                reputation_seller=a.reputation_seller,
                reputation_client=a.reputation_client,
                is_online=a.is_online,
                moltbook_verified=a.moltbook_verified,
                created_at=a.created_at,
            )
            for a in agents
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/agents/{agent_id}", response_model=AdminAgentDetail)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AdminAgentDetail:
    """Get full agent details."""
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AdminAgentDetail(
        agent_id=agent.agent_id,
        display_name=agent.display_name,
        status=agent.status.value,
        balance=agent.balance,
        reputation_seller=agent.reputation_seller,
        reputation_client=agent.reputation_client,
        is_online=agent.is_online,
        moltbook_verified=agent.moltbook_verified,
        created_at=agent.created_at,
        public_key=agent.public_key,
        description=agent.description,
        endpoint_url=agent.endpoint_url,
        capabilities=agent.capabilities,
        moltbook_id=agent.moltbook_id,
        moltbook_username=agent.moltbook_username,
        moltbook_karma=agent.moltbook_karma,
        last_seen=agent.last_seen,
        last_connected_at=agent.last_connected_at,
    )


@router.patch("/agents/{agent_id}/status")
async def update_agent_status(
    agent_id: uuid.UUID,
    data: AgentStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdminAgentDetail:
    """Admin: change agent status (suspend, reactivate, deactivate)."""
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        new_status = AgentStatus(data.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(s.value for s in AgentStatus)}",
        )

    old_status = agent.status
    agent.status = new_status
    await db.commit()
    await db.refresh(agent)

    logger.info(
        "Admin changed agent %s status: %s → %s (reason: %s)",
        agent_id, old_status.value, new_status.value, data.reason or "none",
    )

    return AdminAgentDetail(
        agent_id=agent.agent_id,
        display_name=agent.display_name,
        status=agent.status.value,
        balance=agent.balance,
        reputation_seller=agent.reputation_seller,
        reputation_client=agent.reputation_client,
        is_online=agent.is_online,
        moltbook_verified=agent.moltbook_verified,
        created_at=agent.created_at,
        public_key=agent.public_key,
        description=agent.description,
        endpoint_url=agent.endpoint_url,
        capabilities=agent.capabilities,
        moltbook_id=agent.moltbook_id,
        moltbook_username=agent.moltbook_username,
        moltbook_karma=agent.moltbook_karma,
        last_seen=agent.last_seen,
        last_connected_at=agent.last_connected_at,
    )


@router.patch("/agents/{agent_id}/balance")
async def adjust_agent_balance(
    agent_id: uuid.UUID,
    amount: Decimal = Query(..., description="Amount to add (positive) or deduct (negative)"),
    reason: str = Query("", description="Admin reason"),
    db: AsyncSession = Depends(get_db),
) -> AdminAgentDetail:
    """Admin: manually adjust agent balance."""
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    new_balance = agent.balance + amount
    if new_balance < 0:
        raise HTTPException(status_code=400, detail="Adjustment would result in negative balance")

    old_balance = agent.balance
    agent.balance = new_balance
    await db.commit()
    await db.refresh(agent)

    logger.info(
        "Admin adjusted agent %s balance: %s → %s (delta: %s, reason: %s)",
        agent_id, old_balance, new_balance, amount, reason or "none",
    )

    return AdminAgentDetail(
        agent_id=agent.agent_id,
        display_name=agent.display_name,
        status=agent.status.value,
        balance=agent.balance,
        reputation_seller=agent.reputation_seller,
        reputation_client=agent.reputation_client,
        is_online=agent.is_online,
        moltbook_verified=agent.moltbook_verified,
        created_at=agent.created_at,
        public_key=agent.public_key,
        description=agent.description,
        endpoint_url=agent.endpoint_url,
        capabilities=agent.capabilities,
        moltbook_id=agent.moltbook_id,
        moltbook_username=agent.moltbook_username,
        moltbook_karma=agent.moltbook_karma,
        last_seen=agent.last_seen,
        last_connected_at=agent.last_connected_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Jobs
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/jobs", response_model=PaginatedResponse[AdminJobSummary])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by job status"),
    agent_id: uuid.UUID | None = Query(None, description="Filter by client or seller agent_id"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminJobSummary]:
    """List all jobs with optional filters."""
    query = select(Job)
    count_query = select(func.count()).select_from(Job)

    if status:
        try:
            status_enum = JobStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(Job.status == status_enum)
        count_query = count_query.where(Job.status == status_enum)

    if agent_id:
        agent_filter = (Job.client_agent_id == agent_id) | (Job.seller_agent_id == agent_id)
        query = query.where(agent_filter)
        count_query = count_query.where(agent_filter)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(Job.created_at.desc()).offset(offset).limit(limit)
    )
    jobs = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminJobSummary(
                job_id=j.job_id,
                client_agent_id=j.client_agent_id,
                seller_agent_id=j.seller_agent_id,
                status=j.status.value,
                agreed_price=j.agreed_price,
                created_at=j.created_at,
                updated_at=j.updated_at,
            )
            for j in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=AdminJobDetail)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AdminJobDetail:
    """Get full job details."""
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return AdminJobDetail(
        job_id=job.job_id,
        client_agent_id=job.client_agent_id,
        seller_agent_id=job.seller_agent_id,
        status=job.status.value,
        agreed_price=job.agreed_price,
        created_at=job.created_at,
        updated_at=job.updated_at,
        listing_id=job.listing_id,
        a2a_task_id=job.a2a_task_id,
        acceptance_criteria=job.acceptance_criteria,
        requirements=job.requirements,
        delivery_deadline=job.delivery_deadline,
        negotiation_log=job.negotiation_log,
        current_round=job.current_round,
        max_rounds=job.max_rounds,
        result=job.result,
    )


@router.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: uuid.UUID,
    data: JobStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdminJobDetail:
    """Admin: force-change job status (cancel, complete, fail).

    For jobs with escrow, use the escrow force-refund endpoint separately.
    """
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        new_status = JobStatus(data.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(s.value for s in JobStatus)}",
        )

    # Admin can force any transition, but log it
    old_status = job.status
    job.status = new_status
    job.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(job)

    logger.warning(
        "Admin force-changed job %s status: %s → %s (reason: %s)",
        job_id, old_status.value, new_status.value, data.reason or "none",
    )

    return AdminJobDetail(
        job_id=job.job_id,
        client_agent_id=job.client_agent_id,
        seller_agent_id=job.seller_agent_id,
        status=job.status.value,
        agreed_price=job.agreed_price,
        created_at=job.created_at,
        updated_at=job.updated_at,
        listing_id=job.listing_id,
        a2a_task_id=job.a2a_task_id,
        acceptance_criteria=job.acceptance_criteria,
        requirements=job.requirements,
        delivery_deadline=job.delivery_deadline,
        negotiation_log=job.negotiation_log,
        current_round=job.current_round,
        max_rounds=job.max_rounds,
        result=job.result,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Escrow
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/escrow", response_model=PaginatedResponse[AdminEscrowSummary])
async def list_escrows(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by escrow status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminEscrowSummary]:
    """List all escrow accounts."""
    query = select(EscrowAccount)
    count_query = select(func.count()).select_from(EscrowAccount)

    if status:
        try:
            status_enum = EscrowStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(EscrowAccount.status == status_enum)
        count_query = count_query.where(EscrowAccount.status == status_enum)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(EscrowAccount.funded_at.desc().nulls_last()).offset(offset).limit(limit)
    )
    escrows = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminEscrowSummary(
                escrow_id=e.escrow_id,
                job_id=e.job_id,
                client_agent_id=e.client_agent_id,
                seller_agent_id=e.seller_agent_id,
                amount=e.amount,
                seller_bond_amount=e.seller_bond_amount,
                status=e.status.value,
                funded_at=e.funded_at,
                released_at=e.released_at,
            )
            for e in escrows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/escrow/{escrow_id}/force-refund")
async def force_refund_escrow(
    escrow_id: uuid.UUID,
    data: EscrowRefundRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminEscrowSummary:
    """Admin: force-refund an escrow back to the client.

    Only works on funded escrows. Returns funds to client balance,
    returns seller bond if any, and marks escrow as refunded.
    """
    escrow = await db.get(EscrowAccount, escrow_id)
    if not escrow:
        raise HTTPException(status_code=404, detail="Escrow not found")

    if escrow.status != EscrowStatus.FUNDED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only refund funded escrows (current: {escrow.status.value})",
        )

    # Refund client
    client = await db.get(Agent, escrow.client_agent_id)
    if client:
        client.balance += escrow.amount

    # Return seller bond
    if escrow.seller_bond_amount > 0:
        seller = await db.get(Agent, escrow.seller_agent_id)
        if seller:
            seller.balance += escrow.seller_bond_amount

    escrow.status = EscrowStatus.REFUNDED
    escrow.released_at = datetime.now(UTC)

    # Create audit log entry
    from app.models.escrow import EscrowAuditLog, EscrowAction
    audit = EscrowAuditLog(
        escrow_id=escrow.escrow_id,
        action=EscrowAction.REFUNDED,
        actor_agent_id=None,  # System/admin action
        amount=escrow.amount,
        metadata_={"admin_force_refund": True, "reason": data.reason or "none"},
    )
    db.add(audit)

    await db.commit()
    await db.refresh(escrow)

    logger.warning(
        "Admin force-refunded escrow %s (amount: %s, bond: %s, reason: %s)",
        escrow_id, escrow.amount, escrow.seller_bond_amount, data.reason or "none",
    )

    return AdminEscrowSummary(
        escrow_id=escrow.escrow_id,
        job_id=escrow.job_id,
        client_agent_id=escrow.client_agent_id,
        seller_agent_id=escrow.seller_agent_id,
        amount=escrow.amount,
        seller_bond_amount=escrow.seller_bond_amount,
        status=escrow.status.value,
        funded_at=escrow.funded_at,
        released_at=escrow.released_at,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Accounts
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/accounts", response_model=PaginatedResponse[AdminAccountSummary])
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    verified: bool | None = Query(None, description="Filter by email verification status"),
    search: str | None = Query(None, description="Search email (case-insensitive contains)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminAccountSummary]:
    """List all accounts."""
    query = select(Account)
    count_query = select(func.count()).select_from(Account)

    if verified is not None:
        query = query.where(Account.email_verified.is_(verified))
        count_query = count_query.where(Account.email_verified.is_(verified))

    if search:
        query = query.where(Account.email.ilike(f"%{search}%"))
        count_query = count_query.where(Account.email.ilike(f"%{search}%"))

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(Account.created_at.desc()).offset(offset).limit(limit)
    )
    accounts = result.scalars().all()

    return PaginatedResponse(
        items=[AdminAccountSummary.model_validate(a) for a in accounts],
        total=total,
        limit=limit,
        offset=offset,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Wallet (deposits & withdrawals)
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/deposits", response_model=PaginatedResponse[AdminDepositSummary])
async def list_deposits(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by deposit status"),
    agent_id: uuid.UUID | None = Query(None, description="Filter by agent"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminDepositSummary]:
    """List all deposit transactions."""
    query = select(DepositTransaction)
    count_query = select(func.count()).select_from(DepositTransaction)

    if status:
        try:
            status_enum = DepositStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(DepositTransaction.status == status_enum)
        count_query = count_query.where(DepositTransaction.status == status_enum)

    if agent_id:
        query = query.where(DepositTransaction.agent_id == agent_id)
        count_query = count_query.where(DepositTransaction.agent_id == agent_id)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(DepositTransaction.detected_at.desc()).offset(offset).limit(limit)
    )
    deposits = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminDepositSummary(
                deposit_tx_id=d.deposit_tx_id,
                agent_id=d.agent_id,
                tx_hash=d.tx_hash,
                amount_usdc=d.amount_usdc,
                amount_credits=d.amount_credits,
                status=d.status.value,
                block_number=d.block_number,
                confirmations=d.confirmations,
                detected_at=d.detected_at,
                credited_at=d.credited_at,
            )
            for d in deposits
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/withdrawals", response_model=PaginatedResponse[AdminWithdrawalSummary])
async def list_withdrawals(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by withdrawal status"),
    agent_id: uuid.UUID | None = Query(None, description="Filter by agent"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminWithdrawalSummary]:
    """List all withdrawal requests."""
    query = select(WithdrawalRequest)
    count_query = select(func.count()).select_from(WithdrawalRequest)

    if status:
        try:
            status_enum = WithdrawalStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(WithdrawalRequest.status == status_enum)
        count_query = count_query.where(WithdrawalRequest.status == status_enum)

    if agent_id:
        query = query.where(WithdrawalRequest.agent_id == agent_id)
        count_query = count_query.where(WithdrawalRequest.agent_id == agent_id)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(WithdrawalRequest.requested_at.desc()).offset(offset).limit(limit)
    )
    withdrawals = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminWithdrawalSummary(
                withdrawal_id=w.withdrawal_id,
                agent_id=w.agent_id,
                amount=w.amount,
                fee=w.fee,
                net_payout=w.net_payout,
                destination_address=w.destination_address,
                status=w.status.value,
                tx_hash=w.tx_hash,
                requested_at=w.requested_at,
                processed_at=w.processed_at,
                error_message=w.error_message,
            )
            for w in withdrawals
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Webhooks
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/webhooks", response_model=PaginatedResponse[AdminWebhookSummary])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None, description="Filter by webhook status"),
    agent_id: uuid.UUID | None = Query(None, description="Filter by target agent"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[AdminWebhookSummary]:
    """List all webhook deliveries."""
    query = select(WebhookDelivery)
    count_query = select(func.count()).select_from(WebhookDelivery)

    if status:
        try:
            status_enum = WebhookStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        query = query.where(WebhookDelivery.status == status_enum)
        count_query = count_query.where(WebhookDelivery.status == status_enum)

    if agent_id:
        query = query.where(WebhookDelivery.target_agent_id == agent_id)
        count_query = count_query.where(WebhookDelivery.target_agent_id == agent_id)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(WebhookDelivery.created_at.desc()).offset(offset).limit(limit)
    )
    deliveries = result.scalars().all()

    return PaginatedResponse(
        items=[
            AdminWebhookSummary(
                delivery_id=d.delivery_id,
                target_agent_id=d.target_agent_id,
                event_type=d.event_type,
                status=d.status.value,
                attempts=d.attempts,
                last_error=d.last_error,
                created_at=d.created_at,
            )
            for d in deliveries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/webhooks/{delivery_id}/redeliver")
async def redeliver_webhook(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AdminWebhookSummary:
    """Admin: reset a webhook delivery to pending for redelivery."""
    delivery = await db.get(WebhookDelivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")

    old_status = delivery.status
    delivery.status = WebhookStatus.PENDING
    delivery.attempts = 0
    delivery.last_error = None
    await db.commit()
    await db.refresh(delivery)

    logger.info(
        "Admin reset webhook %s for redelivery (was: %s)",
        delivery_id, old_status.value,
    )

    return AdminWebhookSummary(
        delivery_id=delivery.delivery_id,
        target_agent_id=delivery.target_agent_id,
        event_type=delivery.event_type,
        status=delivery.status.value,
        attempts=delivery.attempts,
        last_error=delivery.last_error,
        created_at=delivery.created_at,
    )
