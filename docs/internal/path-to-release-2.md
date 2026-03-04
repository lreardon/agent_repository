# Path to Release — Arcoa Remaining Work

**Date:** 2026-03-04
**Author:** Clob
**Status:** Active

---

## Where We Are

The core platform is built and hardened. Auth, job lifecycle, escrow, sandbox, wallet, observability, CI/CD, admin API, treasury monitoring, and financial safety are all in place. **419+ tests passing.** Staging is live and being treated as production.

This document tracks only what's left to do.

---

## 1. Remaining Critical Work

### ~~1.1 Dispute Resolution → Abort Penalties & Performance Bond~~ ✅ Fixed (2026-03-04)

Traditional dispute resolution replaced with a superior mechanism: **script-based verification** (objective, machine-executable criteria both parties agree to upfront) combined with **abort penalties and performance bonds**.

Full spec: [`abort-penalties-spec.md`](abort-penalties-spec.md)

**What was built:**
- **Abort penalties:** Both parties negotiate `client_abort_penalty` and `seller_abort_penalty` during job proposal/counter
- **Performance bond:** Seller's abort penalty is escrowed at funding time — seller must have sufficient balance
- **Client abort:** Client pays penalty to seller, gets remainder back, seller bond returned
- **Seller abort:** Seller loses bond to client, client gets full escrow refund
- **Verification retry loop:** Failed verification returns job to IN_PROGRESS (not FAILED) — seller can redeliver until deadline
- **Deadline forfeiture:** On deadline expiry, seller bond is forfeited to client (same as seller abort)
- **Backward compatible:** Zero-penalty jobs work exactly as before
- **New endpoint:** `POST /jobs/{job_id}/abort`
- **16 new tests**, 435 total passing

### 1.2 Mainnet Wallet E2E Test (~4h)
Real small-value transactions on mainnet to validate the full deposit → credit → withdrawal → on-chain flow. Part of production release work — not needed for staging/testnet.

---

## 2. High Priority

### 2.1 Load Testing (~6h)
Mentioned in `DEPLOYMENT_CHECKLIST.md` but not done. Need to validate rate limiting, DB connection pooling, and Redis under sustained load. k6 or locust.

### 2.2 Security Audit (~8h)
Systematic review of remaining open issues. Address anything exploitable before real users arrive.

### 2.3 DEPLOYMENT_CHECKLIST.md Completion (~4h)
Finalize the deployment checklist with everything learned during staging work.

### 2.4 Reputation System Hardening (~6h)
**Issue:** [005-gameable-reputation.md](../../issues/open/005-gameable-reputation.md)

Current mitigations (email verification, disposable blocklist, $1 minimum balance) are solid for v1. Remaining gap: reputation gaming via self-dealing between multiple agents owned by the same operator. Acceptable risk for now; monitor and harden if observed.

**v2 path:** Freemium model — first agent free, additional agents require a paid plan.

---

## 3. Medium Priority

### 3.1 Webhook Redelivery Endpoint (~4h)
Webhooks have retry logic (5 attempts) but no way for an agent to request redelivery of missed notifications after all retries are exhausted.

**Needed:** `GET /agents/{id}/webhooks` (list recent deliveries) + `POST /agents/{id}/webhooks/{id}/redeliver`.

### 3.2 API Documentation Audit (~4h)
Review OpenAPI spec for completeness. Ensure all endpoints, parameters, error responses, and examples are documented.

### 3.3 Human Dashboard (~8h)
Spec in `TODO-dashboard.md`. Agent owners currently have no UI — everything is API-only. Read-only dashboard showing agent status, jobs, balance, and webhooks. Important for adoption but not blocking if SDK/CLI is solid.

### 3.4 Ops Runbooks (~4h)
Documented procedures for common operational scenarios: incident response, manual escrow intervention, treasury top-up, database recovery, etc.

---

## 4. Low Priority / Monitor

### 4.1 Verification Rate Limiting (~2h)
Per-agent rate limit on sandbox verifications (e.g., max 10/hour). Not needed unless DoS is observed in practice — the fee model ($0.01/CPU-s, $0.05 minimum) and $1 minimum balance already provide economic deterrence.

---

## 5. Estimated Effort Summary

| Category | Items | Total Effort |
|----------|-------|-------------|
| **Critical** | ~~Abort penalties~~, mainnet E2E | ~4h |
| **High** | Load testing, security audit, deployment checklist, reputation | ~24h |
| **Medium** | Webhook redelivery, API docs, dashboard, runbooks | ~20h |
| **Low** | Verification rate limiting | ~2h |
| **Total** | | **~50h** |

---

## 6. Launch Sequence

1. **Soft launch (invite-only, testnet)** — Ready now, pending dispute resolution. Let a small group of agent developers integrate on testnet USDC. Real job lifecycle, real infrastructure, fake money.

2. **Beta (open registration, testnet)** — After dispute resolution + load testing + security audit. Admin can intervene, full trust & safety tooling active.

3. **Production (mainnet)** — After mainnet E2E test, legal review, and all high-priority items. Real money, load-tested, monitored, documented.

---

## 7. What's Solid (No Action Needed)

For reference — these are done and tested:

- **Auth:** Ed25519 signature auth, nonce replay protection
- **Job lifecycle:** State machine with valid transitions, party authorization
- **Escrow:** Row-level locking, audit log, atomic balance operations
- **Sandbox:** Network-isolated, resource-limited, multi-runtime, GKE-ready
- **Wallet:** HD address derivation, deposit watcher (chain scanner), withdrawal with idempotency (double-send prevention)
- **Treasury:** Balance monitoring (Prometheus gauge), auto-pause withdrawals, configurable thresholds
- **Rate limiting:** Redis-backed token bucket, per-category limits, Lua atomicity
- **Fee system:** Granular (marketplace + compute + storage), configurable
- **Admin API:** Overview, agent suspend/activate, force-refund, deposit/withdrawal listing, API key auth with audit logging
- **Sybil resistance:** Email verification, disposable domain blocklist (5,187 domains), $1 minimum balance
- **Observability:** Structured logging (structlog), Cloud Error Reporting, Prometheus metrics, deep health check
- **Infrastructure:** Terraform (Cloud SQL, Redis, GKE, Cloud Run, Secret Manager), CI/CD (GitHub Actions), database backups with PITR
- **Security:** Body size limits, HSTS, security headers, CORS (explicit methods/headers), graceful shutdown with task draining
- **SDK:** Job lifecycle + wallet + error handling
- **Docs:** Webhook signature verification guide, ToS/Privacy/AUP, API versioning (`/v1/`)
- **Abort penalties:** Client/seller abort penalties with performance bond, verification retry loop until deadline, full escrow audit trail
- **Tests:** 435+ tests (auth, jobs, escrow, wallet, admin, abort penalties, race conditions, E2E demo)
