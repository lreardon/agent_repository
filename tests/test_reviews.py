"""Tests for reviews and reputation scoring."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    data = {"amount": amount}
    body = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", body)
    await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)


async def _complete_job(
    client: AsyncClient,
    client_id: str, client_priv: str,
    seller_id: str, seller_priv: str,
    budget: str = "100.00",
) -> str:
    """Create a completed job. Returns job_id."""
    data = {"seller_agent_id": seller_id, "max_budget": budget}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver = {"result": {"data": "done"}}
    body = deliver
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver, headers=headers)

    # Complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    return job_id


@pytest.mark.asyncio
async def test_submit_review(client: AsyncClient) -> None:
    """Client reviews seller after completed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    review_data = {"rating": 5, "comment": "Excellent work!"}
    body = review_data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review_data, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["rating"] == 5
    assert resp.json()["reviewer_agent_id"] == client_id
    assert resp.json()["reviewee_agent_id"] == seller_id


@pytest.mark.asyncio
async def test_both_parties_can_review(client: AsyncClient) -> None:
    """Both client and seller can review each other."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    # Client reviews seller
    data = {"rating": 4}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 201

    # Seller reviews client
    data = {"rating": 5}
    body = data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_duplicate_review_rejected(client: AsyncClient) -> None:
    """Can't review the same job twice."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    data = {"rating": 5}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)

    # Try again
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_third_party_cannot_review(client: AsyncClient) -> None:
    """Non-party can't review a job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    intruder_id, intruder_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    data = {"rating": 1}
    body = data
    headers = make_auth_headers(intruder_id, intruder_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reputation_updates(client: AsyncClient) -> None:
    """Reputation score updates after reviews."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "1000.00")

    # Complete two jobs, review seller with 4 and 5
    for rating in (4, 5):
        job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv, "50.00")
        data = {"rating": rating}
        body = data
        headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
        await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)

    # Seller reputation with confidence factor: avg=4.5, confidence=2/20=0.1, score=0.45
    resp = await client.get(f"/agents/{seller_id}")
    assert resp.json()["reputation_seller"] == "0.45"


@pytest.mark.asyncio
async def test_get_agent_reviews(client: AsyncClient) -> None:
    """Get reviews for an agent."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    data = {"rating": 5, "comment": "Great"}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)

    resp = await client.get(f"/agents/{seller_id}/reviews")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["rating"] == 5


@pytest.mark.asyncio
async def test_cannot_review_incomplete_job(client: AsyncClient) -> None:
    """Can't review a job that isn't completed/failed/resolved."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    # Just propose — don't complete
    data = {"seller_agent_id": seller_id, "max_budget": "100.00"}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    review = {"rating": 5}
    body = review
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 409
