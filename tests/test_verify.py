"""Integration tests for the verification flow (deliver → verify → complete/fail)."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair, hash_criteria
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    """Credit agent balance via dev-only deposit endpoint."""
    data = {"amount": amount}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200, f"Dev deposit failed: {resp.status_code} {resp.text}"


async def _setup_funded_job(
    client: AsyncClient,
    client_id: str, client_priv: str,
    seller_id: str, seller_priv: str,
    budget: str = "100.00",
    criteria: dict | None = None,
) -> str:
    """Create, accept, fund a job. Returns job_id."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": criteria or {"version": "1.0", "tests": []},
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    # Accept (seller provides criteria hash)
    used_criteria = criteria or {"version": "1.0", "tests": []}
    accept_data = {"acceptance_criteria_hash": hash_criteria(used_criteria)}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", accept_data)
    await client.post(f"/jobs/{job_id}/accept", json=accept_data, headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    return job_id


@pytest.mark.asyncio
async def test_verify_passes_releases_escrow(client: AsyncClient) -> None:
    """Verify with passing tests → complete + escrow released."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    criteria = {
        "version": "1.0",
        "tests": [
            {"test_id": "has_data", "type": "count_gte", "params": {"path": "$.records", "min_count": 2}},
            {"test_id": "valid_names", "type": "assertion", "params": {"expression": "all(isinstance(r['name'], str) for r in output['records'])"}},
        ],
        "pass_threshold": "all",
    }

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria=criteria)

    # Deliver (seller pays storage fee)
    result = {"records": [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]}
    body = {"result": result}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json={"result": result}, headers=headers)

    # Verify (client pays verification fee)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["status"] == "completed"
    assert body["verification"]["passed"] is True
    assert len(body["verification"]["results"]) == 2
    assert all(r["passed"] for r in body["verification"]["results"])
    # Verify fee breakdown is returned
    assert "fee_charged" in body
    assert body["fee_charged"]["fee_type"] == "verification"

    # Seller: $10 - $0.01 storage + $99.50 escrow payout = $109.49
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "109.49"


@pytest.mark.asyncio
async def test_verify_fails_refunds_escrow(client: AsyncClient) -> None:
    """Verify with failing tests → failed + escrow refunded."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    criteria = {
        "version": "1.0",
        "tests": [
            {"test_id": "min_records", "type": "count_gte", "params": {"path": "$", "min_count": 100}},
        ],
        "pass_threshold": "all",
    }

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria=criteria)

    # Deliver insufficient data
    deliver_data = {"result": [1, 2, 3]}
    body = deliver_data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Verify — should fail
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["status"] == "failed"
    assert body["verification"]["passed"] is False

    # Client refunded — escrow returned, but verification fee is charged even on failure
    # $500 - $100 (funded) + $100 (refund) - $0.05 (verify fee) = $499.95
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "499.95"


@pytest.mark.asyncio
async def test_verify_no_criteria_auto_completes(client: AsyncClient) -> None:
    """No acceptance criteria → auto-complete."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv)

    # Deliver
    body = {"result": {"data": "anything"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json={"result": {"data": "anything"}}, headers=headers)

    # Verify — no tests, auto-complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["status"] == "completed"
    assert body["verification"] is None


@pytest.mark.asyncio
async def test_verify_majority_threshold(client: AsyncClient) -> None:
    """With majority threshold, 2/3 passing tests → complete."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    criteria = {
        "version": "1.0",
        "tests": [
            {"test_id": "t1", "type": "assertion", "params": {"expression": "len(output) > 0"}},
            {"test_id": "t2", "type": "assertion", "params": {"expression": "len(output) > 100"}},  # will fail
            {"test_id": "t3", "type": "assertion", "params": {"expression": "isinstance(output, list)"}},
        ],
        "pass_threshold": "majority",
    }

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria=criteria)

    body = {"result": [1, 2, 3]}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json={"result": [1, 2, 3]}, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["status"] == "completed"  # 2/3 pass with majority threshold
    assert body["verification"]["passed"] is True
    assert body["verification"]["summary"] == "2/3 passed"


@pytest.mark.asyncio
async def test_verify_rejects_non_client(client: AsyncClient) -> None:
    """Only the client (buyer) can trigger verification."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    outsider_id, outsider_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "200.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv)

    # Deliver
    deliver_data = {"result": {"output": "done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Seller tries to verify — should be 403
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 403

    # Random outsider tries to verify — should be 403
    headers = make_auth_headers(outsider_id, outsider_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 403

    # Client can verify — should succeed
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_complete_rejects_non_client(client: AsyncClient) -> None:
    """Only the client (buyer) can complete a job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    outsider_id, outsider_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "200.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv)

    # Deliver
    deliver_data = {"result": {"output": "done"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Seller tries to complete — should be 403
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 403

    # Random outsider tries to complete — should be 403
    headers = make_auth_headers(outsider_id, outsider_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 403

    # Client can complete — should succeed
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200
