"""Tests for key recovery and rotation flow."""

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, Base
from app.main import app
from app.models.account import Account, EmailVerification, VerificationPurpose
from app.models.agent import Agent
from app.redis import get_redis
from app.utils.crypto import generate_keypair

from tests.conftest import make_agent_data, make_auth_headers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client_with_email_required(db_session, _worker_engine, _worker_redis):
    """HTTP test client with email_verification_required=True."""
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

    object.__setattr__(settings, "email_verification_required", True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db_session

    app.dependency_overrides.clear()


async def _create_account_with_agent(
    db: AsyncSession,
    email: str = "recover@example.com",
    email_verified: bool = True,
) -> tuple[Account, Agent, str, str]:
    """Helper: create a verified account with a linked agent.

    Returns (account, agent, private_key_hex, public_key_hex).
    """
    private_key, public_key = generate_keypair()
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=public_key,
        display_name="Recoverable Agent",
        endpoint_url="https://example.com/webhook",
        webhook_secret=secrets.token_hex(32),
    )
    db.add(agent)
    await db.flush()

    account = Account(
        account_id=uuid.uuid4(),
        email=email,
        email_verified=email_verified,
        agent_id=agent.agent_id,
    )
    db.add(account)
    await db.commit()
    return account, agent, private_key, public_key


# ---------------------------------------------------------------------------
# POST /auth/recover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_sends_email_for_eligible_account(client_with_email_required):
    """Recovery request for eligible account sends email."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "recover@example.com"})
        assert resp.status_code == 200
        assert "recovery link has been sent" in resp.json()["message"]
        mock_sender.send.assert_called_once()
        call_args = mock_sender.send.call_args
        assert call_args.kwargs["to"] == "recover@example.com"
        assert "verify-recovery?token=" in call_args.kwargs["body"]
        assert "Key Recovery" in call_args.kwargs["subject"]


@pytest.mark.asyncio
async def test_recover_generic_response_for_nonexistent_email(client_with_email_required):
    """Recovery request for unknown email returns same generic success (no leak)."""
    client, _ = client_with_email_required

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "nobody@example.com"})
        assert resp.status_code == 200
        assert "recovery link has been sent" in resp.json()["message"]
        # No email should actually be sent
        mock_sender.send.assert_not_called()


@pytest.mark.asyncio
async def test_recover_no_email_for_unverified_account(client_with_email_required):
    """Recovery request for account with unverified email doesn't send email."""
    client, db = client_with_email_required
    await _create_account_with_agent(db, email="unverified@example.com", email_verified=False)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "unverified@example.com"})
        assert resp.status_code == 200
        mock_sender.send.assert_not_called()


@pytest.mark.asyncio
async def test_recover_no_email_for_account_without_agent(client_with_email_required):
    """Recovery request for verified account without an agent doesn't send email."""
    client, db = client_with_email_required

    # Account exists, verified, but no agent linked
    account = Account(
        account_id=uuid.uuid4(),
        email="noagent@example.com",
        email_verified=True,
        agent_id=None,
    )
    db.add(account)
    await db.commit()

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "noagent@example.com"})
        assert resp.status_code == 200
        mock_sender.send.assert_not_called()


