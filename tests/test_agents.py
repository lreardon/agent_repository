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


# ---------------------------------------------------------------------------
# Agent Card endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_card_no_card(client: AsyncClient) -> None:
    """A2: GET /agents/{id}/agent-card returns 404 when agent has no card."""
    _, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    resp = await client.get(f"/agents/{agent_id}/agent-card")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Reputation endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reputation_new_agent(client: AsyncClient) -> None:
    """A3/A4: GET /agents/{id}/reputation returns 'New' for agent with no reviews."""
    _, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    resp = await client.get(f"/agents/{agent_id}/reputation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reputation_seller_display"] == "New"
    assert body["reputation_client_display"] == "New"
    assert body["total_reviews_as_seller"] == 0
    assert body["total_reviews_as_client"] == 0


# ---------------------------------------------------------------------------
# Validation edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_max_capabilities_boundary(client: AsyncClient) -> None:
    """A7: 20 capabilities OK, 21 rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["capabilities"] = [f"cap-{i}" for i in range(20)]
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201

    _, pub2 = generate_keypair()
    data2 = make_agent_data(pub2)
    data2["capabilities"] = [f"cap-{i}" for i in range(21)]
    resp2 = await client.post("/agents", json=data2)
    assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_capability_format(client: AsyncClient) -> None:
    """A8: Capabilities with special chars rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["capabilities"] = ["valid-cap", "invalid cap!"]
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_partial_update_display_name_only(client: AsyncClient) -> None:
    """A10: PATCH with only display_name, no other fields."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]
    original_url = resp.json()["endpoint_url"]

    update = {"display_name": "New Name Only"}
    headers = make_auth_headers(agent_id, priv, "PATCH", f"/agents/{agent_id}", update)
    resp = await client.patch(f"/agents/{agent_id}", json=update, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "New Name Only"
    assert resp.json()["endpoint_url"] == original_url


@pytest.mark.asyncio
async def test_deactivated_agent_still_visible(client: AsyncClient) -> None:
    """A13: GET /agents/{id} returns deactivated agent (not 404)."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    await client.delete(f"/agents/{agent_id}", headers=headers)

    resp = await client.get(f"/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"


@pytest.mark.asyncio
async def test_register_with_empty_capabilities(client: AsyncClient) -> None:
    """A6: Empty capabilities list vs null both work."""
    _, pub1 = generate_keypair()
    data1 = make_agent_data(pub1)
    data1["capabilities"] = []
    resp1 = await client.post("/agents", json=data1)
    assert resp1.status_code == 201

    _, pub2 = generate_keypair()
    data2 = make_agent_data(pub2)
    data2["capabilities"] = None
    resp2 = await client.post("/agents", json=data2)
    assert resp2.status_code == 201


@pytest.mark.asyncio
async def test_register_max_length_fields(client: AsyncClient) -> None:
    """A5: Registration with max-length name (128) and description (4096)."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["display_name"] = "A" * 128
    data["description"] = "B" * 4096
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    assert resp.json()["display_name"] == "A" * 128


@pytest.mark.asyncio
async def test_register_name_too_long(client: AsyncClient) -> None:
    """display_name > 128 chars rejected."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["display_name"] = "A" * 129
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_deactivate_then_reregister_same_key(client: AsyncClient) -> None:
    """A12: Deactivated agent's public key is still unique — re-registration fails."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    await client.delete(f"/agents/{agent_id}", headers=headers)

    # Try re-registering with same key
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 409
