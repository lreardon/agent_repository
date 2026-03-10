"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.logging_config import setup_logging, RequestContextMiddleware
from app.redis import get_redis
from app.routers import admin, agents, auth, dashboard, discover, fees, hosting, jobs, listings, reviews, wallet, webhooks, ws

from app.config import settings as _settings
setup_logging(_settings.env)

logger = logging.getLogger(__name__)


async def _recover_wallet_tasks() -> None:
    """Re-spawn confirmation/processing tasks for in-flight deposits and withdrawals."""
    from app.database import async_session
    from app.models.wallet import DepositTransaction, DepositStatus, WithdrawalRequest, WithdrawalStatus
    from app.services.wallet import _wait_and_credit_deposit, _process_withdrawal
    from sqlalchemy import select

    try:
        from app.services.task_registry import registry

        async with async_session() as db:
            # Recover confirming deposits
            result = await db.execute(
                select(DepositTransaction).where(DepositTransaction.status == DepositStatus.CONFIRMING)
            )
            deposits = list(result.scalars().all())
            for dep in deposits:
                logger.info("Recovering confirming deposit %s (block: %s)", dep.deposit_tx_id, dep.block_number)
                task = asyncio.create_task(_wait_and_credit_deposit(dep.deposit_tx_id, dep.block_number))
                registry.register(task, f"recover-deposit-{dep.deposit_tx_id}")

            # Recover pending/processing withdrawals
            result = await db.execute(
                select(WithdrawalRequest).where(
                    WithdrawalRequest.status.in_([WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING])
                )
            )
            withdrawals = list(result.scalars().all())
            for wd in withdrawals:
                logger.info("Recovering pending withdrawal %s", wd.withdrawal_id)
                task = asyncio.create_task(_process_withdrawal(wd.withdrawal_id))
                registry.register(task, f"recover-withdrawal-{wd.withdrawal_id}")

            if deposits or withdrawals:
                logger.info(
                    "Wallet recovery: %d deposits, %d withdrawals re-spawned",
                    len(deposits), len(withdrawals),
                )
    except Exception:
        logger.exception("Wallet task recovery failed")


async def _recover_deadlines(session_factory=None, redis_client=None) -> None:
    """Re-enqueue deadlines for active jobs after server restart.

    ZADD is idempotent — re-adding an existing job_id with the same score
    is a no-op, so this is safe to call unconditionally at startup.
    """
    if session_factory is None:
        from app.database import async_session_factory as session_factory
    from app.models.job import Job, JobStatus
    from app.services.deadline_queue import enqueue_deadline
    from sqlalchemy import select

    import redis.asyncio as aioredis

    try:
        async with session_factory() as db:
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

            own_redis = False
            if redis_client is None:
                from app.redis import redis_pool
                redis_client = aioredis.Redis(connection_pool=redis_pool)
                own_redis = True
            try:
                for job in jobs:
                    await enqueue_deadline(
                        redis_client, job.job_id, job.delivery_deadline.timestamp()
                    )
            finally:
                if own_redis:
                    await redis_client.aclose()

            logger.info("Deadline recovery: re-enqueued %d deadlines", len(jobs))

    except Exception:
        logger.exception("Deadline recovery failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    # Start background tasks
    from app.services.deadline_queue import run_deadline_consumer
    from app.services.deposit_watcher import run_deposit_watcher
    from app.services.webhook_delivery import run_webhook_delivery_loop
    from app.services.hosting.scaler import run_idle_monitor
    from app.config import settings
    deadline_task = asyncio.create_task(run_deadline_consumer())
    deposit_watcher_task = asyncio.create_task(run_deposit_watcher()) if settings.deposit_watcher_enabled else None
    webhook_delivery_task = asyncio.create_task(run_webhook_delivery_loop())
    idle_monitor_task = asyncio.create_task(run_idle_monitor())
    await _recover_wallet_tasks()
    await _recover_deadlines()

    # Start metrics gauge updater
    from app.metrics import run_metrics_updater
    metrics_task = asyncio.create_task(run_metrics_updater())

    yield

    # Cleanup metrics updater
    metrics_task.cancel()
    try:
        await metrics_task
    except asyncio.CancelledError:
        pass

    # Cleanup: first drain in-flight wallet tasks, then cancel background loops
    from app.services.task_registry import registry
    await registry.shutdown(timeout=30)

    idle_monitor_task.cancel()
    webhook_delivery_task.cancel()
    if deposit_watcher_task is not None:
        deposit_watcher_task.cancel()
    deadline_task.cancel()
    for task in (idle_monitor_task, deposit_watcher_task, deadline_task, webhook_delivery_task):
        if task is None:
            continue
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Agent Registry & Marketplace",
    description="A2A-compatible agent-to-agent task marketplace",
    version="1.0.0",
    lifespan=lifespan,
)

# Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator(
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# CORS - restrict to configured origins
from app.config import settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Timestamp", "X-Nonce", "X-Request-ID", "X-Admin-Key"],
)

# Middleware (order matters — outermost first)
if settings.env not in ("development", "test"):
    from app.error_reporting import ErrorReportingMiddleware
    app.add_middleware(ErrorReportingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=52_428_800)  # 50MB for hosting uploads

# ---------------------------------------------------------------------------
# API versioning: /v1 prefix with backward-compatible root mount
# ---------------------------------------------------------------------------
_api_routers = [
    auth.router,
    agents.router,
    listings.router,
    discover.router,
    fees.router,
    jobs.router,
    reviews.router,
    wallet.router,
    webhooks.router,
    ws.router,
    dashboard.router,
    admin.router,
    hosting.router,
]

# Primary versioned routes (/v1/...)
v1_router = APIRouter(prefix="/v1")
for _r in _api_routers:
    v1_router.include_router(_r)
app.include_router(v1_router)

# Backward-compatible root routes (same handlers, no prefix)
for _r in _api_routers:
    app.include_router(_r)

# Static files - serve documentation
from pathlib import Path
static_dir = Path(__file__).parent.parent / "web"
if static_dir.exists():
    app.mount("/docs-site", StaticFiles(directory=str(static_dir), html=True), name="docs-site")


@app.get("/health")
async def health(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> JSONResponse:
    """Deep health check — tests DB and Redis connectivity."""
    import asyncio as _asyncio
    from app.services.task_registry import registry

    components: dict[str, str] = {}

    # DB check
    try:
        async with _asyncio.timeout(2):
            await db.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception:
        components["database"] = "unavailable"

    # Redis check
    try:
        async with _asyncio.timeout(2):
            await redis.ping()
        components["redis"] = "ok"
    except Exception:
        components["redis"] = "unavailable"

    all_ok = all(v == "ok" for v in components.values())
    body = {
        "status": "healthy" if all_ok else "unhealthy",
        "components": components,
        "in_flight_tasks": registry.active_count,
    }
    return JSONResponse(content=body, status_code=200 if all_ok else 503)
