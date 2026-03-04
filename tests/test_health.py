"""Health check endpoint tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient) -> None:
    """Health check returns healthy when DB and Redis are up."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["components"]["database"] == "ok"
    assert body["components"]["redis"] == "ok"
    assert "in_flight_tasks" in body


@pytest.mark.asyncio
async def test_health_reports_components(client: AsyncClient) -> None:
    """Health response includes component breakdown."""
    resp = await client.get("/health")
    body = resp.json()
    assert "components" in body
    assert set(body["components"].keys()) == {"database", "redis"}
