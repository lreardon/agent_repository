"""Tests for application middleware (body size limit, security headers)."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_security_headers_present(client: AsyncClient) -> None:
    """M4: All security headers present on every response."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-xss-protection"] == "1; mode=block"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_body_size_limit_exceeded(client: AsyncClient) -> None:
    """M1: POST with Content-Length > 1MB is rejected with 413."""
    # Send a request with a Content-Length header claiming a huge body
    resp = await client.post(
        "/agents",
        content=b"x",
        headers={"Content-Length": "2000000", "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_body_size_limit_within_range(client: AsyncClient) -> None:
    """M2: POST within 1MB limit passes through (may fail for other reasons)."""
    from tests.conftest import make_agent_data
    from app.utils.crypto import generate_keypair
    _, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    # Should not be 413 — registration should succeed
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_body_size_limit_get_not_checked(client: AsyncClient) -> None:
    """M3: GET requests are not subject to body size check."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_security_headers_on_error_response(client: AsyncClient) -> None:
    """Security headers present even on 404 responses."""
    resp = await client.get("/agents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_security_headers_on_post_response(client: AsyncClient) -> None:
    """Security headers present on POST responses."""
    from app.utils.crypto import generate_keypair
    from tests.conftest import make_agent_data
    _, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    assert resp.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


@pytest.mark.asyncio
async def test_body_size_limit_put_checked(client: AsyncClient) -> None:
    """PUT requests are also subject to body size check."""
    resp = await client.put(
        "/agents/00000000-0000-0000-0000-000000000000",
        content=b"x",
        headers={"Content-Length": "2000000", "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_body_size_limit_exact_boundary(client: AsyncClient) -> None:
    """Request with Content-Length exactly 1MB (1048576) passes."""
    resp = await client.post(
        "/agents",
        content=b"x",
        headers={"Content-Length": "1048576", "Content-Type": "application/json"},
    )
    # 1MB exactly should pass size check (fail for other reasons like bad JSON)
    assert resp.status_code != 413
