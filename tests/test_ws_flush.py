"""Tests for WebSocket pending webhook flush on reconnect."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.routers.ws import _flush_pending_webhooks
from app.utils.crypto import generate_keypair


class FakeWebSocket:
    """Minimal WebSocket mock for testing flush."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class FailingWebSocket:
    """WebSocket mock that fails on send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        raise ConnectionError("WS closed")


async def _make_agent(db: AsyncSession) -> uuid.UUID:
    """Create an agent directly in DB."""
    _, pub = generate_keypair()
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=pub,
        display_name="Test Agent",
        endpoint_url="https://example.com/webhook",
        webhook_secret="s" * 64,
    )
    db.add(agent)
    await db.commit()
    return agent.agent_id


async def _make_delivery(
    db: AsyncSession,
    agent_id: uuid.UUID,
    event_type: str = "job.proposed",
    status: WebhookStatus = WebhookStatus.PENDING,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        delivery_id=uuid.uuid4(),
        target_agent_id=agent_id,
        event_type=event_type,
        payload={"test": True, "event": event_type},
        status=status,
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    return delivery


@pytest.mark.asyncio
async def test_flush_sends_pending_webhooks(db_session: AsyncSession) -> None:
    """Pending webhooks are sent via WS on flush."""
    agent_id = await _make_agent(db_session)
    d1 = await _make_delivery(db_session, agent_id, "job.proposed")
    d2 = await _make_delivery(db_session, agent_id, "job.started")

    ws = FakeWebSocket()
    await _flush_pending_webhooks(ws, db_session, agent_id)

    assert len(ws.sent) == 2
    assert ws.sent[0]["type"] == "event"
    assert ws.sent[0]["event_type"] == "job.proposed"
    assert ws.sent[1]["event_type"] == "job.started"


@pytest.mark.asyncio
async def test_flush_marks_delivered(db_session: AsyncSession) -> None:
    """After flush, deliveries are marked DELIVERED."""
    agent_id = await _make_agent(db_session)
    d1 = await _make_delivery(db_session, agent_id, "job.proposed")

    ws = FakeWebSocket()
    await _flush_pending_webhooks(ws, db_session, agent_id)

    await db_session.refresh(d1)
    assert d1.status == WebhookStatus.DELIVERED


@pytest.mark.asyncio
async def test_flush_skips_already_delivered(db_session: AsyncSession) -> None:
    """Already DELIVERED webhooks are not re-sent."""
    agent_id = await _make_agent(db_session)
    await _make_delivery(db_session, agent_id, "job.proposed", WebhookStatus.DELIVERED)

    ws = FakeWebSocket()
    await _flush_pending_webhooks(ws, db_session, agent_id)

    assert len(ws.sent) == 0


@pytest.mark.asyncio
async def test_flush_skips_other_agents(db_session: AsyncSession) -> None:
    """Deliveries for other agents are not sent."""
    agent_a = await _make_agent(db_session)
    agent_b = await _make_agent(db_session)
    await _make_delivery(db_session, agent_b, "job.proposed")

    ws = FakeWebSocket()
    await _flush_pending_webhooks(ws, db_session, agent_a)

    assert len(ws.sent) == 0


@pytest.mark.asyncio
async def test_flush_stops_on_ws_failure(db_session: AsyncSession) -> None:
    """If WS send fails, flush stops early and uncommitted deliveries stay PENDING."""
    agent_id = await _make_agent(db_session)
    d1 = await _make_delivery(db_session, agent_id, "job.proposed")

    ws = FailingWebSocket()
    await _flush_pending_webhooks(ws, db_session, agent_id)

    await db_session.refresh(d1)
    # Delivery stays PENDING since send failed
    assert d1.status == WebhookStatus.PENDING