@pytest.mark.asyncio
async def test_recover_invalidates_previous_tokens(client_with_email_required, _worker_redis):
    """New recovery request invalidates previous unused recovery tokens."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        # First recovery request
        await client.post("/auth/recover", json={"email": "recover@example.com"})

    # Extract first token
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.email == "recover@example.com",
            EmailVerification.purpose == VerificationPurpose.recovery,
        )
    )
    tokens = list(result.scalars())
    assert len(tokens) == 1
    first_token_value = tokens[0].token

    # Flush rate limit keys so second request isn't throttled
    async for key in _worker_redis.scan_iter("ratelimit:*"):
        await _worker_redis.delete(key)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        # Second recovery request
        resp2 = await client.post("/auth/recover", json={"email": "recover@example.com"})
        assert resp2.status_code == 200, f"Second request failed: {resp2.status_code} {resp2.text}"

    # First token should be invalidated, new one created
    db.expire_all()
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.email == "recover@example.com",
            EmailVerification.purpose == VerificationPurpose.recovery,
        )
    )
    all_tokens = list(result.scalars())
    assert len(all_tokens) == 2
    used_tokens = [t for t in all_tokens if t.used]
    unused_tokens = [t for t in all_tokens if not t.used]
    assert len(used_tokens) == 1
    assert len(unused_tokens) == 1
    assert used_tokens[0].token == first_token_value


@pytest.mark.asyncio
async def test_recover_rate_limited(client_with_email_required):
    """POST /auth/recover is rate limited (same as signup: 1/min)."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        # First request should succeed
        resp = await client.post("/auth/recover", json={"email": "recover@example.com"})
        assert resp.status_code == 200

        # Second immediate request should be rate limited
        resp = await client.post("/auth/recover", json={"email": "recover@example.com"})
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# GET /auth/verify-recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_recovery_returns_recovery_token(client_with_email_required):
    """Valid recovery token returns a one-time recovery token (1hr expiry)."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-recovery?token={token}")
    assert resp.status_code == 200
    data = resp.json()
    assert "recovery_token" in data
    assert data["expires_in_seconds"] == 3600
    assert len(data["recovery_token"]) > 0


@pytest.mark.asyncio
async def test_verify_recovery_html_response(client_with_email_required):
    """Verify recovery with Accept: text/html returns HTML page."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(
        f"/auth/verify-recovery?token={token}",
        headers={"accept": "text/html"},
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Recovery Verified" in resp.text
    assert "rotate-key" in resp.text
    # AUTH-7: Verify a recovery token value appears in the HTML (issued by verify_recovery,
    # different from the verification token used in the URL)
    assert '<div class="token">' in resp.text
    # The token div should contain a non-empty value (the issued recovery token)
    import re
    token_match = re.search(r'<div class="token">(.+?)</div>', resp.text)
    assert token_match is not None, "Recovery token div should contain a value"
    assert len(token_match.group(1).strip()) > 20, "Recovery token should be a substantial string"


