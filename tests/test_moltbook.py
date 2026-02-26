"""Tests for MoltBook identity verification during agent registration."""

from unittest.mock import AsyncMock, patch

import httpx as httpx_lib
import pytest
from httpx import AsyncClient

from app.services.moltbook import MoltBookProfile
from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data


def _fake_profile(**overrides) -> MoltBookProfile:
    defaults = dict(
        moltbook_id="mb_123",
        username="testbot",
        display_name="@testbot",
        karma=50,
        verified=True,
        profile_url="https://moltbook.com/@testbot",
    )
    defaults.update(overrides)
    return MoltBookProfile(**defaults)


@pytest.mark.asyncio
async def test_register_with_moltbook_token(client: AsyncClient) -> None:
    """Registration with valid MoltBook token stores identity fields."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "valid-token-abc"

    with patch("app.services.moltbook.verify_identity_token", new_callable=AsyncMock, return_value=_fake_profile()):
        resp = await client.post("/agents", json=data)

    assert resp.status_code == 201
    body = resp.json()
    assert body["moltbook_id"] == "mb_123"
    assert body["moltbook_username"] == "testbot"
    assert body["moltbook_karma"] == 50
    assert body["moltbook_verified"] is True


@pytest.mark.asyncio
async def test_register_without_moltbook_when_optional(client: AsyncClient) -> None:
    """Registration without MoltBook token succeeds when not required."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)

    resp = await client.post("/agents", json=data)
    assert resp.status_code == 201
    body = resp.json()
    assert body["moltbook_id"] is None
    assert body["moltbook_verified"] is False


@pytest.mark.asyncio
async def test_register_without_moltbook_when_required(client: AsyncClient) -> None:
    """Registration without MoltBook token fails when required."""
    _, pub = generate_keypair()
    data = make_agent_data(pub)

    from app.config import settings as real_settings
    original = real_settings.moltbook_required
    try:
        object.__setattr__(real_settings, "moltbook_required", True)
        resp = await client.post("/agents", json=data)
    finally:
        object.__setattr__(real_settings, "moltbook_required", original)

    assert resp.status_code == 422
    assert "MoltBook" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_duplicate_moltbook_id(client: AsyncClient) -> None:
    """Two agents can't share the same MoltBook identity."""
    profile = _fake_profile(moltbook_id="mb_unique_1")

    with patch("app.services.moltbook.verify_identity_token", new_callable=AsyncMock, return_value=profile):
        # First registration
        _, pub1 = generate_keypair()
        data1 = make_agent_data(pub1)
        data1["moltbook_identity_token"] = "token-1"
        resp1 = await client.post("/agents", json=data1)
        assert resp1.status_code == 201

        # Second registration with same MoltBook ID
        _, pub2 = generate_keypair()
        data2 = make_agent_data(pub2)
        data2["moltbook_identity_token"] = "token-2"
        resp2 = await client.post("/agents", json=data2)

    assert resp2.status_code == 409
    assert "already linked" in resp2.json()["detail"]


@pytest.mark.asyncio
async def test_register_moltbook_invalid_token(client: AsyncClient) -> None:
    """Invalid MoltBook token returns 403."""
    from fastapi import HTTPException

    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "expired-token"

    mock = AsyncMock(side_effect=HTTPException(status_code=403, detail="Invalid or expired MoltBook identity token"))
    with patch("app.services.moltbook.verify_identity_token", mock):
        resp = await client.post("/agents", json=data)

    assert resp.status_code == 403
    assert "Invalid or expired" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_moltbook_api_down(client: AsyncClient) -> None:
    """MoltBook API unavailable returns 502."""
    from fastapi import HTTPException

    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "some-token"

    mock = AsyncMock(side_effect=HTTPException(status_code=502, detail="MoltBook identity verification timed out"))
    with patch("app.services.moltbook.verify_identity_token", mock):
        resp = await client.post("/agents", json=data)

    assert resp.status_code == 502
    assert "timed out" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_register_moltbook_no_api_key_configured(client: AsyncClient) -> None:
    """MoltBook verification fails gracefully when API key not configured."""
    from fastapi import HTTPException

    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "some-token"

    mock = AsyncMock(side_effect=HTTPException(status_code=503, detail="MoltBook identity verification is not configured on this server"))
    with patch("app.services.moltbook.verify_identity_token", mock):
        resp = await client.post("/agents", json=data)

    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# MB1-MB2: karma threshold and field population
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_moltbook_low_karma_currently_allowed(client: AsyncClient) -> None:
    """MB1: moltbook_min_karma config exists but enforcement is not yet implemented.
    Low-karma agents can currently register successfully (known gap — TODO).
    """
    from unittest.mock import AsyncMock, patch
    from app.services.moltbook import MoltBookProfile

    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "low-karma-token"

    low_karma_profile = MoltBookProfile(
        moltbook_id="mb-low-karma",
        username="lowkarma",
        display_name="Low Karma Agent",
        karma=0,
        verified=False,
        profile_url=None,
    )

    mock = AsyncMock(return_value=low_karma_profile)
    with patch("app.services.moltbook.verify_identity_token", mock):
        resp = await client.post("/agents", json=data)

    # Karma enforcement not yet implemented — agent registers with 0 karma
    assert resp.status_code == 201
    assert resp.json()["moltbook_karma"] == 0


@pytest.mark.asyncio
async def test_register_moltbook_fields_populated(client: AsyncClient) -> None:
    """MB2: moltbook_id, username, karma populated on agent after registration."""
    from unittest.mock import AsyncMock, patch
    from app.services.moltbook import MoltBookProfile

    _, pub = generate_keypair()
    data = make_agent_data(pub)
    data["moltbook_identity_token"] = "valid-token"

    profile = MoltBookProfile(
        moltbook_id="mb-abc123",
        username="diamond_coder",
        display_name="Diamond Coder",
        karma=9999,
        verified=True,
        profile_url="https://moltbook.com/@diamond_coder",
    )

    mock = AsyncMock(return_value=profile)
    with patch("app.services.moltbook.verify_identity_token", mock):
        resp = await client.post("/agents", json=data)

    assert resp.status_code == 201
    body = resp.json()
    assert body["moltbook_id"] == "mb-abc123"
    assert body["moltbook_username"] == "diamond_coder"
    assert body["moltbook_karma"] == 9999
    assert body["moltbook_verified"] is True
