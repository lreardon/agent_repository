"""Tests for deadline queue: enqueue, cancel, fail overdue, and startup recovery."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Base
from app.models.agent import Agent
from app.models.escrow import EscrowAccount
from app.models.job import Job, JobStatus
from app.services.deadline_queue import (
    DEADLINE_KEY,
    cancel_deadline,
    enqueue_deadline,
    _fail_overdue_job,
)


@pytest_asyncio.fixture
async def redis_client(_worker_redis) -> aioredis.Redis:
    await _worker_redis.delete(DEADLINE_KEY)
    yield _worker_redis
    await _worker_redis.delete(DEADLINE_KEY)


@pytest_asyncio.fixture
async def test_db(_worker_engine):
    """Session factory backed by the per-worker engine (tables already exist).

    Tests using this fixture do real commits (needed for service-layer mocking),
    but we truncate tables after each test to avoid cross-test contamination.
    """
    factory = async_sessionmaker(_worker_engine, class_=AsyncSession, expire_on_commit=False)
    yield factory

    # Clean up data committed during the test
    async with _worker_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


def _make_agent(**kwargs) -> Agent:
    from app.utils.crypto import generate_keypair
    _, pub = generate_keypair()
    return Agent(
        agent_id=kwargs.get("agent_id", uuid.uuid4()),
        public_key=pub,
        display_name="Test Agent",
        endpoint_url="https://example.com",
        webhook_secret=secrets.token_hex(32),
    )


def _make_job(
    client_agent: Agent,
    seller_agent: Agent,
    status: JobStatus = JobStatus.FUNDED,
    deadline: datetime | None = None,
) -> Job:
    return Job(
        job_id=uuid.uuid4(),
        client_agent_id=client_agent.agent_id,
        seller_agent_id=seller_agent.agent_id,
        status=status,
        agreed_price=Decimal("100.00"),
        delivery_deadline=deadline,
    )


# --- Pure Redis tests (no DB needed) ---

@pytest.mark.asyncio
async def test_enqueue_deadline(redis_client: aioredis.Redis) -> None:
    job_id = uuid.uuid4()
    ts = 1700000000.0
    await enqueue_deadline(redis_client, job_id, ts)
    assert await redis_client.zscore(DEADLINE_KEY, str(job_id)) == ts


@pytest.mark.asyncio
async def test_enqueue_deadline_idempotent(redis_client: aioredis.Redis) -> None:
    job_id = uuid.uuid4()
    await enqueue_deadline(redis_client, job_id, 1700000000.0)
    await enqueue_deadline(redis_client, job_id, 1700000000.0)
    assert await redis_client.zcard(DEADLINE_KEY) == 1


@pytest.mark.asyncio
async def test_cancel_deadline(redis_client: aioredis.Redis) -> None:
    job_id = uuid.uuid4()
    await enqueue_deadline(redis_client, job_id, 1700000000.0)
    await cancel_deadline(redis_client, job_id)
    assert await redis_client.zscore(DEADLINE_KEY, str(job_id)) is None


@pytest.mark.asyncio
async def test_cancel_nonexistent_deadline(redis_client: aioredis.Redis) -> None:
    await cancel_deadline(redis_client, uuid.uuid4())  # should not raise


# --- DB-dependent tests ---

@pytest.mark.asyncio
async def test_fail_overdue_job(test_db) -> None:
    async with test_db() as db:
        client = _make_agent()
        seller = _make_agent()
        db.add_all([client, seller])
        await db.flush()

        job = _make_job(client, seller, JobStatus.FUNDED,
                        datetime.now(UTC) - timedelta(hours=1))
        db.add(job)
        await db.flush()

        escrow = EscrowAccount(
            escrow_id=uuid.uuid4(),
            job_id=job.job_id,
            client_agent_id=client.agent_id,
            seller_agent_id=seller.agent_id,
            amount=Decimal("100.00"),
            status="funded",
        )
        db.add(escrow)
        await db.commit()
        job_id = job.job_id

    with patch("app.database.async_session_factory", test_db), \
         patch("app.database.async_session", test_db):
        await _fail_overdue_job(job_id)

    async with test_db() as db:
        from sqlalchemy import select
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.FAILED


@pytest.mark.asyncio
async def test_fail_overdue_job_ignores_completed(test_db) -> None:
    async with test_db() as db:
        client = _make_agent()
        seller = _make_agent()
        db.add_all([client, seller])
        await db.flush()

        job = _make_job(client, seller, JobStatus.COMPLETED,
                        datetime.now(UTC) - timedelta(hours=1))
        db.add(job)
        await db.commit()
        job_id = job.job_id

    with patch("app.database.async_session_factory", test_db), \
         patch("app.database.async_session", test_db):
        await _fail_overdue_job(job_id)

    async with test_db() as db:
        from sqlalchemy import select
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one()
        assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_fail_overdue_nonexistent_job(test_db) -> None:
    with patch("app.database.async_session_factory", test_db), \
         patch("app.database.async_session", test_db):
        await _fail_overdue_job(uuid.uuid4())  # should log warning, not raise


@pytest.mark.asyncio
async def test_recover_deadlines_enqueues_active_jobs(
    test_db, redis_client: aioredis.Redis
) -> None:
    from app.main import _recover_deadlines

    deadline = datetime.now(UTC) + timedelta(hours=2)

    async with test_db() as db:
        client = _make_agent()
        seller = _make_agent()
        db.add_all([client, seller])
        await db.flush()

        job_funded = _make_job(client, seller, JobStatus.FUNDED, deadline)
        job_in_progress = _make_job(client, seller, JobStatus.IN_PROGRESS, deadline)
        job_delivered = _make_job(client, seller, JobStatus.DELIVERED, deadline)
        job_completed = _make_job(client, seller, JobStatus.COMPLETED, deadline)
        job_no_deadline = _make_job(client, seller, JobStatus.FUNDED, None)
        db.add_all([job_funded, job_in_progress, job_delivered, job_completed, job_no_deadline])
        await db.commit()

        ids = {
            "funded": str(job_funded.job_id),
            "in_progress": str(job_in_progress.job_id),
            "delivered": str(job_delivered.job_id),
            "completed": str(job_completed.job_id),
            "no_deadline": str(job_no_deadline.job_id),
        }

    await _recover_deadlines(session_factory=test_db, redis_client=redis_client)

    members = {m.decode() for m in await redis_client.zrange(DEADLINE_KEY, 0, -1)}
    assert ids["funded"] in members
    assert ids["in_progress"] in members
    assert ids["delivered"] in members
    assert ids["completed"] not in members
    assert ids["no_deadline"] not in members


@pytest.mark.asyncio
async def test_recover_deadlines_empty_db(test_db, redis_client: aioredis.Redis) -> None:
    from app.main import _recover_deadlines

    await _recover_deadlines(session_factory=test_db, redis_client=redis_client)

    assert await redis_client.zcard(DEADLINE_KEY) == 0
