"""Tests for job lifecycle and negotiation protocol."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair, hash_criteria
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    """Register an agent, return (agent_id, private_key)."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


_DEFAULT_CRITERIA = {"version": "1.0", "tests": []}


async def _propose_job(
    client: AsyncClient,
    client_id: str,
    client_priv: str,
    seller_id: str,
    budget: str = "100.00",
    acceptance_criteria: dict | None = _DEFAULT_CRITERIA,
) -> dict:
    """Helper to propose a job."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": acceptance_criteria,
        "requirements": {"input": "pdf", "volume": 100},
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    return resp.json()


async def _seller_accept(
    client: AsyncClient,
    seller_id: str,
    seller_priv: str,
    job_id: str,
    criteria: dict | None = _DEFAULT_CRITERIA,
) -> "httpx.Response":
    """Helper: seller accepts a job, providing the criteria hash."""
    accept_data = {}
    criteria_h = hash_criteria(criteria)
    if criteria_h:
        accept_data["acceptance_criteria_hash"] = criteria_h
    body = accept_data if accept_data else b""
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", body)
    return await client.post(f"/jobs/{job_id}/accept", json=accept_data if accept_data else None, headers=headers)


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

    # Seller accepts directly (must provide criteria hash)
    resp = await _seller_accept(client, seller_id, seller_priv, job_id)
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

    # Accept (seller provides criteria hash)
    await _seller_accept(client, seller_id, seller_priv, job_id)

    # Note: fund endpoint is Day 4 (escrow). For now test the rest of the flow.
    # We'll manually transition to funded by going agreed → funded isn't possible without escrow yet.
    # Test deliver from in_progress state — we need to add a way to get to funded.
    # For now, test that agreed → start fails (needs funded first)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 409  # agreed → in_progress not valid, needs funded


@pytest.mark.asyncio
async def test_get_job_as_client(client: AsyncClient) -> None:
    """Client can view their own job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(client_id, client_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id


@pytest.mark.asyncio
async def test_get_job_as_seller(client: AsyncClient) -> None:
    """Seller can view their own job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id


@pytest.mark.asyncio
async def test_get_job_unauthenticated(client: AsyncClient) -> None:
    """Unauthenticated request to GET /jobs/{id} returns 403."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    resp = await client.get(f"/jobs/{job_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_job_third_party_rejected(client: AsyncClient) -> None:
    """Third party cannot view someone else's job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    intruder_id, intruder_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(intruder_id, intruder_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.status_code == 403


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


# ---------------------------------------------------------------------------
# Helpers for funded job lifecycle
# ---------------------------------------------------------------------------


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    data = {"amount": amount}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200


async def _get_funded_job(
    client: AsyncClient,
) -> tuple[str, str, str, str, str]:
    """Create two agents, propose, accept, fund. Returns (job_id, client_id, client_priv, seller_id, seller_priv)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")

    job = await _propose_job(client, client_id, client_priv, seller_id, "100.00")
    job_id = job["job_id"]

    # Accept (seller provides criteria hash)
    await _seller_accept(client, seller_id, seller_priv, job_id)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    return job_id, client_id, client_priv, seller_id, seller_priv


# ---------------------------------------------------------------------------
# Job start/deliver/fail/dispute tests (J1-J11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_funded_job(client: AsyncClient) -> None:
    """J1: Seller starts a funded job."""
    job_id, _, _, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_non_seller_cannot_start(client: AsyncClient) -> None:
    """J2: Non-seller cannot start job (403)."""
    job_id, client_id, client_priv, _, _ = await _get_funded_job(client)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_start_unfunded_job(client: AsyncClient) -> None:
    """J3: Cannot start unfunded (agreed) job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    await _seller_accept(client, seller_id, seller_priv, job_id)

    # Try to start without funding
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_deliver_job(client: AsyncClient) -> None:
    """J4: Seller delivers result."""
    job_id, _, _, seller_id, seller_priv = await _get_funded_job(client)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    result = {"result": {"output": "done", "files": ["report.pdf"]}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"
    # Result is redacted in non-completed states to prevent free work extraction
    assert resp.json()["result"] is None


@pytest.mark.asyncio
async def test_non_seller_cannot_deliver(client: AsyncClient) -> None:
    """J5: Non-seller cannot deliver (403)."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"output": "hacked"}}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/deliver", result)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_deliver_if_not_in_progress(client: AsyncClient) -> None:
    """J6: Cannot deliver if not in_progress (409)."""
    job_id, _, _, seller_id, seller_priv = await _get_funded_job(client)

    # Try to deliver without starting
    result = {"result": {"output": "premature"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fail_job(client: AsyncClient) -> None:
    """J7: Mark in-progress job as failed."""
    job_id, _, _, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_fail_funded_job_auto_refunds(client: AsyncClient) -> None:
    """J8: Failing a funded job auto-refunds escrow to client."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    # Check client balance after funding (should be 500 - 100 = 400)
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "400.00"

    # Start then fail
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 200

    # Client balance should be restored to 500
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "500.00"


@pytest.mark.asyncio
async def test_dispute_failed_job(client: AsyncClient) -> None:
    """J9: Dispute a failed job."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    await client.post(f"/jobs/{job_id}/fail", headers=headers)

    # Client disputes
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/dispute", b"")
    resp = await client.post(f"/jobs/{job_id}/dispute", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "disputed"


@pytest.mark.asyncio
async def test_cannot_dispute_non_failed_job(client: AsyncClient) -> None:
    """J10: Cannot dispute a job that isn't failed."""
    job_id, client_id, client_priv, _, _ = await _get_funded_job(client)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/dispute", b"")
    resp = await client.post(f"/jobs/{job_id}/dispute", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_third_party_cannot_dispute(client: AsyncClient) -> None:
    """J11: Third party cannot dispute."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    await client.post(f"/jobs/{job_id}/fail", headers=headers)

    intruder_id, intruder_priv = await _create_agent(client)
    headers = make_auth_headers(intruder_id, intruder_priv, "POST", f"/jobs/{job_id}/dispute", b"")
    resp = await client.post(f"/jobs/{job_id}/dispute", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_job_not_found(client: AsyncClient) -> None:
    """J12: 404 for nonexistent job (with auth)."""
    agent_id, priv = await _create_agent(client)
    fake_job = "00000000-0000-0000-0000-000000000000"
    headers = make_auth_headers(agent_id, priv, "GET", f"/jobs/{fake_job}")
    resp = await client.get(f"/jobs/{fake_job}", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_propose_to_nonexistent_seller(client: AsyncClient) -> None:
    """J14: Proposing to a nonexistent seller returns 404."""
    client_id, client_priv = await _create_agent(client)
    data = {
        "seller_agent_id": "00000000-0000-0000-0000-000000000000",
        "max_budget": "50.00",
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_propose_to_deactivated_seller(client: AsyncClient) -> None:
    """J13: Proposing to a deactivated seller returns 404."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    # Deactivate seller
    headers = make_auth_headers(seller_id, seller_priv, "DELETE", f"/agents/{seller_id}")
    await client.delete(f"/agents/{seller_id}", headers=headers)

    data = {"seller_agent_id": seller_id, "max_budget": "50.00"}
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_counter_by_non_party(client: AsyncClient) -> None:
    """J16: Counter proposal by non-party returns 403."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    intruder_id, intruder_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    counter = {"proposed_price": "999.00"}
    headers = make_auth_headers(intruder_id, intruder_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_full_lifecycle_propose_to_complete(client: AsyncClient) -> None:
    """J15: Full lifecycle: propose → accept → fund → start → deliver → complete."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.json()["status"] == "in_progress"

    # Deliver
    result = {"result": {"output": "all done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)
    assert resp.json()["status"] == "delivered"

    # Complete (release escrow)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    # Result is only visible after completion
    assert resp.json()["result"]["output"] == "all done"

    # Verify seller got paid (minus 2.5% fee): 100 * 0.975 = 97.50
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "97.50"


@pytest.mark.asyncio
async def test_accept_by_non_party(client: AsyncClient) -> None:
    """J17: Non-party cannot accept a job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    intruder_id, intruder_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(intruder_id, intruder_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_counter_price_exceeds_million(client: AsyncClient) -> None:
    """J18: Counter with proposed_price > 1,000,000 rejected."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    counter = {"proposed_price": "1000001.00"}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_rounds_one_then_counter_twice(client: AsyncClient) -> None:
    """J19: max_rounds=1, first counter OK, second cancels job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    data = {"seller_agent_id": seller_id, "max_budget": "100.00", "max_rounds": 1}
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    # Round 1 — should succeed
    counter = {"proposed_price": "110.00"}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 200

    # Round 2 — exceeds max_rounds, job should be cancelled
    counter = {"proposed_price": "105.00"}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Acceptance criteria hash tests (ACH1-ACH7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposal_includes_criteria_hash(client: AsyncClient) -> None:
    """ACH1: Job proposal stores and returns acceptance_criteria_hash."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    expected_hash = hash_criteria(_DEFAULT_CRITERIA)
    assert job["acceptance_criteria_hash"] == expected_hash
    # Hash is also in the negotiation log
    assert job["negotiation_log"][0]["acceptance_criteria_hash"] == expected_hash


@pytest.mark.asyncio
async def test_proposal_no_criteria_no_hash(client: AsyncClient) -> None:
    """ACH2: Job without acceptance criteria has null hash."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    job = await _propose_job(
        client, client_id, client_priv, seller_id, acceptance_criteria=None
    )
    assert job["acceptance_criteria_hash"] is None


@pytest.mark.asyncio
async def test_seller_accept_requires_hash(client: AsyncClient) -> None:
    """ACH3: Seller cannot accept a job with criteria without providing the hash."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Seller tries to accept without hash
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 422
    assert "acceptance_criteria_hash" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_seller_accept_wrong_hash(client: AsyncClient) -> None:
    """ACH4: Seller providing wrong hash gets 409."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    accept_data = {"acceptance_criteria_hash": "deadbeef" * 8}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", accept_data)
    resp = await client.post(f"/jobs/{job_id}/accept", json=accept_data, headers=headers)
    assert resp.status_code == 409
    assert "mismatch" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_seller_accept_correct_hash(client: AsyncClient) -> None:
    """ACH5: Seller providing correct hash succeeds."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    resp = await _seller_accept(client, seller_id, seller_priv, job_id)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"
    # Hash recorded in accept log entry
    log = resp.json()["negotiation_log"]
    assert log[-1]["acceptance_criteria_hash"] == hash_criteria(_DEFAULT_CRITERIA)


@pytest.mark.asyncio
async def test_client_accept_no_hash_required(client: AsyncClient) -> None:
    """ACH6: Client (criteria author) can accept without providing hash."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Counter first so client can accept
    counter = {"proposed_price": "110.00"}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", counter)
    await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)

    # Client accepts without hash — should work
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"


@pytest.mark.asyncio
async def test_seller_accept_no_criteria_no_hash_needed(client: AsyncClient) -> None:
    """ACH7: Seller can accept without hash when there are no criteria."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    job = await _propose_job(
        client, client_id, client_priv, seller_id, acceptance_criteria=None
    )
    job_id = job["job_id"]

    # Seller accepts without hash — no criteria, so no hash needed
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"


# ---------------------------------------------------------------------------
# Result redaction tests (RR1-RR3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_redacted_in_delivered_state(client: AsyncClient) -> None:
    """RR1: Deliverable is not visible in delivered state (prevents free work extraction)."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"output": "valuable work product"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)

    # GET the job as client — result should be redacted
    headers = make_auth_headers(client_id, client_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"
    assert resp.json()["result"] is None


@pytest.mark.asyncio
async def test_result_redacted_in_failed_state(client: AsyncClient) -> None:
    """RR2: Deliverable is not visible after verification failure."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"output": "good work"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)

    # Fail the job
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fail", b"")
    await client.post(f"/jobs/{job_id}/fail", headers=headers)

    headers = make_auth_headers(client_id, client_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.json()["status"] == "failed"
    assert resp.json()["result"] is None


@pytest.mark.asyncio
async def test_result_visible_after_completion(client: AsyncClient) -> None:
    """RR3: Deliverable IS visible once job is completed and escrow released."""
    job_id, client_id, client_priv, seller_id, seller_priv = await _get_funded_job(client)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"output": "final deliverable"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    headers = make_auth_headers(client_id, client_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.json()["status"] == "completed"
    assert resp.json()["result"]["output"] == "final deliverable"
