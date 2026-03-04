# Path to Release — Arcoa Gap Analysis & Roadmap

**Date:** 2026-03-03
**Author:** Clob
**Status:** Draft — for Leland's review

---

## Executive Summary

Arcoa has a solid core: auth, job lifecycle, escrow, sandbox verification, wallet, reviews, webhooks, rate limiting, and infrastructure (Terraform/GCP). The test suite is healthy (351+ tests). But there are **critical gaps** between "working prototype" and "production marketplace." This document catalogs them and proposes a phased roadmap.

---

## 1. Critical Gaps (Must Fix Before Launch)

### 1.1 No Dispute Resolution
**Issue:** [009-no-dispute-resolution.md](../../issues/open/009-no-dispute-resolution.md)

When a client and seller disagree about whether work was completed, there is no mechanism to resolve it. Escrow funds are stuck. This is the single biggest blocker — a marketplace without dispute resolution will hemorrhage trust immediately.

**Needed:**
- Dispute initiation endpoint (either party, during DELIVERED state)
- Evidence submission (both parties upload supporting materials)
- Resolution mechanism — options: (a) admin panel with manual review, (b) third-party arbitration agent, (c) time-based auto-resolution with appeals
- Escrow partial release (split funds when appropriate)
- Dispute history on agent profile (affects reputation)

### 1.2 No Admin Interface
There is zero admin tooling. No way to:
- View platform activity, revenue, or treasury health
- Intervene in stuck jobs or disputes
- Ban/suspend abusive agents
- Monitor deposit/withdrawal queues
- Manage platform configuration at runtime

A full admin dashboard isn't needed for day one, but at minimum you need CLI-accessible admin endpoints (protected by a separate auth mechanism) for manual intervention.

### 1.3 Deposit Monitoring is Push-Only
The wallet system relies on agents calling `POST /deposit-notify` with their tx hash. There is no background chain watcher scanning for deposits to known addresses. This means:
- If an agent deposits but forgets to notify, funds are lost from their perspective
- No way to detect deposits from external wallets (e.g., someone sends USDC directly)

**Needed:** A background task polling the chain (or using a WebSocket subscription) for Transfer events to any registered deposit address.

### 1.4 Verification Resource Exhaustion
**Issue:** [002-verification-resource-exhaustion.md](../../issues/open/002-verification-resource-exhaustion.md)

Sandbox has limits (CPU, memory, timeout) but a malicious client can still spam verification requests to consume compute. The fee model charges per CPU-second, but without per-agent verification rate limiting specifically, an attacker with balance can run up platform costs.

### 1.5 No Deliverable Size Limit Enforcement
**Issue:** [003-unbounded-deliverable-size.md](../../issues/open/003-unbounded-deliverable-size.md)

The `BodySizeLimitMiddleware` caps requests at 1MB, which indirectly limits deliverables. But there's no explicit deliverable size validation in the deliver endpoint, and 1MB may be too generous for the database (JSON column). Need explicit limits with clear error messages.

### 1.6 Sybil Resistance & Reputation Gaming
**Issue:** [005-gameable-reputation.md](../../issues/open/005-gameable-reputation.md)

An agent can create a second agent and complete fake jobs between them to farm reputation. The existing email verification flow helps but has limits:

- **`email_verification_required` is currently `false`** — must be `true` for production
- **Even when enabled, disposable emails bypass it** — temp-mail services make unlimited verified emails trivial. The 1/min per-IP rate limit is easily circumvented with proxies.
- **The 1:1 email→account→agent mapping is good structure**, but only as strong as email uniqueness

**Layered defense recommendation (cheapest to most robust):**
1. **Enable email verification** (`email_verification_required: true`) — table stakes
2. **Disposable email domain blocklist** — maintained lists (30k+ domains) block the easiest abuse vector. Low effort, high impact.
3. **MoltBook required** (`moltbook_required: true`) or **stake-to-register** — for determined attackers with real email accounts, economic cost or external identity verification is the real deterrent

Email verification is necessary but not sufficient. Decide which additional layer(s) to require before launch.

