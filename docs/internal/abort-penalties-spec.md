# Abort Penalties & Performance Bond — Design Spec

**Date:** 2026-03-04
**Status:** Approved

## Overview

Both parties escrow a penalty deposit when a job is funded. This creates mutual skin-in-the-game:
- **Client abort penalty:** Compensates the seller for time spent if the client cancels.
- **Seller abort penalty (performance bond):** Compensates the client for opportunity cost if the seller fails to deliver passing work.

## Penalty Matrix

| Scenario | Client receives | Seller receives |
|----------|----------------|----------------|
| Verification passes | Work product | `agreed_price - fees` |
| Client aborts (after funding) | `agreed_price - client_abort_penalty` | `client_abort_penalty` + their bond back |
| Seller aborts | `agreed_price` + `seller_abort_penalty` | Loses bond |
| Verification fails (all retries) | `agreed_price` + `seller_abort_penalty` | Loses bond |
| Deadline expires | `agreed_price` + `seller_abort_penalty` | Loses bond |

## Verification Retry Loop

Failed verification no longer terminates the job. Instead:

```
FUNDED → IN_PROGRESS → DELIVERED → [verify]
                                      ↓ pass → COMPLETED (escrow released)
                                      ↓ fail → back to IN_PROGRESS (seller can redeliver)
                                                  ↓ redeliver → DELIVERED → [verify again]
                                                  ↓ deadline expires → FAILED (bond forfeited)
                                                  ↓ seller aborts → CANCELLED (bond forfeited)
```

Retries continue until:
1. Verification passes → COMPLETED
2. Deadline expires → FAILED, penalties applied
3. Seller explicitly aborts → CANCELLED, penalties applied
4. Client explicitly aborts → CANCELLED, penalties applied

## State Machine Changes

**New transitions:**
- `FUNDED` → `CANCELLED` (either party aborts before work starts)
- `IN_PROGRESS` → `CANCELLED` (either party aborts)
- `DELIVERED` → `IN_PROGRESS` (verification failed, seller retries)

**Removed transitions:**
- `DELIVERED` → `FAILED` (no longer terminal on verify fail)
- `VERIFYING` → `FAILED` (verification fail returns to IN_PROGRESS)
- `IN_PROGRESS` → `FAILED` (use CANCELLED via abort instead)

**`FAILED` is now reserved for:** deadline expiry only (system-initiated).
**`CANCELLED` is used for:** voluntary abort by either party.

## Model Changes

### Job
- `client_abort_penalty: Decimal | None` — negotiated during proposal/counter
- `seller_abort_penalty: Decimal | None` — negotiated during proposal/counter
- Both default to `Decimal("0.00")` if not specified

### EscrowAccount
- `seller_bond_amount: Decimal` — amount held from seller's balance (= seller_abort_penalty)

### EscrowAction (new values)
- `SELLER_BOND_FUNDED` — seller's bond deposited
- `ABORT_CLIENT` — client aborted, penalties distributed
- `ABORT_SELLER` — seller aborted, penalties distributed
- `BOND_FORFEITED` — seller bond paid to client (on fail/deadline/seller abort)
- `BOND_RETURNED` — seller bond returned (on success/client abort)

## Escrow Changes

### fund_job (updated)
1. Deduct `agreed_price` from client balance (existing)
2. Deduct `seller_abort_penalty` from seller balance (new)
3. Store `seller_bond_amount` on escrow record
4. Fail if seller has insufficient balance for the bond

### release_escrow (updated, on verification pass)
1. Pay seller `agreed_price - fees` (existing)
2. Return `seller_bond_amount` to seller balance (new)
3. Audit: `BOND_RETURNED`

### abort_job (new)
**Client aborts:**
1. Refund client: `agreed_price - client_abort_penalty`
2. Pay seller: `client_abort_penalty`
3. Return seller's bond to seller
4. Job → CANCELLED

**Seller aborts (or deadline expires or verification exhausted):**
1. Refund client: full `agreed_price`
2. Pay client: `seller_abort_penalty` (from seller's bond)
3. Job → CANCELLED (abort) or FAILED (deadline)

## Schema Changes

### JobProposal
- Add `client_abort_penalty: Decimal | None = None` (defaults to 0)
- Add `seller_abort_penalty: Decimal | None = None` (defaults to 0)
- Validation: penalties must be ≥ 0, client penalty ≤ max_budget

### CounterProposal
- Add `client_abort_penalty: Decimal | None = None`
- Add `seller_abort_penalty: Decimal | None = None`

### JobResponse
- Expose `client_abort_penalty`, `seller_abort_penalty`

## API Changes

### New: `POST /jobs/{job_id}/abort`
- Either party can call
- Valid from: FUNDED, IN_PROGRESS, DELIVERED
- Applies penalty matrix based on caller
- Returns updated JobResponse

### Updated: `POST /jobs/{job_id}/verify`
- On failure: transition to IN_PROGRESS (not FAILED)
- Return verification result with `retry_allowed: true`

### Updated: Deadline expiry handler
- Apply seller abort penalty (same as seller abort)
- Job → FAILED

## Migration

New Alembic migration:
- Add `client_abort_penalty` and `seller_abort_penalty` to `jobs` table
- Add `seller_bond_amount` to `escrow_accounts` table
- Add new enum values to `escrowaction` type

## Tests Required

1. Propose job with abort penalties
2. Counter-propose with different penalties
3. Fund job deducts seller bond
4. Fund job fails if seller can't cover bond
5. Client aborts — correct penalty distribution
6. Seller aborts — correct penalty distribution
7. Verification fail → back to IN_PROGRESS (not FAILED)
8. Redeliver after failed verification → re-verify
9. Deadline expiry → seller bond forfeited to client
10. Zero-penalty jobs work (backward compatible)
11. Abort from invalid state rejected
12. Abort penalty in negotiation log
13. Escrow audit trail for all abort/bond flows
14. Seller bond returned on successful completion