@pytest.mark.asyncio
async def test_verify_recovery_expired_token(client_with_email_required):
    """Expired recovery verification token returns 410."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=token,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-recovery?token={token}")
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_verify_recovery_invalid_token(client_with_email_required):
    """Nonexistent recovery token returns 404."""
    client, _ = client_with_email_required
    resp = await client.get("/auth/verify-recovery?token=nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_recovery_signup_token_not_accepted(client_with_email_required):
    """A signup-purpose verification token should not work on verify-recovery."""
    client, db = client_with_email_required

    account = Account(
        account_id=uuid.uuid4(),
        email="signup-only@example.com",
    )
    db.add(account)

    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="signup-only@example.com",
        purpose=VerificationPurpose.signup,
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-recovery?token={token}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_recovery_fails_if_account_lost_agent(client_with_email_required):
    """If agent was unlinked between request and verify, recovery fails."""
    client, db = client_with_email_required

    # Account with verified email but no agent
    account = Account(
        account_id=uuid.uuid4(),
        email="orphan@example.com",
        email_verified=True,
        agent_id=None,
    )
    db.add(account)

    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="orphan@example.com",
        purpose=VerificationPurpose.recovery,
        token=token,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db.add(verification)
    await db.commit()

    resp = await client.get(f"/auth/verify-recovery?token={token}")
    assert resp.status_code == 404
    assert "not eligible" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /auth/rotate-key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_key_success(client_with_email_required):
    """Happy path: valid recovery token + new key rotates the key."""
    client, db = client_with_email_required
    account, agent, old_priv, old_pub = await _create_account_with_agent(db)

    # Create a verified recovery with issued recovery token
    recovery_token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=secrets.token_urlsafe(48),
        registration_token=recovery_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    _, new_public_key = generate_keypair()

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/rotate-key", json={
            "recovery_token": recovery_token,
            "new_public_key": new_public_key,
        })
        assert resp.status_code == 200
        assert "rotated successfully" in resp.json()["message"]

        # Confirmation email should be sent
        mock_sender.send.assert_called_once()
        call_args = mock_sender.send.call_args
        assert "rotated" in call_args.kwargs["subject"].lower()

    # Verify key was actually changed in DB
    await db.refresh(agent)
    assert agent.public_key == new_public_key
    assert agent.public_key != old_pub


@pytest.mark.asyncio
async def test_rotate_key_token_single_use(client_with_email_required):
    """Recovery token is invalidated after use — second rotation fails."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    recovery_token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=secrets.token_urlsafe(48),
        registration_token=recovery_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    _, new_key1 = generate_keypair()

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/rotate-key", json={
            "recovery_token": recovery_token,
            "new_public_key": new_key1,
        })
        assert resp.status_code == 200

    # Second attempt with same token should fail
    _, new_key2 = generate_keypair()
    resp = await client.post("/auth/rotate-key", json={
        "recovery_token": recovery_token,
        "new_public_key": new_key2,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rotate_key_expired_recovery_token(client_with_email_required):
    """Expired recovery token returns 410."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    recovery_token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=secrets.token_urlsafe(48),
        registration_token=recovery_token,
        registration_token_expires_at=datetime.now(UTC) - timedelta(hours=1),  # Expired
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    _, new_key = generate_keypair()
    resp = await client.post("/auth/rotate-key", json={
        "recovery_token": recovery_token,
        "new_public_key": new_key,
    })
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_rotate_key_invalid_recovery_token(client_with_email_required):
    """Bogus recovery token returns 401."""
    client, _ = client_with_email_required
    _, new_key = generate_keypair()
    resp = await client.post("/auth/rotate-key", json={
        "recovery_token": "bogus-token",
        "new_public_key": new_key,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rotate_key_duplicate_public_key(client_with_email_required):
    """Rotation fails if new_public_key is already registered to another agent."""
    client, db = client_with_email_required
    account, agent, _, old_pub = await _create_account_with_agent(db)

    # Create another agent with a known public key
    _, other_pub = generate_keypair()
    other_agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=other_pub,
        display_name="Other Agent",
        endpoint_url="https://other.example.com",
        webhook_secret=secrets.token_hex(32),
    )
    db.add(other_agent)
    await db.flush()

    recovery_token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.recovery,
        token=secrets.token_urlsafe(48),
        registration_token=recovery_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    # Try to rotate to the other agent's public key
    resp = await client.post("/auth/rotate-key", json={
        "recovery_token": recovery_token,
        "new_public_key": other_pub,
    })
    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_rotate_key_signup_token_not_accepted(client_with_email_required):
    """A signup-purpose registration token should not work for key rotation."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    reg_token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email="recover@example.com",
        purpose=VerificationPurpose.signup,
        token=secrets.token_urlsafe(48),
        registration_token=reg_token,
        registration_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        used=True,
    )
    db.add(verification)
    await db.commit()

    _, new_key = generate_keypair()
    resp = await client.post("/auth/rotate-key", json={
        "recovery_token": reg_token,
        "new_public_key": new_key,
    })
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Full end-to-end flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_recovery_flow(client_with_email_required):
    """End-to-end: request recovery → verify → rotate key → auth with new key."""
    client, db = client_with_email_required
    account, agent, old_priv, old_pub = await _create_account_with_agent(db)

    # 1. Request recovery
    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "recover@example.com"})
        assert resp.status_code == 200

        # Extract verification token from email
        call_args = mock_sender.send.call_args
        body = call_args.kwargs["body"]
        match = re.search(r"token=([A-Za-z0-9_-]+)", body)
        assert match, "Token not found in recovery email body"
        verify_token = match.group(1)

    # 2. Verify recovery
    resp = await client.get(f"/auth/verify-recovery?token={verify_token}")
    assert resp.status_code == 200
    recovery_token = resp.json()["recovery_token"]
    assert resp.json()["expires_in_seconds"] == 3600

    # 3. Rotate key
    new_priv, new_pub = generate_keypair()

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/rotate-key", json={
            "recovery_token": recovery_token,
            "new_public_key": new_pub,
        })
        assert resp.status_code == 200

        # Confirmation email sent
        mock_sender.send.assert_called_once()

    # 4. Verify old key no longer works and new key does
    await db.refresh(agent)
    assert agent.public_key == new_pub

    # Make an authenticated request with the new key (PATCH is auth-required)
    agent_id = str(agent.agent_id)
    headers = make_auth_headers(agent_id, new_priv, "PATCH", f"/agents/{agent_id}", body=b"{}")
    headers["Content-Type"] = "application/json"
    resp = await client.patch(f"/agents/{agent_id}", headers=headers, content=b"{}")
    assert resp.status_code == 200

    # Old key should fail
    headers = make_auth_headers(agent_id, old_priv, "PATCH", f"/agents/{agent_id}", body=b"{}")
    headers["Content-Type"] = "application/json"
    resp = await client.patch(f"/agents/{agent_id}", headers=headers, content=b"{}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_recovery_normalizes_email(client_with_email_required):
    """Recovery email is normalized (lowercase, stripped)."""
    client, db = client_with_email_required
    await _create_account_with_agent(db)

    with patch("app.services.account.get_email_sender") as mock_sender_factory:
        mock_sender = AsyncMock()
        mock_sender_factory.return_value = mock_sender

        resp = await client.post("/auth/recover", json={"email": "  RECOVER@Example.COM  "})
        assert resp.status_code == 200
        mock_sender.send.assert_called_once()