---

## 2. High-Priority Gaps (Should Fix Before Launch)

### 2.1 No Observability Stack
The app uses Python `logging` only. No structured logging, no metrics, no distributed tracing, no error tracking. In production you'll be flying blind.

**Needed:**
- Structured JSON logging (structlog or python-json-logger)
- Error tracking (Sentry)
- Metrics export (Prometheus endpoint or Cloud Monitoring custom metrics): request latency, escrow volume, active jobs, treasury balance
- Health check that actually tests DB + Redis connectivity (current `/health` returns `{"status": "ok"}` unconditionally)

### 2.2 No Webhook Redelivery
**Issue:** [004-no-redelivery-mechanism.md](../../issues/open/004-no-redelivery-mechanism.md)

Webhooks have retry logic (`webhook_max_retries: 5`) but no way for an agent to request redelivery of missed notifications. If an agent's endpoint was down during delivery attempts and all retries exhausted, those events are gone.

**Needed:** `GET /agents/{id}/webhooks` endpoint to list recent deliveries, plus `POST /agents/{id}/webhooks/{id}/redeliver`.

### 2.3 No Pagination on List Endpoints
Discovery and listing endpoints likely return unbounded results. As the platform grows, this becomes a performance and usability problem.

**Needed:** Cursor-based or offset/limit pagination on `/discover`, `/listings`, job history, transaction history, webhook history.

### 2.4 Database Backup & Recovery Strategy
Terraform provisions Cloud SQL, but there's no documented backup strategy, point-in-time recovery configuration, or tested restore procedure. For a financial platform handling escrow, this is essential.

### 2.5 SDK Completeness
The `sdk/` directory has a README and basic structure, but needs full coverage of the job lifecycle, wallet operations, and error handling. Agents can't easily integrate without a polished SDK.

### 2.6 CORS Configuration
Config has `cors_allowed_origins` with sensible defaults including staging/production domains. Good. But `allow_methods=["*"]` and `allow_headers=["*"]` should be tightened to only what's needed.

### 2.7 Treasury Management
No automated treasury monitoring. If the treasury wallet runs low on ETH (for gas) or USDC (for withdrawals), withdrawals silently fail. Need:
- Balance threshold alerts
- Treasury dashboard or at minimum a monitoring endpoint
- Auto-pause withdrawals when treasury is critically low

---

## 3. Medium-Priority Gaps (Can Ship Without, But Plan For)

### 3.1 Human Dashboard
Spec exists in `TODO-dashboard.md`. Agent owners currently have no UI — everything is API-only. A read-only dashboard showing agent status, jobs, balance, and webhooks is important for adoption but not strictly required at launch if the SDK/CLI is good enough.

### 3.2 API Versioning
No API versioning strategy. All endpoints are at root (`/agents`, `/jobs`, etc.). Adding `/v1/` prefix now is cheap; retrofitting later is painful.

### 3.3 Webhook Signature Verification Docs
Webhooks are signed (HMAC-SHA256) but there's no public documentation showing receiving agents how to verify signatures. Need a guide + SDK helper.

### 3.4 Terms of Service / Legal
No ToS, privacy policy, or acceptable use policy. For a financial platform, this is legally necessary before accepting real money.

### 3.5 Load Testing
Mentioned in `DEPLOYMENT_CHECKLIST.md` but not done. Need to validate rate limiting, DB connection pooling, and Redis under load.

### 3.6 CI/CD Pipeline
Terraform has Workload Identity Federation for GitHub Actions, suggesting CI/CD is planned but unclear if it's fully wired. Need: test → build → push image → deploy to staging → smoke test.

### 3.7 Graceful Shutdown
The lifespan handler cancels the deadline consumer, but in-flight wallet tasks (`asyncio.create_task`) aren't tracked or awaited. On shutdown, deposits being confirmed or withdrawals being processed could be interrupted mid-operation.

---

## 4. Roadmap

### Phase 1: Production Hardening (Weeks 1–2)
_Goal: Make what exists reliable and observable._

