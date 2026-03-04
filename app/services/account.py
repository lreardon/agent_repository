"""Account service: signup, email verification, registration token management, key recovery."""

import logging
import secrets
import uuid
from functools import lru_cache
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.account import Account, EmailVerification, VerificationPurpose
from app.models.agent import Agent
from app.services.email import get_email_sender, make_html_email

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_disposable_domains() -> frozenset[str]:
    """Load the disposable email domain blocklist (cached)."""
    blocklist_path = Path(__file__).parent.parent / "data" / "disposable_domains.txt"
    if not blocklist_path.exists():
        logger.warning("Disposable domain blocklist not found at %s", blocklist_path)
        return frozenset()
    domains = set()
    for line in blocklist_path.read_text().splitlines():
        line = line.strip().lower()
        if line and not line.startswith("#"):
            domains.add(line)
    logger.info("Loaded %d disposable email domains", len(domains))
    return frozenset(domains)


def _check_disposable_email(email: str) -> None:
    """Reject emails from known disposable/temporary email providers."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in _load_disposable_domains():
        raise HTTPException(
            status_code=422,
            detail="Disposable email addresses are not allowed. Please use a permanent email.",
        )

# Verification email link expires in 24 hours
VERIFICATION_EXPIRY = timedelta(hours=24)
# Registration / recovery token (issued after verification) expires in 1 hour
REGISTRATION_TOKEN_EXPIRY = timedelta(hours=1)


async def request_signup(db: AsyncSession, email: str) -> None:
    """Send a verification email. Creates account record if needed.

    Idempotent: re-sends verification if account exists but is unverified.
    If the account already has a linked agent, rejects with 409.
    """
    email = email.strip().lower()
    _check_disposable_email(email)

    # Check if account exists with an active agent
    result = await db.execute(
        select(Account).where(Account.email == email)
    )
    account = result.scalar_one_or_none()

    if account and account.agent_id is not None:
        raise HTTPException(
            status_code=409,
            detail="This email already has an active agent. "
                   "Deactivate your current agent before registering a new one.",
        )

    # Create account if it doesn't exist
    if account is None:
        account = Account(
            account_id=uuid.uuid4(),
            email=email,
        )
        db.add(account)
        await db.flush()

    # Invalidate any existing unused verification tokens for this email
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.email == email,
            EmailVerification.used == False,  # noqa: E712
        )
    )
    for old_token in result.scalars():
        old_token.used = True

    # Create new verification token
    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email=email,
        token=token,
        expires_at=datetime.now(UTC) + VERIFICATION_EXPIRY,
    )
    db.add(verification)
    await db.commit()

    # Send verification email
    verify_url = f"{settings.base_url}/auth/verify-email?token={token}"
    body = (
        f"Click the link below to verify your email and receive a registration token:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not request this, ignore this email."
    )
    sender = get_email_sender()
    await sender.send(
        to=email,
        subject="Agent Registry — Verify your email",
        body=body,
        from_name="Arcoa",
        html=make_html_email(body),
    )
    logger.info("Verification email sent to %s", email)


async def verify_email(db: AsyncSession, token: str) -> tuple[str, int]:
    """Verify email token and return a one-time registration token.

    Returns (registration_token, expires_in_seconds).
    """
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.token == token,
            EmailVerification.used == False,  # noqa: E712
        )
    )
    verification = result.scalar_one_or_none()

    if verification is None:
        raise HTTPException(status_code=404, detail="Invalid or expired verification token")

    if datetime.now(UTC) > verification.expires_at:
        verification.used = True
        await db.commit()
        raise HTTPException(status_code=410, detail="Verification token has expired")

    # Mark as used and generate registration token
    verification.used = True
    registration_token = secrets.token_urlsafe(48)
    verification.registration_token = registration_token
    # Registration token expires 1 hour from now
    verification.registration_token_expires_at = datetime.now(UTC) + REGISTRATION_TOKEN_EXPIRY

    # Mark account as verified
    result = await db.execute(
        select(Account).where(Account.email == verification.email)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    account.email_verified = True
    await db.commit()

    expires_in = int(REGISTRATION_TOKEN_EXPIRY.total_seconds())
    logger.info("Email verified for %s, registration token issued", verification.email)
    return registration_token, expires_in


async def validate_registration_token(db: AsyncSession, token: str) -> Account:
    """Validate a registration token and return the associated account.

    Raises HTTPException if invalid, expired, or the account already has an agent.
    """
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.registration_token == token,
        )
    )
    verification = result.scalar_one_or_none()

    if verification is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid registration token. Sign up at POST /auth/signup to get one.",
        )

    if datetime.now(UTC) > verification.registration_token_expires_at:
        raise HTTPException(status_code=410, detail="Registration token has expired. Request a new signup.")

    result = await db.execute(
        select(Account).where(Account.email == verification.email)
    )
    account = result.scalar_one_or_none()

    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    if account.agent_id is not None:
        raise HTTPException(
            status_code=409,
            detail="This email already has an active agent. "
                   "Deactivate your current agent before registering a new one.",
        )

    return account


async def link_agent_to_account(
    db: AsyncSession, account: Account, agent_id: uuid.UUID
) -> None:
    """Link a newly registered agent to an account."""
    account.agent_id = agent_id
    await db.flush()


# ---------------------------------------------------------------------------
# Key Recovery
# ---------------------------------------------------------------------------


async def request_recovery(db: AsyncSession, email: str) -> None:
    """Send a recovery email if the account is eligible.

    Eligible means: account exists, email_verified=True, has an active agent_id.
    Always returns successfully (don't leak whether the email exists).
    """
    email = email.strip().lower()

    result = await db.execute(
        select(Account).where(Account.email == email)
    )
    account = result.scalar_one_or_none()

    # Silently bail if account doesn't exist, email not verified, or no agent
    if account is None or not account.email_verified or account.agent_id is None:
        return

    # Invalidate any existing unused recovery tokens for this email
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.email == email,
            EmailVerification.purpose == VerificationPurpose.recovery,
            EmailVerification.used == False,  # noqa: E712
        )
    )
    for old_token in result.scalars():
        old_token.used = True

    # Create new recovery verification token
    token = secrets.token_urlsafe(48)
    verification = EmailVerification(
        verification_id=uuid.uuid4(),
        email=email,
        purpose=VerificationPurpose.recovery,
        token=token,
        expires_at=datetime.now(UTC) + VERIFICATION_EXPIRY,
    )
    db.add(verification)
    await db.commit()

    # Send recovery email
    verify_url = f"{settings.base_url}/auth/verify-recovery?token={token}"
    body = (
        f"A key recovery was requested for your account.\n\n"
        f"Click the link below to verify your identity and receive a recovery token:\n\n"
        f"{verify_url}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you did not request this, ignore this email."
    )
    sender = get_email_sender()
    await sender.send(
        to=email,
        subject="Agent Registry — Key Recovery",
        body=body,
        from_name="Arcoa Support",
        html=make_html_email(body),
    )
    logger.info("Recovery email sent to %s", email)


async def verify_recovery(db: AsyncSession, token: str) -> tuple[str, int]:
    """Verify recovery email token and return a one-time recovery token.

    Returns (recovery_token, expires_in_seconds).
    """
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.token == token,
            EmailVerification.purpose == VerificationPurpose.recovery,
            EmailVerification.used == False,  # noqa: E712
        )
    )
    verification = result.scalar_one_or_none()

    if verification is None:
        raise HTTPException(status_code=404, detail="Invalid or expired recovery token")

    if datetime.now(UTC) > verification.expires_at:
        verification.used = True
        await db.commit()
        raise HTTPException(status_code=410, detail="Recovery token has expired")

    # Ensure the account still qualifies (verified email + active agent)
    result = await db.execute(
        select(Account).where(Account.email == verification.email)
    )
    account = result.scalar_one_or_none()
    if account is None or not account.email_verified or account.agent_id is None:
        verification.used = True
        await db.commit()
        raise HTTPException(status_code=404, detail="Account is not eligible for recovery")

    # Mark as used and generate recovery token
    verification.used = True
    recovery_token = secrets.token_urlsafe(48)
    verification.registration_token = recovery_token
    verification.registration_token_expires_at = datetime.now(UTC) + REGISTRATION_TOKEN_EXPIRY

    await db.commit()

    expires_in = int(REGISTRATION_TOKEN_EXPIRY.total_seconds())
    logger.info("Recovery verified for %s, recovery token issued", verification.email)
    return recovery_token, expires_in


async def rotate_key(
    db: AsyncSession, recovery_token: str, new_public_key: str
) -> None:
    """Rotate an agent's public key using a valid recovery token.

    Validates the token, checks for duplicate keys, updates the agent,
    invalidates the token, and sends a confirmation email.
    """
    # Look up the recovery token (stored in registration_token column)
    result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.registration_token == recovery_token,
            EmailVerification.purpose == VerificationPurpose.recovery,
        )
    )
    verification = result.scalar_one_or_none()

    if verification is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid recovery token. Request a new one at POST /auth/recover.",
        )

    if datetime.now(UTC) > verification.registration_token_expires_at:
        raise HTTPException(
            status_code=410,
            detail="Recovery token has expired. Request a new one at POST /auth/recover.",
        )

    # Fetch the account and linked agent
    result = await db.execute(
        select(Account).where(Account.email == verification.email)
    )
    account = result.scalar_one_or_none()

    if account is None or account.agent_id is None:
        raise HTTPException(status_code=404, detail="Account or agent not found")

    # Check for duplicate public key
    result = await db.execute(
        select(Agent).where(Agent.public_key == new_public_key)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Public key already registered")

    # Rotate the key
    result = await db.execute(
        select(Agent).where(Agent.agent_id == account.agent_id)
    )
    agent = result.scalar_one()
    agent.public_key = new_public_key

    # Invalidate recovery token (clear it so it can't be reused)
    verification.registration_token = None
    verification.registration_token_expires_at = None

    await db.commit()

    # Send confirmation email
    try:
        body = (
            f"The public key for your agent has been successfully rotated.\n\n"
            f"Agent: {agent.display_name}\n"
            f"Agent ID: {agent.agent_id}\n\n"
            f"If you did not make this change, please contact support immediately."
        )
        sender = get_email_sender()
        await sender.send(
            to=account.email,
            subject="Agent Registry — Your key has been rotated",
            body=body,
            from_name="Arcoa Support",
            html=make_html_email(body),
        )
    except Exception:
        logger.exception("Failed to send key rotation confirmation email to %s", account.email)

    logger.info("Key rotated for agent %s (account %s)", agent.agent_id, account.email)
