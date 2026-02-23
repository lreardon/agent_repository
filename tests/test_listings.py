"""Tests for listing CRUD and discovery endpoints."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient, private_key: str | None = None) -> tuple[str, str, dict]:
    """Helper: register an agent and return (agent_id, private_key, response_body)."""
    if private_key is None:
        priv, pub = generate_keypair()
    else:
        priv = private_key
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
        pub = SigningKey(priv.encode(), encoder=HexEncoder).verify_key.encode(encoder=HexEncoder).decode()
    data = make_agent_data(pub)
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv, resp.json()


def _listing_data(**overrides: object) -> dict:
    base = {
        "skill_id": "pdf-extraction",
        "description": "Extract data from PDFs",
        "price_model": "per_unit",
        "base_price": "0.05",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_listing(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)
    data = _listing_data()
    body_bytes = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)

    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["skill_id"] == "pdf-extraction"
    assert body["price_model"] == "per_unit"
    assert body["seller_agent_id"] == agent_id


@pytest.mark.asyncio
async def test_create_listing_no_auth(client: AsyncClient) -> None:
    agent_id, _, _ = await _create_agent(client)
    resp = await client.post(f"/agents/{agent_id}/listings", json=_listing_data())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_listing_wrong_agent(client: AsyncClient) -> None:
    agent_a_id, priv_a, _ = await _create_agent(client)
    agent_b_id, _, _ = await _create_agent(client)

    data = _listing_data()
    body_bytes = data
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/agents/{agent_b_id}/listings", body_bytes)

    resp = await client.post(f"/agents/{agent_b_id}/listings", json=data, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_listing(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)
    data = _listing_data()
    body_bytes = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    listing_id = resp.json()["listing_id"]

    resp = await client.get(f"/listings/{listing_id}")
    assert resp.status_code == 200
    assert resp.json()["listing_id"] == listing_id


@pytest.mark.asyncio
async def test_get_listing_not_found(client: AsyncClient) -> None:
    resp = await client.get("/listings/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_listing(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)
    data = _listing_data()
    body_bytes = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    listing_id = resp.json()["listing_id"]

    update = {"base_price": "0.10"}
    body_bytes = update
    headers = make_auth_headers(agent_id, priv, "PATCH", f"/listings/{listing_id}", body_bytes)
    resp = await client.patch(f"/listings/{listing_id}", json=update, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["base_price"] == "0.10"


@pytest.mark.asyncio
async def test_browse_listings(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)

    for cap in ["pdf-extraction", "ocr-processing", "pdf-parse"]:
        data = _listing_data(skill_id=cap)
        body_bytes = data
        headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)
        await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    # Browse all
    resp = await client.get("/listings")
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    # Filter by capability
    resp = await client.get("/listings?skill_id=pdf")
    assert resp.status_code == 200
    assert len(resp.json()) == 2  # pdf-extraction and pdf-parse


@pytest.mark.asyncio
async def test_discover(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)

    for cap, price in [("pdf-extraction", "0.05"), ("ocr-processing", "0.10"), ("pdf-parse", "0.50")]:
        data = _listing_data(skill_id=cap, base_price=price)
        body_bytes = data
        headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)
        await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    # Discover all
    resp = await client.get("/discover")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 3

    # Filter by capability
    resp = await client.get("/discover?skill_id=pdf")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Filter by max_price
    resp = await client.get("/discover?max_price=0.10")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Filter by price_model
    resp = await client.get("/discover?price_model=per_unit")
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    resp = await client.get("/discover?price_model=flat")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_discover_includes_seller_info(client: AsyncClient) -> None:
    agent_id, priv, _ = await _create_agent(client)
    data = _listing_data()
    body_bytes = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", body_bytes)
    await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    resp = await client.get("/discover")
    assert resp.status_code == 200
    result = resp.json()[0]
    assert "seller_display_name" in result
    assert "seller_reputation" in result
    assert result["seller_agent_id"] == agent_id
