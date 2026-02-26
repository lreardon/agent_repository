"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.routers import agents, discover, fees, jobs, listings, reviews, wallet

logger = logging.getLogger(__name__)

async def _recover_wallet_tasks() -> None:
    """Re-spawn confirmation/processing tasks for in-flight deposits and withdrawals."""
    from app.database import async_session_factory
    from app.models.wallet import WalletDeposit, WalletWithdrawal
    from app.services.wallet import _watch_deposit_confirmations, _process_withdrawal
    from sqlalchemy import select

    try:
        async with async_session_factory() as db:
            # Recover confirming deposits
            result = await db.execute(
                select(WalletDeposit).where(WalletDeposit.status == "confirming")
            )
            deposits = list(result.scalars().all())
            for dep in deposits:
                logger.info("Recovering confirming deposit %s (tx: %s)", dep.deposit_id, dep.tx_hash)
                asyncio.create_task(_watch_deposit_confirmations(dep.deposit_id, dep.tx_hash))

            # Recover pending/processing withdrawals
            result = await db.execute(
                select(WalletWithdrawal).where(
                    WalletWithdrawal.status.in_(["pending", "processing"])
                )
            )
            withdrawals = list(result.scalars().all())
            for wd in withdrawals:
                logger.info("Recovering pending withdrawal %s", wd.withdrawal_id)
                asyncio.create_task(_process_withdrawal(wd.withdrawal_id))

            if deposits or withdrawals:
                logger.info(
                    "Wallet recovery: %d deposits, %d withdrawals re-spawned",
                    len(deposits), len(withdrawals),
                )
    except Exception:
        logger.exception("Wallet task recovery failed")


async def _recover_deadlines() -> None:
    """Re-enqueue deadlines for active jobs after server restart.

    ZADD is idempotent — re-adding an existing job_id with the same score
    is a no-op, so this is safe to call unconditionally at startup.
    """
    from app.database import async_session_factory
    from app.models.job import Job, JobStatus
    from app.services.deadline_queue import DEADLINE_KEY, enqueue_deadline
    from app.redis import redis_pool
    from sqlalchemy import select

    import redis.asyncio as aioredis

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Job).where(
                    Job.status.in_([
                        JobStatus.FUNDED,
                        JobStatus.IN_PROGRESS,
                        JobStatus.DELIVERED,
                    ]),
                    Job.delivery_deadline.isnot(None),
                )
            )
            jobs = list(result.scalars().all())

            if not jobs:
                logger.info("Deadline recovery: no active jobs with deadlines")
                return

            redis = aioredis.Redis(connection_pool=redis_pool)
            try:
                for job in jobs:
                    await enqueue_deadline(
                        redis, job.job_id, job.delivery_deadline.timestamp()
                    )
            finally:
                await redis.aclose()

            logger.info("Deadline recovery: re-enqueued %d deadlines", len(jobs))

    except Exception:
        logger.exception("Deadline recovery failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    # Start background tasks
    from app.services.deadline_queue import run_deadline_consumer
    deadline_task = asyncio.create_task(run_deadline_consumer())
    await _recover_wallet_tasks()
    await _recover_deadlines()

    yield

    # Cleanup
    deadline_task.cancel()
    try:
        await deadline_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Agent Registry & Marketplace",
    description="A2A-compatible agent-to-agent task marketplace",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS - restrict to configured origins
from app.config import settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware (order matters — outermost first)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1_048_576)

# Routers
app.include_router(agents.router)
app.include_router(listings.router)
app.include_router(discover.router)
app.include_router(fees.router)
app.include_router(jobs.router)
app.include_router(reviews.router)
app.include_router(wallet.router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
