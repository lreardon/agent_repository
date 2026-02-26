"""Account service: signup, email verification, registration token management."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.account import Account, EmailVerification
from app.services.email import get_email_sender

logger = logging.getLogger(__name__)

# Verification email link expires in 24 hours
VERIFICATION_EXPIRY = timedelta(hours=24)
# Registration token (issued after verification) expires in 1 hour
REGISTRATION_TOKEN_EXPIRY = timedelta(hours=1)


async def request_signup(db: AsyncSession, email: str) -> None:
    """Send a verification email. Creates account record if needed.

    Idempotent: re-sends verification if account exists but is unverified.
    If the account already has a linked agent, rejects with 409.
    """
    email = email.strip().lower()

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
    sender = get_email_sender()
    await sender.send(
        to=email,
        subject="Agent Registry — Verify your email",
        body=(
            f"Click the link below to verify your email and receive a registration token:\n\n"
            f"{verify_url}\n\n"
            f"This link expires in 24 hours.\n\n"
            f"If you did not request this, ignore this email."
        ),
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
