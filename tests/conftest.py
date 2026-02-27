"""Test configuration and fixtures.

Supports parallel execution via pytest-xdist (pytest -n auto).
Each worker gets its own Postgres schema. Within a worker, tables are created
once per session and each test runs inside a rolled-back transaction (fast).
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.redis import get_redis
from app.utils.crypto import generate_keypair


# ---------------------------------------------------------------------------
# Per-worker database isolation (for pytest-xdist)
# ---------------------------------------------------------------------------

def _worker_schema(worker_id: str) -> str:
    """Each xdist worker gets its own Postgres schema for isolation."""
    if worker_id == "master":
        return "public"
    return f"test_{worker_id}"


def _worker_redis_db(worker_id: str) -> int:
    if worker_id == "master":
        return 0
    return int(worker_id.replace("gw", "")) + 1


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "master"


async def _setup_schema(schema: str) -> None:
    """Create per-worker schema and tables using asyncpg (no psycopg2 needed)."""
    # Create schema if needed (autocommit via raw connection)
    engine_auto = create_async_engine(
        settings.test_database_url, isolation_level="AUTOCOMMIT"
    )
    async with engine_auto.connect() as conn:
        if schema != "public":
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await engine_auto.dispose()

    # Create tables in the worker schema
    engine = create_async_engine(
        settings.test_database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text(
            f"DO $$ DECLARE r RECORD; "
            f"BEGIN FOR r IN (SELECT typname FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
            f"WHERE n.nspname = '{schema}' AND t.typtype = 'e') "
            f"LOOP EXECUTE 'DROP TYPE IF EXISTS {schema}.' || quote_ident(r.typname) || ' CASCADE'; END LOOP; END $$;"
        ))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _teardown_schema(schema: str) -> None:
    """Drop per-worker schema or clean public schema."""
    engine = create_async_engine(
        settings.test_database_url, isolation_level="AUTOCOMMIT"
    )
    async with engine.connect() as conn:
        if schema != "public":
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        else:
            engine2 = create_async_engine(settings.test_database_url)
            async with engine2.begin() as conn2:
                await conn2.run_sync(Base.metadata.drop_all)
                await conn2.execute(text(
                    "DO $$ DECLARE r RECORD; "
                    "BEGIN FOR r IN (SELECT typname FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
                    "WHERE n.nspname = 'public' AND t.typtype = 'e') "
                    "LOOP EXECUTE 'DROP TYPE IF EXISTS ' || quote_ident(r.typname) || ' CASCADE'; END LOOP; END $$;"
                ))
                await conn2.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await engine2.dispose()
    await engine.dispose()


@pytest.fixture(scope="session")
def _worker_db_setup(worker_id: str) -> tuple[str, str]:
    """Create per-worker schema and tables once per session (sync wrapper).

    Returns (async_db_url, schema_name).
    """
    schema = _worker_schema(worker_id)
    asyncio.run(_setup_schema(schema))

    yield settings.test_database_url, schema

    asyncio.run(_teardown_schema(schema))


# ---------------------------------------------------------------------------
# Per-test fixtures: transaction rollback isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_settings() -> None:
    """Snapshot settings before each test and restore after to prevent mutation bleed."""
    original = settings.model_dump()
    object.__setattr__(settings, "dev_deposit_enabled", True)
    object.__setattr__(settings, "email_verification_required", False)
    yield  # type: ignore[misc]
    for key, value in original.items():
        object.__setattr__(settings, key, value)


@pytest_asyncio.fixture
async def _worker_engine(_worker_db_setup: tuple[str, str]) -> AsyncGenerator[AsyncEngine, None]:
    """Async engine for this worker's test DB (created per-test, cheap)."""
    url, schema = _worker_db_setup
    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def _worker_redis(worker_id: str) -> AsyncGenerator[aioredis.Redis, None]:
    """Per-worker Redis connection using separate DB numbers."""
    base_url = settings.redis_url.rsplit("/", 1)[0]
    db_num = _worker_redis_db(worker_id)
    redis_client = aioredis.from_url(f"{base_url}/{db_num}")
    await redis_client.flushdb()
    yield redis_client
    await redis_client.flushdb()
    await redis_client.aclose()


@pytest_asyncio.fixture
async def db_session(
    _worker_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session wrapped in a transaction that rolls back after the test.

    Tests that call session.commit() will commit the inner SAVEPOINT, not the
    outer transaction — so data is still rolled back at the end.
    """
    async with _worker_engine.connect() as conn:
        txn = await conn.begin()
        nested = await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        @sqlalchemy.event.listens_for(session.sync_session, "after_transaction_end")
        def reopen_nested(session_sync, transaction):  # type: ignore[no-untyped-def]
            if conn.closed:
                return
            if not conn.in_nested_transaction():
                conn.sync_connection.begin_nested()  # type: ignore[union-attr]

        yield session

        await session.close()
        await txn.rollback()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    _worker_engine: AsyncEngine,
    _worker_redis: aioredis.Redis,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with overridden DB and Redis dependencies."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[aioredis.Redis, None]:
        yield _worker_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    # Flush rate limit keys for this test
    async for key in _worker_redis.scan_iter("ratelimit:*"):
        await _worker_redis.delete(key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_agent_data(public_key: str | None = None) -> dict:
    """Factory for agent registration payload."""
    if public_key is None:
        _, public_key = generate_keypair()
    return {
        "public_key": public_key,
        "display_name": "Test Agent",
        "description": "A test agent",
        "endpoint_url": "https://example.com/webhook",
        "capabilities": ["test-cap", "another-cap"],
    }


def make_auth_headers(
    agent_id: str,
    private_key_hex: str,
    method: str,
    path: str,
    body: bytes | dict | list | None = None,
) -> dict[str, str]:
    """Build signed auth headers for a request."""
    from datetime import UTC, datetime

    from app.utils.crypto import generate_nonce, sign_request

    if body is None:
        body_bytes = b""
    elif isinstance(body, bytes):
        body_bytes = body
    else:
        body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()

    timestamp = datetime.now(UTC).isoformat()
    signature = sign_request(private_key_hex, timestamp, method, path, body_bytes)
    nonce = generate_nonce()
    return {
        "Authorization": f"AgentSig {agent_id}:{signature}",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
    }
