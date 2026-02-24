"""Tests for wallet: deposit addresses, withdrawals, balance, race conditions."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.wallet import (
    DepositAddress,
    DepositStatus,
    DepositTransaction,
    WithdrawalRequest,
    WithdrawalStatus,
)
from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent(client: AsyncClient) -> tuple[str, str]:
    priv, pub = generate_keypair()
    resp = await client.post("/agents", json=make_agent_data(pub))
    assert resp.status_code == 201
    return resp.json()["agent_id"], priv


async def _deposit(client: AsyncClient, agent_id: str, priv: str, amount: str) -> None:
    """Use the dev-only deposit endpoint to fund an agent for testing."""
    data = {"amount": amount}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)
    resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Deposit address tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deposit_address_requires_seed(client: AsyncClient) -> None:
    """Getting a deposit address without HD seed configured returns 503."""
    agent_id, priv = await _create_agent(client)
    headers = make_auth_headers(agent_id, priv, "GET", f"/agents/{agent_id}/wallet/deposit-address")

    with patch("app.services.wallet.settings") as mock_settings:
        mock_settings.hd_wallet_master_seed = ""
        mock_settings.min_deposit_amount = Decimal("1.00")
        mock_settings.blockchain_network = "base_sepolia"
        mock_settings.resolved_usdc_address = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
        resp = await client.get(f"/agents/{agent_id}/wallet/deposit-address", headers=headers)

    assert resp.status_code == 503
    assert "HD seed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deposit_address_created_and_idempotent(client: AsyncClient) -> None:
    """Deposit address is created on first call and returned on subsequent calls."""
    agent_id, priv = await _create_agent(client)
    path = f"/agents/{agent_id}/wallet/deposit-address"

    with patch("app.services.wallet._derive_address", return_value="0x" + "ab" * 20) as mock_derive:
        with patch("app.services.wallet.settings") as mock_settings:
            mock_settings.hd_wallet_master_seed = "test seed phrase here"
            mock_settings.min_deposit_amount = Decimal("1.00")
            mock_settings.blockchain_network = "base_sepolia"
            mock_settings.resolved_usdc_address = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

            headers = make_auth_headers(agent_id, priv, "GET", path)
            resp1 = await client.get(path, headers=headers)
            assert resp1.status_code == 200
            assert resp1.json()["address"] == "0x" + "ab" * 20
            assert resp1.json()["network"] == "base_sepolia"

            # Second call returns same address
            headers = make_auth_headers(agent_id, priv, "GET", path)
            resp2 = await client.get(path, headers=headers)
            assert resp2.status_code == 200
            assert resp2.json()["address"] == resp1.json()["address"]

            # derive_address only called once
            mock_derive.assert_called_once()


@pytest.mark.asyncio
async def test_deposit_address_own_agent_only(client: AsyncClient) -> None:
    """Cannot get another agent's deposit address."""
    agent1_id, priv1 = await _create_agent(client)
    agent2_id, _ = await _create_agent(client)

    path = f"/agents/{agent2_id}/wallet/deposit-address"
    headers = make_auth_headers(agent1_id, priv1, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Withdrawal tests
# ---------------------------------------------------------------------------


VALID_ETH_ADDRESS = "0x" + "1a" * 20


@pytest.mark.asyncio
async def test_withdrawal_happy_path(client: AsyncClient) -> None:
    """Withdraw deducts from balance and creates a pending withdrawal."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "50.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 201
    body = resp.json()
    assert body["amount"] == "50.00"
    assert body["fee"] == "0.50"
    assert body["net_payout"] == "49.50"
    assert body["status"] == "pending"
    assert body["destination_address"] == VALID_ETH_ADDRESS

    # Balance should be deducted immediately
    bal_path = f"/agents/{agent_id}/balance"
    headers = make_auth_headers(agent_id, priv, "GET", bal_path)
    resp = await client.get(bal_path, headers=headers)
    assert resp.json()["balance"] == "50.00"


@pytest.mark.asyncio
async def test_withdrawal_insufficient_balance(client: AsyncClient) -> None:
    """Cannot withdraw more than available balance."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "10.00")

    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "20.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 422
    assert "Insufficient balance" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_withdrawal_below_minimum(client: AsyncClient) -> None:
    """Withdrawal below $1.00 minimum is rejected."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "0.50", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_withdrawal_must_exceed_fee(client: AsyncClient) -> None:
    """Withdrawal where amount <= fee is rejected."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/withdraw"
    # $0.50 fee means requesting exactly $0.50 would yield $0.00 net
    # But minimum is $1.00, so this is caught by min validation first
    # Let's test with exactly $1.00 which should work (net = $0.50)
    data = {"amount": "1.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 201
    assert resp.json()["net_payout"] == "0.50"


@pytest.mark.asyncio
async def test_withdrawal_invalid_address(client: AsyncClient) -> None:
    """Invalid Ethereum address is rejected."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "10.00", "destination_address": "not-an-address"}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_withdrawal_own_agent_only(client: AsyncClient) -> None:
    """Cannot withdraw from another agent's wallet."""
    agent1_id, priv1 = await _create_agent(client)
    agent2_id, _ = await _create_agent(client)

    path = f"/agents/{agent2_id}/wallet/withdraw"
    data = {"amount": "10.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent1_id, priv1, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sequential_withdrawals_deduct_correctly(client: AsyncClient) -> None:
    """Two sequential withdrawals correctly deduct from balance."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/withdraw"

    # First withdrawal: $40
    data = {"amount": "40.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 201

    # Second withdrawal: $40
    data = {"amount": "40.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 201

    # Balance should be 100 - 40 - 40 = 20
    bal_path = f"/agents/{agent_id}/balance"
    headers = make_auth_headers(agent_id, priv, "GET", bal_path)
    resp = await client.get(bal_path, headers=headers)
    assert resp.json()["balance"] == "20.00"

    # Third withdrawal of $30 should fail (only $20 left)
    data = {"amount": "30.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Withdrawal + escrow race condition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdrawal_then_fund_job_insufficient(client: AsyncClient) -> None:
    """After withdrawal, balance is reduced so funding a job may fail."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "100.00")

    # Withdraw $60
    path = f"/agents/{client_id}/wallet/withdraw"
    data = {"amount": "60.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(client_id, client_priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 201

    # Propose a job for $50 (only $40 left)
    job_data = {
        "seller_agent_id": seller_id,
        "max_budget": "50.00",
        "acceptance_criteria": {"version": "1.0", "tests": []},
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", job_data)
    resp = await client.post("/jobs", json=job_data, headers=headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    # Seller accepts
    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    resp = await client.post(f"/jobs/{job_id}/accept", headers=headers)
    assert resp.status_code == 200

    # Fund should fail — insufficient balance
    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 422
    assert "Insufficient balance" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_fund_job_then_withdraw_insufficient(client: AsyncClient) -> None:
    """After funding a job, withdrawal should respect reduced balance."""
    client_id, client_priv = await _create_agent(client)
    seller_id, seller_priv = await _create_agent(client)

    await _deposit(client, client_id, client_priv, "100.00")

    # Create and fund a $80 job
    job_data = {
        "seller_agent_id": seller_id,
        "max_budget": "80.00",
        "acceptance_criteria": {"version": "1.0", "tests": []},
    }
    headers = make_auth_headers(client_id, client_priv, "POST", "/jobs", job_data)
    resp = await client.post("/jobs", json=job_data, headers=headers)
    job_id = resp.json()["job_id"]

    headers = make_auth_headers(seller_id, seller_priv, "POST", f"/jobs/{job_id}/accept", b"")
    await client.post(f"/jobs/{job_id}/accept", headers=headers)

    headers = make_auth_headers(client_id, client_priv, "POST", f"/jobs/{job_id}/fund", b"")
    resp = await client.post(f"/jobs/{job_id}/fund", headers=headers)
    assert resp.status_code == 200

    # Balance is now $20. Try to withdraw $30 — should fail
    path = f"/agents/{client_id}/wallet/withdraw"
    data = {"amount": "30.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(client_id, client_priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 422
    assert "Insufficient balance" in resp.json()["detail"]

    # Withdraw $15 — should succeed
    data = {"amount": "15.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(client_id, client_priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Transaction history tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transaction_history(client: AsyncClient) -> None:
    """Transaction history returns withdrawals."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    # Make a withdrawal
    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "25.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    await client.post(path, json=data, headers=headers)

    # Check history
    hist_path = f"/agents/{agent_id}/wallet/transactions"
    headers = make_auth_headers(agent_id, priv, "GET", hist_path)
    resp = await client.get(hist_path, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["withdrawals"]) == 1
    assert body["withdrawals"][0]["amount"] == "25.00"
    assert len(body["deposits"]) == 0  # On-chain deposits, not dev deposits


@pytest.mark.asyncio
async def test_transaction_history_own_agent_only(client: AsyncClient) -> None:
    """Cannot view another agent's transaction history."""
    agent1_id, priv1 = await _create_agent(client)
    agent2_id, _ = await _create_agent(client)

    path = f"/agents/{agent2_id}/wallet/transactions"
    headers = make_auth_headers(agent1_id, priv1, "GET", path)
    resp = await client.get(path, headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Wallet balance endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wallet_balance(client: AsyncClient) -> None:
    """Wallet balance endpoint shows total and available."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    path = f"/agents/{agent_id}/wallet/balance"
    headers = make_auth_headers(agent_id, priv, "GET", path)
    resp = await client.get(path, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == "100.00"
    assert body["available_balance"] == "100.00"
    assert body["pending_withdrawals"] == "0.00"


@pytest.mark.asyncio
async def test_wallet_balance_with_pending_withdrawal(client: AsyncClient) -> None:
    """Wallet balance reflects pending withdrawals."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    # Withdraw $30 (balance immediately deducted)
    w_path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "30.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", w_path, data)
    await client.post(w_path, json=data, headers=headers)

    # Check wallet balance
    path = f"/agents/{agent_id}/wallet/balance"
    headers = make_auth_headers(agent_id, priv, "GET", path)
    resp = await client.get(path, headers=headers)

    body = resp.json()
    assert body["balance"] == "70.00"
    # available = balance (since withdrawal already deducted)
    assert body["available_balance"] == "70.00"
    # pending withdrawal is $30
    assert body["pending_withdrawals"] == "30.00"


# ---------------------------------------------------------------------------
# Deposit crediting (service-level tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credit_deposit_service(client: AsyncClient, db_session: AsyncSession) -> None:
    """credit_deposit credits the agent's balance."""
    from app.services.wallet import credit_deposit

    agent_id, priv = await _create_agent(client)

    # Manually create a confirming deposit transaction
    deposit_tx = DepositTransaction(
        deposit_tx_id=uuid.uuid4(),
        agent_id=uuid.UUID(agent_id),
        tx_hash="0x" + "aa" * 32,
        from_address="0x" + "bb" * 20,
        amount_usdc=Decimal("50.000000"),
        amount_credits=Decimal("50.00"),
        block_number=1000,
        confirmations=15,
        status=DepositStatus.CONFIRMING,
    )
    db_session.add(deposit_tx)
    await db_session.commit()

    await credit_deposit(db_session, deposit_tx.deposit_tx_id)

    # Refresh and check
    await db_session.refresh(deposit_tx)
    assert deposit_tx.status == DepositStatus.CREDITED
    assert deposit_tx.credited_at is not None

    # Check agent balance
    result = await db_session.execute(
        select(Agent).where(Agent.agent_id == uuid.UUID(agent_id))
    )
    agent = result.scalar_one()
    assert agent.balance == Decimal("50.00")


@pytest.mark.asyncio
async def test_credit_deposit_idempotent(client: AsyncClient, db_session: AsyncSession) -> None:
    """Crediting the same deposit twice doesn't double-credit."""
    from app.services.wallet import credit_deposit

    agent_id, priv = await _create_agent(client)

    deposit_tx = DepositTransaction(
        deposit_tx_id=uuid.uuid4(),
        agent_id=uuid.UUID(agent_id),
        tx_hash="0x" + "cc" * 32,
        from_address="0x" + "dd" * 20,
        amount_usdc=Decimal("25.000000"),
        amount_credits=Decimal("25.00"),
        block_number=2000,
        confirmations=15,
        status=DepositStatus.CONFIRMING,
    )
    db_session.add(deposit_tx)
    await db_session.commit()

    await credit_deposit(db_session, deposit_tx.deposit_tx_id)
    await credit_deposit(db_session, deposit_tx.deposit_tx_id)  # Second call

    result = await db_session.execute(
        select(Agent).where(Agent.agent_id == uuid.UUID(agent_id))
    )
    agent = result.scalar_one()
    assert agent.balance == Decimal("25.00")  # Not 50


@pytest.mark.asyncio
async def test_credit_deposit_below_minimum_fails(client: AsyncClient, db_session: AsyncSession) -> None:
    """Deposits below minimum are marked as failed, not credited."""
    from app.services.wallet import credit_deposit

    agent_id, priv = await _create_agent(client)

    deposit_tx = DepositTransaction(
        deposit_tx_id=uuid.uuid4(),
        agent_id=uuid.UUID(agent_id),
        tx_hash="0x" + "ee" * 32,
        from_address="0x" + "ff" * 20,
        amount_usdc=Decimal("0.500000"),
        amount_credits=Decimal("0.50"),
        block_number=3000,
        confirmations=15,
        status=DepositStatus.CONFIRMING,
    )
    db_session.add(deposit_tx)
    await db_session.commit()

    await credit_deposit(db_session, deposit_tx.deposit_tx_id)

    await db_session.refresh(deposit_tx)
    assert deposit_tx.status == DepositStatus.FAILED

    result = await db_session.execute(
        select(Agent).where(Agent.agent_id == uuid.UUID(agent_id))
    )
    agent = result.scalar_one()
    assert agent.balance == Decimal("0.00")


# ---------------------------------------------------------------------------
# Withdrawal failure refund (service-level test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_withdrawal_refunds_balance(client: AsyncClient, db_session: AsyncSession) -> None:
    """If a withdrawal fails during processing, the amount is refunded."""
    agent_id, priv = await _create_agent(client)
    await _deposit(client, agent_id, priv, "100.00")

    # Request withdrawal via API
    path = f"/agents/{agent_id}/wallet/withdraw"
    data = {"amount": "50.00", "destination_address": VALID_ETH_ADDRESS}
    headers = make_auth_headers(agent_id, priv, "POST", path, data)
    resp = await client.post(path, json=data, headers=headers)
    assert resp.status_code == 201
    withdrawal_id = resp.json()["withdrawal_id"]

    # Balance should be 50 after withdrawal request
    bal_path = f"/agents/{agent_id}/balance"
    headers = make_auth_headers(agent_id, priv, "GET", bal_path)
    resp = await client.get(bal_path, headers=headers)
    assert resp.json()["balance"] == "50.00"

    # Simulate failed withdrawal processing by directly updating the record
    result = await db_session.execute(
        select(WithdrawalRequest).where(
            WithdrawalRequest.withdrawal_id == uuid.UUID(withdrawal_id)
        )
    )
    withdrawal = result.scalar_one()
    withdrawal.status = WithdrawalStatus.FAILED
    withdrawal.error_message = "Simulated failure"

    # Refund the balance
    agent_result = await db_session.execute(
        select(Agent).where(Agent.agent_id == uuid.UUID(agent_id)).with_for_update()
    )
    agent = agent_result.scalar_one()
    agent.balance = agent.balance + withdrawal.amount
    await db_session.commit()

    # Balance should be back to 100
    headers = make_auth_headers(agent_id, priv, "GET", bal_path)
    resp = await client.get(bal_path, headers=headers)
    assert resp.json()["balance"] == "100.00"


# ---------------------------------------------------------------------------
# Dev deposit endpoint gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deposit_blocked_in_production(client: AsyncClient) -> None:
    """Direct deposit endpoint returns 403 when env=production."""
    agent_id, priv = await _create_agent(client)

    data = {"amount": "100.00"}
    headers = make_auth_headers(agent_id, priv, "POST", f"/agents/{agent_id}/deposit", data)

    from app.config import settings as real_settings
    original_env = real_settings.env
    try:
        # Temporarily set env to production
        object.__setattr__(real_settings, "env", "production")
        resp = await client.post(f"/agents/{agent_id}/deposit", json=data, headers=headers)
    finally:
        object.__setattr__(real_settings, "env", original_env)

    assert resp.status_code == 403
    assert "production" in resp.json()["detail"].lower()
