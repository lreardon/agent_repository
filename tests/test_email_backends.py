"""Tests for email sender backends (Resend, SendGrid, SMTP, Log)."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.config import settings
from app.services.email import (
    ResendEmailSender,
    SendGridEmailSender,
    get_email_sender,
)


# ---- Resend tests ----

@pytest.fixture
def resend_settings():
    object.__setattr__(settings, "email_backend", "resend")
    object.__setattr__(settings, "resend_api_key", "re_test_xxx")
    object.__setattr__(settings, "resend_from_address", "noreply@arcoa.ai")
    yield


def test_get_email_sender_returns_resend(resend_settings):
    sender = get_email_sender()
    assert isinstance(sender, ResendEmailSender)


@pytest.mark.asyncio
async def test_resend_send_success(resend_settings):
    sender = ResendEmailSender()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"id":"msg_xxx"}'

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        await sender.send(to="test@example.com", subject="Test Subject", body="Test body")

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == "https://api.resend.com/emails"
    payload = call_kwargs[1]["json"]
    assert payload["to"] == ["test@example.com"]
    assert payload["subject"] == "Test Subject"
    assert payload["from"] == "noreply@arcoa.ai"
    assert payload["text"] == "Test body"
    assert "Bearer re_test_xxx" in call_kwargs[1]["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_resend_send_api_error(resend_settings):
    sender = ResendEmailSender()

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.text = "Validation error"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Resend API returned 422"):
            await sender.send(to="test@example.com", subject="Test", body="Body")


@pytest.mark.asyncio
async def test_resend_missing_api_key(resend_settings):
    object.__setattr__(settings, "resend_api_key", "")
    sender = ResendEmailSender()

    with pytest.raises(RuntimeError, match="RESEND_API_KEY is not configured"):
        await sender.send(to="test@example.com", subject="Test", body="Body")


# ---- SendGrid tests ----

@pytest.fixture
def sendgrid_settings():
    object.__setattr__(settings, "email_backend", "sendgrid")
    object.__setattr__(settings, "sendgrid_api_key", "SG.test-key-xxx")
    object.__setattr__(settings, "sendgrid_from_address", "noreply@arcoa.ai")
    yield


def test_get_email_sender_returns_sendgrid(sendgrid_settings):
    sender = get_email_sender()
    assert isinstance(sender, SendGridEmailSender)


# ---- Routing tests ----

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
