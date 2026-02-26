"""Job lifecycle and negotiation business logic."""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.models.agent import Agent, AgentStatus
from app.models.job import Job, JobStatus, VALID_TRANSITIONS
from app.schemas.job import AcceptJob, CounterProposal, JobProposal
from app.utils.crypto import hash_criteria


def _assert_transition(current: JobStatus, target: JobStatus) -> None:
    """Raise 409 if the state transition is not valid."""
    if target not in VALID_TRANSITIONS.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from {current.value} to {target.value}",
        )


def _assert_party(
    job: Job, agent_id: uuid.UUID, allowed: str = "both"
) -> None:
    """Ensure agent is a party to the job. allowed: 'client', 'seller', 'both'."""
    is_client = job.client_agent_id == agent_id
    is_seller = job.seller_agent_id == agent_id
    if allowed == "client" and not is_client:
        raise HTTPException(status_code=403, detail="Only the client can perform this action")
    if allowed == "seller" and not is_seller:
        raise HTTPException(status_code=403, detail="Only the seller can perform this action")
    if allowed == "both" and not (is_client or is_seller):
        raise HTTPException(status_code=403, detail="Not a party to this job")


async def _get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def propose_job(
    db: AsyncSession, client_agent_id: uuid.UUID, data: JobProposal
) -> Job:
    """Client proposes a new job to a seller."""
    # Verify both agents exist and are active
    for aid in (client_agent_id, data.seller_agent_id):
        result = await db.execute(select(Agent).where(Agent.agent_id == aid))
        agent = result.scalar_one_or_none()
        if agent is None or agent.status != AgentStatus.ACTIVE:
            raise HTTPException(status_code=404, detail=f"Agent {aid} not found or not active")

    if client_agent_id == data.seller_agent_id:
        raise HTTPException(status_code=422, detail="Cannot propose a job to yourself")

    criteria_hash = hash_criteria(data.acceptance_criteria)

    initial_log = [{
        "round": 0,
        "proposer": str(client_agent_id),
        "proposed_price": str(data.max_budget),
        "requirements": data.requirements,
        "acceptance_criteria": data.acceptance_criteria,
        "acceptance_criteria_hash": criteria_hash,
        "timestamp": datetime.now(UTC).isoformat(),
    }]

    job = Job(
        job_id=uuid.uuid4(),
        client_agent_id=client_agent_id,
        seller_agent_id=data.seller_agent_id,
        listing_id=data.listing_id,
        status=JobStatus.PROPOSED,
        acceptance_criteria=data.acceptance_criteria,
        acceptance_criteria_hash=criteria_hash,
        requirements=data.requirements,
        agreed_price=data.max_budget,
        delivery_deadline=data.delivery_deadline,
        max_rounds=data.max_rounds,
        current_round=0,
        negotiation_log=initial_log,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def counter_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID, data: CounterProposal
) -> Job:
    """Either party submits a counter-proposal."""
    job = await _get_job(db, job_id)
    _assert_party(job, agent_id)

    # Must be in proposed or negotiating
    if job.status not in (JobStatus.PROPOSED, JobStatus.NEGOTIATING):
        raise HTTPException(status_code=409, detail=f"Cannot counter in status {job.status.value}")

    # Check round limit
    if job.current_round >= job.max_rounds:
        job.status = JobStatus.CANCELLED
        await db.commit()
        raise HTTPException(status_code=409, detail="Maximum negotiation rounds exceeded, job cancelled")

    job.status = JobStatus.NEGOTIATING
    job.current_round += 1
    job.agreed_price = data.proposed_price

    log_entry = {
        "round": job.current_round,
        "proposer": str(agent_id),
        "proposed_price": str(data.proposed_price),
        "counter_terms": data.counter_terms,
        "accepted_terms": data.accepted_terms,
        "message": data.message,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if job.negotiation_log is None:
        job.negotiation_log = []
    job.negotiation_log = [*job.negotiation_log, log_entry]

    await db.commit()
    await db.refresh(job)
    return job


async def accept_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID,
    data: AcceptJob | None = None,
) -> Job:
    """Either party accepts current terms.

    When acceptance criteria exist, the accepting party must provide the
    criteria hash to prove they have reviewed the verification logic.
    The client (who authored the criteria) is exempt from this requirement.
    """
    job = await _get_job(db, job_id)
    _assert_party(job, agent_id)
    _assert_transition(job.status, JobStatus.AGREED)

    # Seller must confirm they've reviewed the acceptance criteria
    is_seller = agent_id == job.seller_agent_id
    if is_seller and job.acceptance_criteria is not None:
        provided_hash = data.acceptance_criteria_hash if data else None
        if not provided_hash:
            raise HTTPException(
                status_code=422,
                detail="Seller must provide acceptance_criteria_hash to confirm "
                       "review of the verification criteria before accepting. "
                       f"Expected hash: review the criteria and provide its SHA-256 hash.",
            )
        if provided_hash != job.acceptance_criteria_hash:
            raise HTTPException(
                status_code=409,
                detail="acceptance_criteria_hash mismatch. The criteria may have "
                       "changed. Please review the current acceptance_criteria "
                       "and provide the correct hash.",
            )

    job.status = JobStatus.AGREED
    log_entry = {
        "action": "accepted",
        "by": str(agent_id),
        "agreed_price": str(job.agreed_price),
        "acceptance_criteria_hash": job.acceptance_criteria_hash,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    job.negotiation_log = [*(job.negotiation_log or []), log_entry]

    await db.commit()
    await db.refresh(job)
    return job


async def start_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID
) -> Job:
    """Seller begins work. Job must be funded."""
    job = await _get_job(db, job_id)
    _assert_party(job, agent_id, allowed="seller")
    _assert_transition(job.status, JobStatus.IN_PROGRESS)

    job.status = JobStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(job)
    return job


