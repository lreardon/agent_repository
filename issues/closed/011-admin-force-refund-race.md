# Admin force-refund missing row-level lock

**Severity:** 🟡 Medium
**Status:** ✅ Fixed (2026-03-04)
**Source:** Security Audit 2026-03-04

## Description

The admin `force_refund_escrow` endpoint in `app/routers/admin.py` uses `db.get(EscrowAccount, escrow_id)` without `SELECT FOR UPDATE`. If two admins simultaneously trigger a force-refund on the same escrow, both could succeed, double-crediting the client's balance.

## Impact

Double-refund of escrow funds. Low probability (requires concurrent admin action on the same escrow) but could cause balance inflation.

## Fix

Use `SELECT FOR UPDATE` on the escrow row, and lock agent balance rows before crediting:

```python
result = await db.execute(
    select(EscrowAccount).where(EscrowAccount.escrow_id == escrow_id).with_for_update()
)
escrow = result.scalar_one_or_none()
```

Also lock client/seller balance rows as done in `escrow.py` functions.

## Effort

~30 minutes. Follow the same pattern as `release_escrow` / `refund_escrow`.
