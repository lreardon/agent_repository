"""Tests for abort penalties and performance bond feature.

Covers: penalty negotiation, seller bond escrow, client abort, seller abort,
verification retry loop, deadline forfeiture, zero-penalty backward compat,
and escrow audit trails.
"""

import base64
import json
import os
import shutil
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.escrow import EscrowAccount, EscrowAuditLog, EscrowAction
from app.models.job import Job, JobStatus
from app.utils.crypto import generate_keypair, hash_criteria
from tests.conftest import make_agent_data, make_auth_headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS_SCRIPT = base64.b64encode(b"import sys; sys.exit(0)").decode()
_DEFAULT_CRITERIA = {"script": _PASS_SCRIPT, "runtime": "python:3.13"}


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    data = {"amount": amount}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200


async def _get_balance(client: AsyncClient, agent_id: str, priv: str) -> Decimal:
    headers = make_auth_headers(agent_id, priv, "GET", f"/agents/{agent_id}/balance")
    resp = await client.get(f"/agents/{agent_id}/balance", headers=headers)
    return Decimal(resp.json()["balance"])


async def _propose_with_penalties(
    client: AsyncClient,
    client_id: str,
    client_priv: str,
    seller_id: str,
    budget: str = "100.00",
    client_abort_penalty: str = "10.00",
    seller_abort_penalty: str = "20.00",
    criteria: dict | None = None,
) -> dict:
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "client_abort_penalty": client_abort_penalty,
        "seller_abort_penalty": seller_abort_penalty,
        "acceptance_criteria": criteria,
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    return resp.json()


async def _fund_job_with_penalties(
    client: AsyncClient,
    client_id: str,
    client_priv: str,
    seller_id: str,
    seller_priv: str,
    budget: str = "100.00",
    client_abort_penalty: str = "10.00",
    seller_abort_penalty: str = "20.00",
    criteria: dict | None = None,
) -> str:
    """Create, accept, and fund a job with penalties. Returns job_id."""
    job = await _propose_with_penalties(
        client, client_id, client_priv, seller_id,
        budget, client_abort_penalty, seller_abort_penalty, criteria,
    )
    job_id = job["job_id"]

    # Seller accepts
    accept_data = {}
    if criteria:
        accept_data["acceptance_criteria_hash"] = hash_criteria(criteria)
    body = accept_data if accept_data else b""
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", body)
    resp = await client.post(f"/jobs/{job_id}/accept", json=accept_data if accept_data else None, headers=headers)
    assert resp.status_code == 200

    # Client funds
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    return job_id


# ---------------------------------------------------------------------------
# 1. Propose job with abort penalties
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_job_with_penalties(client: AsyncClient) -> None:
    """Penalties are stored and returned in job response."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job = await _propose_with_penalties(client, client_id, client_priv, seller_id)
    assert job["client_abort_penalty"] == "10.00"
    assert job["seller_abort_penalty"] == "20.00"


@pytest.mark.asyncio
async def test_propose_penalty_exceeds_budget_rejected(client: AsyncClient) -> None:
    """client_abort_penalty > max_budget is rejected."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    data = {
        "seller_agent_id": seller_id,
        "max_budget": "50.00",
        "client_abort_penalty": "60.00",
        "seller_abort_penalty": "10.00",
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_propose_seller_penalty_exceeds_budget_rejected(client: AsyncClient) -> None:
    """seller_abort_penalty > max_budget is rejected."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    data = {
        "seller_agent_id": seller_id,
        "max_budget": "50.00",
        "client_abort_penalty": "10.00",
        "seller_abort_penalty": "60.00",
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. Counter-propose with different penalties
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counter_with_penalties(client: AsyncClient) -> None:
    """Counter-proposal can change penalty amounts."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job = await _propose_with_penalties(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    # Seller counters with different penalties
    counter = {
        "proposed_price": "90.00",
        "client_abort_penalty": "15.00",
        "seller_abort_penalty": "25.00",
    }
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["client_abort_penalty"] == "15.00"
    assert data["seller_abort_penalty"] == "25.00"
    assert data["agreed_price"] == "90.00"


