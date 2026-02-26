"""Tests for rate limiting (app/auth/rate_limit.py).

These tests are marked to run LAST (via pytest_collection_modifyitems or module ordering)
to avoid polluting Redis rate limit buckets for other tests.
"""

import pytest
from httpx import AsyncClient

from app.config import settings
from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data


@pytest.mark.asyncio
async def test_rate_limit_headers_present(client: AsyncClient) -> None:
    """RL1: Rate limit headers present in response."""
    resp = await client.get("/discover")
    assert "x-ratelimit-limit" in resp.headers
    assert "x-ratelimit-remaining" in resp.headers


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429(client: AsyncClient) -> None:
    """RL2: Exceed rate limit → 429."""
    original_cap = settings.rate_limit_discovery_capacity
    original_ref = settings.rate_limit_discovery_refill_per_min
    object.__setattr__(settings, "rate_limit_discovery_capacity", 2)
    object.__setattr__(settings, "rate_limit_discovery_refill_per_min", 0)
    try:
        for _ in range(5):
            resp = await client.get("/discover")
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Rate limit exceeded"
    finally:
        object.__setattr__(settings, "rate_limit_discovery_capacity", original_cap)
        object.__setattr__(settings, "rate_limit_discovery_refill_per_min", original_ref)


@pytest.mark.asyncio
async def test_rate_limit_anonymous_request(client: AsyncClient) -> None:
    """RL4: Anonymous requests get rate limited."""
    original_cap = settings.rate_limit_read_capacity
    original_ref = settings.rate_limit_read_refill_per_min
    object.__setattr__(settings, "rate_limit_read_capacity", 2)
    object.__setattr__(settings, "rate_limit_read_refill_per_min", 0)
    try:
        _, pub = generate_keypair()
        resp = await client.post("/agents", json=make_agent_data(pub))
        assert resp.status_code == 201
        agent_id = resp.json()["agent_id"]

        for _ in range(5):
            resp = await client.get(f"/agents/{agent_id}")
            if resp.status_code == 429:
                break
        assert resp.status_code == 429
    finally:
        object.__setattr__(settings, "rate_limit_read_capacity", original_cap)
        object.__setattr__(settings, "rate_limit_read_refill_per_min", original_ref)


@pytest.mark.asyncio
async def test_rate_limit_different_buckets(client: AsyncClient) -> None:
    """RL3: Discovery and read use different rate bucket keys."""
    from app.auth.rate_limit import _get_rate_config

    disc_cap, disc_refill, disc_cat = _get_rate_config("GET", "/discover")
    read_cap, read_refill, read_cat = _get_rate_config("GET", "/agents/some-id")
    reg_cap, reg_refill, reg_cat = _get_rate_config("POST", "/agents")
    write_cap, write_refill, write_cat = _get_rate_config("POST", "/agents/some-id/deactivate")
    job_cap, job_refill, job_cat = _get_rate_config("POST", "/jobs/123/counter")

    assert disc_cat == "discovery"
    assert read_cat == "read"
    assert reg_cat == "registration"
    assert write_cat == "write"
    assert job_cat == "job_lifecycle"
    assert job_cap <= 20