async def deliver_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID, result: dict
) -> Job:
    """Seller submits deliverable."""
    job = await _get_job(db, job_id)
    _assert_party(job, agent_id, allowed="seller")
    _assert_transition(job.status, JobStatus.DELIVERED)

    job.status = JobStatus.DELIVERED
    job.result = result
    await db.commit()
    await db.refresh(job)
    return job


async def fail_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID
) -> Job:
    """Mark job as failed. If escrow is funded, refund to client."""
    job = await _get_job(db, job_id)
    _assert_party(job, agent_id)
    _assert_transition(job.status, JobStatus.FAILED)

    # Check if there's a funded escrow to refund
    from app.models.escrow import EscrowAccount, EscrowStatus
    escrow_result = await db.execute(
        select(EscrowAccount).where(EscrowAccount.job_id == job_id)
    )
    escrow = escrow_result.scalar_one_or_none()
    if escrow and escrow.status == EscrowStatus.FUNDED:
        from app.services.escrow import refund_escrow
        await refund_escrow(db, job_id)
        # refund_escrow already sets job.status = FAILED and commits
        await db.refresh(job)
        return job

    job.status = JobStatus.FAILED
    await db.commit()
    await db.refresh(job)
    return job


async def dispute_job(
    db: AsyncSession, job_id: uuid.UUID, agent_id: uuid.UUID
) -> Job:
    """Either party disputes a failed job. V1: disabled — use reviews instead."""
    raise HTTPException(
        status_code=501,
        detail="Dispute resolution is not available in V1. Use reviews to provide feedback on completed or failed jobs.",
    )
    # V2: re-enable dispute flow
    # job = await _get_job(db, job_id)
    # _assert_party(job, agent_id)
    # _assert_transition(job.status, JobStatus.DISPUTED)
    # job.status = JobStatus.DISPUTED
    # await db.commit()
    # await db.refresh(job)
    # return job


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    """Get job by ID (public)."""
    return await _get_job(db, job_id)


    # enforce_deadlines removed — replaced by deadline_queue sorted-set consumer
