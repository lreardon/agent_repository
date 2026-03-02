"""Tests for SendGrid email sender."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.config import settings
from app.services.email import SendGridEmailSender, get_email_sender


@pytest.fixture(autouse=True)
def _sendgrid_settings():
    """Enable sendgrid backend for these tests."""
    object.__setattr__(settings, "email_backend", "sendgrid")
    object.__setattr__(settings, "sendgrid_api_key", "SG.test-key-xxx")
    object.__setattr__(settings, "sendgrid_from_address", "noreply@arcoa.ai")
    yield


def test_get_email_sender_returns_sendgrid():
    sender = get_email_sender()
    assert isinstance(sender, SendGridEmailSender)


@pytest.mark.asyncio
async def test_sendgrid_send_success():
    sender = SendGridEmailSender()

    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = ""

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await sender.send(
            to="test@example.com",
            subject="Test Subject",
            body="Test body",
        )

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == "https://api.sendgrid.com/v3/mail/send"
    payload = call_kwargs[1]["json"]
    assert payload["personalizations"][0]["to"][0]["email"] == "test@example.com"
    assert payload["subject"] == "Test Subject"
    assert payload["from"]["email"] == "noreply@arcoa.ai"
    assert payload["content"][0]["value"] == "Test body"
    assert "Bearer SG.test-key-xxx" in call_kwargs[1]["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_sendgrid_send_api_error():
    sender = SendGridEmailSender()

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="SendGrid API returned 403"):
            await sender.send(to="test@example.com", subject="Test", body="Body")


@pytest.mark.asyncio
async def test_sendgrid_missing_api_key():
    object.__setattr__(settings, "sendgrid_api_key", "")
    sender = SendGridEmailSender()

    with pytest.raises(RuntimeError, match="SENDGRID_API_KEY is not configured"):
        await sender.send(to="test@example.com", subject="Test", body="Body")


def test_get_email_sender_log_default():
    object.__setattr__(settings, "email_backend", "log")
    from app.services.email import LogEmailSender
    sender = get_email_sender()
    assert isinstance(sender, LogEmailSender)


def test_get_email_sender_smtp():
    object.__setattr__(settings, "email_backend", "smtp")
    from app.services.email import SmtpEmailSender
    sender = get_email_sender()
    assert isinstance(sender, SmtpEmailSender)
