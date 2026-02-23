"""Tests for job lifecycle and negotiation protocol."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    """Register an agent, return (agent_id, private_key)."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _propose_job(
    client: AsyncClient,
    client_id: str,
    client_priv: str,
    seller_id: str,
    budget: str = "100.00",
) -> dict:
    """Helper to propose a job."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": {"version": "1.0", "tests": []},
        "requirements": {"input": "pdf", "volume": 100},
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_propose_job(client: AsyncClient) -> None:
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    assert job["status"] == "proposed"
    assert job["client_agent_id"] == client_id
    assert job["seller_agent_id"] == seller_id
    assert job["agreed_price"] == "100.00"


@pytest.mark.asyncio
async def test_propose_job_to_self(client: AsyncClient) -> None:
    agent_id, priv = await _create_agent(client)
    data = {
        "seller_agent_id": agent_id,
        "max_budget": "50.00",
    }
    body = data
    headers = make_auth_headers(agent_id, priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_propose_job_no_auth(client: AsyncClient) -> None:
    _, _ = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    resp = await client.post("/jobs", json={"seller_agent_id": seller_id, "max_budget": "50.00"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_counter_proposal(client: AsyncClient) -> None:
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Seller counters
    counter = {"proposed_price": "120.00", "message": "Need more for this volume"}
    body = counter
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", body)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "negotiating"
    assert resp.json()["current_round"] == 1
    assert resp.json()["agreed_price"] == "120.00"


@pytest.mark.asyncio
async def test_accept_after_counter(client: AsyncClient) -> None:
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Seller counters
    counter = {"proposed_price": "110.00"}
    body = counter
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", body)
    await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)

    # Client accepts
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"


@pytest.mark.asyncio
async def test_accept_directly(client: AsyncClient) -> None:
    """Seller can accept the initial proposal without countering."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Seller accepts directly
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"


@pytest.mark.asyncio
async def test_max_rounds_exceeded(client: AsyncClient) -> None:
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    # Propose with max 2 rounds
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "max_rounds": 2,
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    # Round 1
    counter = {"proposed_price": "110.00"}
    body = counter
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", body)
    await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)

    # Round 2
    counter = {"proposed_price": "105.00"}
    body = counter
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/counter", body)
    await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)

    # Round 3 — should fail (max 2 rounds)
    counter = {"proposed_price": "107.00"}
    body = counter
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", body)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_invalid_state_transition(client: AsyncClient) -> None:
    """Can't start a job that isn't funded."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Try to start without accepting/funding
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_third_party_cannot_act(client: AsyncClient) -> None:
    """A third agent can't interact with someone else's job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    intruder_id, intruder_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(intruder_id, intruder_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_deliver_and_fail_flow(client: AsyncClient) -> None:
    """Full flow: propose → accept → fund(skip for now) → start → deliver → fail → dispute."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Note: fund endpoint is Day 4 (escrow). For now test the rest of the flow.
    # We'll manually transition to funded by going agreed → funded isn't possible without escrow yet.
    # Test deliver from in_progress state — we need to add a way to get to funded.
    # For now, test that agreed → start fails (needs funded first)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 409  # agreed → in_progress not valid, needs funded


@pytest.mark.asyncio
async def test_get_job(client: AsyncClient) -> None:
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    resp = await client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id


@pytest.mark.asyncio
async def test_negotiation_log_appends(client: AsyncClient) -> None:
    """Verify negotiation log is append-only and preserves history."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]
    assert len(job["negotiation_log"]) == 1  # initial proposal

    # Counter
    counter = {"proposed_price": "110.00", "message": "Higher please"}
    body = counter
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", body)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert len(resp.json()["negotiation_log"]) == 2

    # Accept
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert len(resp.json()["negotiation_log"]) == 3
    assert resp.json()["negotiation_log"][-1]["action"] == "accepted"
