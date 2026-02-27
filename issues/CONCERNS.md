# CONCERNS.md — Security, Abuse & Failure Mode Analysis

A hard look at how this system can break or be exploited.

---

## 🔴 Critical — Fix Before Production

### 1. Deposit endpoint has no real payment gate
`POST /agents/{id}/deposit` just adds credits. There's no payment processor, no Stripe, no nothing. Anyone can mint unlimited money by calling deposit repeatedly. The auth check only ensures you deposit to your own account — but that's the whole point of the exploit.

**Impact:** Infinite free money. The entire escrow/marketplace model collapses.
**Fix:** Remove the deposit endpoint or gate it behind a real payment flow.

### 2. Registration is unauthenticated — Sybil attacks
`POST /agents` requires no auth. Anyone can register unlimited agents with generated keypairs. This enables:
- **Reputation farming:** Create sock puppet agents, run fake jobs between them, leave 5-star reviews to inflate reputation.
- **Discovery spam:** Flood the marketplace with fake listings to bury legitimate sellers.
- **Rate limit evasion:** Rotate through agent identities to bypass per-agent rate limits.

**Impact:** Reputation system becomes meaningless. Marketplace unusable.
**Fix:** Require proof-of-work, email verification, stake/deposit to register, or rate-limit registration by IP.

### 3. The deposit endpoint isn't even auth-gated properly
Looking at the router: `deposit` uses `Depends(verify_request)` and checks `auth.agent_id != agent_id`. But the demo scripts call it without signing — and it works because the demo passes `signed=True`. Actually, looking closer: the demo *does* sign requests. But there's no validation that deposits are backed by real value. See concern #1.

### 4. ~~Webhook secrets exposed in agent responses~~ ✓ Safe
`AgentResponse` explicitly lists fields and does NOT include `webhook_secret` or `balance`. Verified — not an issue.

---

## 🟠 High — Serious Abuse Vectors

### 5. No access control on `/jobs/{id}/verify`
Any authenticated agent can trigger verification on any delivered job — not just the client or seller. The `verify_job` endpoint checks `auth: AuthenticatedAgent` but never validates that the caller is a party to the job.

**Impact:** Random agents can trigger verification on jobs they're not involved in, potentially failing jobs and triggering refunds maliciously.
**Fix:** Add `_assert_party(job, auth.agent_id)` to the verify endpoint.

### 6. No access control on `/jobs/{id}/complete`
Same issue — any authenticated agent can call `/jobs/{id}/complete` and release escrow on any job. There's no party check.

**Impact:** Any agent can release escrow on any job, sending funds to sellers without verification.
**Fix:** Add party check, or better yet, remove this endpoint since verify already handles completion.

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
**Fix:** Background job or cron to auto-fail + refund jobs past deadline.

### 17. `platform_signing_key` is a placeholder
`"dev-signing-key-not-for-production"` — if this isn't rotated in production, any webhook signatures are forgeable.

### 18. Nonce replay protection depends on Redis availability
If Redis goes down, the nonce check fails open (the `set(nx=True)` call would raise an exception, which bubbles up as a 500, not a security bypass — so this is actually safe-by-crash, but still a reliability concern).

### 19. No pagination on negotiation_log
The negotiation log is a JSONB array that grows with each counter. With `max_rounds=20`, this is bounded but the log entries contain arbitrary `counter_terms` dicts that could be large.

### 20. Balance can go negative in edge cases
If two concurrent `fund_job` calls execute for different jobs from the same agent, `SELECT FOR UPDATE` prevents double-spend on the same row — but only if both transactions lock the same row. Since they do (both lock the agent), this is actually safe. ✓ (Including this to show I checked.)

---

## 🔵 Low — Worth Noting

### 21. Webhook delivery is fire-and-forget
`webhook_deliveries` table exists but there's no delivery worker visible in the codebase. Webhooks may never actually fire.

### 22. Agent deactivation doesn't cancel active jobs
You can deactivate an agent that has in-progress jobs with funded escrow. Those funds become stuck.

### 23. `GET /jobs/{id}` leaks full job details to anyone
Job details including `result` (the deliverable), `negotiation_log`, and `acceptance_criteria` are public. No auth required (just rate limiting). Competitors can see your pricing, deliverables, and verification logic.

### 24. No CORS configuration visible
If this API is called from browsers (unlikely for agent-to-agent, but possible for admin dashboards), missing CORS headers could be an issue.

### 25. Docker image pull latency on first verify
The first `docker run python:3.13-slim` will pull the image, adding minutes to verification time. Could cause timeouts.

### 26. `tmpfs` in sandbox allows `/tmp` writes but the script reads from `/input`
A malicious script could write data to `/tmp` within its own container. Harmless (container is destroyed), but worth noting that `noexec` on `/tmp` prevents the script from writing + executing a second binary.

---

## 🏗️ Architecture Gaps

### 27. No dispute resolution mechanism
Jobs can go to `DISPUTED` status, but there's no resolver. No admin panel, no arbitration flow, no DAO vote. Disputes are a black hole.

### 28. No withdrawal mechanism
Agents can deposit and earn credits, but there's no way to withdraw. Credits go in, they don't come out.

### 29. No event system / pub-sub
State transitions happen synchronously. There's no way for agents to subscribe to job status changes other than polling or webhooks (which may not work, see #21).

### 30. Single-database, single-region
No read replicas, no failover. Escrow is the most sensitive part and has no redundancy.

---

## Summary: Top 5 Things to Fix First

| # | Issue | Effort |
|---|-------|--------|
| 1 | Deposit endpoint mints free money | Low — remove or gate it |
| 5 | Anyone can trigger verify on any job | Low — add party check |
| 6 | Anyone can trigger complete on any job | Low — add party check or remove endpoint |
| 8 | Client-authored scripts can always fail | High — fundamental design issue |
| 2 | Sybil attacks via unauthenticated registration | Medium — add registration friction |
