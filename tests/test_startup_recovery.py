"""Tests for wallet startup recovery (_recover_wallet_tasks)."""

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.wallet import (
    DepositStatus,
    DepositTransaction,
    WithdrawalRequest,
    WithdrawalStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(db: AsyncSession) -> Agent:
    agent = Agent(
        agent_id=uuid.uuid4(),
        public_key="pk_" + uuid.uuid4().hex[:32],
        display_name="Test Agent",
        endpoint_url="https://example.com/webhook",
        webhook_secret="secret123",
    )
    db.add(agent)
    return agent


async def _make_deposit(
    db: AsyncSession, agent_id: uuid.UUID, status: DepositStatus,
) -> DepositTransaction:
    dep = DepositTransaction(
        deposit_tx_id=uuid.uuid4(),
        agent_id=agent_id,
        tx_hash="0x" + uuid.uuid4().hex,
        from_address="0x" + "aa" * 20,
        amount_usdc=Decimal("10.000000"),
        amount_credits=Decimal("10.00"),
        block_number=12345,
        status=status,
    )
    db.add(dep)
    return dep


async def _make_withdrawal(
    db: AsyncSession, agent_id: uuid.UUID, status: WithdrawalStatus,
) -> WithdrawalRequest:
    wd = WithdrawalRequest(
        withdrawal_id=uuid.uuid4(),
        agent_id=agent_id,
        amount=Decimal("5.00"),
        fee=Decimal("0.50"),
        net_payout=Decimal("4.50"),
        destination_address="0x" + "bb" * 20,
        status=status,
    )
    db.add(wd)
    return wd


class _MockSessionCtx:
    """Async context manager that yields a fixed session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        # Release any implicit transaction started by recovery queries,
        # otherwise read locks block the fixture's DROP TABLE teardown.
        if self._session.in_transaction():
            await self._session.commit()


def _mock_session_factory(session: AsyncSession) -> MagicMock:
    """Return a callable that mimics async_session() -> async context manager."""
    return MagicMock(side_effect=lambda: _MockSessionCtx(session))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirming_deposits_recovered(db_session: AsyncSession) -> None:
    """Deposits in CONFIRMING status are re-spawned on startup."""
    agent = _make_agent(db_session)
    dep = await _make_deposit(db_session, agent.agent_id, DepositStatus.CONFIRMING)
    await db_session.commit()

    mock_wait = AsyncMock()

    with patch("app.database.async_session", _mock_session_factory(db_session)), \
         patch("app.services.wallet._wait_and_credit_deposit", mock_wait), \
         patch.object(asyncio, "create_task") as mock_ct:
        from app.main import _recover_wallet_tasks
        await _recover_wallet_tasks()

    mock_wait.assert_called_once_with(dep.deposit_tx_id, dep.block_number)
    assert mock_ct.call_count == 1


@pytest.mark.asyncio
async def test_pending_and_processing_withdrawals_recovered(db_session: AsyncSession) -> None:
    """Withdrawals in PENDING or PROCESSING status are re-spawned on startup."""
    agent = _make_agent(db_session)
    wd_pending = await _make_withdrawal(db_session, agent.agent_id, WithdrawalStatus.PENDING)
    wd_processing = await _make_withdrawal(db_session, agent.agent_id, WithdrawalStatus.PROCESSING)
    await db_session.commit()

    mock_proc = AsyncMock()

    with patch("app.database.async_session", _mock_session_factory(db_session)), \
         patch("app.services.wallet._process_withdrawal", mock_proc), \
         patch.object(asyncio, "create_task") as mock_ct:
        from app.main import _recover_wallet_tasks
        await _recover_wallet_tasks()

    assert mock_proc.call_count == 2
    called_ids = {call[0][0] for call in mock_proc.call_args_list}
    assert called_ids == {wd_pending.withdrawal_id, wd_processing.withdrawal_id}
    assert mock_ct.call_count == 2


@pytest.mark.asyncio
async def test_completed_and_failed_not_recovered(db_session: AsyncSession) -> None:
    """Completed/failed deposits and withdrawals are NOT re-spawned."""
    agent = _make_agent(db_session)
    await _make_deposit(db_session, agent.agent_id, DepositStatus.CREDITED)
    await _make_deposit(db_session, agent.agent_id, DepositStatus.FAILED)
    await _make_deposit(db_session, agent.agent_id, DepositStatus.PENDING)
    await _make_withdrawal(db_session, agent.agent_id, WithdrawalStatus.COMPLETED)
    await _make_withdrawal(db_session, agent.agent_id, WithdrawalStatus.FAILED)
    await db_session.commit()

    with patch("app.database.async_session", _mock_session_factory(db_session)), \
         patch("app.services.wallet._wait_and_credit_deposit") as mock_wait, \
         patch("app.services.wallet._process_withdrawal") as mock_proc, \
         patch.object(asyncio, "create_task") as mock_ct:
        from app.main import _recover_wallet_tasks
        await _recover_wallet_tasks()

    mock_wait.assert_not_called()
    mock_proc.assert_not_called()
    mock_ct.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_empty_database(db_session: AsyncSession) -> None:
    """Recovery handles an empty DB gracefully (no tasks created, no errors)."""
    with patch("app.database.async_session", _mock_session_factory(db_session)), \
         patch("app.services.wallet._wait_and_credit_deposit") as mock_wait, \
         patch("app.services.wallet._process_withdrawal") as mock_proc, \
         patch.object(asyncio, "create_task") as mock_ct:
        from app.main import _recover_wallet_tasks
        await _recover_wallet_tasks()

    mock_wait.assert_not_called()
    mock_proc.assert_not_called()
    mock_ct.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_handles_db_error() -> None:
    """Recovery logs and does not crash when the DB raises an error."""
    broken_ctx = AsyncMock()
    broken_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("DB connection failed"))
    broken_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=broken_ctx)

    with patch("app.database.async_session", mock_factory), \
         patch("app.main.logger") as mock_logger:
        from app.main import _recover_wallet_tasks
        # Should not raise
        await _recover_wallet_tasks()

    mock_logger.exception.assert_called_once_with("Wallet task recovery failed")
