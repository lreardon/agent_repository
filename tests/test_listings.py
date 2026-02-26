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
        resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
        assert resp.status_code == 201

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
        resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
        assert resp.status_code == 201

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
    create_resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert create_resp.status_code == 201

    resp = await client.get("/discover")
    assert resp.status_code == 200
    result = resp.json()[0]
    assert "seller_display_name" in result
    assert "seller_reputation" in result
    assert result["seller_agent_id"] == agent_id


# ---------------------------------------------------------------------------
# Additional listing tests (L1-L7, D1-D8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_listing_status_paused(client: AsyncClient) -> None:
    """L1: Update listing status to paused."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    listing_data = _listing_data()
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", listing_data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=listing_data, headers=headers)
    listing_id = resp.json()["listing_id"]

    update = {"status": "paused"}
    headers = make_auth_headers(agent_id, priv, "PATCH", f"/listings/{listing_id}", update)
    resp = await client.patch(f"/listings/{listing_id}", json=update, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_update_listing_wrong_owner(client: AsyncClient) -> None:
    """L2: Non-owner cannot update listing."""
    priv_a, pub_a = generate_keypair()
    priv_b, pub_b = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub_a))
    agent_a_id = resp.json()["agent_id"]
    resp = await client.post("/agents", json=make_agent_data(pub_b))
    agent_b_id = resp.json()["agent_id"]

    listing_data = _listing_data()
    headers = make_auth_headers(agent_a_id, priv_a, "POST", f"/agents/{agent_a_id}/listings", listing_data)
    resp = await client.post(f"/agents/{agent_a_id}/listings", json=listing_data, headers=headers)
    listing_id = resp.json()["listing_id"]

    update = {"description": "hacked"}
    headers = make_auth_headers(agent_b_id, priv_b, "PATCH", f"/listings/{listing_id}", update)
    resp = await client.patch(f"/listings/{listing_id}", json=update, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_browse_listings_pagination(client: AsyncClient) -> None:
    """L4: Browse with limit and offset."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Create 3 listings with different skill_ids
    for i in range(3):
        data = _listing_data()
        data["skill_id"] = f"skill-{i}"
        headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
        resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
        assert resp.status_code == 201

    resp = await client.get("/listings?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = await client.get("/listings?limit=2&offset=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_discover_excludes_paused_listings(client: AsyncClient) -> None:
    """D8: Discover excludes paused/archived listings."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    listing_data = _listing_data()
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", listing_data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=listing_data, headers=headers)
    listing_id = resp.json()["listing_id"]

    # Pause it
    update = {"status": "paused"}
    headers = make_auth_headers(agent_id, priv, "PATCH", f"/listings/{listing_id}", update)
    await client.patch(f"/listings/{listing_id}", json=update, headers=headers)

    # Discover should not include it
    resp = await client.get("/discover")
    assert resp.status_code == 200
    ids = [r["listing_id"] for r in resp.json()]
    assert listing_id not in ids


@pytest.mark.asyncio
async def test_discover_with_max_price_filter(client: AsyncClient) -> None:
    """D2: Discover with max_price filter."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Create cheap and expensive listings
    cheap = _listing_data()
    cheap["base_price"] = "10.00"
    cheap["skill_id"] = "cheap-skill"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", cheap)
    await client.post(f"/agents/{agent_id}/listings", json=cheap, headers=headers)

    expensive = _listing_data()
    expensive["base_price"] = "500.00"
    expensive["skill_id"] = "expensive-skill"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", expensive)
    await client.post(f"/agents/{agent_id}/listings", json=expensive, headers=headers)

    resp = await client.get("/discover?max_price=50")
    assert resp.status_code == 200
    for r in resp.json():
        assert float(r["base_price"]) <= 50


@pytest.mark.asyncio
async def test_discover_with_price_model_filter(client: AsyncClient) -> None:
    """D3: Discover with price_model filter."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = _listing_data()
    data["price_model"] = "per_hour"
    data["skill_id"] = "hourly-skill"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    resp = await client.get("/discover?price_model=per_hour")
    assert resp.status_code == 200
    for r in resp.json():
        assert r["price_model"] == "per_hour"


@pytest.mark.asyncio
async def test_browse_listings_skill_id_filter(client: AsyncClient) -> None:
    """L3: Browse with skill_id filter — partial match (ILIKE)."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = _listing_data()
    data["skill_id"] = "image-generation"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    resp = await client.get("/listings?skill_id=image")
    assert resp.status_code == 200
    assert any(r["skill_id"] == "image-generation" for r in resp.json())


@pytest.mark.asyncio
async def test_create_listing_deactivated_agent(client: AsyncClient) -> None:
    """L6: Create listing with deactivated agent fails."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Deactivate
    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    await client.delete(f"/agents/{agent_id}", headers=headers)

    # Try to create listing — auth should fail (deactivated)
    data = _listing_data()
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_listing_price_boundary(client: AsyncClient) -> None:
    """L7: Price exactly 1,000,000 accepted, over rejected."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    # Exactly 1M — should work
    data = _listing_data()
    data["base_price"] = "1000000.00"
    data["skill_id"] = "max-price-skill"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    assert resp.status_code == 201

    # Over 1M — rejected
    data2 = _listing_data()
    data2["base_price"] = "1000001.00"
    data2["skill_id"] = "over-price-skill"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data2)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data2, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discover_deactivated_agent_excluded(client: AsyncClient) -> None:
    """D7: Discover excludes deactivated agents' listings."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = _listing_data()
    data["skill_id"] = "will-deactivate"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)
    listing_id = resp.json()["listing_id"]

    # Deactivate the agent
    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    await client.delete(f"/agents/{agent_id}", headers=headers)

    # Discover should exclude
    resp = await client.get("/discover")
    ids = [r["listing_id"] for r in resp.json()]
    assert listing_id not in ids


@pytest.mark.asyncio
async def test_discover_pagination(client: AsyncClient) -> None:
    """D5: Discover with offset and limit."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    for i in range(3):
        data = _listing_data()
        data["skill_id"] = f"discover-page-{i}"
        headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
        await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    resp = await client.get("/discover?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


@pytest.mark.asyncio
async def test_discover_combined_filters(client: AsyncClient) -> None:
    """D4: Discover with combined max_price + price_model."""
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    agent_id = resp.json()["agent_id"]

    data = _listing_data()
    data["base_price"] = "25.00"
    data["price_model"] = "per_call"
    data["skill_id"] = "combo-filter"
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", data)
    await client.post(f"/agents/{agent_id}/listings", json=data, headers=headers)

    resp = await client.get("/discover?max_price=50&price_model=per_call")
    assert resp.status_code == 200
    for r in resp.json():
        assert float(r["base_price"]) <= 50
        assert r["price_model"] == "per_call"
