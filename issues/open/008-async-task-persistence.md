# Deposit and withdrawal tasks not persisted on startup

**Severity:** 🔴 Critical
**Status:** 🟡 Open
**Source:** CONCERNS2.md #21, #22, CONCERNS3.md #7, CONCERNS3-claude.md #21, #22, #41

## Description

The confirmation watcher tasks (`_wait_and_credit_deposit`) and withdrawal processor (`_process_withdrawal`) are spawned as `asyncio.create_task`. If the server restarts while:

- A deposit is in `CONFIRMING` status
- A withdrawal is in `PENDING` or `PROCESSING` status

The async tasks are lost and never recovered. The records remain stuck in their intermediate states forever.

### Deposit Impact
Deposits that were detected but not yet confirmed are silently lost on server restart. User's deposited USDC appears to vanish.

### Withdrawal Impact
Worse: If a withdrawal was in `PROCESSING` status, USDC may have been sent on-chain but the status was never updated. This creates:
- User thinks withdrawal failed (status stuck at PROCESSING)
- Risk of double-send if task ran partially but was retried on restart

## Mitigation Status

**None.** No startup recovery mechanism exists.

## Fix Options

### Option 1: Startup Recovery in Lifespan
On application startup, query for orphaned records and re-spawn watchers or reconcile on-chain state.

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: recover orphaned tasks
    await recover_orphaned_deposits()
    await recover_orphaned_withdrawals()
    yield
    # Shutdown: wait for tasks
    await shutdown_tasks()

async def recover_orphaned_deposits() -> None:
    # Find deposits in CONFIRMING status older than N minutes
    async with get_db() as db:
        cutoff = datetime.now(UTC) - timedelta(minutes=30)
        result = await db.execute(
            select(DepositTransaction).where(
                DepositTransaction.status == DepositStatus.CONFIRMING,
                DepositTransaction.detected_at < cutoff
            )
        )
        for deposit in result.scalars():
            # Re-spawn confirmation watcher
            asyncio.create_task(
                _wait_and_credit_deposit(deposit.deposit_tx_id, deposit.block_number)
            )

async def recover_orphaned_withdrawals() -> None:
    # Check PENDING and PROCESSING withdrawals on-chain
    for withdrawal in get_orphaned_withdrawals():
        # Check on-chain status
        onchain_status = check_withdrawal_on_chain(withdrawal.tx_hash)
        if onchain_status == "completed":
            # Mark as COMPLETED
            withdrawal.status = WithdrawalStatus.COMPLETED
        elif withdrawal.status == WithdrawalStatus.PROCESSING and onchain_status == "pending":
            # Likely failed mid-process, mark as PENDING for retry
            withdrawal.status = WithdrawalStatus.PENDING
```

**Pros:**
- Simple to implement
- Recovers on every startup
- Self-healing

**Cons:**
- Tasks could be in inconsistent state for a short time
- Race conditions possible

### Option 2: Durable Task Queue
Replace `asyncio.create_task` with a durable queue (e.g., Celery, arq, Redis Queue).

**Pros:**
- Tasks survive server restarts
- Built-in retry mechanisms
- Better monitoring and observability

**Cons:**
- Requires additional infrastructure
- More complex deployment
- Another service to manage

### Option 3: Database-Driven State Machine
Don't use in-memory tasks at all. Store task state in database and have a single worker process them.

**Pros:**
- Fully durable
- Can inspect task state via DB
- Easier debugging

**Cons:**
- Requires worker process
- Polling overhead or pub/sub complexity

**Recommendation:** Start with Option 1 (startup recovery) for immediate fix. Plan migration to Option 2 (durable queue) for production.

## Related Issues

- #007: HD wallet seed security (funds at risk)
- #015: Treasury wallet (related withdrawal processing)

## References

- CONCERNS2.md #21, #22
- CONCERNS3.md #7
- CONCERNS3-claude.md #21, #22, #41
