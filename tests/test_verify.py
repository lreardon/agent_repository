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
    """Create, accept, fund, start a job. Returns job_id."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": criteria,
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    # Accept (provide criteria hash only if criteria are set)
    accept_data = {}
    if criteria is not None:
        accept_data["acceptance_criteria_hash"] = hash_criteria(criteria)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept",
                                accept_data if accept_data else b"")
    await client.post(f"/jobs/{job_id}/accept", json=accept_data if accept_data else None, headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    return job_id


@pytest.mark.asyncio
async def test_proposal_rejects_declarative_criteria(client: AsyncClient) -> None:
    """Job proposals with declarative v1.0 criteria are rejected at schema validation."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    declarative_criteria = {
        "version": "1.0",
        "tests": [
            {"test_id": "check", "type": "count_gte", "params": {"path": "$", "min_count": 1}},
        ],
        "pass_threshold": "all",
    }
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "acceptance_criteria": declarative_criteria,
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422
    assert "script" in resp.json()["detail"][0]["msg"].lower() or "not supported" in resp.json()["detail"][0]["msg"].lower()


@pytest.mark.asyncio
async def test_verify_no_criteria_auto_completes(client: AsyncClient) -> None:
    """No acceptance criteria → verify auto-completes the job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv)

    # Deliver
    body = {"result": {"data": "anything"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json={"result": {"data": "anything"}}, headers=headers)

    # Verify — no criteria, auto-complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job"]["status"] == "completed"
    assert body["verification"] is None


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
