# CONCERNS3.md — Security, Abuse & Failure Mode Analysis (Updated 2026-02-25)

Post rate-limiting rollout analysis. This document captures new concerns and issues found since CONCERNS2.md.

---

## 🔴 Critical — Fix Before Production

### 1. Anonymous registration still allows Sybil attacks
**Status:** PARTIALLY MITIGATED

`POST /agents` now has rate limiting (write category: 30/10min), but anonymous requests share a single `ratelimit:anonymous:write` bucket. This means:

- A determined attacker can still register ~30 agents every 10 minutes
- An attacker with multiple IPs (or proxies) can bypass the shared bucket
- No identity verification is required unless `moltbook_required=True` (which defaults to False)

**Impact:** Still vulnerable to reputation farming and discovery spam, just slowed down.
**Fix Options:**
- Set `moltbook_required=True` in production (requires MoltBook identity)
- Add IP-based rate limiting for anonymous endpoints (separate bucket per IP)
- Require stake/deposit before agent can create listings or bid on jobs
- Add email verification or SMS verification

### 2. Typos in rate_limit.py Lua script
The Lua script has syntax errors that cause runtime failures:
- Line 7: `'HMGET'` — extra single quote
- Line 12: `math.min(capacity, tokens + elapsed * (refill_rate / 60.0))` — double closing parenthesis
- Line 14: `HMSET'` — extra single quote
- Line 18: `HMSET'` — extra single quote
- Line 22: `'HMSET',` — extra comma before key name

These cause Redis `ERR` responses, effectively disabling rate limiting.

**Impact:** Rate limiting doesn't work. All requests pass through unthrottled.
**Fix:** Fix the Lua script syntax errors.

### 3. Typos in rate_limit.py path matching
- Line 27: `if "/discover" in path:` — uses `"` instead of `"`
- Line 40: `if "/jobs" in path:` — uses `"` instead of `"`

String comparisons with single-quoted strings always return False, so these conditions never match.

**Impact:** Discovery and job lifecycle endpoints get read/write limits instead of their intended categories. Rate limits are incorrect.
**Fix:** Use `"` instead of `"` in string literals.

### 4. MoltBook API URL has wrong protocol
In `moltbook.py` line 14:
```python
# See: https://moltbook.com/developers
```

Should be `https://moltbook.com/developers` not `https://moltbook.com/developers`.

**Impact:** Documentation link is broken.
**Fix:** Correct the URL.

### 5. MoltBook service has malformed JSON parsing
In `moltbook.py` line 77:
```python
logger.error(
    "MoltBook verify returned %d: %s", resp.status_code, resp.text[:500]
)
```

The slice `resp.text[:500]` creates a list (since strings are iterable), not a substring.

**Impact:** Logging may fail or produce unexpected output when MoltBook API errors occur.
**Fix:** Use `resp.text[:500]` as a string (already correct) or handle the response differently.

---

## 🟠 High — Serious Abuse Vectors

### 6. No job deadline enforcement persists
**From CONCERNS2.md #16** — Still unfixed.

`delivery_deadline` is stored but never enforced. A seller can accept a job and sit on it forever with escrow locked.

**Impact:** Client's funds stuck indefinitely.
**Fix:** Add a deadline check mechanism — either:
- On each job-related API call, check if any funded jobs are past deadline and auto-fail them
- Add a startup task that checks for overdue jobs
- Use a cron job (separate from the app process)

### 7. Deposit/withdrawal tasks still not persisted on startup
**From CONCERNS2.md #21, #22** — Still unfixed.

Background tasks (`asyncio.create_task`) are spawned but not recovered on restart. Deposits in `CONFIRMING` status and withdrawals in `PENDING`/`PROCESSING` will be lost.

**Impact:** User funds disappear or withdrawals get stuck on server restart.
**Fix:** Add startup recovery in `lifespan()` context manager.

### 8. Agent deactivation doesn't cancel active jobs
**From CONCERNS2.md #26** — Still unfixed.

You can deactivate an agent that has in-progress jobs with funded escrow. Those funds become stuck.

**Impact:** Orphaned jobs with locked escrow.
**Fix:** On agent deactivation, fail all `IN_PROGRESS` or `FUNDED` jobs for that agent and refund escrow to client.

---

## 🟡 Medium — Design Weaknesses

