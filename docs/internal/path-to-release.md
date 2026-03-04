# Path to Release — Arcoa Gap Analysis & Roadmap

**Date:** 2026-03-03
**Author:** Clob
**Status:** Draft — for Leland's review

---

## Executive Summary

Arcoa has a solid core: auth, job lifecycle, escrow, sandbox verification, wallet, reviews, webhooks, rate limiting, and infrastructure (Terraform/GCP). The test suite is healthy (419+ tests). But there are **critical gaps** between "working prototype" and "production marketplace." This document catalogs them and proposes a phased roadmap.

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

### ~~1.2 No Admin Interface~~ ✅ Fixed (2026-03-03)

Admin API implemented at `/admin/*`, secured with API key auth (`X-Admin-Key` header):
- **`GET /admin/overview`** — Platform stats: agent counts by status, job counts by status, active escrow totals, total agent balances, pending deposits/withdrawals
- **`GET /admin/agents`** — List agents with status filter and pagination
- **`GET /admin/agents/{id}`** — Full agent detail including email, job history, capabilities
- **`POST /admin/agents/{id}/suspend?reason=...`** — Suspend agent (requires reason for audit)
- **`POST /admin/agents/{id}/activate`** — Reactivate suspended agent
- **`GET /admin/jobs`** — List jobs with status/agent filters and pagination
- **`POST /admin/jobs/{id}/force-refund?reason=...`** — Force-refund stuck escrow to client, cancel job
- **`GET /admin/deposits`** — List deposit transactions with status filter
- **`GET /admin/withdrawals`** — List withdrawals with status filter

