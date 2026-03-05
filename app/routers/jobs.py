"""Job lifecycle endpoints."""

import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.redis import get_redis
from app.models.job import JobStatus
from app.schemas.job import AcceptJob, CounterProposal, DeliverPayload, JobProposal, JobResponse, VerifyResponse
from app.schemas.escrow import EscrowResponse
from app.schemas.errors import JOB_ERRORS, OWNER_ERRORS
from app.services import escrow as escrow_service
from app.services import job as job_service
from app.services.test_runner import run_script_test

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=201, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def propose_job(
    data: JobProposal,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Client proposes a job."""
    job = await job_service.propose_job(db, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.get("/{job_id}", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
            responses=JOB_ERRORS)
async def get_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Get job details. Only parties to the job can view it."""
    job = await job_service.get_job(db, job_id)
    if auth.agent_id != job.client_agent_id and auth.agent_id != job.seller_agent_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Not a party to this job")
    return JobResponse.model_validate(job)


@router.post("/{job_id}/counter", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def counter_job(
    job_id: uuid.UUID,
    data: CounterProposal,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Either party submits a counter-proposal."""
    job = await job_service.counter_job(db, job_id, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/accept", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def accept_job(
    job_id: uuid.UUID,
    data: AcceptJob | None = None,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Accept current terms. Seller must include acceptance_criteria_hash."""
    job = await job_service.accept_job(db, job_id, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/fund", response_model=EscrowResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def fund_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> EscrowResponse:
    """Client funds escrow for an agreed job."""
    escrow = await escrow_service.fund_job(db, job_id, auth.agent_id)
    return EscrowResponse.model_validate(escrow)


@router.post("/{job_id}/complete", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def complete_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Complete job — release escrow to seller. Client only.

    Only valid for jobs without acceptance_criteria. If acceptance_criteria are
    defined, use POST /jobs/{job_id}/verify instead — verification runs the
    criteria in a sandbox and auto-completes or fails the job.
    """
    from fastapi import HTTPException as HTTPExc
    job = await job_service.get_job(db, job_id)
    if auth.agent_id != job.client_agent_id:
        raise HTTPExc(status_code=403, detail="Only the client can complete a job")
    if job.acceptance_criteria is not None:
        raise HTTPExc(
            status_code=409,
            detail="This job has acceptance criteria. Use POST /jobs/{job_id}/verify to run verification.",
        )
    escrow = await escrow_service.release_escrow(db, job_id)
    job = await job_service.get_job(db, job_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/start", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def start_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Seller begins work. Job must be funded."""
    job = await job_service.start_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/deliver", dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def deliver_job(
    job_id: uuid.UUID,
    data: DeliverPayload,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Seller submits deliverable. Storage fee is charged to the seller."""
    from app.services.fees import calculate_storage_fee, charge_fee

    # Calculate and charge storage fee before accepting delivery
    storage_fee = calculate_storage_fee(data.result)
    await charge_fee(db, auth.agent_id, storage_fee)

    job = await job_service.deliver_job(db, job_id, auth.agent_id, data.result)
    return {
        **JobResponse.model_validate(job).model_dump(mode="json"),
        "fee_charged": storage_fee.to_dict(),
    }


@router.post("/{job_id}/verify", dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def verify_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Run acceptance tests on delivered job. Auto-completes or fails.

    Verification fee is charged to the client — even if verification fails.
    This disincentivizes resource-exhaustion attacks and rigged scripts.
    Only one verification may run per job at a time.
    """
    import time as _time
    from fastapi import HTTPException as HTTPExc
    from app.services.fees import calculate_verification_fee, charge_fee

    # Prevent concurrent verification for the same job
    lock_key = f"verify_lock:{job_id}"
    acquired = await redis.set(lock_key, "1", nx=True, ex=600)  # 10min TTL safety net
    if not acquired:
        raise HTTPExc(status_code=409, detail="Verification already in progress for this job")

    try:
        job = await job_service.get_job(db, job_id)

        if auth.agent_id != job.client_agent_id:
            raise HTTPExc(status_code=403, detail="Only the client can trigger verification")

        if job.status.value != "delivered":
            raise HTTPExc(status_code=409, detail=f"Job must be delivered to verify, currently {job.status.value}")

        # Run verification
        criteria = job.acceptance_criteria or {}
        output = job.result

        # Reject any non-script criteria (declarative v1.0 not supported)
        if criteria and not criteria.get("script"):
            raise HTTPExc(
                status_code=422,
                detail="Only script-based acceptance criteria are supported. "
                       "Provide a 'script' key (base64-encoded) in acceptance_criteria.",
            )

        cpu_seconds = 0.0
        if criteria.get("script"):
            # Script-based: run in Docker sandbox
            suite_result = await run_script_test(criteria, output)
            # Use actual elapsed time from sandbox
            if suite_result.sandbox_result:
                cpu_seconds = suite_result.sandbox_result.elapsed_seconds
        else:
            # No criteria defined — charge minimum fee, auto-complete
            verify_fee = calculate_verification_fee(0.0)
            await charge_fee(db, auth.agent_id, verify_fee)
            escrow = await escrow_service.release_escrow(db, job_id)
            job = await job_service.get_job(db, job_id)
            resp = VerifyResponse(job=JobResponse.model_validate(job), verification=None)
            return {**resp.model_dump(mode="json"), "fee_charged": verify_fee.to_dict()}

        # Charge verification fee AFTER running (based on actual resource usage)
        # but BEFORE releasing escrow (so fee is paid regardless of outcome)
        verify_fee = calculate_verification_fee(cpu_seconds)
        await charge_fee(db, auth.agent_id, verify_fee)

        verification = suite_result.to_dict()

        if suite_result.passed:
            escrow = await escrow_service.release_escrow(db, job_id)
            job = await job_service.get_job(db, job_id)
        else:
            # Verification failed — seller is responsible for the compute cost.
            # Refund client's verification fee, charge seller instead.
            from app.models.agent import Agent
            from sqlalchemy import select as sel

            # Refund client
            client_row = await db.execute(sel(Agent).where(Agent.agent_id == auth.agent_id).with_for_update())
            client_agent = client_row.scalar_one()
            client_agent.balance += verify_fee.amount

            # Charge seller (best-effort — if seller can't cover, platform absorbs)
            seller_row = await db.execute(sel(Agent).where(Agent.agent_id == job.seller_agent_id).with_for_update())
            seller_agent = seller_row.scalar_one()
            if seller_agent.balance >= verify_fee.amount:
                seller_agent.balance -= verify_fee.amount

            # Return to IN_PROGRESS — seller can redeliver until deadline
            job.status = JobStatus.IN_PROGRESS
            await db.commit()
            await db.refresh(job)

        resp = VerifyResponse(
            job=JobResponse.model_validate(job),
            verification=verification,
        )
        result_dict = {**resp.model_dump(mode="json"), "fee_charged": verify_fee.to_dict()}
        if not suite_result.passed:
            result_dict["retry_allowed"] = True
        return result_dict
    finally:
        await redis.delete(lock_key)


@router.post("/{job_id}/fail", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def fail_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Mark job as failed. Refunds escrow to client if funded.

    Only valid for jobs without acceptance_criteria. If acceptance_criteria are
    defined, use POST /jobs/{job_id}/verify — a failed verification run
    automatically fails the job and refunds escrow.
    """
    from fastapi import HTTPException as HTTPExc
    job = await job_service.get_job(db, job_id)
    if job.acceptance_criteria is not None:
        raise HTTPExc(
            status_code=409,
            detail="This job has acceptance criteria. Use POST /jobs/{job_id}/verify to run verification.",
        )
    job = await job_service.fail_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/abort", response_model=JobResponse, dependencies=[Depends(check_rate_limit)],
             responses=JOB_ERRORS)
async def abort_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Abort a funded job. Either party can abort.

    Penalties are applied based on who aborts:
    - Client aborts: pays client_abort_penalty to seller, gets remainder back.
      Seller's performance bond is returned.
    - Seller aborts: loses performance bond (seller_abort_penalty) to client.
      Client gets full escrow refund + bond.

    Valid from: FUNDED, IN_PROGRESS, DELIVERED.
    """
    from fastapi import HTTPException as HTTPExc
    job = await job_service.get_job(db, job_id)
    if auth.agent_id != job.client_agent_id and auth.agent_id != job.seller_agent_id:
        raise HTTPExc(status_code=403, detail="Not a party to this job")
    if job.status not in (JobStatus.FUNDED, JobStatus.IN_PROGRESS, JobStatus.DELIVERED):
        raise HTTPExc(status_code=409, detail=f"Cannot abort job in status {job.status.value}")

    await escrow_service.abort_job(db, job_id, auth.agent_id)
    job = await job_service.get_job(db, job_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/dispute", response_model=JobResponse, dependencies=[Depends(check_rate_limit)], include_in_schema=False)
async def dispute_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Dispute a failed job."""
    job = await job_service.dispute_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)