# ---------------------------------------------------------------------------
# 3. Fund job deducts seller bond
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fund_deducts_seller_bond(client: AsyncClient) -> None:
    """Funding a job with seller_abort_penalty deducts bond from seller's balance."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", seller_abort_penalty="20.00",
    )

    # Client: 500 - 100 (escrow) = 400
    assert await _get_balance(client, client_id, client_priv) == Decimal("400.00")
    # Seller: 100 - 20 (bond) = 80
    assert await _get_balance(client, seller_id, seller_priv) == Decimal("80.00")


# ---------------------------------------------------------------------------
# 4. Fund fails if seller can't cover bond
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fund_fails_seller_insufficient_for_bond(client: AsyncClient) -> None:
    """Funding fails if seller doesn't have enough balance for the performance bond."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "5.00")  # Not enough for $20 bond

    job = await _propose_with_penalties(
        client, client_id, client_priv, seller_id,
        seller_abort_penalty="20.00",
    )
    job_id = job["job_id"]

    # Accept
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund should fail — seller can't cover bond
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 422
    assert "Seller has insufficient balance for performance bond" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Client aborts — correct penalty distribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_abort_penalty_distribution(client: AsyncClient) -> None:
    """Client aborts: pays penalty to seller, gets remainder. Seller bond returned."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", client_abort_penalty="10.00", seller_abort_penalty="20.00",
    )

    # Start the job
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Client aborts
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Client: started 500, escrowed 100, got back 90 (100-10 penalty) → 490
    assert await _get_balance(client, client_id, client_priv) == Decimal("490.00")
    # Seller: started 100, bonded 20, got penalty 10 + bond back 20 → 110
    assert await _get_balance(client, seller_id, seller_priv) == Decimal("110.00")


# ---------------------------------------------------------------------------
# 6. Seller aborts — correct penalty distribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seller_abort_penalty_distribution(client: AsyncClient) -> None:
    """Seller aborts: loses bond. Client gets full refund + bond."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", client_abort_penalty="10.00", seller_abort_penalty="20.00",
    )

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Seller aborts
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Client: 500 - 100 (escrow) + 100 (refund) + 20 (bond forfeit) → 520
    assert await _get_balance(client, client_id, client_priv) == Decimal("520.00")
    # Seller: 100 - 20 (bond) → 80 (bond lost)
    assert await _get_balance(client, seller_id, seller_priv) == Decimal("80.00")


