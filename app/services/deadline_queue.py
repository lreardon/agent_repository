"""Job deadline queue using Redis sorted set.

When a job is funded with a delivery_deadline, we ZADD the job_id with
score = deadline unix timestamp. A single async consumer uses BZPOPMIN
to block until the next deadline is due, then auto-fails the job and
refunds escrow.
"""

import asyncio
import logging
import time
import uuid

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

DEADLINE_KEY = "job:deadlines"


async def enqueue_deadline(
    redis: aioredis.Redis,
    job_id: uuid.UUID,
    deadline_timestamp: float,
) -> None:
    """Schedule a job for deadline enforcement."""
    await redis.zadd(DEADLINE_KEY, {str(job_id): deadline_timestamp})
    logger.info("Enqueued deadline for job %s at %s", job_id, deadline_timestamp)


async def cancel_deadline(redis: aioredis.Redis, job_id: uuid.UUID) -> None:
    """Remove a job from the deadline queue (e.g. on completion)."""
    await redis.zrem(DEADLINE_KEY, str(job_id))


async def run_deadline_consumer() -> None:
    """Block on the sorted set, processing jobs as their deadlines arrive.

    This replaces a polling loop — it sleeps until the next deadline is due
    rather than waking every N seconds.
    """
    from app.database import async_session_factory
    from app.redis import redis_pool

    redis = aioredis.Redis(connection_pool=redis_pool)

    while True:
        try:
            # Peek at the earliest deadline
            entries = await redis.zrangebyscore(
                DEADLINE_KEY, "-inf", "+inf", start=0, num=1, withscores=True
            )

            if not entries:
                # Nothing queued — sleep briefly and retry
                await asyncio.sleep(10)
                continue

            job_id_bytes, deadline_ts = entries[0]
            now = time.time()

            if deadline_ts > now:
                # Sleep until the deadline (or 60s max to pick up new earlier deadlines)
                sleep_time = min(deadline_ts - now, 60.0)
                await asyncio.sleep(sleep_time)
                continue

            # Deadline has passed — remove and process
            removed = await redis.zrem(DEADLINE_KEY, job_id_bytes)
            if not removed:
                # Another consumer got it
                continue

            job_id = uuid.UUID(job_id_bytes.decode())
            await _fail_overdue_job(job_id)

        except asyncio.CancelledError:
            logger.info("Deadline consumer shutting down")
            break
        except Exception:
            logger.exception("Deadline consumer error, retrying in 5s")
            await asyncio.sleep(5)

    await redis.aclose()


async def _fail_overdue_job(job_id: uuid.UUID) -> None:
    """Fail a single overdue job and refund escrow."""
    from app.database import async_session_factory
    from app.models.job import Job, JobStatus
    from app.services.escrow import refund_escrow
    from sqlalchemy import select

    try:
        async with async_session_factory() as db:
            result = await db.execute(select(Job).where(Job.job_id == job_id))
            job = result.scalar_one_or_none()

            if job is None:
                logger.warning("Deadline fired for nonexistent job %s", job_id)
                return

            # Only fail if still in a state where deadline matters
            if job.status not in (
                JobStatus.FUNDED,
                JobStatus.IN_PROGRESS,
                JobStatus.DELIVERED,
            ):
                logger.info(
                    "Job %s already in state %s, skipping deadline enforcement",
                    job_id, job.status.value,
                )
                return

            job.status = JobStatus.FAILED
            await db.flush()
            await refund_escrow(db, job.job_id)
            await db.commit()
            logger.info("Auto-failed overdue job %s", job_id)

    except Exception:
        logger.exception("Failed to enforce deadline for job %s", job_id)
