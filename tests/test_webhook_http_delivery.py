"""Tests for HTTP webhook delivery worker."""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.services.webhook_delivery import deliver_pending_http_webhooks, MAX_ATTEMPTS
from app.utils.crypto import generate_keypair


async def _make_agent(
    db: AsyncSession,
    endpoint_url: str | None = "https://example.com/webhook",
) -> Agent:
    """Create an agent directly in DB."""
    _, pub = generate_keypair()
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=pub,
        display_name="Test Agent",
        endpoint_url=endpoint_url,
        webhook_secret="s" * 64,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def _make_delivery(
    db: AsyncSession,
    agent_id: uuid.UUID,
    event_type: str = "job.proposed",
    status: WebhookStatus = WebhookStatus.PENDING,
    attempts: int = 0,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        delivery_id=uuid.uuid4(),
        target_agent_id=agent_id,
        event_type=event_type,
        payload={"test": True},
        status=status,
        attempts=attempts,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


@pytest.mark.asyncio
async def test_successful_http_delivery(db_session: AsyncSession) -> None:
    """Successful HTTP POST marks delivery as DELIVERED."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id)

    mock_response = httpx.Response(200, request=httpx.Request("POST", agent.endpoint_url))

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        count = await deliver_pending_http_webhooks(db_session)

    assert count == 1
    await db_session.refresh(delivery)
    assert delivery.status == WebhookStatus.DELIVERED
    assert delivery.attempts == 1


@pytest.mark.asyncio
async def test_failed_http_delivery_increments_attempts(db_session: AsyncSession) -> None:
    """Failed HTTP POST increments attempts and sets last_error."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id)

    mock_response = httpx.Response(500, text="Internal Server Error",
                                   request=httpx.Request("POST", agent.endpoint_url))

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        await deliver_pending_http_webhooks(db_session)

    await db_session.refresh(delivery)
    assert delivery.status == WebhookStatus.PENDING  # Not yet at max attempts
    assert delivery.attempts == 1
    assert "500" in delivery.last_error


@pytest.mark.asyncio
async def test_max_retries_marks_failed(db_session: AsyncSession) -> None:
    """After MAX_ATTEMPTS failures, delivery is marked FAILED."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id, attempts=MAX_ATTEMPTS - 1)

    mock_response = httpx.Response(503, text="Service Unavailable",
                                   request=httpx.Request("POST", agent.endpoint_url))

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        await deliver_pending_http_webhooks(db_session)

    await db_session.refresh(delivery)
    assert delivery.status == WebhookStatus.FAILED
    assert delivery.attempts == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_exception_increments_attempts(db_session: AsyncSession) -> None:
    """Network exceptions increment attempts and record error."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id)

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        await deliver_pending_http_webhooks(db_session)

    await db_session.refresh(delivery)
    assert delivery.attempts == 1
    assert "Connection refused" in delivery.last_error


@pytest.mark.asyncio
async def test_agents_without_endpoint_url_skipped(db_session: AsyncSession) -> None:
    """Agents without endpoint_url are not processed."""
    agent = await _make_agent(db_session, endpoint_url=None)
    delivery = await _make_delivery(db_session, agent.agent_id)

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        count = await deliver_pending_http_webhooks(db_session)

    assert count == 0
    await db_session.refresh(delivery)
    assert delivery.status == WebhookStatus.PENDING


@pytest.mark.asyncio
async def test_ws_connected_agents_skipped(db_session: AsyncSession) -> None:
    """Agents connected via WebSocket are skipped."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id)

    with patch("app.services.webhook_delivery.manager") as mock_manager, \
         patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_manager.is_connected.return_value = True
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        count = await deliver_pending_http_webhooks(db_session)

    # Was found but skipped — still counts as 0 processed since no HTTP call made
    await db_session.refresh(delivery)
    assert delivery.status == WebhookStatus.PENDING
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_no_pending_deliveries_is_noop(db_session: AsyncSession) -> None:
    """No pending deliveries returns 0."""
    count = await deliver_pending_http_webhooks(db_session)
    assert count == 0


@pytest.mark.asyncio
async def test_delivery_sends_correct_headers(db_session: AsyncSession) -> None:
    """HTTP delivery includes correct Content-Type and signature headers."""
    agent = await _make_agent(db_session)
    delivery = await _make_delivery(db_session, agent.agent_id)

    mock_response = httpx.Response(200, request=httpx.Request("POST", agent.endpoint_url))

    with patch("app.services.webhook_delivery.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        await deliver_pending_http_webhooks(db_session)

    call_args = mock_client.post.call_args
    headers = call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"
    assert "X-Webhook-Timestamp" in headers
    assert "X-Webhook-Signature" in headers
