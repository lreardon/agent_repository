"""Tests for authentication middleware: signatures, timestamps, nonces, deactivated agents."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair, generate_nonce, sign_request
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


@pytest.mark.asyncio
async def test_missing_auth_headers(client: AsyncClient) -> None:
    """Request without Authorization header returns 403."""
    agent_id, _ = await _create_agent(client)
    resp = await client.get(f"/agents/{agent_id}/balance")
    assert resp.status_code == 403
    assert "Missing authentication" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_wrong_auth_scheme(client: AsyncClient) -> None:
    """Request with Bearer instead of AgentSig returns 403."""
    agent_id, _ = await _create_agent(client)
    resp = await client.get(
        f"/agents/{agent_id}/balance",
        headers={"Authorization": "Bearer faketoken", "X-Timestamp": datetime.now(UTC).isoformat()},
    )
    assert resp.status_code == 403
    assert "Invalid authorization scheme" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_malformed_auth_header(client: AsyncClient) -> None:
    """Authorization header without colon separator returns 403."""
    agent_id, _ = await _create_agent(client)
    resp = await client.get(
        f"/agents/{agent_id}/balance",
        headers={"Authorization": "AgentSig noseparator", "X-Timestamp": datetime.now(UTC).isoformat()},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_expired_timestamp(client: AsyncClient) -> None:
    """Request with timestamp older than max_age returns 403."""
    agent_id, priv = await _create_agent(client)
    old_timestamp = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    path = f"/agents/{agent_id}/balance"
    signature = sign_request(priv, old_timestamp, "GET", path, b"")
    resp = await client.get(
        path,
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": old_timestamp,
            "X-Nonce": generate_nonce(),
        },
    )
    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_future_timestamp(client: AsyncClient) -> None:
    """Request with timestamp far in the future returns 403."""
    agent_id, priv = await _create_agent(client)
    future_timestamp = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
    path = f"/agents/{agent_id}/balance"
    signature = sign_request(priv, future_timestamp, "GET", path, b"")
    resp = await client.get(
        path,
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": future_timestamp,
            "X-Nonce": generate_nonce(),
        },
    )
    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_replayed_nonce(client: AsyncClient) -> None:
    """Reusing a nonce returns 403 on second request."""
    agent_id, priv = await _create_agent(client)
    path = f"/agents/{agent_id}/balance"

    # First request — should succeed
    headers = make_auth_headers(agent_id, priv, "GET", path)
    nonce = headers["X-Nonce"]
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 200

    # Second request with same nonce — must fail
    timestamp = datetime.now(UTC).isoformat()
    signature = sign_request(priv, timestamp, "GET", path, b"")
    resp = await client.get(
        path,
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,  # reuse!
        },
    )
    assert resp.status_code == 403
    assert "Nonce already used" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_wrong_private_key(client: AsyncClient) -> None:
    """Request signed with wrong key returns 403."""
    agent_id, _ = await _create_agent(client)
    wrong_priv, _ = generate_keypair()  # different keypair
    path = f"/agents/{agent_id}/balance"
    headers = make_auth_headers(agent_id, wrong_priv, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 403
    assert "Invalid signature" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_tampered_body(client: AsyncClient) -> None:
    """Signature computed over different body than what's sent returns 403."""
    agent_id, priv = await _create_agent(client)
    path = f"/agents/{agent_id}/deposit"

    # Sign with one body, send another
    original_data = {"amount": "100.00"}
    headers = make_auth_headers(agent_id, priv, "POST", path, original_data)
    tampered_data = {"amount": "999999.00"}
    resp = await client.post(path, json=tampered_data, headers=headers)
    assert resp.status_code == 403
    assert "Invalid signature" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deactivated_agent_rejected(client: AsyncClient) -> None:
    """Deactivated agent's requests are rejected."""
    agent_id, priv = await _create_agent(client)

    # Deactivate
    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    resp = await client.delete(f"/agents/{agent_id}", headers=headers)
    assert resp.status_code == 204

    # Try to check balance — should be rejected
    headers = make_auth_headers(agent_id, priv, "GET", f"/agents/{agent_id}/balance")
    resp = await client.get(f"/agents/{agent_id}/balance", headers=headers)
    assert resp.status_code == 403
    assert "not active" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_nonexistent_agent_id(client: AsyncClient) -> None:
    """Auth with a valid signature but nonexistent agent_id returns 403."""
    priv, pub = generate_keypair()
    fake_id = "00000000-0000-0000-0000-000000000000"
    path = f"/agents/{fake_id}/balance"
    headers = make_auth_headers(fake_id, priv, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 403
    assert "Agent not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signature_wrong_method(client: AsyncClient) -> None:
    """Signature for GET doesn't work for POST (method is part of signed message)."""
    agent_id, priv = await _create_agent(client)
    path = f"/agents/{agent_id}/deposit"
    data = {"amount": "10.00"}

    # Sign as GET, send as POST
    timestamp = datetime.now(UTC).isoformat()
    body_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    signature = sign_request(priv, timestamp, "GET", path, body_bytes)
    resp = await client.post(
        path,
        json=data,
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": timestamp,
            "X-Nonce": generate_nonce(),
        },
    )
    assert resp.status_code == 403
    assert "Invalid signature" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_signature_wrong_path(client: AsyncClient) -> None:
    """Signature for one path doesn't work on another (path is part of signed message)."""
    agent_id, priv = await _create_agent(client)
    real_path = f"/agents/{agent_id}/balance"

    # Sign for a different path
    timestamp = datetime.now(UTC).isoformat()
    signature = sign_request(priv, timestamp, "GET", "/agents/fake/balance", b"")
    resp = await client.get(
        real_path,
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": timestamp,
            "X-Nonce": generate_nonce(),
        },
    )
    assert resp.status_code == 403
    assert "Invalid signature" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Additional auth edge cases (AU1, AU3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_without_nonce_succeeds(client: AsyncClient) -> None:
    """AU1: Missing X-Nonce header — request still succeeds (nonce is optional)."""
    agent_id, priv = await _create_agent(client)

    timestamp = datetime.now(UTC).isoformat()
    signature = sign_request(priv, timestamp, "GET", f"/agents/{agent_id}/balance", b"")

    resp = await client.get(
        f"/agents/{agent_id}/balance",
        headers={
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": timestamp,
            # No X-Nonce header
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_with_deactivated_agent_rejected(client: AsyncClient) -> None:
    """AU3: Auth with deactivated agent returns 403 'not active'."""
    agent_id, priv = await _create_agent(client)

    # Deactivate
    headers = make_auth_headers(agent_id, priv, "DELETE", f"/agents/{agent_id}")
    resp = await client.delete(f"/agents/{agent_id}", headers=headers)
    assert resp.status_code == 204

    # Try authenticated request
    headers = make_auth_headers(agent_id, priv, "GET", f"/agents/{agent_id}/balance")
    resp = await client.get(f"/agents/{agent_id}/balance", headers=headers)
    assert resp.status_code == 403
    assert "not active" in resp.json()["detail"]
