# Verify endpoint lacks access control

**Severity:** 🟠 High
**Status:** ✅ Closed
**Source:** CONCERNS.md #5, CONCERNS2.md #5, CONCERNS3-claude.md #5

## Description

Original issue: Any authenticated agent could trigger verification on any delivered job — not just the client. The `verify_job` endpoint checked `auth: AuthenticatedAgent` but never validated that the caller was the client (job buyer).

## Impact

Random agents could trigger verification on jobs they're not involved in, potentially failing jobs and triggering refunds maliciously.

## Fix

Added party check to `verify_job` endpoint in `app/routers/jobs.py`:

```python
@router.post("/{job_id}/verify", dependencies=[Depends(check_rate_limit)])
async def verify_job(
    job_id: uuid.UUID,
    auth: AuthenticatedAgent = Depends(verify_request),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from fastapi import HTTPException as HTTPExc
    job = await job_service.get_job(db, job_id)

    if auth.agent_id != job.client_agent_id:
        raise HTTPExc(status_code=403, detail="Only the client can trigger verification")
    
    # ... rest of verification logic
```

This ensures only the client (buyer) can trigger verification. Combined with fix for issue #6 (complete endpoint) and the result redaction from issue #8, sellers cannot initiate verification on their own jobs either.

## Related Issues

- #006 (closed): Complete endpoint access control
- #008 (closed): Client rigs verification (mitigated by access control)

## References

- CONCERNS.md #5
- CONCERNS2.md #5
- CONCERNS3-claude.md #5
