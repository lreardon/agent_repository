"""Email sending service.

Supports four backends:
- Resend via REST API (production / staging) — cheap, simple
- SMTP via aiosmtplib (alternative production)
- Log-only (development / testing) — logs the email instead of sending

Set EMAIL_BACKEND=resend and RESEND_API_KEY for production.
Set EMAIL_BACKEND=smtp and configure SMTP_* settings for SMTP.
Default is EMAIL_BACKEND=log which just logs the verification link.
"""

import html as _html
import logging
import re
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)

_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="48" height="48">'
    '<circle cx="16" cy="16" r="14" fill="#1a1612" stroke="#c9a962" stroke-width="2"/>'
    '<path d="M9 16C9 16 12 12 16 12C20 12 23 16 23 16C23 16 20 20 16 20C12 20 9 16 9 16Z"'
    ' stroke="#c9a962" stroke-width="1.5" fill="none"/>'
    '<circle cx="16" cy="16" r="2" fill="#c9a962"/>'
    '<path d="M16 4V7" stroke="#c9a962" stroke-width="1" stroke-linecap="round"/>'
    '<path d="M16 25V28" stroke="#c9a962" stroke-width="1" stroke-linecap="round"/>'
    '<path d="M4 16H7" stroke="#c9a962" stroke-width="1" stroke-linecap="round"/>'
    '<path d="M25 16H28" stroke="#c9a962" stroke-width="1" stroke-linecap="round"/>'
    '</svg>'
)


def make_html_email(body: str) -> str:
    """Wrap plain-text email body in branded HTML with the Arcoa logo."""
    escaped = _html.escape(body)
    linked = re.sub(r'(https?://\S+)', r'<a href="\1" style="color:#c9a962">\1</a>', escaped)
    content = (
        '<p style="margin:0 0 16px 0">'
        + linked.replace('\n\n', '</p><p style="margin:0 0 16px 0">').replace('\n', '<br>')
        + '</p>'
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width"></head>'
        '<body style="margin:0;padding:20px;background:#f5f5f5;font-family:sans-serif">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden">'
        f'<div style="background:#1a1612;padding:24px;text-align:center">{_LOGO_SVG}</div>'
        f'<div style="padding:32px;color:#333;font-size:15px;line-height:1.6">{content}</div>'
        '</div></body></html>'
    )


class EmailSender(Protocol):
    async def send(
        self, to: str, subject: str, body: str,
        from_name: str = "", html: str | None = None,
    ) -> None: ...


class LogEmailSender:
    """Development sender — logs email content instead of sending."""

    async def send(
        self, to: str, subject: str, body: str,
        from_name: str = "", html: str | None = None,
    ) -> None:
        logger.info("EMAIL from=%s to=%s subject=%s\n%s", from_name or "noreply", to, subject, body)


class ResendEmailSender:
    """Production sender — sends via Resend REST API using httpx."""

    async def send(
        self, to: str, subject: str, body: str,
        from_name: str = "", html: str | None = None,
    ) -> None:
        import httpx

        if not settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY is not configured")

        from_field = (
            f"{from_name} <{settings.resend_from_address}>"
            if from_name
            else settings.resend_from_address
        )
        payload: dict = {
            "from": from_field,
            "to": [to],
            "subject": subject,
            "text": body,
        }
        if html is not None:
            payload["html"] = html

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


class SmtpEmailSender:
    """Alternative production sender — sends via SMTP."""

    async def send(
        self, to: str, subject: str, body: str,
        from_name: str = "", html: str | None = None,
    ) -> None:
        import aiosmtplib
        from email.message import EmailMessage

        from_addr = (
            f"{from_name} <{settings.smtp_from_address}>"
            if from_name
            else settings.smtp_from_address
        )
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype="html")

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
    if settings.email_backend == "smtp":
        return SmtpEmailSender()
    return LogEmailSender()
