# No job timeout / deadline enforcement

**Severity:** 🟠 High
**Status:** ✅ Closed
**Source:** CONCERNS.md #16, CONCERNS2.md #16, CONCERNS3.md #6, CONCERNS3-claude.md #16

## Description

`delivery_deadline` is stored in the Job model but never enforced. A seller can accept a job and sit on it forever with escrow locked.

## Impact

- Client's funds stuck indefinitely in escrow
- No way for client to recover funds without seller cooperation
- Marketplace deadlocks possible
- Poor user experience

## Mitigation Status

**✅ Fully implemented.** Three-layer enforcement:

1. **Redis sorted-set deadline queue** (`app/services/deadline_queue.py`): When a job is funded with a `delivery_deadline`, it's added to a Redis sorted set. A background consumer (`run_deadline_consumer`) blocks until the next deadline fires, then auto-fails the job and refunds escrow.
2. **Startup recovery** (`_recover_deadlines()` in `app/main.py`): On startup, all active jobs (FUNDED/IN_PROGRESS/DELIVERED) with deadlines are re-enqueued via idempotent ZADD — survives Redis data loss and server restarts.
3. **Cancellation on completion**: When a job completes or is refunded, its deadline is removed from the queue (`cancel_deadline` called in `app/services/escrow.py`).

Test coverage in `tests/test_deadline_queue.py` (9 tests).

## Fix Options

### Option 1: Lazy Expiry on API Access
When any job-related endpoint is called, check for overdue jobs and auto-fail them.

**Pros:**
- Simple to implement
- No background process needed
- Deadline enforced on first access

**Cons:**
- If no one accesses job, it stays stuck
- Requires adding check to multiple endpoints

### Option 2: Startup Recovery
On application startup, query for all funded jobs past deadline and fail them.

**Pros:**
- Catches stuck jobs on restart
- One place to implement

**Cons:**
- Jobs remain stuck between startup times
- Doesn't catch deadlines during runtime

### Option 3: Scheduled Task (Cron)
Separate cron job periodically checks and fails overdue jobs.

**Pros:**
- Consistent enforcement
- Independent of API traffic
- Can alert on overdue jobs

**Cons:**
- Requires external cron configuration
- Another service to manage

### Option 4: Deadline Queue with Worker
Use a Redis sorted set (similar to removed background monitor) with a consumer worker that fails jobs as they reach deadline.

**Pros:**
- Real-time enforcement
- Proactive failures
- Can send notifications

**Cons:**
- Requires worker process
- More complex infrastructure

**Recommendation:** Start with Option 1 (lazy expiry) + Option 2 (startup recovery) for simplicity. Add Option 4 (queue worker) for production reliability.

## Proposed Implementation

```python
# In job_service.py
async def check_and_fail_overdue_jobs(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    result = await db.execute(
        select(Job).where(
            Job.status == JobStatus.IN_PROGRESS,
            Job.delivery_deadline < now
        )
    )
    for job in result.scalars():
        await fail_job(db, job.job_id, None, reason="Delivery deadline exceeded")
        # Send notification to both parties

# In main.py lifespan
@app.on_event("startup")
async def on_startup():
    async with get_db() as db:
        await check_and_fail_overdue_jobs(db)

# In job endpoints (lazy check)
async def on_job_access(job_id: UUID) -> None:
    async with get_db() as db:
        job = await get_job(db, job_id)
        if job.delivery_deadline and job.status == JobStatus.IN_PROGRESS:
            if datetime.now(UTC) > job.delivery_deadline:
                await fail_job(db, job_id, None, reason="Delivery deadline exceeded")
```

## Related Issues

- #015: HD wallet seed in .env (funds security)
- #026: Agent deactivation doesn't cancel active jobs (similar stuck funds problem)

## References

- CONCERNS.md #16
- CONCERNS2.md #16
- CONCERNS3.md #6
- CONCERNS3-claude.md #16
