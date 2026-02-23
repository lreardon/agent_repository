"""Tests for escrow: fund, release, refund, double-spend prevention, insufficient balance."""

import json
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    data = {"amount": amount}
    body = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", body)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200


async def _propose_and_accept(
    client: AsyncClient,
    client_id: str, client_priv: str,
    seller_id: str, seller_priv: str,
    budget: str = "100.00",
) -> str:
    """Propose a job and have seller accept. Returns job_id."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": {"version": "1.0", "tests": []},
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Seller accepts
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"
    return job_id


@pytest.mark.asyncio
async def test_fund_job(client: AsyncClient) -> None:
    """Happy path: fund escrow for an agreed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "funded"
    assert resp.json()["amount"] == "100.00"

    # Check client balance decreased
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "400.00"

    # Check job status is funded
    resp = await client.get(f"/jobs/{job_id}")
    assert resp.json()["status"] == "funded"


@pytest.mark.asyncio
async def test_fund_insufficient_balance(client: AsyncClient) -> None:
    """Funding with insufficient balance fails."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "50.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 422
    assert "Insufficient balance" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_double_fund_prevention(client: AsyncClient) -> None:
    """Can't fund the same job twice."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Try to fund again
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fund_release_flow(client: AsyncClient) -> None:
    """Full flow: fund → start → deliver → complete (releases escrow to seller)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.json()["status"] == "in_progress"

    # Deliver
    deliver_data = {"result": {"data": [1, 2, 3]}}
    body = deliver_data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    assert resp.json()["status"] == "delivered"

    # Complete (release escrow) — for now any party can trigger
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.json()["status"] == "completed"

    # Seller should have balance = 100 - 2.5% fee = 97.50
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "97.50"

    # Client balance should still be 400.00
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "400.00"


@pytest.mark.asyncio
async def test_fund_refund_flow(client: AsyncClient) -> None:
    """Fund → start → deliver → fail → refund."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"data": "bad output"}}
    body = deliver_data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Fail (triggers refund)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.json()["status"] == "failed"

    # Client should be refunded — balance back to 500
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "500.00"


@pytest.mark.asyncio
async def test_seller_cannot_fund(client: AsyncClient) -> None:
    """Only the client can fund escrow."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 403
