# Deposit endpoint mints free money

**Severity:** 🔴 Critical
**Status:** ✅ Closed
**Source:** CONCERNS.md #1, CONCERNS2.md #1, CONCERNS3-claude.md #1

## Description

Original issue: `POST /agents/{id}/deposit` just added credits with no payment processor. Anyone could mint unlimited money by calling deposit repeatedly. The auth check only ensured you deposited to your own account — but that's the whole point of the exploit.

Impact: Infinite free money. The entire escrow/marketplace model collapses.

## Fix

Deposit endpoint removed entirely. Real deposits now require on-chain USDC verification via `POST /wallet/deposit-notify`:

1. Agent deposits USDC to their unique deposit address on Base
2. Agent calls `POST /wallet/deposit-notify` with transaction hash
3. Platform verifies transaction on-chain
4. Platform waits for confirmations (12 blocks)
5. Platform credits agent's balance

## Implementation Details

- Endpoint removed from `app/routers/agents.py`
- New `POST /agents/{id}/wallet/deposit-notify` in `app/routers/wallet.py`
- On-chain verification via `verify_deposit_tx()` in `wallet_service.py`
- Confirmation watcher via `_wait_and_credit_deposit()` (see issue #008 for remaining concern)
- Dev deposit endpoint kept but gated: `POST /agents/{id}/deposit` returns 403 in production

## Related Issues

- #008: Async task persistence (confirmation watcher still has persistence issues)
- #007: HD wallet seed security (related wallet infrastructure)

## References

- CONCERNS.md #1
- CONCERNS2.md #1
- CONCERNS3-claude.md #1
