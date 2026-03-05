"""Tests for webhook/push notification service and redelivery endpoints."""

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.services.webhooks import (
    build_a2a_push_notification,
    enqueue_webhook,
    notify_job_event,
    sign_webhook_payload,
    _EVENT_STATE_MAP,
)
from tests.conftest import make_agent_data, make_auth_headers
from app.utils.crypto import generate_keypair


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


# ---------------------------------------------------------------------------
# Helper: create agent directly in DB and return (agent_id_str, private_key)
# ---------------------------------------------------------------------------

async def _create_agent(db_session: AsyncSession) -> tuple[str, str]:
    """Create an agent directly in DB, returning (agent_id_str, private_key_hex)."""
    private_key, public_key = generate_keypair()
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key=public_key,
        display_name="Test Agent",
        endpoint_url="https://example.com/webhook",
        webhook_secret="s" * 64,
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return str(agent.agent_id), private_key


async def _create_delivery(
    db_session: AsyncSession,
    agent_id: uuid.UUID,
    event_type: str = "job.proposed",
    status: WebhookStatus = WebhookStatus.PENDING,
) -> WebhookDelivery:
    """Insert a WebhookDelivery directly into the DB."""
    delivery = WebhookDelivery(
        delivery_id=uuid.uuid4(),
        target_agent_id=agent_id,
        event_type=event_type,
        payload={"test": True},
        status=status,
        attempts=3 if status == WebhookStatus.FAILED else 0,
        last_error="timeout" if status == WebhookStatus.FAILED else None,
    )
    db_session.add(delivery)
    await db_session.commit()
    await db_session.refresh(delivery)
    return delivery


# ---------------------------------------------------------------------------
# Tests: GET /agents/{agent_id}/webhooks — list deliveries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_webhook_deliveries_empty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Listing webhooks for an agent with no deliveries returns empty list."""
    agent_id, private_key = await _create_agent(db_session)
    path = f"/agents/{agent_id}/webhooks"
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_webhook_deliveries_with_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Listing webhooks returns delivery records for the agent."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    # Create deliveries with different statuses
    await _create_delivery(db_session, agent_uuid, "job.proposed", WebhookStatus.PENDING)
    await _create_delivery(db_session, agent_uuid, "job.started", WebhookStatus.DELIVERED)
    await _create_delivery(db_session, agent_uuid, "job.failed", WebhookStatus.FAILED)

    path = f"/agents/{agent_id}/webhooks"
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 3

    # Verify fields are present
    for item in items:
        assert "delivery_id" in item
        assert "event_type" in item
        assert "status" in item
        assert "attempts" in item
        assert "created_at" in item
        assert "last_error" in item


