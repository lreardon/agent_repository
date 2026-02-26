"""Tests for reviews and reputation scoring."""

import json

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    """Credit agent balance via dev-only deposit endpoint."""
    data = {"amount": amount}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200, f"Dev deposit failed: {resp.status_code} {resp.text}"


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
    assert resp.status_code == 201, f"Propose failed: {resp.status_code} {resp.text}"
    job_id = resp.json()["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200, f"Accept failed: {resp.status_code} {resp.text}"

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200, f"Fund failed: {resp.status_code} {resp.text}"

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200, f"Start failed: {resp.status_code} {resp.text}"

    # Deliver
    deliver = {"result": {"data": "done"}}
    body = deliver
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver, headers=headers)
    assert resp.status_code == 200, f"Deliver failed: {resp.status_code} {resp.text}"

    # Complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200, f"Complete failed: {resp.status_code} {resp.text}"

    return job_id


@pytest.mark.asyncio
async def test_submit_review(client: AsyncClient) -> None:
    """Client reviews seller after completed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

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
    await _deposit(client, seller_id, seller_priv, "10.00")

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
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    data = {"rating": 5}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 201

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
    await _deposit(client, seller_id, seller_priv, "10.00")

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
    await _deposit(client, seller_id, seller_priv, "10.00")

    # Complete two jobs, review seller with 4 and 5
    for rating in (4, 5):
        job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv, "50.00")
        data = {"rating": rating}
        body = data
        headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
        resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
        assert resp.status_code == 201

    # Seller reputation with confidence factor: avg=4.5, confidence=2/20=0.1, score=0.45
    resp = await client.get(f"/agents/{seller_id}")
    assert resp.status_code == 200
    assert resp.json()["reputation_seller"] == "0.45"


@pytest.mark.asyncio
async def test_get_agent_reviews(client: AsyncClient) -> None:
    """Get reviews for an agent."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)

    data = {"rating": 5, "comment": "Great"}
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=data, headers=headers)
    assert resp.status_code == 201

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
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    review = {"rating": 5}
    body = review
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", body)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Additional review tests (R1-R9)
# ---------------------------------------------------------------------------


async def _setup_and_complete_job(client: AsyncClient) -> tuple[str, str, str, str, str]:
    """Helper: create agents, deposit, complete job. Returns (client_id, client_priv, seller_id, seller_priv, job_id)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _complete_job(client, client_id, client_priv, seller_id, seller_priv)
    return client_id, client_priv, seller_id, seller_priv, job_id


@pytest.mark.asyncio
async def test_review_with_tags_and_comment(client: AsyncClient) -> None:
    """R7: Review with tags and comment."""
    client_id, client_priv, seller_id, seller_priv, job_id = await _setup_and_complete_job(client)

    review = {"rating": 4, "tags": ["fast", "reliable"], "comment": "Great work!"}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", review)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["tags"] == ["fast", "reliable"]
    assert body["comment"] == "Great work!"
    assert body["role"] == "client_reviewing_seller"


@pytest.mark.asyncio
async def test_review_rating_boundaries(client: AsyncClient) -> None:
    """R8/R9: Rating 1 and 5 accepted, 0 and 6 rejected."""
    client_id, client_priv, seller_id, seller_priv, job_id = await _setup_and_complete_job(client)

    # Rating 1 — valid
    review = {"rating": 1}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", review)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 201

    # Need a new completed job for invalid rating tests
    client_id2, client_priv2, _, _, job_id2 = await _setup_and_complete_job(client)

    # Rating 0 — invalid
    review = {"rating": 0}
    headers = make_auth_headers(client_id2, client_priv2, "POST", f"/jobs/{job_id2}/reviews", review)
    resp = await client.post(f"/jobs/{job_id2}/reviews", json=review, headers=headers)
    assert resp.status_code == 422

    # Rating 6 — invalid
    review = {"rating": 6}
    headers = make_auth_headers(client_id2, client_priv2, "POST", f"/jobs/{job_id2}/reviews", review)
    resp = await client.post(f"/jobs/{job_id2}/reviews", json=review, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_reviews_for_job(client: AsyncClient) -> None:
    """R6: GET /jobs/{id}/reviews returns all reviews for a job."""
    client_id, client_priv, seller_id, seller_priv, job_id = await _setup_and_complete_job(client)

    # Both parties review
    review = {"rating": 5}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", review)
    await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)

    review = {"rating": 4}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/reviews", review)
    await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)

    resp = await client.get(f"/jobs/{job_id}/reviews")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_review_failed_job_allowed(client: AsyncClient) -> None:
    """R1: Can review a failed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    # Propose → accept → fund → start → fail
    data = {"seller_agent_id": seller_id, "max_budget": "100.00"}
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    await client.post(f"/jobs/{job_id}/fail", headers=headers)

    # Review the failed job
    review = {"rating": 2, "comment": "Did not deliver"}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", review)
    resp = await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_get_agent_reviews_pagination(client: AsyncClient) -> None:
    """R5: GET /agents/{id}/reviews with pagination."""
    client_id, client_priv, seller_id, seller_priv, job_id = await _setup_and_complete_job(client)

    review = {"rating": 5}
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/reviews", review)
    await client.post(f"/jobs/{job_id}/reviews", json=review, headers=headers)

    resp = await client.get(f"/agents/{seller_id}/reviews?limit=1&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


def test_recency_weight_recent() -> None:
    """R3: Reviews from last 30 days get 2x weight."""
    from app.services.review import _recency_weight
    from datetime import UTC, datetime, timedelta

    recent = datetime.now(UTC) - timedelta(days=5)
    assert _recency_weight(recent) == 2.0

    mid = datetime.now(UTC) - timedelta(days=60)
    assert _recency_weight(mid) == 1.5

    old = datetime.now(UTC) - timedelta(days=120)
    assert _recency_weight(old) == 1.0


def test_recency_weight_boundary_30_days() -> None:
    """R3: Exactly 30 days → 2x, 31 days → 1.5x."""
    from app.services.review import _recency_weight
    from datetime import UTC, datetime, timedelta

    at_30 = datetime.now(UTC) - timedelta(days=30)
    assert _recency_weight(at_30) == 2.0

    at_31 = datetime.now(UTC) - timedelta(days=31)
    assert _recency_weight(at_31) == 1.5

    at_90 = datetime.now(UTC) - timedelta(days=90)
    assert _recency_weight(at_90) == 1.5

    at_91 = datetime.now(UTC) - timedelta(days=91)
    assert _recency_weight(at_91) == 1.0
