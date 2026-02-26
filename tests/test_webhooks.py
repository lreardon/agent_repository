"""Tests for webhook/push notification service."""

import hashlib
import hmac
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.webhooks import (
    build_a2a_push_notification,
    enqueue_webhook,
    notify_job_event,
    sign_webhook_payload,
    _EVENT_STATE_MAP,
)


def test_sign_webhook_payload() -> None:
    """WH1: HMAC-SHA256 signature is correct."""
    secret = "test-secret"
    timestamp = "2026-01-01T00:00:00Z"
    body = '{"hello":"world"}'
    sig = sign_webhook_payload(secret, timestamp, body)
    expected = hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()
    assert sig == expected


def test_build_a2a_push_notification_structure() -> None:
    """WH2: JSON-RPC 2.0 structure is correct."""
    payload = build_a2a_push_notification(
        task_id="task-1",
        context_id="ctx-1",
        state="working",
        event_type="job.started",
        details={"job_id": "abc"},
    )
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tasks/pushNotification"
    params = payload["params"]
    assert params["taskId"] == "task-1"
    assert params["contextId"] == "ctx-1"
    assert params["status"]["state"] == "working"
    parts = params["status"]["message"]["parts"]
    assert len(parts) == 1
    assert parts[0]["kind"] == "data"
    assert parts[0]["data"]["event"] == "job.started"
    assert parts[0]["data"]["job_id"] == "abc"


def test_event_state_mapping_coverage() -> None:
    """WH6: All event types have state mappings."""
    expected_events = {
        "job.proposed", "job.counter_received", "job.accepted", "job.funded",
        "job.started", "job.delivered", "job.completed", "job.failed",
        "job.disputed", "job.resolved", "job.deadline_warning",
    }
    assert expected_events == set(_EVENT_STATE_MAP.keys())


@pytest.mark.asyncio
async def test_enqueue_webhook_creates_record(db_session: AsyncSession) -> None:
    """WH3: enqueue_webhook creates a PENDING delivery record."""
    from app.models.webhook import WebhookDelivery, WebhookStatus
    from sqlalchemy import select

    # Need an agent for FK — create one directly
    from app.models.agent import Agent
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key="a" * 64,
        display_name="Test",
        endpoint_url="https://example.com",
        webhook_secret="secret",
    )
    db_session.add(agent)
    await db_session.commit()

    delivery = await enqueue_webhook(
        db_session, agent.agent_id, "job.proposed", {"test": True}
    )
    assert delivery.status == WebhookStatus.PENDING
    assert delivery.event_type == "job.proposed"
    assert delivery.target_agent_id == agent.agent_id

    # Verify in DB
    result = await db_session.execute(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery.delivery_id)
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_notify_job_event_creates_two_deliveries(db_session: AsyncSession) -> None:
    """WH4: notify_job_event creates deliveries for both parties."""
    from app.models.agent import Agent
    from app.models.job import Job, JobStatus

    client_agent = Agent(
        agent_id=uuid.uuid4(), public_key="b" * 64,
        display_name="Client", endpoint_url="https://c.example.com",
        webhook_secret="s1",
    )
    seller_agent = Agent(
        agent_id=uuid.uuid4(), public_key="c" * 64,
        display_name="Seller", endpoint_url="https://s.example.com",
        webhook_secret="s2",
    )
    db_session.add_all([client_agent, seller_agent])
    await db_session.flush()

    job = Job(
        job_id=uuid.uuid4(),
        client_agent_id=client_agent.agent_id,
        seller_agent_id=seller_agent.agent_id,
        status=JobStatus.PROPOSED,
        agreed_price=None,
    )
    db_session.add(job)
    await db_session.commit()

    deliveries = await notify_job_event(
        db_session, job.job_id, "job.proposed", {"price": "10.00"}
    )
    assert len(deliveries) == 2
    target_ids = {d.target_agent_id for d in deliveries}
    assert client_agent.agent_id in target_ids
    assert seller_agent.agent_id in target_ids


@pytest.mark.asyncio
async def test_notify_job_event_nonexistent_job(db_session: AsyncSession) -> None:
    """WH5: nonexistent job returns empty list."""
    deliveries = await notify_job_event(db_session, uuid.uuid4(), "job.proposed", {})
    assert deliveries == []


def test_sign_webhook_payload_deterministic() -> None:
    """Same inputs always produce same signature."""
    sig1 = sign_webhook_payload("secret", "ts1", "body1")
    sig2 = sign_webhook_payload("secret", "ts1", "body1")
    assert sig1 == sig2


def test_sign_webhook_payload_different_secrets() -> None:
    """Different secrets produce different signatures."""
    sig1 = sign_webhook_payload("secret-a", "ts1", "body1")
    sig2 = sign_webhook_payload("secret-b", "ts1", "body1")
    assert sig1 != sig2


def test_build_notification_with_none_context_id() -> None:
    """Push notification works with no context_id."""
    payload = build_a2a_push_notification(
        task_id="t1", context_id=None, state="completed",
        event_type="job.completed", details={},
    )
    assert payload["params"]["contextId"] is None


def test_event_state_map_working_states() -> None:
    """Verify working states mapped correctly."""
    assert _EVENT_STATE_MAP["job.started"] == "working"
    assert _EVENT_STATE_MAP["job.delivered"] == "working"
    assert _EVENT_STATE_MAP["job.completed"] == "completed"
    assert _EVENT_STATE_MAP["job.failed"] == "failed"
