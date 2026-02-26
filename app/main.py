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
    from app.database import async_session
    from app.models.wallet import DepositTransaction, DepositStatus, WithdrawalRequest, WithdrawalStatus
    from app.services.wallet import _wait_and_credit_deposit, _process_withdrawal
    from sqlalchemy import select

    try:
        async with async_session() as db:
            # Recover confirming deposits
            result = await db.execute(
                select(DepositTransaction).where(DepositTransaction.status == DepositStatus.CONFIRMING)
            )
            deposits = list(result.scalars().all())
            for dep in deposits:
                logger.info("Recovering confirming deposit %s (block: %s)", dep.deposit_tx_id, dep.block_number)
                asyncio.create_task(_wait_and_credit_deposit(dep.deposit_tx_id, dep.block_number))

            # Recover pending/processing withdrawals
            result = await db.execute(
                select(WithdrawalRequest).where(
                    WithdrawalRequest.status.in_([WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING])
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    # Start background tasks
    from app.services.deadline_queue import run_deadline_consumer
    deadline_task = asyncio.create_task(run_deadline_consumer())
    await _recover_wallet_tasks()

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
