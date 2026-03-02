"""Tests for human confirmation flow features.

Covers:
- Feature 1: verify-email HTML vs JSON content negotiation
- Feature 2: registration confirmation email
- Feature 3: GET /agents/{agent_id}/status (JSON + HTML)
- Feature 4: webhook notification on registration
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.account import Account, EmailVerification
from app.models.agent import Agent
from app.redis import get_redis
from app.utils.crypto import generate_keypair

from tests.conftest import make_agent_data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client_with_email_required(db_session, _worker_engine, _worker_redis):
    """HTTP test client with email_verification_required=True."""
    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield _worker_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    async for key in _worker_redis.scan_iter("ratelimit:*"):
        await _worker_redis.delete(key)

    object.__setattr__(settings, "email_verification_required", True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db_session

    app.dependency_overrides.clear()


async def _setup_verified_account(db: AsyncSession, email: str = "test@example.com"):
    """Create an account + valid registration token. Returns (account, reg_token)."""
    reg_token = secrets.token_urlsafe(48)
    account = Account(
        account_id=uuid.uuid4(),
        email=email,
        email_verified=True,
    )
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email=email,
        token=secrets.token_urlsafe(48),
        registration_token=reg_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()
    return account, reg_token


async def _create_agent_directly(db: AsyncSession) -> Agent:
    """Insert an agent directly in DB for testing."""
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=generate_keypair()[1],
        display_name="Status Test Agent",
        description="An agent for testing",
        endpoint_url="https://example.com/webhook",
        capabilities=["search", "code-gen"],
        webhook_secret=secrets.token_hex(32),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


# ---------------------------------------------------------------------------
# Feature 1: verify-email content negotiation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_email_returns_html_when_accept_html(client_with_email_required):
    """GET /auth/verify-email with Accept: text/html should return an HTML page."""
    client, db = client_with_email_required

    token = secrets.token_urlsafe(48)
    account = Account(account_id=uuid.uuid4(), email="html@example.com")
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="html@example.com",
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(
        f"/auth/verify-email?token={token}",
        headers={"Accept": "text/html"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Email Verified" in body
    assert "Registration Token" in body
    assert "POST /agents" in body
    # The registration token should be embedded in the page
    assert len(body) > 100  # sanity check that it's a full HTML page


@pytest.mark.asyncio
async def test_verify_email_returns_json_by_default(client_with_email_required):
    """GET /auth/verify-email without Accept: text/html returns JSON."""
    client, db = client_with_email_required

    token = secrets.token_urlsafe(48)
    account = Account(account_id=uuid.uuid4(), email="json@example.com")
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="json@example.com",
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
    assert data["message"] == "Email verified."


@pytest.mark.asyncio
async def test_verify_email_json_with_accept_json(client_with_email_required):
    """GET /auth/verify-email with Accept: application/json returns JSON."""
    client, db = client_with_email_required

    token = secrets.token_urlsafe(48)
    account = Account(account_id=uuid.uuid4(), email="ajson@example.com")
    db.add(account)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="ajson@example.com",
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(
        f"/auth/verify-email?token={token}",
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "registration_token" in data
    assert data["expires_in_seconds"] == 3600


# ---------------------------------------------------------------------------
# Feature 2: registration confirmation email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_sends_confirmation_email(client_with_email_required):
    """After POST /agents with email verification, a confirmation email is sent."""
    client, db = client_with_email_required
    account, reg_token = await _setup_verified_account(db, "confirm@example.com")

    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)
    agent_data["registration_token"] = reg_token

    with patch("app.services.agent.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        # Also mock enqueue_webhook to avoid DB issues with separate commit
        with patch("app.services.agent.enqueue_webhook", new_callable=AsyncMock):
            resp = await client.post("/agents", json=agent_data)

    assert resp.status_code == 201
    mock_sender.send.assert_called_once()
    call_kwargs = mock_sender.send.call_args.kwargs
    assert call_kwargs["to"] == "confirm@example.com"
    assert "registered" in call_kwargs["subject"].lower() or "registered" in call_kwargs["body"].lower()
    assert "Test Agent" in call_kwargs["body"]
    assert resp.json()["agent_id"] in call_kwargs["body"]


@pytest.mark.asyncio
async def test_registration_no_email_when_no_account(client):
    """When registering without email verification, no confirmation email is sent."""
    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)

    with patch("app.services.agent.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        with patch("app.services.agent.enqueue_webhook", new_callable=AsyncMock):
            resp = await client.post("/agents", json=agent_data)

    assert resp.status_code == 201
    mock_sender.send.assert_not_called()


# ---------------------------------------------------------------------------
# Feature 3: GET /agents/{agent_id}/status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_status_returns_json(client, db_session):
    """GET /agents/{agent_id}/status returns correct JSON."""
    agent = await _create_agent_directly(db_session)

    resp = await client.get(f"/agents/{agent.agent_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == str(agent.agent_id)
    assert data["display_name"] == "Status Test Agent"
    assert data["status"] == "active"
    assert data["capabilities"] == ["search", "code-gen"]
    assert "created_at" in data
    assert "last_seen" in data


@pytest.mark.asyncio
async def test_agent_status_returns_html(client, db_session):
    """GET /agents/{agent_id}/status with Accept: text/html returns HTML page."""
    agent = await _create_agent_directly(db_session)

    resp = await client.get(
        f"/agents/{agent.agent_id}/status",
        headers={"Accept": "text/html"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Status Test Agent" in body
    assert "ACTIVE" in body
    assert str(agent.agent_id) in body
    assert "search" in body
    assert "code-gen" in body
    # Green checkmark for active agents
    assert "\u2705" in body


@pytest.mark.asyncio
async def test_agent_status_404_nonexistent(client):
    """GET /agents/{agent_id}/status returns 404 for nonexistent agent."""
    fake_id = uuid.uuid4()
    resp = await client.get(f"/agents/{fake_id}/status")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_status_deactivated_agent(client, db_session):
    """GET /agents/{agent_id}/status shows deactivated status correctly."""
    from app.models.agent import AgentStatus

    agent = await _create_agent_directly(db_session)
    agent.status = AgentStatus.DEACTIVATED
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await client.get(f"/agents/{agent.agent_id}/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"

    # HTML version should show red X
    resp_html = await client.get(
        f"/agents/{agent.agent_id}/status",
        headers={"Accept": "text/html"},
    )
    assert "\u274c" in resp_html.text
    assert "DEACTIVATED" in resp_html.text


# ---------------------------------------------------------------------------
# Feature 4: webhook notification on registration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_enqueues_webhook(client, db_session):
    """POST /agents enqueues an agent.registered webhook."""
    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)

    with patch("app.services.agent.enqueue_webhook", new_callable=AsyncMock) as mock_enqueue:
        with patch("app.services.agent.build_a2a_push_notification") as mock_build:
            mock_build.return_value = {"test": "payload"}
            resp = await client.post("/agents", json=agent_data)

    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    mock_build.assert_called_once()
    build_kwargs = mock_build.call_args.kwargs
    assert build_kwargs["event_type"] == "agent.registered"
    assert build_kwargs["state"] == "completed"
    assert build_kwargs["details"]["agent_id"] == agent_id
    assert build_kwargs["details"]["display_name"] == "Test Agent"
    assert build_kwargs["details"]["status"] == "active"
    assert build_kwargs["details"]["capabilities"] == ["test-cap", "another-cap"]

    mock_enqueue.assert_called_once()
    enqueue_args = mock_enqueue.call_args
    assert str(enqueue_args.args[1]) == agent_id  # target_agent_id
    assert enqueue_args.args[2] == "agent.registered"  # event_type


@pytest.mark.asyncio
async def test_registration_webhook_has_a2a_format(client, db_session):
    """The webhook payload should be A2A-compliant push notification."""
    _, public_key = generate_keypair()
    agent_data = make_agent_data(public_key)

    with patch("app.services.agent.enqueue_webhook", new_callable=AsyncMock) as mock_enqueue:
        resp = await client.post("/agents", json=agent_data)

    assert resp.status_code == 201

    # The payload passed to enqueue_webhook should be A2A formatted
    payload = mock_enqueue.call_args.args[3]
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tasks/pushNotification"
    assert payload["params"]["status"]["state"] == "completed"
    parts = payload["params"]["status"]["message"]["parts"]
    assert parts[0]["kind"] == "data"
    assert parts[0]["data"]["event"] == "agent.registered"
