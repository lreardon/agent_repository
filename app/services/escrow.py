"""Escrow business logic — fund, release, refund with row-level locking."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.escrow import (
    EscrowAccount,
    EscrowAction,
    EscrowAuditLog,
    EscrowStatus,
)
from app.models.job import Job, JobStatus


async def _log_audit(
    db: AsyncSession,
    escrow_id: uuid.UUID,
    action: EscrowAction,
    amount: Decimal,
    actor_agent_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """Append to the immutable audit log."""
    entry = EscrowAuditLog(
        escrow_audit_id=uuid.uuid4(),
        escrow_id=escrow_id,
        action=action,
        actor_agent_id=actor_agent_id,
        amount=amount,
        metadata_=metadata,
    )
    db.add(entry)


async def fund_job(
    db: AsyncSession, job_id: uuid.UUID, client_agent_id: uuid.UUID
) -> EscrowAccount:
    """Fund escrow for a job. Atomic: debit client balance + create escrow.

    Uses SELECT FOR UPDATE on the agent's balance row to prevent double-spend.
    """
    # Get job and validate state
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.AGREED:
        raise HTTPException(status_code=409, detail=f"Job must be in agreed status, currently {job.status.value}")
    if job.client_agent_id != client_agent_id:
        raise HTTPException(status_code=403, detail="Only the client can fund the escrow")

    amount = job.agreed_price
    if amount is None or amount <= 0:
        raise HTTPException(status_code=422, detail="Job has no agreed price")

    # Check for existing escrow (prevent double-fund)
    existing = await db.execute(
        select(EscrowAccount).where(EscrowAccount.job_id == job_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Escrow already exists for this job")

    # Lock the client's balance row — critical for preventing double-spend
    result = await db.execute(
        select(Agent).where(Agent.agent_id == client_agent_id).with_for_update()
    )
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client agent not found")

    # Check sufficient balance AFTER acquiring the lock
    if client.balance < amount:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient balance: {client.balance} < {amount}",
        )

    # Atomic: debit client + create funded escrow
    client.balance = client.balance - amount

    now = datetime.now(UTC)
    escrow = EscrowAccount(
        escrow_id=uuid.uuid4(),
        job_id=job_id,
        client_agent_id=client_agent_id,
        seller_agent_id=job.seller_agent_id,
        amount=amount,
        status=EscrowStatus.FUNDED,
        funded_at=now,
    )
    db.add(escrow)

    # Transition job to funded
    job.status = JobStatus.FUNDED

    # Audit log
    await _log_audit(db, escrow.escrow_id, EscrowAction.CREATED, amount, client_agent_id)
    await _log_audit(db, escrow.escrow_id, EscrowAction.FUNDED, amount, client_agent_id)

    await db.commit()
    await db.refresh(escrow)
    return escrow


async def release_escrow(
    db: AsyncSession, job_id: uuid.UUID
) -> EscrowAccount:
    """Release escrow to seller on job completion. Platform takes fee.

    Called by the platform after acceptance tests pass.
    """
    result = await db.execute(
        select(EscrowAccount).where(EscrowAccount.job_id == job_id).with_for_update()
    )
    escrow = result.scalar_one_or_none()
    if escrow is None:
        raise HTTPException(status_code=404, detail="Escrow not found for this job")
    if escrow.status != EscrowStatus.FUNDED:
        raise HTTPException(status_code=409, detail=f"Escrow must be funded, currently {escrow.status.value}")

    # Calculate base marketplace fee (split between client and seller)
    from app.services.fees import calculate_base_fee, charge_fee
    client_base_fee, seller_base_fee = calculate_base_fee(escrow.amount)
    total_fee = client_base_fee.amount + seller_base_fee.amount
    seller_payout = escrow.amount - seller_base_fee.amount

    # Charge client's share of the base fee from their balance
    # (separate from the escrow — client pays agreed_price + their fee share)
    result = await db.execute(
        select(Agent).where(Agent.agent_id == escrow.client_agent_id).with_for_update()
    )
    client = result.scalar_one_or_none()
    if client is not None and client.balance >= client_base_fee.amount:
        client.balance -= client_base_fee.amount
    # If client can't cover their base fee share, it's absorbed by the platform
    # (the job still completes — we don't block completion over a small fee)

    # Lock seller's balance row
    result = await db.execute(
        select(Agent).where(Agent.agent_id == escrow.seller_agent_id).with_for_update()
    )
    seller = result.scalar_one_or_none()
    if seller is None:
        raise HTTPException(status_code=404, detail="Seller agent not found")

    # Credit seller (minus their base fee share)
    seller.balance = seller.balance + seller_payout

    # Update escrow
    now = datetime.now(UTC)
    escrow.status = EscrowStatus.RELEASED
    escrow.released_at = now

    # Update job — must be in delivered (or verifying) status
    job_result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = job_result.scalar_one()
    if job.status not in (JobStatus.DELIVERED, JobStatus.VERIFYING):
        raise HTTPException(status_code=409, detail=f"Job must be delivered to complete, currently {job.status.value}")
    job.status = JobStatus.COMPLETED

    # Audit
    await _log_audit(
        db, escrow.escrow_id, EscrowAction.RELEASED, seller_payout, None,
        {
            "total_fee": str(total_fee),
            "client_base_fee": str(client_base_fee.amount),
            "seller_base_fee": str(seller_base_fee.amount),
            "fee_base_percent": str(settings.fee_base_percent),
        },
    )

    await db.commit()
    await db.refresh(escrow)
    return escrow


async def refund_escrow(
    db: AsyncSession, job_id: uuid.UUID
) -> EscrowAccount:
    """Refund escrow to client on job failure."""
    result = await db.execute(
        select(EscrowAccount).where(EscrowAccount.job_id == job_id).with_for_update()
    )
    escrow = result.scalar_one_or_none()
    if escrow is None:
        raise HTTPException(status_code=404, detail="Escrow not found for this job")
    if escrow.status != EscrowStatus.FUNDED:
        raise HTTPException(status_code=409, detail=f"Escrow must be funded, currently {escrow.status.value}")

    # Lock client's balance row
    result = await db.execute(
        select(Agent).where(Agent.agent_id == escrow.client_agent_id).with_for_update()
    )
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Client agent not found")

    # Full refund
    client.balance = client.balance + escrow.amount

    escrow.status = EscrowStatus.REFUNDED
    escrow.released_at = datetime.now(UTC)

    # Update job to failed if not already
    job_result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = job_result.scalar_one()
    if job.status != JobStatus.FAILED:
        job.status = JobStatus.FAILED

    await _log_audit(db, escrow.escrow_id, EscrowAction.REFUNDED, escrow.amount, None)

    await db.commit()
    await db.refresh(escrow)
    return escrow
