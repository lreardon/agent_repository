"""Tests that job lifecycle endpoints fire webhook notifications."""

import base64
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.job import Job, JobStatus
from app.models.webhook import WebhookDelivery, WebhookStatus
from app.utils.crypto import generate_keypair, hash_criteria
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    """Register an agent, return (agent_id, private_key)."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _fund_agent(db: AsyncSession, agent_id: str, amount: Decimal) -> None:
    """Credit an agent's balance directly."""
    result = await db.execute(select(Agent).where(Agent.agent_id == uuid.UUID(agent_id)))
    agent = result.scalar_one()
    agent.balance += amount
    await db.commit()


_PASS_SCRIPT = base64.b64encode(b"import sys; sys.exit(0)").decode()
_DEFAULT_CRITERIA = {"script": _PASS_SCRIPT, "runtime": "python:3.13"}


async def _propose_job(
    client: AsyncClient, client_id: str, client_priv: str, seller_id: str,
    acceptance_criteria: dict | None = None,
) -> dict:
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "acceptance_criteria": acceptance_criteria,
        "requirements": {"input": "test"},
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    return resp.json()


async def _get_deliveries(db: AsyncSession, job_id: str) -> list[WebhookDelivery]:
    """Get all webhook deliveries related to a job."""
    result = await db.execute(
        select(WebhookDelivery).order_by(WebhookDelivery.created_at.asc())
    )
    all_deliveries = list(result.scalars().all())
    # Filter by job_id in payload
    return [d for d in all_deliveries if job_id in str(d.payload)]


@pytest.mark.asyncio
async def test_propose_creates_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """propose_job fires job.proposed notification."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    deliveries = await _get_deliveries(db_session, job["job_id"])

    assert len(deliveries) == 2
    assert all(d.event_type == "job.proposed" for d in deliveries)
    target_ids = {str(d.target_agent_id) for d in deliveries}
    assert client_id in target_ids
    assert seller_id in target_ids


@pytest.mark.asyncio
async def test_deliver_creates_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """deliver_job fires job.delivered notification."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _fund_agent(db_session, client_id, Decimal("500.00"))
    await _fund_agent(db_session, seller_id, Decimal("500.00"))

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"output": "done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    assert resp.status_code == 200

    deliveries = await _get_deliveries(db_session, job_id)
    event_types = [d.event_type for d in deliveries]
    assert "job.delivered" in event_types


@pytest.mark.asyncio
async def test_complete_creates_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """complete_job fires job.completed notification."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _fund_agent(db_session, client_id, Decimal("500.00"))
    await _fund_agent(db_session, seller_id, Decimal("500.00"))

    # No acceptance_criteria so we can use manual complete
    job = await _propose_job(client, client_id, client_priv, seller_id, acceptance_criteria=None)
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"output": "done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200

    deliveries = await _get_deliveries(db_session, job_id)
    event_types = [d.event_type for d in deliveries]
    assert "job.completed" in event_types


@pytest.mark.asyncio
async def test_start_creates_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """start_job fires job.started notification."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _fund_agent(db_session, client_id, Decimal("500.00"))
    await _fund_agent(db_session, seller_id, Decimal("500.00"))

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200

    deliveries = await _get_deliveries(db_session, job_id)
    event_types = [d.event_type for d in deliveries]
    assert "job.started" in event_types


@pytest.mark.asyncio
async def test_full_lifecycle_notifications(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Full flow: propose → accept → fund → start → deliver → complete fires notifications at each step."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _fund_agent(db_session, client_id, Decimal("500.00"))
    await _fund_agent(db_session, seller_id, Decimal("500.00"))

    # No acceptance_criteria for manual complete
    job = await _propose_job(client, client_id, client_priv, seller_id, acceptance_criteria=None)
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"output": "done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    deliveries = await _get_deliveries(db_session, job_id)
    event_types = [d.event_type for d in deliveries]

    assert "job.proposed" in event_types
    assert "job.accepted" in event_types
    assert "job.funded" in event_types
    assert "job.started" in event_types
    assert "job.delivered" in event_types
    assert "job.completed" in event_types
