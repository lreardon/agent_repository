"""Integration tests for script-based acceptance criteria verification.

Tests the full flow: propose job with script criteria → fund → deliver → verify.
Requires Docker for sandbox execution.
"""

import base64
import json
import shutil

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


def _b64(script: str) -> str:
    return base64.b64encode(script.encode()).decode()


_docker = pytest.mark.skipif(
    not shutil.which("docker"), reason="Docker not available",
)


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    body_bytes = json.dumps({"amount": amount}).encode()
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", body_bytes)
    await client.post(
        f"/agents/{agent_id}/deposit",
        content=body_bytes,
        headers={**headers, "Content-Type": "application/json"},
    )


async def _setup_funded_job(
    client: AsyncClient,
    client_id: str, client_priv: str,
    seller_id: str, seller_priv: str,
    criteria: dict,
    budget: str = "100.00",
) -> str:
    """Create, accept, fund, start a job. Returns job_id."""
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": criteria,
    }
    body_bytes = json.dumps(data).encode()
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body_bytes)
    resp = await client.post(
        "/jobs",
        content=body_bytes,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 201, f"Job creation failed: {resp.text}"
    job_id = resp.json()["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    return job_id


@_docker
@pytest.mark.asyncio
async def test_script_verify_pass_releases_escrow(client: AsyncClient) -> None:
    """Script exits 0 → job completed, escrow released to seller."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    script = """
import json, sys
with open('/input/result.json') as f:
    data = json.load(f)
if not isinstance(data, list) or len(data) < 3:
    sys.exit(1)
print(f"Validated {len(data)} records")
"""
    criteria = {"script": _b64(script), "runtime": "python:3.11", "timeout_seconds": 30}

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria)

    # Deliver
    result = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]
    deliver_bytes = json.dumps({"result": result}).encode()
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_bytes)
    await client.post(f"/jobs/{job_id}/deliver", content=deliver_bytes, headers={**headers, "Content-Type": "application/json"})

    # Verify
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["status"] == "completed"
    assert data["verification"]["passed"] is True
    assert data["verification"]["sandbox"]["exit_code"] == 0
    assert "3 records" in data["verification"]["sandbox"]["stdout"]

    # Seller got paid (minus 2.5% fee)
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "97.50"


@_docker
@pytest.mark.asyncio
async def test_script_verify_fail_refunds_escrow(client: AsyncClient) -> None:
    """Script exits 1 → job failed, escrow refunded to client."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    script = """
import json, sys
with open('/input/result.json') as f:
    data = json.load(f)
if len(data) < 100:
    print(f"Need 100+ records, got {len(data)}", file=sys.stderr)
    sys.exit(1)
"""
    criteria = {"script": _b64(script), "runtime": "python:3.11"}

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria)

    # Deliver insufficient data
    deliver_bytes = json.dumps({"result": [1, 2, 3]}).encode()
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_bytes)
    await client.post(f"/jobs/{job_id}/deliver", content=deliver_bytes, headers={**headers, "Content-Type": "application/json"})

    # Verify — should fail
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["status"] == "failed"
    assert data["verification"]["passed"] is False
    assert "Need 100+" in data["verification"]["sandbox"]["stderr"]

    # Client refunded
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "500.00"


@pytest.mark.asyncio
async def test_script_criteria_validation_on_proposal(client: AsyncClient) -> None:
    """Invalid script criteria rejected at job proposal time."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    # Invalid runtime
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "acceptance_criteria": {
            "script": _b64("print('hi')"),
            "runtime": "malware:latest",
        },
    }
    body_bytes = json.dumps(data).encode()
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body_bytes)
    resp = await client.post("/jobs", content=body_bytes, headers={**headers, "Content-Type": "application/json"})
    assert resp.status_code == 422

    # Timeout too large
    data["acceptance_criteria"] = {
        "script": _b64("print('hi')"),
        "runtime": "python:3.11",
        "timeout_seconds": 9999,
    }
    body_bytes = json.dumps(data).encode()
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body_bytes)
    resp = await client.post("/jobs", content=body_bytes, headers={**headers, "Content-Type": "application/json"})
    assert resp.status_code == 422


@_docker
@pytest.mark.asyncio
async def test_declarative_tests_still_work(client: AsyncClient) -> None:
    """Original declarative test format still works alongside script-based."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    # Old-style declarative criteria
    criteria = {
        "version": "1.0",
        "tests": [
            {"test_id": "has_data", "type": "count_gte", "params": {"path": "$", "min_count": 2}},
        ],
        "pass_threshold": "all",
    }

    job_id = await _setup_funded_job(client, client_id, client_priv, seller_id, seller_priv, criteria)

    deliver_bytes = json.dumps({"result": [1, 2, 3]}).encode()
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_bytes)
    await client.post(f"/jobs/{job_id}/deliver", content=deliver_bytes, headers={**headers, "Content-Type": "application/json"})

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["status"] == "completed"
    assert data["verification"]["passed"] is True
    assert "sandbox" not in data["verification"]  # No sandbox for declarative tests
