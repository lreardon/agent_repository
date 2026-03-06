"""Tests for escrow: fund, release, refund, double-spend prevention, insufficient balance."""

import json
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair, hash_criteria
from tests.conftest import make_agent_data, make_auth_headers


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    data = {"amount": amount}
    body = data
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", body)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200


async def _propose_and_accept(
    client: AsyncClient,
    client_id: str, client_priv: str,
    seller_id: str, seller_priv: str,
    budget: str = "100.00",
    acceptance_criteria: dict | None = None,
) -> str:
    """Propose a job and have seller accept. Returns job_id.

    Defaults to no acceptance_criteria so tests can use /complete and /fail freely.
    Pass acceptance_criteria explicitly for tests that exercise /verify.
    """
    data = {
        "seller_agent_id": seller_id,
        "max_budget": budget,
        "acceptance_criteria": acceptance_criteria,
    }
    body = data
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", body)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Seller accepts (provide criteria hash only if criteria are set)
    accept_data = {}
    if acceptance_criteria is not None:
        accept_data["acceptance_criteria_hash"] = hash_criteria(acceptance_criteria)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept",
                                accept_data if accept_data else b"")
    resp = await client.post(f"/jobs/{job_id}/accept",
                             json=accept_data if accept_data else None, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "agreed"
    return job_id


@pytest.mark.asyncio
async def test_fund_job(client: AsyncClient) -> None:
    """Happy path: fund escrow for an agreed job."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "funded"
    assert resp.json()["amount"] == "100.00"

    # Check client balance decreased
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "400.00"

    # Check job status is funded
    headers = make_auth_headers(client_id, client_priv, "GET", f"/jobs/{job_id}")
    resp = await client.get(f"/jobs/{job_id}", headers=headers)
    assert resp.json()["status"] == "funded"


@pytest.mark.asyncio
async def test_fund_insufficient_balance(client: AsyncClient) -> None:
    """Funding with insufficient balance fails."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "50.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Insufficient balance" in detail
    # Verify error includes actual and required amounts (ESC-6)
    assert "50" in detail  # actual balance
    assert "100" in detail  # required amount


@pytest.mark.asyncio
async def test_double_fund_prevention(client: AsyncClient) -> None:
    """Can't fund the same job twice."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Try to fund again
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fund_release_flow(client: AsyncClient) -> None:
    """Full flow: fund → start → deliver → complete (releases escrow to seller)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"

    # Deliver
    deliver_data = {"result": {"data": [1, 2, 3]}}
    body = deliver_data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivered"

    # Complete (release escrow) — for now any party can trigger
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    # Seller: started $10.00, no storage fee (0%), received $100.00 (escrow, no base fee)
    # = $10.00 + $100.00 = $110.00
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "110.00"

    # Client: started $500, funded $100 escrow (=$400), no base fee
    # = $400.00
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "400.00"


@pytest.mark.asyncio
async def test_fund_refund_flow(client: AsyncClient) -> None:
    """Fund → start → deliver → fail → refund."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    resp = await client.post(f"/jobs/{job_id}/start", headers=headers)
    assert resp.status_code == 200

    # Deliver
    deliver_data = {"result": {"data": "bad output"}}
    body = deliver_data
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", body)
    resp = await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    assert resp.status_code == 200

    # Fail (triggers refund)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # Client should be refunded — balance back to 500 (no base fee on failure)
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "500.00"


@pytest.mark.asyncio
async def test_seller_cannot_fund(client: AsyncClient) -> None:
    """Only the client can fund escrow."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Additional escrow tests (E1-E8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_escrow_platform_fee(client: AsyncClient) -> None:
    """E1/E2: Release escrow verifies base fee split and seller payout."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "1000.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "200.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"data": "output"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    # Seller: started $10.00, no storage fee, received $200.00 (no base fee)
    # = $10.00 + $200.00 = $210.00
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "210.00"


@pytest.mark.asyncio
async def test_refund_restores_full_balance(client: AsyncClient) -> None:
    """E3: Refund restores client's full balance (no fee deducted)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "300.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "150.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Fail triggers refund
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    await client.post(f"/jobs/{job_id}/fail", headers=headers)

    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "300.00"


@pytest.mark.asyncio
async def test_complete_rejects_non_client(client: AsyncClient) -> None:
    """Only client can complete (release escrow)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    result = {"result": {"done": True}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", result)
    await client.post(f"/jobs/{job_id}/deliver", json=result, headers=headers)

    # Seller tries to complete — should fail
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_refund_returns_seller_bond(client: AsyncClient) -> None:
    """Refund returns seller bond when job fails (seller_abort_penalty > 0)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    # Propose with seller_abort_penalty (bond)
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "client_abort_penalty": "0.00",
        "seller_abort_penalty": "20.00",
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Seller accepts
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200

    # Fund (collects seller bond)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Seller: 100 - 20 (bond) = 80
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "80.00"

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"data": "bad output"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Fail (triggers refund)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    # Client gets full escrow refund
    headers = make_auth_headers(client_id, client_priv, "GET", f"/agents/{client_id}/balance")
    resp = await client.get(f"/agents/{client_id}/balance", headers=headers)
    assert resp.json()["balance"] == "500.00"

    # Seller gets bond back: 80 + 20 = 100 (no storage fee)
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "100.00"


@pytest.mark.asyncio
async def test_escrow_audit_log_populated(client: AsyncClient, db_session) -> None:
    """E7: Escrow audit log entries created for a full fund→deliver→complete flow."""
    import uuid as _uuid
    from sqlalchemy import select as sa_select
    from app.models.escrow import EscrowAccount, EscrowAuditLog, EscrowAction

    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Start
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    # Deliver
    deliver_data = {"result": {"data": [1, 2, 3]}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    # Complete (release escrow)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    # Query audit log for this escrow
    escrow_result = await db_session.execute(
        sa_select(EscrowAccount).where(EscrowAccount.job_id == _uuid.UUID(job_id))
    )
    escrow = escrow_result.scalar_one()

    audit_result = await db_session.execute(
        sa_select(EscrowAuditLog)
        .where(EscrowAuditLog.escrow_id == escrow.escrow_id)
        .order_by(EscrowAuditLog.timestamp)
    )
    entries = list(audit_result.scalars().all())
    actions = [e.action for e in entries]

    # Unconditional assertions — these entries MUST exist
    assert len(entries) >= 3
    assert EscrowAction.CREATED in actions
    assert EscrowAction.FUNDED in actions
    assert EscrowAction.RELEASED in actions
    for entry in entries:
        assert entry.escrow_id is not None
        assert entry.action is not None
        assert entry.amount is not None


@pytest.mark.asyncio
async def test_release_returns_seller_bond(client: AsyncClient) -> None:
    """H4/ESC-2: Seller bond returned on release when seller_abort_penalty > 0."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "100.00")

    # Propose with seller_abort_penalty (bond)
    data = {
        "seller_agent_id": seller_id,
        "max_budget": "100.00",
        "client_abort_penalty": "0.00",
        "seller_abort_penalty": "20.00",
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Seller accepts
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    # Fund (collects seller bond of 20)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)

    # Seller: 100 - 20 (bond) = 80
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "80.00"

    # Start → deliver → complete (release)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)

    deliver_data = {"result": {"data": "output"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    await client.post(f"/jobs/{job_id}/complete", headers=headers)

    # Seller should get: 80 (remaining) + 100.00 (payout, no fee) + 20.00 (bond) = 200.00
    headers = make_auth_headers(seller_id, seller_priv, "GET", f"/agents/{seller_id}/balance")
    resp = await client.get(f"/agents/{seller_id}/balance", headers=headers)
    assert resp.json()["balance"] == "200.00"


# ---------------------------------------------------------------------------
# Double-release / double-refund prevention (M1 / ESC-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_release_prevention(client: AsyncClient) -> None:
    """Cannot release escrow twice (completing an already completed job)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund → start → deliver → complete
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)
    deliver_data = {"result": {"data": "output"}}
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/deliver", deliver_data)
    await client.post(f"/jobs/{job_id}/deliver", json=deliver_data, headers=headers)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 200

    # Try to complete again — should fail (already completed)
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/complete", b"")
    resp = await client.post(f"/jobs/{job_id}/complete", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_double_refund_prevention(client: AsyncClient) -> None:
    """Cannot refund escrow twice (failing an already failed job)."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")
    await _deposit(client, seller_id, seller_priv, "10.00")
    job_id = await _propose_and_accept(client, client_id, client_priv, seller_id, seller_priv, "100.00")

    # Fund → start → fail
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    await client.post(f"/jobs/{job_id}/fund", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/start", b"")
    await client.post(f"/jobs/{job_id}/start", headers=headers)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 200

    # Try to fail again — should fail (already failed)
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/fail", b"")
    resp = await client.post(f"/jobs/{job_id}/fail", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_fund_not_agreed_job_fails(client: AsyncClient) -> None:
    """Cannot fund a job that isn't in agreed status."""
    client_id, client_priv = await _create_agent(client)
    seller_id, _ = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "500.00")

    # Propose but don't accept
    data = {"seller_agent_id": seller_id, "max_budget": "100.00"}
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", data)
    resp = await client.post("/jobs", json=data, headers=headers)
    job_id = resp.json()["job_id"]

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 409
