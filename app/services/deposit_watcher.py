"""Background service: watches for USDC transfers to deposit addresses."""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.wallet import DepositAddress, DepositStatus, DepositTransaction
from app.services.wallet import _usdc_to_credits

logger = logging.getLogger(__name__)

# ERC-20 Transfer event ABI
TRANSFER_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": False, "name": "value", "type": "uint256"},
    ],
    "name": "Transfer",
    "type": "event",
}

USDC_DECIMALS = 6
USDC_SCALE = 10**USDC_DECIMALS


async def _get_deposit_addresses() -> dict[str, uuid.UUID]:
    """Fetch all deposit addresses mapped to agent IDs.

    Returns: {address_lower: agent_id}
    """
    async with async_session() as db:
        result = await db.execute(select(DepositAddress))
        addrs = result.scalars().all()
        return {addr.address.lower(): addr.agent_id for addr in addrs}


async def _get_last_scanned_block() -> int:
    """Get the last block we scanned for deposits.

    Returns 0 if never scanned (will scan from deployment block).
    """
    from app.models.deposit_watcher_state import DepositWatcherState

    async with async_session() as db:
        result = await db.execute(select(DepositWatcherState))
        state = result.scalar_one_or_none()
        if state is None:
            return 0
        return state.last_scanned_block


async def _update_last_scanned_block(block_number: int) -> None:
    """Update the last scanned block number."""
    from app.models.deposit_watcher_state import DepositWatcherState

    async with async_session() as db:
        result = await db.execute(select(DepositWatcherState))
        state = result.scalar_one_or_none()
        if state is None:
            state = DepositWatcherState(last_scanned_block=block_number)
            db.add(state)
        else:
            state.last_scanned_block = block_number
        await db.commit()


async def _is_duplicate_deposit(tx_hash: str) -> bool:
    """Check if deposit transaction already exists."""
    async with async_session() as db:
        result = await db.execute(
            select(DepositTransaction).where(DepositTransaction.tx_hash == tx_hash)
        )
        return result.scalar_one_or_none() is not None


async def _create_deposit_record(
    agent_id: uuid.UUID,
    tx_hash: str,
    from_address: str,
    amount_raw: int,
    block_number: int,
) -> None:
    """Create a DepositTransaction record for a detected transfer."""
    credits = _usdc_to_credits(amount_raw)

    # Check minimum deposit amount
    if credits < settings.min_deposit_amount:
        logger.info(
            "Ignoring deposit %s: below minimum ($%s < $%s)",
            tx_hash, credits, settings.min_deposit_amount,
        )
        return

    # Check for duplicates
    if await _is_duplicate_deposit(tx_hash):
        logger.debug("Skipping duplicate deposit %s", tx_hash)
        return

    async with async_session() as db:
        deposit = DepositTransaction(
            deposit_tx_id=uuid.uuid4(),
            agent_id=agent_id,
            tx_hash=tx_hash,
            from_address=from_address,
            amount_usdc=Decimal(amount_raw) / Decimal(USDC_SCALE),
            amount_credits=credits,
            block_number=block_number,
            status=DepositStatus.CONFIRMING,
            detected_at=datetime.now(UTC),
        )
        db.add(deposit)
        await db.commit()

        logger.info(
            "Deposit detected: tx=%s agent=%s amount=%s USDC ($%s) block=%s",
            tx_hash, agent_id, deposit.amount_usdc, credits, block_number,
        )

        # Spawn confirmation watcher
        from app.services.task_registry import registry
        from app.services.wallet import _wait_and_credit_deposit
        task = asyncio.create_task(
            _wait_and_credit_deposit(deposit.deposit_tx_id, block_number)
        )
        registry.register(task, f"deposit-confirm-{deposit.deposit_tx_id}")


async def _scan_block_range(
    w3,
    deposit_addresses: dict[str, uuid.UUID],
    from_block: int,
    to_block: int,
) -> int:
    """Scan a range of blocks for USDC transfers to deposit addresses.

    Returns: number of deposits found
    """
    from web3.contract import ContractEvent

    usdc = w3.eth.contract(
        address=w3.to_checksum_address(settings.resolved_usdc_address),
        abi=[TRANSFER_EVENT_ABI],
    )

    # Query Transfer events to any of our deposit addresses
    event_filter = usdc.events.Transfer().build_filter()
    event_filter.argument_filters["to"] = list(deposit_addresses.keys())

    try:
        logs = await event_filter.get_logs(fromBlock=from_block, toBlock=to_block)
    except Exception as e:
        logger.error("Error scanning blocks %s-%s: %s", from_block, to_block, e)
        return 0

    deposits_found = 0
    for log in logs:
        dest = log["args"]["to"].lower()
        if dest in deposit_addresses:
            agent_id = deposit_addresses[dest]

            # Skip if already processed (check via tx_hash)
            tx_hash = log["transactionHash"].hex()
            if await _is_duplicate_deposit(tx_hash):
                continue

            await _create_deposit_record(
                agent_id=agent_id,
                tx_hash=tx_hash,
                from_address=log["args"]["from"],
                amount_raw=log["args"]["value"],
                block_number=log["blockNumber"],
            )
            deposits_found += 1

    return deposits_found


async def run_deposit_watcher() -> None:
    """Background task: continuously scan blockchain for new deposits.

    Polls for new blocks and scans for USDC Transfer events to deposit
    addresses. Automatically creates deposit records and spawns confirmation tasks.
    """
    from decimal import Decimal
    from web3 import AsyncWeb3, AsyncHTTPProvider

    logger.info("Deposit watcher started")

    w3 = None
    try:
        w3 = AsyncWeb3(AsyncHTTPProvider(settings.resolved_rpc_url))
        deposit_addresses = {}
        last_block = 0

        while True:
            try:
                # Refresh deposit addresses every minute
                deposit_addresses = await _get_deposit_addresses()
                if not deposit_addresses:
                    logger.warning("No deposit addresses found, waiting...")
                    await asyncio.sleep(30)
                    continue

                # Get current block number
                current_block = await w3.eth.block_number

                # Get last scanned block (default to current - 1000 to start)
                last_block = await _get_last_scanned_block()
                if last_block == 0:
                    last_block = max(0, current_block - 1000)
                    logger.info("Initial scan from block %s", last_block)

                # Scan in chunks of 1000 blocks (to avoid hitting RPC limits)
                chunk_size = 1000
                scan_from = last_block + 1

                while scan_from <= current_block:
                    scan_to = min(scan_from + chunk_size - 1, current_block)

                    deposits_found = await _scan_block_range(
                        w3=w3,
                        deposit_addresses=deposit_addresses,
                        from_block=scan_from,
                        to_block=scan_to,
                    )

                    # Update last scanned block
                    await _update_last_scanned_block(scan_to)

                    if deposits_found > 0:
                        logger.info(
                            "Scanned blocks %s-%s: found %d deposits",
                            scan_from, scan_to, deposits_found,
                        )

                    scan_from = scan_to + 1

                    # Small delay between chunks to avoid RPC throttling
                    await asyncio.sleep(0.5)

                # Wait for new blocks (Base L2 ~2s block time)
                await asyncio.sleep(4)

            except asyncio.CancelledError:
                logger.info("Deposit watcher cancelled")
                raise
            except Exception as e:
                logger.error("Deposit watcher error: %s", e, exc_info=True)
                await asyncio.sleep(10)  # Wait before retrying

    finally:
        if w3:
            await w3.aclose()
        logger.info("Deposit watcher stopped")
