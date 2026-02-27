"""Tests for email verification gated registration."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import get_db, Base
from app.main import app
from app.models.account import Account, EmailVerification
from app.redis import get_redis
from app.utils.crypto import generate_keypair

from tests.conftest import make_agent_data


@pytest_asyncio.fixture
async def client_with_email_required(db_session, _worker_engine, _worker_redis):
    """HTTP test client with email_verification_required=True.

    Re-uses the per-test db_session (transaction rollback) from conftest.
    """
    import redis.asyncio as aioredis

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield _worker_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    # Flush rate limit keys
    async for key in _worker_redis.scan_iter("ratelimit:*"):
        await _worker_redis.delete(key)

    # Enable email verification requirement
    object.__setattr__(settings, "email_verification_required", True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db_session

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_signup_sends_verification_email(client_with_email_required):
    client, _ = client_with_email_required

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/signup", json={"email": "test@example.com"})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Verification email sent. Check your inbox."
        mock_sender.send.assert_called_once()
        call_args = mock_sender.send.call_args
        assert call_args.kwargs["to"] == "test@example.com"
        assert "verify-email?token=" in call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_signup_normalizes_email(client_with_email_required):
    client, db = client_with_email_required

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/signup", json={"email": "  TEST@Example.COM  "})
        assert resp.status_code == 200

    # Using shared db_session directly
    from sqlalchemy import select
    result = await db.execute(select(Account))
    account = result.scalar_one()
    assert account.email == "test@example.com"


@pytest.mark.asyncio
async def test_signup_rejects_invalid_email(client_with_email_required):
    client, _ = client_with_email_required
    resp = await client.post("/auth/signup", json={"email": "not-an-email"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_signup_rejects_if_agent_already_linked(client_with_email_required):
    """Cannot sign up again if email already has an active agent."""
    client, db = client_with_email_required

    # Create a real agent, then link it to an account
    # Using shared db_session directly
    from app.models.agent import Agent
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=generate_keypair()[1],
        display_name="Existing Agent",
        endpoint_url="https://example.com",
        webhook_secret=secrets.token_hex(32),
    )
    db.add(agent)
    await db.flush()
    account = Account(
        account_id=uuid.uuid4(),
        email="taken@example.com",
        email_verified=True,
        agent_id=agent.agent_id,
    )
    db.add(account)
    await db.commit()

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/signup", json={"email": "taken@example.com"})
        assert resp.status_code == 409
        assert "already has an active agent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_verify_email_returns_registration_token(client_with_email_required):
    client, db = client_with_email_required

    # Create account + verification token directly
    token = secrets.token_urlsafe(48)
    # Using shared db_session directly
    account = Account(
        account_id=uuid.uuid4(),
        email="verify@example.com",
    )
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="verify@example.com",
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-email?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert "registration_token" in data
    assert data["expires_in_seconds"] == 3600
    assert len(data["registration_token"]) > 0


@pytest.mark.asyncio
async def test_verify_email_expired_token(client_with_email_required):
    client, db = client_with_email_required

    token = secrets.token_urlsafe(48)
    # Using shared db_session directly
    account = Account(
        account_id=uuid.uuid4(),
        email="expired@example.com",
    )
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="expired@example.com",
        token=token,
        expires_at=datetime.now(UTC) - timedelta(hours=1),  # Already expired
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-email?token={token}")
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_verify_email_invalid_token(client_with_email_required):
    client, _ = client_with_email_required
    resp = await client.get("/auth/verify-email?token=nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_full_signup_verify_register_flow(client_with_email_required):
    """End-to-end: signup → verify → register agent."""
    client, db = client_with_email_required

    # 1. Signup
    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender
        resp = await client.post("/auth/signup", json={"email": "flow@example.com"})
        assert resp.status_code == 200

        # Extract token from the email body
        call_args = mock_sender.send.call_args
        body = call_args.kwargs["body"]
        # Find token= in the URL
        import re
        match = re.search(r"token=([A-Za-z0-9_-]+)", body)
        assert match, "Token not found in email body"
        verify_token = match.group(1)

    # 2. Verify email
    resp = await client.get(f"/auth/verify-email?token={verify_token}")
    assert resp.status_code == 200
    registration_token = resp.json()["registration_token"]

    # 3. Register agent with registration token
    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)
    agent_data["registration_token"] = registration_token

    resp = await client.post("/agents", json=agent_data)
    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    # 4. Verify account is linked
    # Using shared db_session directly
    from sqlalchemy import select
    result = await db.execute(
        select(Account).where(Account.email == "flow@example.com")
    )
    account = result.scalar_one()
    assert str(account.agent_id) == agent_id


@pytest.mark.asyncio
async def test_register_without_token_when_required(client_with_email_required):
    """POST /agents without registration token should fail when email_verification_required=True."""
    client, _ = client_with_email_required

    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)
    # No registration_token

    resp = await client.post("/agents", json=agent_data)
    assert resp.status_code == 422
    assert "Registration token required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_with_invalid_token(client_with_email_required):
    client, _ = client_with_email_required

    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)
    agent_data["registration_token"] = "bogus-token"

    resp = await client.post("/agents", json=agent_data)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_registration_token_cannot_be_reused(client_with_email_required):
    """Registration token is one-use: second registration with same token should fail."""
    client, db = client_with_email_required

    # Create verified account with registration token
    reg_token = secrets.token_urlsafe(48)
    # Using shared db_session directly
    account = Account(
        account_id=uuid.uuid4(),
        email="reuse@example.com",
        email_verified=True,
    )
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="reuse@example.com",
        token=secrets.token_urlsafe(48),
        registration_token=reg_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    # First registration succeeds
    _, pk1 = generate_keypair()
    agent_data1 = make_agent_data(pk1)
    agent_data1["registration_token"] = reg_token
    resp = await client.post("/agents", json=agent_data1)
    assert resp.status_code == 201

    # Second registration with same token fails (account already has agent)
    _, pk2 = generate_keypair()
    agent_data2 = make_agent_data(pk2)
    agent_data2["registration_token"] = reg_token
    resp = await client.post("/agents", json=agent_data2)
    assert resp.status_code == 409
    assert "already has an active agent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_works_without_email_when_not_required(client):
    """When email_verification_required=False, registration works as before (no token needed)."""
    # client fixture from conftest.py has email_verification_required=False (default)
    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)
    resp = await client.post("/agents", json=agent_data)
    assert resp.status_code == 201
