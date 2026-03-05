"""Tests for discovery online/offline filter."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.utils.crypto import generate_keypair
from tests.conftest import make_auth_headers


async def _create_agent_with_listing(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    is_online: bool = False,
    skill_id: str = "test-skill",
) -> str:
    """Register an agent, set online status, and create a listing. Returns agent_id."""
    priv, pub = generate_keypair()
    data = {
        "public_key": pub,
        "display_name": f"Agent-{skill_id}",
        "endpoint_url": "https://example.com/agent",
        "capabilities": [skill_id],
    }
    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    agent_id = resp.json()["agent_id"]

    # Set online status directly in DB
    result = await db_session.execute(
        select(Agent).where(Agent.agent_id == uuid.UUID(agent_id))
    )
    agent = result.scalar_one()
    agent.is_online = is_online
    await db_session.commit()

    # Create a listing
    listing_data = {
        "skill_id": skill_id,
        "description": f"Listing for {skill_id}",
        "price_model": "per_call",
        "base_price": "10.00",
    }
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/listings", listing_data)
    resp = await client.post(f"/agents/{agent_id}/listings", json=listing_data, headers=headers)
    assert resp.status_code == 201

    return agent_id


@pytest.mark.asyncio
async def test_discover_online_true(client: AsyncClient, db_session: AsyncSession) -> None:
    """Discover with online=True shows only online agents."""
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="online-skill")
    await _create_agent_with_listing(client, db_session, is_online=False, skill_id="offline-skill")

    resp = await client.get("/discover?online=true")
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) == 1
    for r in results:
        assert r["is_online"] is True


@pytest.mark.asyncio
async def test_discover_online_false(client: AsyncClient, db_session: AsyncSession) -> None:
    """Discover with online=False shows only offline agents."""
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="on-skill")
    await _create_agent_with_listing(client, db_session, is_online=False, skill_id="off-skill")

    resp = await client.get("/discover?online=false")
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) == 1
    for r in results:
        assert r["is_online"] is False


@pytest.mark.asyncio
async def test_discover_no_online_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    """Discover without online filter shows all agents (default)."""
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="both-on")
    await _create_agent_with_listing(client, db_session, is_online=False, skill_id="both-off")

    resp = await client.get("/discover")
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) >= 2
    online_states = {r["is_online"] for r in results}
    assert True in online_states
    assert False in online_states


@pytest.mark.asyncio
async def test_discover_online_combined_with_skill_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Online filter works together with skill_id filter."""
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="pdf-online")
    await _create_agent_with_listing(client, db_session, is_online=False, skill_id="pdf-offline")
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="ocr-online")

    resp = await client.get("/discover?online=true&skill_id=pdf")
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) == 1
    assert results[0]["skill_id"] == "pdf-online"
    assert results[0]["is_online"] is True


@pytest.mark.asyncio
async def test_discover_result_includes_is_online(client: AsyncClient, db_session: AsyncSession) -> None:
    """DiscoverResult schema includes is_online field."""
    await _create_agent_with_listing(client, db_session, is_online=True, skill_id="schema-test")

    resp = await client.get("/discover")
    assert resp.status_code == 200
    result = resp.json()["items"][0]
    assert "is_online" in result
    assert isinstance(result["is_online"], bool)
