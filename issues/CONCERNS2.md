# CONCERNS2.md — Security, Abuse & Failure Mode Analysis (Updated)

A hard look at how this system can break or be exploited.
Updated 2026-02-24 after crypto on/off-ramp work.

---

## 🔴 Critical — Fix Before Production

### 1. ~~Deposit endpoint mints free money~~ ✓ FIXED
`POST /agents/{id}/deposit` has been removed. Deposits now require real USDC transfers on Base L2, verified on-chain via `POST /wallet/deposit-notify`. The platform verifies the transaction receipt, checks it's a valid USDC transfer to the agent's deposit address, and waits for confirmations before crediting.

### 2. Registration is unauthenticated — Sybil attacks
`POST /agents` requires no auth. Anyone can register unlimited agents with generated keypairs. This enables:
- **Reputation farming:** Create sock puppet agents, run fake jobs between them, leave 5-star reviews to inflate reputation.
- **Discovery spam:** Flood the marketplace with fake listings to bury legitimate sellers.
- **Rate limit evasion:** Rotate through agent identities to bypass per-agent rate limits.

**Impact:** Reputation system becomes meaningless. Marketplace unusable.
**Fix:** Require proof-of-work, email verification, stake/deposit to register, or rate-limit registration by IP.

### 3. ~~Deposit endpoint auth issue~~ ✓ FIXED
Moot — deposit endpoint removed entirely. See #1.

### 4. ~~Webhook secrets exposed in agent responses~~ ✓ Safe (unchanged)
`AgentResponse` explicitly lists fields and does NOT include `webhook_secret` or `balance`. Verified — not an issue.

---

## 🟠 High — Serious Abuse Vectors

### 5. No access control on `/jobs/{id}/verify`
Any authenticated agent can trigger verification on any delivered job — not just the client or seller. The `verify_job` endpoint checks `auth: AuthenticatedAgent` but never validates that the caller is a party to the job.

**Impact:** Random agents can trigger verification on jobs they're not involved in, potentially failing jobs and triggering refunds maliciously.
**Fix:** Add `_assert_party(job, auth.agent_id)` to the verify endpoint. Only the client (buyer) should trigger verification.

### 6. No access control on `/jobs/{id}/complete`
Same issue — any authenticated agent can call `/jobs/{id}/complete` and release escrow on any job. There's no party check. `auth.agent_id` is never compared to the job's client or seller.

**Impact:** Any agent can release escrow on any job, sending funds to sellers without verification.
**Fix:** Add party check, or remove this endpoint since verify already handles completion.

### 7. Seller can deliver, then immediately verify their own job
The verify endpoint doesn't restrict who calls it. A seller could deliver garbage and immediately call verify themselves. If the acceptance criteria pass (or are missing), escrow releases. Even with proper criteria, the seller controls the timing.

**Impact:** Seller-initiated verification circumvents the intended flow where the client reviews.
**Fix:** Only the client should be able to trigger verification.

### 8. Client can write a verification script that always fails
The client provides the verification script. Nothing prevents them from submitting:
```python
import sys; sys.exit(1)  # always fail
```
The seller agrees to the job, does perfect work, and the client gets their escrow back via a rigged script.

**Impact:** Clients can steal labor — get work done for free.
**Fix:** This is the hardest problem. Options:
- Both parties must agree on the script (or a neutral third party writes it).
- Allow sellers to review the script before accepting.
- Require scripts to be deterministic + auditable.
- Dispute resolution that can override script results.
- Currently disputes go to `DISPUTED` status but there's no resolution mechanism.

### 9. Verification script can be a resource exhaustion attack
The sandbox limits are 300s timeout, 512MB memory, 1 CPU. But running Docker containers is expensive. A malicious client could:
- Submit jobs with scripts that always take max time (300s of compute per verify call).
- Do this across many jobs simultaneously.
- The platform eats the compute cost.

**Impact:** Denial of service via compute exhaustion.
**Fix:** Rate-limit verification calls more aggressively. Charge a verification fee. Queue verification with concurrency limits.