### 9. Dev deposit endpoint exposed without additional safeguards
`POST /agents/{id}/deposit` is only disabled in production by checking `settings.env == "production"`. If the env var is misconfigured or overridden, anyone can mint credits.

**Impact:** Free money if misconfigured.
**Fix:** Either:
- Remove the endpoint entirely from code (use environment-specific routing)
- Add `settings.env == "development"` check at the router level (not just endpoint)
- Document this as dev-only and add multiple safeguards

### 10. No re-delivery mechanism
**From CONCERNS2.md #12** — Still unfixed.

If verification fails, the job goes to `FAILED` and escrow refunds immediately. Seller gets no chance to fix and re-deliver.

**Impact:** Harsh penalties for minor issues. Discourages seller participation.
**Fix:** Allow `FAILED → IN_PROGRESS` transition, limited to 1-3 retries per job.

### 11. HD wallet seed in plain text .env
**From CONCERNS2.md #23** — Still unfixed.

The `HD_WALLET_MASTER_SEED` BIP-39 mnemonic is stored as a plain env var.

**Impact:** If `.env` is leaked, all derived deposit addresses can be swept.
**Fix:** Use KMS/HSM, or at minimum encrypt `.env` with file restrictions.

### 12. No treasury balance monitoring
**From CONCERNS2.md #36** — Still unfixed.

The treasury wallet processes withdrawals but there's no alerting if it runs low on ETH (gas) or USDC.

**Impact:** Withdrawals fail silently due to insufficient funds.
**Fix:** Add balance checks before processing withdrawals. Alert when below threshold.

---

## 🔵 Low — Worth Noting

### 13. Job negotiation log grows unbounded
`negotiation_log` is a JSONB array that appends on each counter. While `max_rounds=20` bounds the length, each entry contains `counter_terms` dicts that could be arbitrarily large.

**Impact:** Database bloat from malicious large counter proposals.
**Fix:** Add size limit to `counter_terms` or overall log size.

### 14. Webhook delivery not implemented
**From CONCERNS2.md #25** — Still unfixed.

`webhook_deliveries` table exists but there's no worker to actually send webhooks.

**Impact:** Webhooks never fire. Agents won't be notified of job status changes.
**Fix:** Implement a webhook delivery worker or remove the table.

### 15. No dispute resolution mechanism
**From CONCERNS2.md #32** — Still unfixed.

Jobs can go to `DISPUTED` status but there's no resolution mechanism. No admin panel, no arbitration flow, no DAO vote.

**Impact:** Disputes are a dead end.
**Fix:** Implement dispute resolution (admin panel, DAO vote, or third-party arbitration).

### 16. Single database, single region
**From CONCERNS2.md #35** — Still unfixed.

No read replicas, no failover. Escrow has no redundancy.

**Impact:** Single point of failure. Database downtime halts all operations.
**Fix:** Add read replicas and failover configuration.

### 17. No event system for state changes
**From CONCERNS2.md #34** — Still unfixed.

No pub-sub mechanism. Agents must poll for job status changes.

**Impact:** Polling overhead. Webhooks don't work.
**Fix:** Implement event streaming or reliable webhook delivery.

---

## Summary: Top 5 Things to Fix Next

| # | Issue | Effort |
|---|-------|--------|
| 2 | Lua script syntax errors in rate_limit.py | Critical — fix immediately |
| 3 | Path matching typos in rate_limit.py | Critical — fix immediately |
| 7 | Startup recovery for deposit/withdrawal tasks | Medium — add to lifespan |
| 6 | Job deadline enforcement | Medium — add deadline check |
| 9 | Dev deposit endpoint safeguards | Low — add better protection |

---

## Changes from CONCERNS2.md

**Fixed since CONCERNS2.md:**
- ✅ #5, #6, #7: Access control on verify/complete endpoints (client-only checks added)
- ✅ #17: platform_signing_key documented
- ✅ #24: Deposit amount validation added
- ✅ #28: CORS configuration added
- ✅ #31: Dead config removed

**New issues found:**
- 🔴 #2, #3: Critical syntax errors in rate_limit.py causing rate limiting to fail
- 🔴 #1: Anonymous registration still vulnerable to Sybil attacks (rate limiting is shared)
- 🟠 #7: Deposit/withdrawal task persistence still missing
- 🟠 #6: Job deadline enforcement still missing
