"""Email sending service.

Supports two backends:
- SMTP via aiosmtplib (production)
- Log-only (development / testing) — logs the email instead of sending

Set EMAIL_BACKEND=smtp and configure SMTP_* settings for production.
Default is EMAIL_BACKEND=log which just logs the verification link.
"""

import logging
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class LogEmailSender:
    """Development sender — logs email content instead of sending."""

    async def send(self, to: str, subject: str, body: str) -> None:
        logger.info("EMAIL to=%s subject=%s\n%s", to, subject, body)


class SmtpEmailSender:
    """Production sender — sends via SMTP."""

    async def send(self, to: str, subject: str, body: str) -> None:
        import aiosmtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = settings.smtp_from_address
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_use_tls,
        )


def get_email_sender() -> EmailSender:
    if settings.email_backend == "smtp":
        return SmtpEmailSender()
    return LogEmailSender()