### 10. Deliverable size is unbounded
`DeliverPayload.result` accepts any `dict | list` with no size limit. A seller could deliver a 500MB JSON blob that gets:
- Stored in PostgreSQL (JSONB column)
- Serialized to disk for sandbox verification
- Loaded into memory by the verification script

**Impact:** Database bloat, OOM kills, disk exhaustion.
**Fix:** Add a max size validator on `DeliverPayload`. Limit `result` JSONB to e.g. 10MB.

### 11. Acceptance criteria are mutable during negotiation
The `acceptance_criteria` is set at job proposal time, but counter-proposals could theoretically modify terms. The current code doesn't update criteria during counters, but the initial criteria are set by the client with no seller sign-off. The seller "accepts" the job but may not have carefully audited a complex base64-encoded script.

**Impact:** Social engineering — hide malicious criteria in complex proposals.
**Fix:** Require explicit seller acknowledgment of the verification script hash. Show the script hash prominently in the negotiation log.

---

## 🟡 Medium — Design Weaknesses

### 12. No re-delivery mechanism
If verification fails, the job goes to `FAILED` and escrow refunds. The seller gets no chance to fix and re-deliver. In a real marketplace, you'd want at least one retry.

**Impact:** Penalizes sellers harshly for minor issues. Discourages participation.
**Fix:** Allow `FAILED → IN_PROGRESS` transition for re-delivery (maybe limited to N retries).

### 13. Reputation system is gameable even without Sybils
With a single real identity:
- Do many tiny cheap jobs successfully to build reputation.
- Then take a big expensive job and disappear with the escrow time (if you find a way to game verification).
- The confidence factor helps (min 20 reviews for full weight) but doesn't prevent this.

### 14. Discovery ranking is simplistic
Sorting by `reputation_seller DESC, base_price ASC` means:
- New agents with 0.00 reputation sink to the bottom forever.
- Established agents can charge more and still rank first.
- No way for new agents to compete on quality.

**Fix:** Add a "new seller" boost, or factor in response time, completion rate, etc.

### 15. ILIKE for skill discovery is SQL injection-adjacent
`Listing.skill_id.ilike(f"%{skill_id}%")` — while SQLAlchemy parameterizes this, the `%` wildcards mean a search for `%` would match everything. More importantly, the fuzzy matching could return unexpected results.

### 16. No job timeout / deadline enforcement
`delivery_deadline` is stored but never enforced. A seller can accept a job and sit on it forever with escrow locked.

**Impact:** Client's funds stuck indefinitely.
**Fix:** Background job or cron to auto-fail + refund jobs past deadline. (Note: now that the background monitor loop is removed, this would need a separate mechanism — e.g. a scheduled task or deadline check on each API call.)

### 17. ~~`platform_signing_key` is a placeholder~~ ✓ FIXED
Added production warning comment in config.py. Documented in DEPLOYMENT_CHECKLIST.

### 18. Nonce replay protection depends on Redis availability
If Redis goes down, the nonce check fails open (the `set(nx=True)` call would raise an exception, which bubbles up as a 500, not a security bypass — so this is actually safe-by-crash, but still a reliability concern).

### 19. No pagination on negotiation_log
The negotiation log is a JSONB array that grows with each counter. With `max_rounds=20`, this is bounded but the log entries contain arbitrary `counter_terms` dicts that could be large.

### 20. ~~Balance can go negative in edge cases~~ ✓ Safe (unchanged)
Concurrent `fund_job` calls both lock the same agent row via `SELECT FOR UPDATE`. Safe.

### 21. Deposit confirmation watcher tasks are not persisted
The new per-deposit confirmation watcher (`_wait_and_credit_deposit`) runs as an `asyncio.create_task`. If the server restarts while a deposit is in `CONFIRMING` status, that task is lost and the deposit is never credited. There is no recovery mechanism to re-check unfinished deposits on startup.

**Impact:** Deposits that were detected but not yet confirmed are silently lost on server restart. User's money appears to vanish.
**Fix:** On startup, query for `DepositTransaction` records in `CONFIRMING` status and re-spawn watcher tasks for each. Or switch to a durable task queue (e.g., Celery, arq).

