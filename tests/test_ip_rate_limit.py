"""Tests for IP-based rate limiting (anonymous) and registration throttle."""

import pytest
from httpx import AsyncClient

from app.config import settings
from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data


@pytest.mark.asyncio
async def test_anonymous_requests_use_ip_bucketing(client: AsyncClient) -> None:
    """Different IPs get separate rate limit buckets for anonymous requests."""
    object.__setattr__(settings, "rate_limit_discovery_capacity", 2)
    object.__setattr__(settings, "rate_limit_discovery_refill_per_min", 0)

    # Exhaust limit for IP 10.0.0.1
    for _ in range(5):
        resp = await client.get(
            "/discover",
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
    assert resp.status_code == 429

    # A different IP should still be allowed
    resp = await client.get(
        "/discover",
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_authenticated_requests_use_agent_id_bucketing(client: AsyncClient) -> None:
    """Authenticated requests use agent_id, not IP, as bucket key.

    The rate limiter extracts agent_id from the Authorization header for
    bucketing — it doesn't validate the signature. We use a fake agent_id
    to verify that authenticated requests share a bucket by agent_id
    regardless of IP.
    """
    object.__setattr__(settings, "rate_limit_discovery_capacity", 2)
    object.__setattr__(settings, "rate_limit_discovery_refill_per_min", 0)

    fake_agent_id = "test-agent-bucket"

    # Make requests from two "different IPs" with the same agent_id.
    # They should share the same bucket.
    for _ in range(5):
        resp = await client.get(
            "/discover",
            headers={
                "Authorization": f"AgentSig {fake_agent_id}:fakesig",
                "X-Forwarded-For": "10.0.0.1",
            },
        )
    assert resp.status_code == 429

    # Even from a different IP, same agent_id is still blocked
    resp = await client.get(
        "/discover",
        headers={
            "Authorization": f"AgentSig {fake_agent_id}:fakesig",
            "X-Forwarded-For": "10.0.0.99",
        },
    )
    assert resp.status_code == 429

    # But an anonymous request from a new IP should work fine (different bucket)
    resp = await client.get(
        "/discover",
        headers={"X-Forwarded-For": "10.0.0.200"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_registration_has_tighter_limit(client: AsyncClient) -> None:
    """POST /agents (registration) uses a separate, tighter rate limit."""
    object.__setattr__(settings, "rate_limit_registration_capacity", 2)
    object.__setattr__(settings, "rate_limit_registration_refill_per_min", 0)

    for _ in range(5):
        _, pub = generate_keypair()
        resp = await client.post(
            "/agents",
            json=make_agent_data(pub),
            headers={"X-Forwarded-For": "10.0.0.50"},
        )
        if resp.status_code == 429:
            break
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_x_forwarded_for_respected(client: AsyncClient) -> None:
    """X-Forwarded-For header is used to identify the client IP."""
    from unittest.mock import MagicMock

    from app.auth.rate_limit import _get_client_ip

    # Simulate request with X-Forwarded-For
    mock_request = MagicMock()
    mock_request.headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"

    assert _get_client_ip(mock_request) == "1.2.3.4"

    # Without X-Forwarded-For, falls back to client.host
    mock_request.headers = {}
    assert _get_client_ip(mock_request) == "127.0.0.1"

    # With client=None, falls back to 'unknown'
    mock_request.client = None
    assert _get_client_ip(mock_request) == "unknown"


@pytest.mark.asyncio
async def test_exhausting_one_ip_does_not_affect_another(client: AsyncClient) -> None:
    """Exhausting one IP's anonymous limit does not starve another IP."""
    object.__setattr__(settings, "rate_limit_discovery_capacity", 2)
    object.__setattr__(settings, "rate_limit_discovery_refill_per_min", 0)

    # Exhaust IP-A
    for _ in range(5):
        resp = await client.get(
            "/discover",
            headers={"X-Forwarded-For": "192.168.1.1"},
        )
    assert resp.status_code == 429

    # IP-B should be unaffected
    resp = await client.get(
        "/discover",
        headers={"X-Forwarded-For": "192.168.1.2"},
    )
    assert resp.status_code == 200