@pytest.mark.asyncio
async def test_list_webhook_deliveries_filtered_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Filtering by status returns only matching deliveries."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    await _create_delivery(db_session, agent_uuid, "job.proposed", WebhookStatus.PENDING)
    await _create_delivery(db_session, agent_uuid, "job.started", WebhookStatus.DELIVERED)
    await _create_delivery(db_session, agent_uuid, "job.failed", WebhookStatus.FAILED)

    # Filter by failed
    path = f"/agents/{agent_id}/webhooks"
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers, params={"status": "failed"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["status"] == "failed"

    # Filter by pending
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers, params={"status": "pending"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_list_webhook_deliveries_invalid_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Invalid status filter returns 400."""
    agent_id, private_key = await _create_agent(db_session)
    path = f"/agents/{agent_id}/webhooks"
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers, params={"status": "bogus"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_webhook_deliveries_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Limit and offset work correctly."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    for i in range(5):
        await _create_delivery(db_session, agent_uuid, f"job.event{i}", WebhookStatus.PENDING)

    path = f"/agents/{agent_id}/webhooks"
    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers, params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    headers = make_auth_headers(agent_id, private_key, "GET", path)
    resp = await client.get(path, headers=headers, params={"limit": 2, "offset": 4})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# Tests: POST /agents/{agent_id}/webhooks/{delivery_id}/redeliver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redeliver_failed_webhook(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Redelivering a FAILED webhook resets it to PENDING."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    delivery = await _create_delivery(db_session, agent_uuid, "job.proposed", WebhookStatus.FAILED)

    path = f"/agents/{agent_id}/webhooks/{delivery.delivery_id}/redeliver"
    headers = make_auth_headers(agent_id, private_key, "POST", path)
    resp = await client.post(path, headers=headers)
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "pending"
    # WHK-4: Verify redeliver fully resets delivery state
    assert body["attempts"] == 0
    assert body["last_error"] is None
    assert body["delivery_id"] == str(delivery.delivery_id)


@pytest.mark.asyncio
async def test_redeliver_delivered_webhook(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Redelivering a DELIVERED webhook also resets to PENDING."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    delivery = await _create_delivery(db_session, agent_uuid, "job.started", WebhookStatus.DELIVERED)

    path = f"/agents/{agent_id}/webhooks/{delivery.delivery_id}/redeliver"
    headers = make_auth_headers(agent_id, private_key, "POST", path)
    resp = await client.post(path, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    # WHK-4: Verify redeliver fully resets delivery state
    assert body["attempts"] == 0
    assert body["last_error"] is None


@pytest.mark.asyncio
async def test_redeliver_already_pending_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Redelivering an already PENDING webhook returns 409 Conflict."""
    agent_id, private_key = await _create_agent(db_session)
    agent_uuid = uuid.UUID(agent_id)

    delivery = await _create_delivery(db_session, agent_uuid, "job.proposed", WebhookStatus.PENDING)

    path = f"/agents/{agent_id}/webhooks/{delivery.delivery_id}/redeliver"
    headers = make_auth_headers(agent_id, private_key, "POST", path)
    resp = await client.post(path, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_redeliver_wrong_agent_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Redelivering a delivery that belongs to another agent returns 404."""
    agent_id_a, pk_a = await _create_agent(db_session)
    agent_id_b, pk_b = await _create_agent(db_session)

    # Create delivery for agent A
    delivery = await _create_delivery(
        db_session, uuid.UUID(agent_id_a), "job.proposed", WebhookStatus.FAILED
    )

    # Agent B tries to redeliver agent A's delivery
    path = f"/agents/{agent_id_b}/webhooks/{delivery.delivery_id}/redeliver"
    headers = make_auth_headers(agent_id_b, pk_b, "POST", path)
    resp = await client.post(path, headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_redeliver_nonexistent_delivery_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Redelivering a nonexistent delivery_id returns 404."""
    agent_id, private_key = await _create_agent(db_session)
    fake_delivery_id = uuid.uuid4()

    path = f"/agents/{agent_id}/webhooks/{fake_delivery_id}/redeliver"
    headers = make_auth_headers(agent_id, private_key, "POST", path)
    resp = await client.post(path, headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Signature generation and verification
# ---------------------------------------------------------------------------


def test_sign_webhook_payload_verification_roundtrip() -> None:
    """Signature can be verified by recomputing with the same inputs."""
    secret = "my-agent-webhook-secret"
    timestamp = "2026-03-01T12:00:00+00:00"
    body = '{"jsonrpc":"2.0","method":"tasks/pushNotification","params":{}}'

    signature = sign_webhook_payload(secret, timestamp, body)

    # Verify by recomputing
    message = f"{timestamp}.{body}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


def test_sign_webhook_payload_tampered_body() -> None:
    """Changing the body invalidates the signature."""
    secret = "secret"
    timestamp = "2026-01-01T00:00:00Z"
    body = '{"event":"job.proposed"}'
    sig = sign_webhook_payload(secret, timestamp, body)

    # Tamper with the body
    tampered_body = '{"event":"job.completed"}'
    tampered_message = f"{timestamp}.{tampered_body}"
    tampered_sig = hmac.new(secret.encode(), tampered_message.encode(), hashlib.sha256).hexdigest()
    assert sig != tampered_sig


@pytest.mark.asyncio
async def test_list_webhooks_wrong_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """M20/WHK-1: Listing webhooks for another agent returns 403."""
    agent_id_a, pk_a = await _create_agent(db_session)
    agent_id_b, pk_b = await _create_agent(db_session)

    # Agent B tries to list agent A's webhooks
    path = f"/agents/{agent_id_a}/webhooks"
    headers = make_auth_headers(agent_id_b, pk_b, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 403


def test_sign_webhook_payload_tampered_timestamp() -> None:
    """Changing the timestamp invalidates the signature."""
    secret = "secret"
    body = '{"data":true}'
    sig1 = sign_webhook_payload(secret, "2026-01-01T00:00:00Z", body)
    sig2 = sign_webhook_payload(secret, "2026-01-01T00:00:01Z", body)
    assert sig1 != sig2