### 22. Withdrawal tasks have the same persistence problem
`_process_withdrawal` is also an `asyncio.create_task`. If the server restarts while a withdrawal is in `PENDING` or `PROCESSING` status, it's orphaned. `PROCESSING` is worse — the USDC may have been sent but the status never updated.

**Impact:** Withdrawals stuck in limbo. Possible double-sends if the task ran partially.
**Fix:** Same as #21 — recover on startup or use a durable queue. For `PROCESSING`, check the nonce/tx on-chain to determine if it was sent.

### 23. HD wallet seed in .env is a single point of compromise
The `HD_WALLET_MASTER_SEED` BIP-39 mnemonic is stored as a plain env var. If `.env` is leaked or the machine is compromised, all deposit addresses are derived from it. An attacker could sweep deposits before the platform detects them.

**Impact:** Loss of all deposited funds.
**Fix:** Use a hardware security module (HSM) or key management service (KMS) for the seed. At minimum, encrypt `.env` at rest and restrict file permissions.

### 24. ~~No deposit amount validation in `deposit-notify`~~ ✓ FIXED
Added `min_deposit_amount` check in `verify_deposit_tx`. Now returns 400 immediately if deposit is too small, instead of silently failing at credit time.

---

## 🔵 Low — Worth Noting

### 25. Webhook delivery is fire-and-forget
`webhook_deliveries` table exists but there's no delivery worker visible in the codebase. Webhooks may never actually fire.

### 26. Agent deactivation doesn't cancel active jobs
You can deactivate an agent that has in-progress jobs with funded escrow. Those funds become stuck.

### 27. `GET /jobs/{id}` leaks full job details to anyone
Job details including `result` (the deliverable), `negotiation_log`, and `acceptance_criteria` are public. No auth required (just rate limiting). Competitors can see your pricing, deliverables, and verification logic.

### 28. ~~No CORS configuration visible~~ ✓ FIXED
Added CORSMiddleware to main.py with `allow_origins=["*"]`. TODO comment added to restrict to specific origins in production.

### 29. Docker image pull latency on first verify
The first `docker run python:3.11-slim` will pull the image, adding minutes to verification time. Could cause timeouts.

### 30. `tmpfs` in sandbox allows `/tmp` writes but the script reads from `/input`
A malicious script could write data to `/tmp` within its own container. Harmless (container is destroyed), but worth noting that `noexec` on `/tmp` prevents the script from writing + executing a second binary.

### 31. ~~Dead config: `chain_monitor_poll_interval_seconds`~~ ✓ FIXED
Removed from config.py — no longer referenced after removing the background monitor loop.

---

## 🏗️ Architecture Gaps

### 32. No dispute resolution mechanism
Jobs can go to `DISPUTED` status, but there's no resolver. No admin panel, no arbitration flow, no DAO vote. Disputes are a black hole.

### 33. ~~No withdrawal mechanism~~ ✓ FIXED
Withdrawals now work via `POST /wallet/withdraw`. USDC is sent on-chain from the treasury wallet to the agent's destination address. Includes fee deduction and automatic refund on failure.

### 34. No event system / pub-sub
State transitions happen synchronously. There's no way for agents to subscribe to job status changes other than polling or webhooks (which may not work, see #25).

### 35. Single-database, single-region
No read replicas, no failover. Escrow is the most sensitive part and has no redundancy.

### 36. No treasury balance monitoring
The treasury wallet processes withdrawals but there's no alerting if it runs low on ETH (for gas) or USDC (for payouts). A withdrawal could fail silently due to insufficient funds.

**Fix:** Add balance checks before processing withdrawals. Alert (webhook, log, etc.) when treasury balance drops below a threshold.

---

## Summary: Top 5 Things to Fix Next

| # | Issue | Effort |
|---|-------|--------|
| 5 | Anyone can trigger verify on any job | Low — add party check |
| 6 | Anyone can trigger complete on any job | Low — add party check or remove endpoint |
| 21 | Deposit watcher tasks lost on restart | Medium — recover on startup |
| 22 | Withdrawal tasks lost on restart | Medium — recover on startup |
| 8 | Client-authored scripts can always fail | High — fundamental design issue |