# ---------------------------------------------------------------------------
# 7. Verification fail → back to IN_PROGRESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_fail_returns_to_in_progress(client: AsyncClient) -> None:
    """Failed verification returns job to in_progress, not failed."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    # Use a script that always fails
    fail_script = base64.b64encode(b"import sys; sys.exit(1)").decode()
    criteria = {"script": fail_script, "runtime": "python:3.13"}

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", seller_abort_penalty="20.00", criteria=criteria,
    )

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"answer": "wrong"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Verify — should fail but return to in_progress
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"]["status"] == "in_progress"
    assert data["retry_allowed"] is True


# ---------------------------------------------------------------------------
# 8. Redeliver after failed verification → re-verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(
    bool(os.environ.get("CI")) or not shutil.which("docker"),
    reason="Docker sandbox not available in CI",
)
async def test_redeliver_after_failed_verify(client: AsyncClient) -> None:
    """Seller can redeliver after failed verification, then verify again."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    # Script that passes if result has key "correct"
    script = base64.b64encode(
        b'import json,sys\ndata=json.load(open("/input/result.json"))\nsys.exit(0 if "correct" in data else 1)'
    ).decode()
    criteria = {"script": script, "runtime": "python:3.13"}

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", seller_abort_penalty="20.00", criteria=criteria,
    )

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # First delivery — wrong answer
    deliver_data = {"result": {"wrong": True}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Verify — fails
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.json()["job"]["status"] == "in_progress"

    # Second delivery — correct answer
    deliver_data = {"result": {"correct": True}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    assert resp.status_code == 200

    # Verify — passes
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/verify", b"")
    resp = await client.post(f"/jobs/{job_id}/verify", headers=headers)
    assert resp.json()["job"]["status"] == "completed"


# ---------------------------------------------------------------------------
# 9. (Deadline expiry tested in test_deadline_queue.py — bond forfeiture
#     is handled by abort_job(is_deadline=True))
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 10. Zero-penalty jobs (backward compatible)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_penalty_job(client: AsyncClient) -> None:
    """Jobs without penalties work as before — abort is a clean cancel."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", client_abort_penalty="0.00", seller_abort_penalty="0.00",
    )

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Client aborts — should just be a clean refund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Full refund, no penalties
    assert await _get_balance(client, client_id, client_priv) == Decimal("500.00")
    assert await _get_balance(client, seller_id, seller_priv) == Decimal("10.00")


# ---------------------------------------------------------------------------
# 11. Abort from invalid state rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_from_proposed_rejected(client: AsyncClient) -> None:
    """Cannot abort a job that isn't funded yet."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job = await _propose_with_penalties(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_abort_from_completed_rejected(client: AsyncClient) -> None:
    """Cannot abort a completed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")

    # Create a no-criteria job and complete it
    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="50.00", client_abort_penalty="0.00", seller_abort_penalty="0.00",
    )

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    deliver_data = {"result": {"done": True}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    # Try to abort
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 12. Abort penalty in negotiation log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_penalties_in_negotiation_log(client: AsyncClient) -> None:
    """Penalty amounts appear in the negotiation log during counters."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")

    job = await _propose_with_penalties(client, client_id, client_priv, seller_id)
    job_id = job["job_id"]

    counter = {
        "proposed_price": "80.00",
        "client_abort_penalty": "5.00",
        "seller_abort_penalty": "15.00",
    }
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/counter", counter)
    resp = await client.post(f"/jobs/{job_id}/counter", json=counter, headers=headers)
    log = resp.json()["negotiation_log"]

    # The counter entry should have penalty info
    counter_entry = log[-1]
    assert counter_entry["client_abort_penalty"] == "5.00"
    assert counter_entry["seller_abort_penalty"] == "15.00"


# ---------------------------------------------------------------------------
# 13. Escrow audit trail for abort flows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escrow_audit_trail_on_seller_abort(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Seller abort produces correct audit trail entries."""
    import uuid as _uuid
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", client_abort_penalty="10.00", seller_abort_penalty="20.00",
    )

    # Start and abort by seller
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/abort", b"")
    await client.post(f"/jobs/{job_id}/abort", headers=headers)

    # Check audit trail
    escrow_result = await db_session.execute(
        select(EscrowAccount).where(EscrowAccount.job_id == _uuid.UUID(job_id))
    )
    escrow = escrow_result.scalar_one()

    audit_result = await db_session.execute(
        select(EscrowAuditLog)
        .where(EscrowAuditLog.escrow_id == escrow.escrow_id)
        .order_by(EscrowAuditLog.timestamp)
    )
    entries = list(audit_result.scalars().all())
    actions = [e.action for e in entries]

    assert EscrowAction.CREATED in actions
    assert EscrowAction.FUNDED in actions
    assert EscrowAction.SELLER_BOND_FUNDED in actions
    assert EscrowAction.ABORT_SELLER in actions
    assert EscrowAction.BOND_FORFEITED in actions


# ---------------------------------------------------------------------------
# 14. Seller bond returned on successful completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bond_returned_on_completion(client: AsyncClient) -> None:
    """Seller's bond is returned when job completes successfully (no criteria)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
        budget="100.00", client_abort_penalty="10.00", seller_abort_penalty="20.00",
    )

    # Start, deliver, complete (no criteria — manual complete)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    deliver_data = {"result": {"done": True}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200

    # Seller should have: 80 (remaining after bond) + escrow payout + bond back
    # Escrow payout = 100 - 0.50 (0.5% seller base fee) = 99.50
    # But seller also paid storage fee (~0.01) on delivery
    # Bond (20) returned. Total: 80 + 99.50 + 20 - 0.01 = 199.49
    seller_bal = await _get_balance(client, seller_id, seller_priv)
    assert seller_bal == Decimal("199.49")


# ---------------------------------------------------------------------------
# 15. Non-party cannot abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_third_party_cannot_abort(client: AsyncClient) -> None:
    """A non-party agent cannot abort a job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)
    third_id, third_priv = await _create_agent(client)
    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    job_id = await _fund_job_with_penalties(
        client, client_id, client_priv, seller_id, seller_priv,
    )

    headers = make_auth_headers(third_id, third_priv, "POST", f"/jobs/{job_id}/abort", b"")
    resp = await client.post(f"/jobs/{job_id}/abort", headers=headers)
    assert resp.status_code == 403
