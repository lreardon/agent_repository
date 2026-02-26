"""Test configuration and fixtures."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.redis import get_redis
from app.utils.crypto import generate_keypair


@pytest.fixture(autouse=True)
def _isolate_settings() -> None:
    """Snapshot settings before each test and restore after to prevent mutation bleed.

    Uses object.__setattr__ to mutate the *same* Settings instance in-place,
    since app modules hold direct references via `from app.config import settings`.
    """
    original = settings.model_dump()
    yield  # type: ignore[misc]
    for key, value in original.items():
        object.__setattr__(settings, key, value)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh engine + tables per test, tear down after."""
    test_engine = create_async_engine(settings.test_database_url)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        # Drop orphaned Postgres enum types that drop_all leaves behind
        await conn.execute(sqlalchemy.text(
            "DO $$ DECLARE r RECORD; "
            "BEGIN FOR r IN (SELECT typname FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
            "WHERE n.nspname = 'public' AND t.typtype = 'e') "
            "LOOP EXECUTE 'DROP TYPE IF EXISTS ' || quote_ident(r.typname) || ' CASCADE'; END LOOP; END $$;"
        ))
        # Clear stale alembic version so migrations can re-run cleanly
        await conn.execute(sqlalchemy.text("DROP TABLE IF EXISTS alembic_version"))

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with overridden DB and Redis dependencies."""
    test_engine = create_async_engine(settings.test_database_url)
    test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            yield session

    redis_client = aioredis.from_url(settings.redis_url)

    async def override_get_redis() -> AsyncGenerator[aioredis.Redis, None]:
        yield redis_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    # Flush rate limit keys to prevent cross-test contamination
    async for key in redis_client.scan_iter("ratelimit:*"):
        await redis_client.delete(key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await redis_client.aclose()
    await test_engine.dispose()
    app.dependency_overrides.clear()


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
    """Build signed auth headers for a request.

    body can be bytes, dict/list (serialized to match httpx), or None (empty).
    """
    from datetime import UTC, datetime

    from app.utils.crypto import generate_nonce, sign_request

    if body is None:
        body_bytes = b""
    elif isinstance(body, bytes):
        body_bytes = body
    else:
        # Match httpx's default JSON serialization (no spaces, UTF-8 not escaped)
        body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()

    timestamp = datetime.now(UTC).isoformat()
    signature = sign_request(private_key_hex, timestamp, method, path, body_bytes)
    nonce = generate_nonce()
    return {
        "Authorization": f"AgentSig {agent_id}:{signature}",
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
    }