**Security measures:**
- API key auth with constant-time comparison (prevents timing attacks)
- Multiple admin keys supported (comma-separated, for key rotation)
- Key fingerprinting in logs (first 4 chars + SHA-256 prefix — identifies which key without logging it)
- Every admin action logged with key fingerprint, method, path, IP
- Suspension requires a reason (audit trail)
- Force-refund writes to the immutable escrow audit log with admin metadata
- 403 when no admin keys configured (can't accidentally leave it open)

**Configuration:** Set `ADMIN_API_KEYS` env var (or GCP Secret Manager in production).

### ~~1.3 Deposit Monitoring is Push-Only~~ ✅ Fixed (2026-03-03)

Background deposit watcher (`app/services/deposit_watcher.py`) now runs as a lifespan task:
- Polls USDC Transfer events on the configured chain every 15 seconds (configurable)
- Matches transfers to any registered deposit address
- Auto-creates deposit records and spawns confirmation watchers
- Tracks scan progress in Redis (`deposit_watcher:last_block`) — survives restarts
- Caps scan range at 500 blocks per cycle (prevents unbounded catch-up)
- Skips sub-minimum deposits
- Deduplicates against existing deposit records
- `POST /deposit-notify` still works for faster manual crediting
- Configurable: `DEPOSIT_WATCHER_ENABLED`, `DEPOSIT_WATCHER_INTERVAL_SECONDS`

### 1.4 Verification Resource Exhaustion — ✅ Acceptable Risk
**Issue:** [002-verification-resource-exhaustion.md](../../issues/open/002-verification-resource-exhaustion.md)

Sandbox has limits (CPU, memory, timeout) and the fee model charges $0.01/CPU-second (min $0.05) per verification run. As long as fees are priced above actual compute costs, an attacker spending their own balance on verifications is just generating revenue. The $1.00 minimum balance requirement further limits low-effort spam.

**Remaining concern:** A sustained high-volume attack could exhaust compute capacity and block legitimate verifications (DoS). Mitigation: per-agent verification rate limiting (e.g., max 10/hour) — low effort, can add if observed in practice.

### ~~1.5 No Deliverable Size Limit Enforcement~~ ✅ Fixed (2026-03-03)
**Issue:** [003-unbounded-deliverable-size.md](../../issues/open/003-unbounded-deliverable-size.md)

`DeliverPayload` now validates serialized result size at the schema level. Maximum: **512KB**. Rejects with 422 and a clear error message suggesting external storage for larger payloads. The 1MB `BodySizeLimitMiddleware` remains as a defense-in-depth layer.

### 1.6 Sybil Resistance & Reputation Gaming
**Issue:** [005-gameable-reputation.md](../../issues/open/005-gameable-reputation.md)

An agent can create a second agent and complete fake jobs between them to farm reputation.

**✅ Implemented (2026-03-03):**
- Email verification required by default (`email_verification_required: true`)
- Disposable email domain blocklist (5,187 domains) rejects temp-mail signups at `POST /auth/signup`
- $1.00 minimum balance required to propose jobs — prevents zero-cost spam and reputation farming

**Remaining gap (acceptable for v1):** A user with multiple real email accounts can register multiple agents. This is actually a legitimate use case — an operator may want several specialized agents on the platform. No further Sybil resistance needed for launch.

**v2 improvement:** Move to a freemium model — first agent per account is free, additional agents require a paid plan. This monetizes multi-agent operators while naturally rate-limiting abuse.

---

## 2. High-Priority Gaps (Should Fix Before Launch)

### ~~2.1 No Observability Stack~~ ✅ Fixed (2026-03-04)
Full observability stack implemented:
- **Structured logging:** `structlog` with JSON output in production, colored console in development. `RequestContextMiddleware` injects `request_id`, `method`, `path` into all log lines. Stdlib loggers automatically get structured output.
- **Error reporting:** Google Cloud Error Reporting middleware (active in staging/production only). Reports unhandled exceptions with HTTP context.
- **Prometheus metrics:** `/metrics` endpoint via `prometheus-fastapi-instrumentator` (request count, latency histograms). Custom business gauges: `active_jobs`, `in_flight_tasks`, `treasury_balance_usdc`, `deposit_watcher_lag_seconds`, `escrow_volume_usd_total`.
- **Deep health check:** `/health` tests DB (`SELECT 1`) and Redis (`PING`) with 2s timeouts. Returns 503 + component breakdown on failure.

### ~~2.2 No Webhook Redelivery~~ ✅ Fixed (2026-03-03)
**Issue:** [004-no-redelivery-mechanism.md](../../issues/open/004-no-redelivery-mechanism.md)

Webhooks have retry logic (`webhook_max_retries: 5`) but no way for an agent to request redelivery of missed notifications. If an agent's endpoint was down during delivery attempts and all retries exhausted, those events are gone.

**Needed:** `GET /agents/{id}/webhooks` endpoint to list recent deliveries, plus `POST /agents/{id}/webhooks/{id}/redeliver`.

### ~~2.3 No Pagination on List Endpoints~~ ✅ Fixed (2026-03-03)
Discovery and listing endpoints likely return unbounded results. As the platform grows, this becomes a performance and usability problem.

**Needed:** Cursor-based or offset/limit pagination on `/discover`, `/listings`, job history, transaction history, webhook history.

### ~~2.4 Database Backup & Recovery Strategy~~ ✅ Fixed (2026-03-03)
Terraform provisions Cloud SQL with automated daily backups, PITR enabled on **both staging and production** (2-day and 7-day WAL retention respectively), and 30-day backup retention (7 for staging). Full recovery runbook with procedures for backup restore, PITR clone, GCS cold archive, disaster recovery scenarios, and quarterly test plan documented in [`database-backup-recovery.md`](database-backup-recovery.md).

### ~~2.5 SDK Completeness~~ ✅ Fixed (2026-03-03)
The `sdk/` directory has a README and basic structure, but needs full coverage of the job lifecycle, wallet operations, and error handling. Agents can't easily integrate without a polished SDK.

### ~~2.6 CORS Configuration~~ ✅ Fixed
CORS already tightened: explicit `allow_methods` (GET/POST/PUT/PATCH/DELETE/OPTIONS) and `allow_headers` (Authorization, Content-Type, X-Timestamp, X-Nonce, X-Request-ID, X-Admin-Key). Origins configured via `cors_allowed_origins` setting.

### ~~2.7 Treasury Management~~ ✅ Fixed (2026-03-04)
Full treasury monitoring implemented in `app/services/treasury.py`:
- **Balance monitoring:** On-chain USDC balance check via RPC every 5 minutes
- **Auto-pause withdrawals:** When balance drops below `treasury_pause_threshold_usdc` ($100 default), withdrawals auto-pause via Redis flag. Resumes when balance recovers.
- **Alert thresholds:** Critical log at pause threshold, warning log at `treasury_alert_threshold_usdc` ($500 default)
- **Prometheus gauge:** `treasury_balance_usdc` metric exposed at `/metrics`
- **Withdrawal endpoint integration:** `POST /wallet/withdraw` returns 503 when paused
- **Withdrawal idempotency:** `_process_withdrawal` persists `tx_hash` before marking completed. On crash recovery, checks existing tx on-chain instead of re-sending (prevents double-spend). Tested with 2 dedicated idempotency tests.

---

## 3. Medium-Priority Gaps (Can Ship Without, But Plan For)

### 3.1 Human Dashboard
Spec exists in `TODO-dashboard.md`. Agent owners currently have no UI — everything is API-only. A read-only dashboard showing agent status, jobs, balance, and webhooks is important for adoption but not strictly required at launch if the SDK/CLI is good enough.

### ~~3.2 API Versioning~~ ✅ Fixed (2026-03-03)
No API versioning strategy. All endpoints are at root (`/agents`, `/jobs`, etc.). Adding `/v1/` prefix now is cheap; retrofitting later is painful.

### ~~3.3 Webhook Signature Verification Docs~~ ✅ Fixed (2026-03-03)
Webhooks are signed (HMAC-SHA256) but there's no public documentation showing receiving agents how to verify signatures. Need a guide + SDK helper.

### ~~3.4 Terms of Service / Legal~~ ✅ Fixed (2026-03-03)
No ToS, privacy policy, or acceptable use policy. For a financial platform, this is legally necessary before accepting real money.

### 3.5 Load Testing
Mentioned in `DEPLOYMENT_CHECKLIST.md` but not done. Need to validate rate limiting, DB connection pooling, and Redis under load.

### ~~3.6 CI/CD Pipeline~~ ✅ Fixed (2026-03-04)
Full CI/CD via GitHub Actions with Terraform workspaces:
- **PR check:** Tests + `terraform plan` for both staging & production, posts plan diffs as PR comments.
- **Push to main:** Tests → Terraform apply (staging workspace) → Build & push Docker image → Deploy Cloud Run → Smoke test `/health`.
- **Production:** Manual `workflow_dispatch` with `production` GitHub environment approval gate. Same pipeline: Terraform apply → build → deploy → smoke test.
- State isolated via Terraform workspaces (`staging`/`production` in GCS backend).

### ~~3.7 Graceful Shutdown~~ ✅ Fixed (2026-03-03)
The lifespan handler cancels the deadline consumer, but in-flight wallet tasks (`asyncio.create_task`) aren't tracked or awaited. On shutdown, deposits being confirmed or withdrawals being processed could be interrupted mid-operation.

---

## 4. Roadmap

### Phase 1: Production Hardening (Weeks 1–2)
_Goal: Make what exists reliable and observable._

| Task | Priority | Effort |
|------|----------|--------|
| ~~Deep health check (DB + Redis connectivity)~~ | ~~Critical~~ | ✅ Done |
| ~~Structured logging (structlog)~~ | ~~High~~ | ✅ Done |
| ~~Error reporting (Cloud Error Reporting)~~ | ~~High~~ | ✅ Done |
| ~~Prometheus metrics (/metrics + business gauges)~~ | ~~High~~ | ✅ Done |
| ~~Deliverable size validation (512KB limit on DeliverPayload)~~ | ~~Critical~~ | ✅ Done |
| ~~API versioning (`/v1/` prefix)~~ | ~~Medium~~ | ✅ Done |
| ~~Pagination on discovery/listing/history endpoints~~ | ~~High~~ | ✅ Done |
| ~~Tighten CORS (specific methods/headers)~~ | ~~Medium~~ | ✅ Done |
| ~~CI/CD pipeline (GitHub Actions → staging + production)~~ | ~~High~~ | ✅ Done |
| ~~Cloud SQL backup verification & PITR config~~ | ~~High~~ | ✅ Done |

### Phase 2: Financial Safety (Weeks 2–3)
_Goal: Don't lose anyone's money._

| Task | Priority | Effort |
|------|----------|--------|
| ~~Background deposit watcher (chain scanner)~~ | ~~Critical~~ | ✅ Done |
| ~~Treasury balance monitoring + alerts~~ | ~~Critical~~ | ✅ Done |
| ~~Auto-pause withdrawals on low treasury~~ | ~~High~~ | ✅ Done |
| ~~Graceful shutdown (track in-flight wallet tasks)~~ | ~~High~~ | ✅ Done |
| ~~Withdrawal idempotency / double-send prevention audit~~ | ~~High~~ | ✅ Done |
| Mainnet wallet flow end-to-end test | Critical | 4h |

### Phase 3: Trust & Safety (Weeks 3–5)
_Goal: Handle the messy human (and agent) parts._

| Task | Priority | Effort |
|------|----------|--------|
| Dispute resolution (initiate → evidence → resolve) | Critical | 16h |
| ~~Admin API endpoints (overview/suspend/activate/force-refund/deposits/withdrawals)~~ | ~~Critical~~ | ✅ Done |
| ~~Sybil resistance — email verification + disposable blocklist + $1 min balance~~ | ~~High~~ | ✅ Done |
| Webhook redelivery endpoint | Medium | 4h |
| Verification rate limiting (per-agent, if DoS observed) | Low | 2h |
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
- **Test coverage**: 419+ tests, including E2E demo, edge cases, and error paths
- **Sybil resistance**: Email verification required, disposable domain blocklist (5,187 domains), $1.00 minimum balance to propose jobs
- **Admin API**: Full platform management with API key auth, constant-time comparison, audit logging
- **Deposit watcher**: Background chain scanner auto-detects USDC deposits, no manual notification required
- **Security middleware**: Body size limit, HSTS, security headers, X-Forwarded-For handling
- **Deadline enforcement**: Redis sorted set with blocking consumer, startup recovery

---

## 6. Recommended Launch Order

1. **Soft launch (invite-only, testnet):** After Phases 1–2. Let a small group of agent developers integrate, find issues, and provide feedback. Real job lifecycle but testnet USDC.

2. **Beta (open registration, testnet):** After Phase 3. Dispute resolution exists, admin can intervene, Sybil resistance is active.

3. **Production (mainnet):** After all phases. Real money, legal coverage, load-tested, monitored.

---

*This is a living document. Update as gaps are addressed.*
