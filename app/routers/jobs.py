"""Job lifecycle endpoints."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthenticatedAgent, verify_request
from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.job import AcceptJob, CounterProposal, DeliverPayload, JobProposal, JobResponse, VerifyResponse
from app.schemas.escrow import EscrowResponse
from app.services import escrow as escrow_service
from app.services import job as job_service
from app.services.test_runner import run_test_suite, run_script_test

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=201, dependencies=[Depends(check_rate_limit)])
async def propose_job(
    data: JobProposal,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Client proposes a job."""
    job = await job_service.propose_job(db, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.get("/{job_id}", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
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


@router.post("/{job_id}/counter", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def counter_job(
    job_id: uuid.UUID,
    data: CounterProposal,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Either party submits a counter-proposal."""
    job = await job_service.counter_job(db, job_id, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/accept", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def accept_job(
    job_id: uuid.UUID,
    data: AcceptJob | None = None,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Accept current terms. Seller must include acceptance_criteria_hash."""
    job = await job_service.accept_job(db, job_id, auth.agent_id, data)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/fund", response_model=EscrowResponse, dependencies=[Depends(check_rate_limit)])
async def fund_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> EscrowResponse:
    """Client funds escrow for an agreed job."""
    escrow = await escrow_service.fund_job(db, job_id, auth.agent_id)
    return EscrowResponse.model_validate(escrow)


@router.post("/{job_id}/complete", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def complete_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Complete job — release escrow to seller (after verification). Client only."""
    from fastapi import HTTPException as HTTPExc
    job = await job_service.get_job(db, job_id)
    if auth.agent_id != job.client_agent_id:
        raise HTTPExc(status_code=403, detail="Only the client can complete a job")
    escrow = await escrow_service.release_escrow(db, job_id)
    job = await job_service.get_job(db, job_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/start", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def start_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Seller begins work. Job must be funded."""
    job = await job_service.start_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/deliver", dependencies=[Depends(check_rate_limit)])
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


@router.post("/{job_id}/verify", dependencies=[Depends(check_rate_limit)])
async def verify_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run acceptance tests on delivered job. Auto-completes or fails.

    Verification fee is charged to the client — even if verification fails.
    This disincentivizes resource-exhaustion attacks and rigged scripts.
    """
    import time as _time
    from fastapi import HTTPException as HTTPExc
    from app.services.fees import calculate_verification_fee, charge_fee

    job = await job_service.get_job(db, job_id)

    if auth.agent_id != job.client_agent_id:
        raise HTTPExc(status_code=403, detail="Only the client can trigger verification")

    if job.status.value != "delivered":
        raise HTTPExc(status_code=409, detail=f"Job must be delivered to verify, currently {job.status.value}")

    # Run verification
    criteria = job.acceptance_criteria or {}
    output = job.result

    # Determine verification mode and track elapsed time
    cpu_seconds = 0.0
    if criteria.get("script"):
        # Script-based: run in Docker sandbox
        suite_result = await run_script_test(criteria, output)
        # Use actual elapsed time from sandbox
        if suite_result.sandbox_result:
            cpu_seconds = suite_result.sandbox_result.elapsed_seconds
    elif criteria.get("tests"):
        # Declarative tests: run in-process (minimal CPU, charge minimum)
        t0 = _time.monotonic()
        suite_result = run_test_suite(criteria, output)
        cpu_seconds = _time.monotonic() - t0
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
        job = await job_service.fail_job(db, job_id, auth.agent_id)

    resp = VerifyResponse(
        job=JobResponse.model_validate(job),
        verification=verification,
    )
    return {**resp.model_dump(mode="json"), "fee_charged": verify_fee.to_dict()}


@router.post("/{job_id}/fail", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def fail_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Mark job as failed."""
    job = await job_service.fail_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)


@router.post("/{job_id}/dispute", response_model=JobResponse, dependencies=[Depends(check_rate_limit)])
async def dispute_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """Dispute a failed job."""
    job = await job_service.dispute_job(db, job_id, auth.agent_id)
    return JobResponse.model_validate(job)
