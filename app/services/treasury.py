"""Treasury monitoring: balance checks, alerts, and auto-pause withdrawals."""

import logging
from decimal import Decimal

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# Redis key for withdrawal pause state
WITHDRAWALS_PAUSED_KEY = "treasury:withdrawals_paused"


async def get_treasury_usdc_balance() -> Decimal | None:
    """Fetch treasury USDC balance from chain. Returns None if not configured."""
    if not settings.treasury_wallet_address:
        return None

    try:
        from web3 import AsyncWeb3, AsyncHTTPProvider

        w3 = AsyncWeb3(AsyncHTTPProvider(settings.resolved_rpc_url))

        abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function",
            }
        ]

        usdc = w3.eth.contract(
            address=w3.to_checksum_address(settings.resolved_usdc_address),
            abi=abi,
        )
        treasury_addr = w3.to_checksum_address(settings.treasury_wallet_address)
        raw_balance = await usdc.functions.balanceOf(treasury_addr).call()
        return Decimal(raw_balance) / Decimal(1_000_000)
    except Exception:
        logger.warning("Failed to fetch treasury balance", exc_info=True)
        return None


async def check_treasury_and_update_pause() -> None:
    """Check treasury balance and auto-pause/resume withdrawals.

    Called periodically by the metrics updater.
    """
    balance = await get_treasury_usdc_balance()
    if balance is None:
        return

    from app.redis import redis_pool
    r = aioredis.Redis(connection_pool=redis_pool)
    try:
        currently_paused = await r.get(WITHDRAWALS_PAUSED_KEY)

        if balance < settings.treasury_pause_threshold_usdc:
            if not currently_paused:
                await r.set(WITHDRAWALS_PAUSED_KEY, "1")
                logger.critical(
                    "Treasury balance CRITICAL: $%s USDC — withdrawals AUTO-PAUSED (threshold: $%s)",
                    balance, settings.treasury_pause_threshold_usdc,
                )
        elif balance < settings.treasury_alert_threshold_usdc:
            # Above pause threshold but below alert — warn but allow withdrawals
            if currently_paused:
                await r.delete(WITHDRAWALS_PAUSED_KEY)
                logger.warning(
                    "Treasury balance recovering: $%s USDC — withdrawals RESUMED (still below alert threshold $%s)",
                    balance, settings.treasury_alert_threshold_usdc,
                )
            else:
                logger.warning(
                    "Treasury balance LOW: $%s USDC (alert threshold: $%s)",
                    balance, settings.treasury_alert_threshold_usdc,
                )
        else:
            # Healthy — ensure not paused
            if currently_paused:
                await r.delete(WITHDRAWALS_PAUSED_KEY)
                logger.info(
                    "Treasury balance healthy: $%s USDC — withdrawals RESUMED",
                    balance,
                )
    finally:
        await r.aclose()


async def are_withdrawals_paused(redis_client: aioredis.Redis | None = None) -> bool:
    """Check if withdrawals are currently paused due to low treasury."""
    own = False
    if redis_client is None:
        from app.redis import redis_pool
        redis_client = aioredis.Redis(connection_pool=redis_pool)
        own = True
    try:
        return bool(await redis_client.get(WITHDRAWALS_PAUSED_KEY))
    finally:
        if own:
            await redis_client.aclose()
