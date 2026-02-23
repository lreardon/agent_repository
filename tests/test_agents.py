"""Tests for agent CRUD and balance endpoints."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


@pytest.mark.asyncio
async def test_register_agent(client: AsyncClient) -> None:
    """Happy path: register a new agent."""
    private_key, public_key = generate_keypair()
    data = make_agent_data(public_key)

    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["public_key"] == public_key
    assert body["display_name"] == "Test Agent"
    assert body["status"] == "active"
    assert "agent_id" in body


@pytest.mark.asyncio
async def test_register_duplicate_key(client: AsyncClient) -> None:
    """Registering with a duplicate public key returns 409."""
    _, public_key = generate_keypair()
    data = make_agent_data(public_key)

    resp1 = await client.post("/agents", json=data)
    assert resp1.status_code == 201

    resp2 = await client.post("/agents", json=data)
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_get_agent(client: AsyncClient) -> None:
    """Happy path: get agent by ID."""
    _, public_key = generate_keypair()
    data = make_agent_data(public_key)
    resp = await client.post("/agents", json=data)
    agent_id = resp.json()["agent_id"]

    resp = await client.get(f"/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_get_agent_not_found(client: AsyncClient) -> None:
    """Getting a nonexistent agent returns 404."""
    resp = await client.get("/agents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_agent(client: AsyncClient) -> None:
    """Happy path: update own agent."""
    private_key, public_key = generate_keypair()
    data = make_agent_data(public_key)
    resp = await client.post("/agents", json=data)
    agent_id = resp.json()["agent_id"]

    update_data = {"display_name": "Updated Name"}
    body_bytes = update_data
    headers = make_auth_headers(agent_id, private_key, "PATCH", f"/agents/{agent_id}", body_bytes)

    resp = await client.patch(f"/agents/{agent_id}", json=update_data, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Updated Name"


@pytest.mark.asyncio
async def test_update_agent_wrong_owner(client: AsyncClient) -> None:
    """Updating another agent's profile returns 403."""
    private_key_a, public_key_a = generate_keypair()
    _, public_key_b = generate_keypair()

    resp_a = await client.post("/agents", json=make_agent_data(public_key_a))
    agent_a_id = resp_a.json()["agent_id"]

    resp_b = await client.post("/agents", json=make_agent_data(public_key_b))
    agent_b_id = resp_b.json()["agent_id"]

    update_data = {"display_name": "Hacked"}
    body_bytes = update_data
    headers = make_auth_headers(agent_a_id, private_key_a, "PATCH", f"/agents/{agent_b_id}", body_bytes)

    resp = await client.patch(f"/agents/{agent_b_id}", json=update_data, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_agent_no_auth(client: AsyncClient) -> None:
    """Updating without auth returns 403."""
    _, public_key = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(public_key))
    agent_id = resp.json()["agent_id"]

    resp = await client.patch(f"/agents/{agent_id}", json={"display_name": "No Auth"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_deactivate_agent(client: AsyncClient) -> None:
    """Happy path: deactivate own agent."""
    private_key, public_key = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(public_key))
    agent_id = resp.json()["agent_id"]

    headers = make_auth_headers(agent_id, private_key, "DELETE", f"/agents/{agent_id}")
    resp = await client.delete(f"/agents/{agent_id}", headers=headers)
    assert resp.status_code == 204

    # Verify deactivated
    resp = await client.get(f"/agents/{agent_id}")
    assert resp.json()["status"] == "deactivated"


@pytest.mark.asyncio
async def test_deposit_and_balance(client: AsyncClient) -> None:
    """Happy path: deposit credits and check balance."""
    private_key, public_key = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(public_key))
    agent_id = resp.json()["agent_id"]

    # Deposit
    deposit_data = {"amount": "100.50"}
    body_bytes = deposit_data
    headers = make_auth_headers(agent_id, private_key, "POST", f"/agents/{agent_id}/deposit", body_bytes)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=deposit_data, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["balance"] == "100.50"

    # Check balance
    headers = make_auth_headers(agent_id, private_key, "GET", f"/agents/{agent_id}/balance")
    resp = await client.get(f"/agents/{agent_id}/balance", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["balance"] == "100.50"


@pytest.mark.asyncio
async def test_balance_no_auth(client: AsyncClient) -> None:
    """Checking balance without auth returns 403."""
    _, public_key = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(public_key))
    agent_id = resp.json()["agent_id"]

    resp = await client.get(f"/agents/{agent_id}/balance")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_register_invalid_url(client: AsyncClient) -> None:
    """Registration with non-HTTPS URL fails validation."""
    _, public_key = generate_keypair()
    data = make_agent_data(public_key)
    data["endpoint_url"] = "http://example.com/webhook"

    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_private_ip_url(client: AsyncClient) -> None:
    """Registration with private IP URL fails validation (SSRF protection)."""
    _, public_key = generate_keypair()
    data = make_agent_data(public_key)
    data["endpoint_url"] = "https://192.168.1.1/webhook"

    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422
