"""Tests for optional endpoint_url and hosting_mode registration."""

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data


@pytest.mark.asyncio
async def test_register_client_only_no_endpoint(client: AsyncClient) -> None:
    """Registration with hosting_mode='client_only' (no endpoint_url) succeeds."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Client Only Agent",
        "description": "No endpoint needed",
        "hosting_mode": "client_only",
        "capabilities": ["chat"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["hosting_mode"] == "client_only"
    assert body["endpoint_url"] is None
    assert body["is_online"] is False


@pytest.mark.asyncio
async def test_register_websocket_no_endpoint(client: AsyncClient) -> None:
    """Registration with hosting_mode='websocket' (no endpoint_url) succeeds."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "WebSocket Agent",
        "description": "Connects via WS",
        "hosting_mode": "websocket",
        "capabilities": ["realtime"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["hosting_mode"] == "websocket"
    assert body["endpoint_url"] is None


@pytest.mark.asyncio
async def test_register_external_without_endpoint_fails(client: AsyncClient) -> None:
    """Registration with hosting_mode='external' without endpoint_url fails."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Bad External Agent",
        "hosting_mode": "external",
        "capabilities": ["test"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_external_with_endpoint(client: AsyncClient) -> None:
    """Registration with hosting_mode='external' and endpoint_url succeeds."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["hosting_mode"] = "external"
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["hosting_mode"] == "external"
    assert body["endpoint_url"] is not None


@pytest.mark.asyncio
async def test_register_backward_compat_with_endpoint(client: AsyncClient) -> None:
    """Existing registration flow (endpoint_url provided, no hosting_mode) still works."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    # No explicit hosting_mode — should auto-resolve to 'external'
    assert "hosting_mode" not in data or data.get("hosting_mode") is None
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["hosting_mode"] == "external"
    assert body["endpoint_url"] == data["endpoint_url"]


@pytest.mark.asyncio
async def test_register_backward_compat_no_endpoint(client: AsyncClient) -> None:
    """Registration without endpoint_url or hosting_mode defaults to client_only."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Auto Client Agent",
        "capabilities": ["basic"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["hosting_mode"] == "client_only"
    assert body["endpoint_url"] is None


@pytest.mark.asyncio
async def test_platform_agent_card_for_client_only(client: AsyncClient) -> None:
    """Non-external agents get a platform-generated agent card."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Card Test Agent",
        "description": "Testing platform card",
        "hosting_mode": "client_only",
        "capabilities": ["pdf-extraction", "ocr"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    # Check agent card was generated
    resp = await client.get(f"/agents/{agent_id}/agent-card")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Card Test Agent"
    assert card["version"] == "1.0.0"
    assert len(card["skills"]) == 2
    skill_ids = {s["id"] for s in card["skills"]}
    assert skill_ids == {"pdf-extraction", "ocr"}


@pytest.mark.asyncio
async def test_platform_agent_card_for_websocket(client: AsyncClient) -> None:
    """WebSocket agents also get a platform-generated agent card."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "WS Card Agent",
        "hosting_mode": "websocket",
        "capabilities": ["streaming"],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    resp = await client.get(f"/agents/{agent_id}/agent-card")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "WS Card Agent"
    assert len(card["skills"]) == 1
    assert card["skills"][0]["id"] == "streaming"


@pytest.mark.asyncio
async def test_register_invalid_hosting_mode(client: AsyncClient) -> None:
    """Registration with invalid hosting_mode fails."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Bad Mode Agent",
        "hosting_mode": "invalid_mode",
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_websocket_mode_with_endpoint_fails(client: AsyncClient) -> None:
    """Registration with hosting_mode='websocket' and endpoint_url fails."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Bad WS Agent",
        "hosting_mode": "websocket",
        "endpoint_url": "https://example.com/webhook",
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_client_only_with_endpoint_fails(client: AsyncClient) -> None:
    """Registration with hosting_mode='client_only' and endpoint_url fails."""
    _, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": "Bad Client Agent",
        "hosting_mode": "client_only",
        "endpoint_url": "https://example.com/webhook",
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 422