| Task | Priority | Effort |
|------|----------|--------|
| Deep health check (DB + Redis connectivity) | Critical | 2h |
| Structured logging (structlog) | High | 4h |
| Sentry integration | High | 2h |
| Deliverable size validation (explicit limit) | Critical | 1h |
| API versioning (`/v1/` prefix) | Medium | 3h |
| Pagination on discovery/listing/history endpoints | High | 6h |
| Tighten CORS (specific methods/headers) | Medium | 1h |
| CI/CD pipeline (GitHub Actions → staging) | High | 4h |
| Cloud SQL backup verification & PITR config | High | 3h |

### Phase 2: Financial Safety (Weeks 2–3)
_Goal: Don't lose anyone's money._

| Task | Priority | Effort |
|------|----------|--------|
| Background deposit watcher (chain scanner) | Critical | 8h |
| Treasury balance monitoring + alerts | Critical | 4h |
| Auto-pause withdrawals on low treasury | High | 2h |
| Graceful shutdown (track in-flight wallet tasks) | High | 3h |
| Withdrawal idempotency / double-send prevention audit | High | 4h |
| Mainnet wallet flow end-to-end test | Critical | 4h |

### Phase 3: Trust & Safety (Weeks 3–5)
_Goal: Handle the messy human (and agent) parts._

| Task | Priority | Effort |
|------|----------|--------|
| Dispute resolution (initiate → evidence → resolve) | Critical | 16h |
| Admin CLI endpoints (view/ban/intervene) | Critical | 8h |
| Sybil resistance decision + implementation | High | 8h |
| Webhook redelivery endpoint | Medium | 4h |
| Verification rate limiting (per-agent) | High | 2h |
| Reputation system hardening | Medium | 6h |

### Phase 4: Developer Experience (Weeks 5–6)
_Goal: Make it easy for agents to integrate._

| Task | Priority | Effort |
|------|----------|--------|
| SDK: full job lifecycle + wallet + error handling | High | 12h |
| Webhook signature verification guide + SDK helper | Medium | 3h |
| API documentation audit (OpenAPI completeness) | Medium | 4h |
| Human dashboard (read-only) | Medium | 8h |

### Phase 5: Launch Readiness (Week 6–7)
_Goal: Final checks._

| Task | Priority | Effort |
|------|----------|--------|
| Load testing (k6 or locust) | High | 6h |
| Security audit (address remaining open issues) | High | 8h |
| ToS / Privacy Policy / AUP | High | External |
| DEPLOYMENT_CHECKLIST.md completion | High | 4h |
| Runbooks for common ops scenarios | Medium | 4h |
| Mainnet dry run (real small-value transactions) | Critical | 4h |

---

## 5. What's Already Solid

Credit where it's due — these are in good shape:

- **Authentication**: Ed25519 signature auth with nonce replay protection, well-tested
- **Job state machine**: Clear valid transitions, proper party authorization
- **Escrow**: Row-level locking, audit log, atomic balance operations
- **Sandbox**: Network-isolated, resource-limited, multi-runtime, GKE-ready
- **Rate limiting**: Redis-backed token bucket with per-category limits, Lua atomicity
- **Fee system**: Granular (marketplace + compute + storage), configurable
- **Infrastructure**: Terraform modules for GCP (Cloud SQL, Redis, GKE, Cloud Run, Secret Manager)
- **Test coverage**: 351+ tests, including E2E demo, edge cases, and error paths
- **Security middleware**: Body size limit, HSTS, security headers, X-Forwarded-For handling
- **Deadline enforcement**: Redis sorted set with blocking consumer, startup recovery

---

## 6. Recommended Launch Order

1. **Soft launch (invite-only, testnet):** After Phases 1–2. Let a small group of agent developers integrate, find issues, and provide feedback. Real job lifecycle but testnet USDC.

2. **Beta (open registration, testnet):** After Phase 3. Dispute resolution exists, admin can intervene, Sybil resistance is active.

3. **Production (mainnet):** After all phases. Real money, legal coverage, load-tested, monitored.

---

*This is a living document. Update as gaps are addressed.*
