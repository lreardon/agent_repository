"""Prometheus custom metrics for business KPIs."""

import asyncio
import logging
import time

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

# --- Custom metrics ---

escrow_volume_total = Counter(
    "escrow_volume_usd_total",
    "Total escrow volume in USD",
)

active_jobs_gauge = Gauge(
    "active_jobs",
    "Number of jobs in active states (funded/in_progress/delivered)",
)

treasury_balance_gauge = Gauge(
    "treasury_balance_usdc",
    "Treasury wallet USDC balance",
)

deposit_watcher_lag_seconds = Gauge(
    "deposit_watcher_lag_seconds",
    "Seconds since last successful deposit watcher scan",
)

in_flight_tasks_gauge = Gauge(
    "in_flight_tasks",
    "Number of in-flight async wallet tasks",
)

# Redis key where the deposit watcher records its last scan timestamp
DEPOSIT_WATCHER_LAST_SCAN_KEY = "deposit_watcher:last_scan_ts"


async def update_gauges_once() -> None:
    """Update gauge metrics from DB/Redis. Call periodically."""
    try:
        from app.database import async_session_factory
        from app.models.job import Job, JobStatus
        from app.services.task_registry import registry
        from sqlalchemy import select, func

        async with async_session_factory() as db:
            # Active jobs count
            result = await db.execute(
                select(func.count()).select_from(Job).where(
                    Job.status.in_([
                        JobStatus.FUNDED,
                        JobStatus.IN_PROGRESS,
                        JobStatus.DELIVERED,
                    ])
                )
            )
            active_jobs_gauge.set(result.scalar() or 0)

        in_flight_tasks_gauge.set(registry.active_count)

    except Exception:
        logger.warning("Failed to update DB gauge metrics", exc_info=True)

    # Deposit watcher lag from Redis
    try:
        import redis.asyncio as aioredis
        from app.redis import redis_pool

        r = aioredis.Redis(connection_pool=redis_pool)
        try:
            last_scan = await r.get(DEPOSIT_WATCHER_LAST_SCAN_KEY)
            if last_scan is not None:
                lag = time.time() - float(last_scan)
                deposit_watcher_lag_seconds.set(max(0, lag))
        finally:
            await r.aclose()
    except Exception:
        logger.warning("Failed to update deposit watcher lag metric", exc_info=True)


async def update_treasury_balance() -> None:
    """Update treasury USDC balance and check pause thresholds. Expensive — call infrequently."""
    try:
        from app.services.treasury import check_treasury_and_update_pause, get_treasury_usdc_balance

        # Run the pause/resume check (also fetches balance)
        await check_treasury_and_update_pause()

        # Update the Prometheus gauge
        balance = await get_treasury_usdc_balance()
        if balance is not None:
            treasury_balance_gauge.set(float(balance))

    except Exception:
        logger.warning("Failed to update treasury balance metric", exc_info=True)


async def run_metrics_updater(interval: int = 30) -> None:
    """Background loop to update gauge metrics.

    DB/Redis gauges update every `interval` seconds.
    Treasury balance updates every 5 minutes (on-chain RPC call).
    """
    treasury_interval = 300  # 5 minutes
    cycles_per_treasury = treasury_interval // interval
    cycle = 0

    while True:
        await update_gauges_once()

        if cycle % cycles_per_treasury == 0:
            await update_treasury_balance()

        cycle += 1
        await asyncio.sleep(interval)
