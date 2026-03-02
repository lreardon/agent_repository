"""Email sending service.

Supports four backends:
- Resend via REST API (production / staging) — cheap, simple
- SendGrid via REST API (alternative production)
- SMTP via aiosmtplib (alternative production)
- Log-only (development / testing) — logs the email instead of sending

Set EMAIL_BACKEND=resend and RESEND_API_KEY for production.
Set EMAIL_BACKEND=sendgrid and SENDGRID_API_KEY for SendGrid.
Set EMAIL_BACKEND=smtp and configure SMTP_* settings for SMTP.
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


class ResendEmailSender:
    """Production sender — sends via Resend REST API using httpx."""

    async def send(self, to: str, subject: str, body: str) -> None:
        import httpx

        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is not configured")

        payload = {
            "from": settings.resend_from_address,
            "to": [to],
            "subject": subject,
            "text": body,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.error(
                    "Resend API error: status=%d body=%s",
                    resp.status_code,
                    resp.text,
                )
                raise RuntimeError(f"Resend API returned {resp.status_code}: {resp.text}")

        logger.info("Email sent via Resend to=%s subject=%s", to, subject)


class SendGridEmailSender:
    """Production sender — sends via SendGrid v3 REST API using httpx."""

    async def send(self, to: str, subject: str, body: str) -> None:
        import httpx

        if not settings.sendgrid_api_key:
            raise RuntimeError("SENDGRID_API_KEY is not configured")

        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": settings.sendgrid_from_address},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code not in (200, 202):
                logger.error(
                    "SendGrid API error: status=%d body=%s",
                    resp.status_code,
                    resp.text,
                )
                raise RuntimeError(f"SendGrid API returned {resp.status_code}: {resp.text}")

        logger.info("Email sent via SendGrid to=%s subject=%s", to, subject)


class SmtpEmailSender:
    """Alternative production sender — sends via SMTP."""

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
    if settings.email_backend == "resend":
        return ResendEmailSender()
    if settings.email_backend == "sendgrid":
        return SendGridEmailSender()
    if settings.email_backend == "smtp":
        return SmtpEmailSender()
    return LogEmailSender()
