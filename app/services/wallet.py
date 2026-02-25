"""Wallet service: HD address derivation, deposits, withdrawals, chain monitoring."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.wallet import (
    DepositAddress,
    DepositStatus,
    DepositTransaction,
    WithdrawalRequest,
    WithdrawalStatus,
)

logger = logging.getLogger(__name__)

# USDC has 6 decimals on-chain
USDC_DECIMALS = 6
USDC_SCALE = 10**USDC_DECIMALS

# Minimal ERC-20 ABI for Transfer event + transfer function
ERC20_TRANSFER_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _derive_address(index: int) -> str:
    """Derive an Ethereum address from the HD master seed at the given index.

    Uses BIP-44 path: m/44'/60'/0'/0/{index}
    (60 = Ethereum coin type; works for all EVM chains including Base)
    """
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(
        settings.hd_wallet_master_seed,
        account_path=f"m/44'/60'/0'/0/{index}",
    )
    return acct.address


def _usdc_to_credits(usdc_raw: int) -> Decimal:
    """Convert raw USDC (6 decimals) to platform credits (2 decimals)."""
    return Decimal(usdc_raw) / Decimal(USDC_SCALE)


def _credits_to_usdc_raw(credits: Decimal) -> int:
    """Convert platform credits to raw USDC units."""
    return int(credits * USDC_SCALE)


# ---------------------------------------------------------------------------
# Deposit address management
# ---------------------------------------------------------------------------


async def get_or_create_deposit_address(
    db: AsyncSession, agent_id: uuid.UUID
) -> DepositAddress:
    """Return the agent's unique deposit address, creating one if needed."""
    result = await db.execute(
        select(DepositAddress).where(DepositAddress.agent_id == agent_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    # Determine next derivation index
    max_idx_result = await db.execute(
        select(func.coalesce(func.max(DepositAddress.derivation_index), -1))
    )
    next_index = max_idx_result.scalar() + 1

    if not settings.hd_wallet_master_seed:
        raise HTTPException(
            status_code=503,
            detail="Wallet infrastructure not configured (missing HD seed)",
        )

    address = _derive_address(next_index)

    deposit_addr = DepositAddress(
        deposit_address_id=uuid.uuid4(),
        agent_id=agent_id,
        address=address,
        derivation_index=next_index,
    )
    db.add(deposit_addr)
    await db.commit()
    await db.refresh(deposit_addr)
    return deposit_addr


# ---------------------------------------------------------------------------
# Balance helpers
# ---------------------------------------------------------------------------


async def get_pending_withdrawal_total(
    db: AsyncSession, agent_id: uuid.UUID
) -> Decimal:
    """Sum of all pending/processing withdrawals for an agent."""
    result = await db.execute(
        select(func.coalesce(func.sum(WithdrawalRequest.amount), Decimal("0.00")))
        .where(WithdrawalRequest.agent_id == agent_id)
        .where(
            WithdrawalRequest.status.in_([
                WithdrawalStatus.PENDING,
                WithdrawalStatus.PROCESSING,
            ])
        )
    )
    return result.scalar()


async def get_available_balance(
    db: AsyncSession, agent_id: uuid.UUID
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (total_balance, available_balance, pending_withdrawals).

    available = balance - pending_withdrawals.
    Balance already reflects immediate deductions from withdrawals,
    so pending_withdrawals here captures the amount still in-flight
    that hasn't been finalized (completed or refunded).

    NOTE: For accurate race-condition-free reads, callers that mutate
    balance should SELECT FOR UPDATE the agent row first.
    """
    agent_result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    pending = await get_pending_withdrawal_total(db, agent_id)
    # Balance already has withdrawals deducted, so available = balance
    # pending is informational (shows how much is still in-flight)
    return agent.balance, agent.balance, pending


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


async def request_withdrawal(
    db: AsyncSession,
    agent_id: uuid.UUID,
    amount: Decimal,
    destination_address: str,
) -> WithdrawalRequest:
    """Request a withdrawal. Immediately deducts from balance to prevent races.

    The amount is the total deducted from the agent's balance.
    The agent receives (amount - fee) in USDC.
    """
    fee = settings.withdrawal_flat_fee
    net_payout = amount - fee

    if net_payout <= Decimal("0"):
        raise HTTPException(
            status_code=422,
            detail=f"Withdrawal amount must exceed the ${fee} fee",
        )

    if amount < settings.min_withdrawal_amount:
        raise HTTPException(
            status_code=422,
            detail=f"Minimum withdrawal is ${settings.min_withdrawal_amount}",
        )

    if amount > settings.max_withdrawal_amount:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum withdrawal is ${settings.max_withdrawal_amount}",
        )

    # Lock the agent's balance row — serializes with fund_job and other withdrawals
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id).with_for_update()
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    if agent.balance < amount:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient balance: ${agent.balance} < ${amount}",
        )

    # Immediately deduct — this is the key race-condition protection
    agent.balance = agent.balance - amount

    withdrawal = WithdrawalRequest(
        withdrawal_id=uuid.uuid4(),
        agent_id=agent_id,
        amount=amount,
        fee=fee,
        net_payout=net_payout,
        destination_address=destination_address,
        status=WithdrawalStatus.PENDING,
    )
    db.add(withdrawal)
    await db.commit()
    await db.refresh(withdrawal)

    logger.info(
        "Withdrawal %s created: agent=%s amount=%s fee=%s net=%s dest=%s",
        withdrawal.withdrawal_id, agent_id, amount, fee, net_payout, destination_address,
    )

    # Process immediately in the background
    asyncio.create_task(_process_withdrawal(withdrawal.withdrawal_id))

    return withdrawal


# ---------------------------------------------------------------------------
# Per-withdrawal processor (background task)
# ---------------------------------------------------------------------------


async def _process_withdrawal(withdrawal_id: uuid.UUID) -> None:
    """Background task: send USDC on-chain for a single withdrawal."""
    from app.database import async_session

    if not settings.treasury_wallet_private_key:
        logger.error("Treasury wallet not configured — cannot process withdrawal %s", withdrawal_id)
        return

    from eth_account import Account
    from web3 import AsyncWeb3, AsyncHTTPProvider

    w3 = AsyncWeb3(AsyncHTTPProvider(settings.resolved_rpc_url))
    treasury = Account.from_key(settings.treasury_wallet_private_key)
    usdc = w3.eth.contract(
        address=w3.to_checksum_address(settings.resolved_usdc_address),
        abi=ERC20_TRANSFER_ABI,
    )

    async with async_session() as db:
        result = await db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.withdrawal_id == withdrawal_id)
            .with_for_update()
        )
        withdrawal = result.scalar_one_or_none()
        if withdrawal is None or withdrawal.status != WithdrawalStatus.PENDING:
            return

        withdrawal.status = WithdrawalStatus.PROCESSING
        await db.commit()

        try:
            raw_amount = _credits_to_usdc_raw(withdrawal.net_payout)
            dest = w3.to_checksum_address(withdrawal.destination_address)

            nonce = await w3.eth.get_transaction_count(treasury.address)
            tx = await usdc.functions.transfer(dest, raw_amount).build_transaction({
                "from": treasury.address,
                "nonce": nonce,
                "chainId": settings.chain_id,
                "gas": 100_000,
                "maxFeePerGas": await w3.eth.gas_price * 2,
                "maxPriorityFeePerGas": await w3.eth.max_priority_fee,
            })

            signed = treasury.sign_transaction(tx)
            tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)

            withdrawal.tx_hash = tx_hash.hex()
            withdrawal.status = WithdrawalStatus.COMPLETED
            withdrawal.processed_at = datetime.now(UTC)
            await db.commit()

            logger.info(
                "Withdrawal %s completed: tx=%s amount=%s",
                withdrawal.withdrawal_id, withdrawal.tx_hash, withdrawal.net_payout,
            )

        except Exception as e:
            logger.error("Withdrawal %s failed: %s", withdrawal.withdrawal_id, str(e))
            withdrawal.status = WithdrawalStatus.FAILED
            withdrawal.error_message = str(e)[:1000]
            withdrawal.processed_at = datetime.now(UTC)

            # Refund the amount back to the agent's balance
            agent_result = await db.execute(
                select(Agent)
                .where(Agent.agent_id == withdrawal.agent_id)
                .with_for_update()
            )
            agent = agent_result.scalar_one()
            agent.balance = agent.balance + withdrawal.amount
            await db.commit()

            logger.info(
                "Withdrawal %s refunded %s to agent %s",
                withdrawal.withdrawal_id, withdrawal.amount, withdrawal.agent_id,
            )


# ---------------------------------------------------------------------------
# Deposit crediting
# ---------------------------------------------------------------------------


async def credit_deposit(db: AsyncSession, deposit_tx_id: uuid.UUID) -> None:
    """Credit an agent's balance for a confirmed deposit."""
    result = await db.execute(
        select(DepositTransaction)
        .where(DepositTransaction.deposit_tx_id == deposit_tx_id)
        .with_for_update()
    )
    deposit = result.scalar_one_or_none()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Deposit not found")
    if deposit.status == DepositStatus.CREDITED:
        return  # Idempotent

    # Defense-in-depth: reject under-minimum deposits even if verify_deposit_tx already checks
    if deposit.amount_credits < settings.min_deposit_amount:
        logger.warning(
            "Deposit %s below minimum ($%s < $%s) — marking failed",
            deposit_tx_id, deposit.amount_credits, settings.min_deposit_amount,
        )
        deposit.status = DepositStatus.FAILED
        await db.commit()
        return

    # Lock agent balance
    agent_result = await db.execute(
        select(Agent).where(Agent.agent_id == deposit.agent_id).with_for_update()
    )
    agent = agent_result.scalar_one()

    agent.balance = agent.balance + deposit.amount_credits
    deposit.status = DepositStatus.CREDITED
    deposit.credited_at = datetime.now(UTC)
    await db.commit()

    logger.info(
        "Deposit %s credited: agent=%s amount=%s",
        deposit_tx_id, deposit.agent_id, deposit.amount_credits,
    )


# ---------------------------------------------------------------------------
# Per-deposit confirmation watcher
# ---------------------------------------------------------------------------


async def verify_deposit_tx(
    db: AsyncSession, agent_id: uuid.UUID, tx_hash: str,
) -> DepositTransaction:
    """Verify a tx_hash is a valid USDC transfer to the agent's deposit address.

    Creates a DepositTransaction record and returns it. Raises HTTPException on
    invalid/mismatched transactions.
    """
    from web3 import AsyncWeb3, AsyncHTTPProvider

    # Check for duplicate
    existing = await db.execute(
        select(DepositTransaction).where(DepositTransaction.tx_hash == tx_hash)
    )
    if (dep := existing.scalar_one_or_none()) is not None:
        return dep

    # Get agent's deposit address
    result = await db.execute(
        select(DepositAddress).where(DepositAddress.agent_id == agent_id)
    )
    deposit_addr = result.scalar_one_or_none()
    if deposit_addr is None:
        raise HTTPException(status_code=404, detail="No deposit address found for this agent")

    w3 = AsyncWeb3(AsyncHTTPProvider(settings.resolved_rpc_url))
    try:
        receipt = await w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        raise HTTPException(status_code=404, detail="Transaction not found on chain")

    if receipt.status == 0:
        raise HTTPException(status_code=400, detail="Transaction reverted on chain")

    # Decode USDC Transfer events from the receipt
    usdc = w3.eth.contract(
        address=w3.to_checksum_address(settings.resolved_usdc_address),
        abi=ERC20_TRANSFER_ABI,
    )
    transfers = usdc.events.Transfer().process_receipt(receipt)

    # Find the transfer to this agent's deposit address
    matched = None
    for transfer in transfers:
        if transfer.args["to"].lower() == deposit_addr.address.lower():
            matched = transfer
            break

    if matched is None:
        raise HTTPException(
            status_code=400,
            detail=f"Transaction does not contain a USDC transfer to {deposit_addr.address}",
        )

    raw_amount = matched.args["value"]
    credits = _usdc_to_credits(raw_amount)

    # Validate minimum deposit amount immediately (not at credit time)
    if credits < settings.min_deposit_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Deposit amount ${credits} is below minimum of ${settings.min_deposit_amount}",
        )

    deposit_tx = DepositTransaction(
        deposit_tx_id=uuid.uuid4(),
        agent_id=agent_id,
        tx_hash=tx_hash,
        from_address=matched.args["from"],
        amount_usdc=Decimal(raw_amount) / Decimal(USDC_SCALE),
        amount_credits=credits,
        block_number=receipt.blockNumber,
        status=DepositStatus.CONFIRMING,
    )
    db.add(deposit_tx)
    await db.commit()
    await db.refresh(deposit_tx)

    logger.info(
        "Deposit registered: tx=%s agent=%s amount=%s USDC — waiting for %d confirmations",
        tx_hash, agent_id, deposit_tx.amount_usdc, settings.deposit_confirmations_required,
    )
    return deposit_tx


async def _wait_and_credit_deposit(deposit_tx_id: uuid.UUID, block_number: int) -> None:
    """Background task: wait for enough confirmations, then credit the deposit."""
    from web3 import AsyncWeb3, AsyncHTTPProvider
    from app.database import async_session

    w3 = AsyncWeb3(AsyncHTTPProvider(settings.resolved_rpc_url))
    required = settings.deposit_confirmations_required

    while True:
        try:
            current_block = await w3.eth.block_number
            confirmations = current_block - block_number

            if confirmations >= required:
                async with async_session() as db:
                    await credit_deposit(db, deposit_tx_id)
                logger.info("Deposit %s confirmed and credited (%d confirmations)", deposit_tx_id, confirmations)
                return

            logger.debug(
                "Deposit %s: %d/%d confirmations",
                deposit_tx_id, confirmations, required,
            )
        except Exception as e:
            logger.error("Error checking confirmations for %s: %s", deposit_tx_id, e)

        # Base L2 has ~2s block times; poll every 4s
        await asyncio.sleep(4)



# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------


async def get_deposit_history(
    db: AsyncSession, agent_id: uuid.UUID
) -> list[DepositTransaction]:
    result = await db.execute(
        select(DepositTransaction)
        .where(DepositTransaction.agent_id == agent_id)
        .order_by(DepositTransaction.detected_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


async def get_withdrawal_history(
    db: AsyncSession, agent_id: uuid.UUID
) -> list[WithdrawalRequest]:
    result = await db.execute(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.agent_id == agent_id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())
