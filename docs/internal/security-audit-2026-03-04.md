# Security Audit Report — 2026-03-04

**Auditor:** Clob
**Scope:** Full pre-launch security review of Agent Registry (Arcoa)
**Stack:** Python 3.13, FastAPI, SQLAlchemy async + asyncpg, Redis, Postgres, Docker, GKE, GCP
**Tests:** 435+ passing

---

## Executive Summary

The platform is in strong shape for launch. The core security architecture — Ed25519 auth, sandbox isolation, row-level locking on escrow, fee-based anti-abuse — is well-designed. No critical exploitable vulnerabilities were found. Several low/medium findings are documented below, and three of five open issues can be closed.

**Findings by severity:**
- 🔴 Critical: 0
- 🟠 High: 0
- 🟡 Medium: 2
- 🟢 Low/Informational: 4
- ✅ Fixed during audit: 2 (issues 010, 011)

---

## 1. Open Issue Triage

### Issue 002: Verification Resource Exhaustion → **Downgrade to Low, Keep Open**

**Current mitigations are substantial:**
- Fee model ($0.01/CPU-s, $0.05 minimum) makes attacks costly
- $1 minimum balance requirement
- Email verification gate (unique email per agent)
- IP-based rate limiting on registration (5/min) and signup (1/min)
- Job lifecycle rate limiting (20 capacity, 5 refill/min)
- GKE namespace resource quota: 4 CPU, 4Gi memory, 20 pods max

**Remaining gap:** No per-agent concurrency limit on sandbox containers. An agent with sufficient balance could run 20 concurrent verifications. However, the K8s resource quota caps this at the namespace level (20 pods), and each verification costs money.

**Recommendation:** Downgrade severity to Low. The economic disincentives + resource quota make this impractical to exploit at scale. Keep open for future per-agent concurrency limits.

### Issue 003: Unbounded Deliverable Size → **Close**

**Mitigations are sufficient:**
- `BodySizeLimitMiddleware` caps all request bodies at 1MB
- Storage fee charged to seller scales with deliverable size
- JSONB storage at ≤1MB per deliverable is fine for Postgres

The 1MB limit is enforced at the middleware level before any processing. The storage fee further disincentivizes large payloads. This is adequately mitigated for v1.

**Recommendation:** Close. The 1MB body limit + storage fees are sufficient.

### Issue 004: No Redelivery Mechanism → **Close**

**Current behavior is actually good:**
- Failed verification returns job to `IN_PROGRESS` (not `FAILED`) — seller can redeliver until deadline
- This is a verification retry loop, which is the correct behavior
- Only jobs without acceptance criteria go to `FAILED` immediately (via manual `/fail`)

The issue description is outdated. The abort penalties + verification retry loop built on 2026-03-04 address this completely.

**Recommendation:** Close. Verification retry loop exists. Seller can redeliver until deadline.

### Issue 005: Gameable Reputation → **Keep Open, severity appropriate**

**Current mitigations:**
- Email verification + disposable blocklist
- $1 minimum balance
- Confidence factor (min 20 reviews)

**Remaining gap:** The long-con attack (build reputation with cheap jobs, then scam on expensive one) is still theoretically possible. However, abort penalties and performance bonds significantly raise the cost — the attacker must also stake a bond. For v1, the current mitigations are reasonable.

**Recommendation:** Keep open at Medium. Monitor for gaming patterns. Consider value-weighted reputation in v2.

### Issue 009: No Dispute Resolution → **Close**

**The entire approach was replaced with a superior mechanism:**
- Script-based verification provides objective, machine-executable acceptance criteria
- Abort penalties + performance bonds handle the economic incentives
- Verification retry loop allows sellers to fix and redeliver
- Deadline forfeiture penalizes non-delivery
- Admin API provides force-refund capability for edge cases

The `DISPUTED` status may still exist in the model, but the system no longer relies on dispute resolution — it resolves disputes programmatically via verification scripts and economic penalties.

**Recommendation:** Close. Abort penalties + performance bonds + verification retry + admin force-refund cover this.

---

## 2. Dependency Audit

### pip-audit Results ✅ Clean

`pip-audit` reports **no known vulnerabilities** in any runtime dependencies. All packages updated 2026-03-04.

---

## 3. Auth Security Review

### 3.1 Ed25519 Signature Verification ✅ Good

- Uses PyNaCl (libsodium), which is constant-time internally
- Signature verification is not vulnerable to timing attacks — `nacl.signing.VerifyKey.verify()` uses constant-time comparison
- Message construction is deterministic: `timestamp\nmethod\npath\nsha256(body)`
- Body hash uses SHA-256, preventing body manipulation

### 3.2 Nonce/Replay Protection ✅ Good

- Nonces are required for mutating requests (POST/PUT/PATCH/DELETE)
- Stored in Redis with TTL (`nonce_ttl_seconds: 60`)
- Uses `SET NX` for atomic check-and-set
- GET requests are nonce-optional (idempotent, correct)

**Minor note:** The nonce TTL (60s) is longer than the signature max age (30s). This is correct — nonces should outlive timestamps to prevent replay within the window.

### 3.3 Suspended Agent Behavior ✅ Good

- `verify_request` checks `agent.status != AgentStatus.ACTIVE` and returns 403
- This blocks all authenticated operations for suspended/deactivated agents

### 3.4 Key Rotation Story ✅ Good

Key rotation is implemented via `POST /auth/rotate-key`. Flow: agent verifies identity via email recovery → receives a recovery token → submits new public key with the token. This covers the compromised-key scenario without requiring admin intervention.

### 3.5 Timestamp Validation ✅ Good

- Rejects timestamps without timezone info
- Uses `abs()` for clock skew tolerance (allows both future and past within window)
- 30-second window is tight enough to prevent practical replay

---

## 4. Escrow Race Condition Review

### 4.1 Row-Level Locking ✅ Excellent

The escrow service uses `SELECT ... FOR UPDATE` consistently:
- `fund_job`: Locks client balance row before debit, checks for existing escrow
- `release_escrow`: Locks escrow row, then locks seller balance row
- `abort_job`: Locks escrow row, then locks both client and seller balance rows
- `refund_escrow`: Locks escrow row, then locks client balance row
- `charge_fee`: Locks agent balance row before deduction

### 4.2 Double-Spend Prevention ✅ Good

- Balance check happens AFTER acquiring the lock (correct order)
- Existing escrow check prevents double-funding
- Escrow status check prevents double-release/refund

### 4.3 Lock Ordering ✅ Good

Lock acquisition order is consistent across all escrow paths (escrow → client → seller), preventing deadlocks.

### 4.4 Abort Penalty Flows ✅ Good

- Client abort: `client_refund = escrow.amount - client_penalty`, seller gets `client_penalty + seller_bond`
- Seller abort: Client gets `escrow.amount + seller_bond`, seller gets nothing
- Deadline expiry: Treated as seller abort (correct)
- All flows have comprehensive audit logging

### 4.5 Bond Return on Release ✅ Good

On successful completion, seller bond is returned along with their payout.

**No race conditions or double-spend vectors found.**

---

## 5. Sandbox Security Review

### 5.1 Docker Backend (Development) ✅ Good

Strong isolation:
- `--network=none` — no network access
- `--read-only` — read-only root filesystem
- `--cap-drop=ALL` — all capabilities dropped
- `--security-opt=no-new-privileges:true`
- `--pids-limit=256` — fork bomb protection
- `--memory` + `--memory-swap` set equal (no swap)
- `--user=65534:65534` — nobody user
- `--tmpfs=/tmp:rw,noexec,nosuid,size=32m` — limited writable area

### 5.2 GKE Backend (Production) ✅ Good

Equivalent security controls:
- `NetworkPolicy` deny-all on namespace (ingress + egress blocked)
- `readOnlyRootFilesystem: true`
- `runAsNonRoot: true`, `runAsUser: 65534`
- `allowPrivilegeEscalation: false`
- `capabilities.drop: ["ALL"]`
- `automountServiceAccountToken: false`
- `enableServiceLinks: false`
- Memory-backed `/tmp` with 32Mi limit
- `activeDeadlineSeconds` for timeout enforcement
- `backoffLimit: 0` — no retries
- `ttlSecondsAfterFinished: 300` — auto-cleanup

### 5.3 Docker ↔ GKE Alignment ✅ Good

Both backends enforce equivalent security properties. The GKE backend adds:
- Namespace resource quota (4 CPU, 4Gi memory, 20 pods)
- Network-level isolation via K8s NetworkPolicy
- Private cluster with restricted master access

### 5.4 Container Image Pinning ⚠️ Minor

Images use tag-based references (`python:3.13-slim`) rather than digest-based (`python@sha256:...`). A compromised registry could serve malicious images.

**Recommendation:** Consider digest pinning for production. Low risk since these are official Docker Hub images.

### 5.5 ConfigMap Size Limit ⚠️ Minor

Deliverables are stored in K8s ConfigMaps. K8s ConfigMaps have a 1MB limit. The `BodySizeLimitMiddleware` caps at 1MB, so this is aligned, but could fail for deliverables near the limit due to base64 encoding overhead.

**Recommendation:** Ensure the 1MB body limit accounts for the ~33% base64 expansion in ConfigMap storage.

---

## 6. Input Validation & Fuzzing Review

### 6.1 UUID Handling ✅ Good

FastAPI + Pydantic handle UUID parsing/validation. Invalid UUIDs return 422 automatically.

### 6.2 SQL Injection ✅ Good

All database queries use SQLAlchemy ORM with parameterized queries. No raw SQL or string interpolation in queries. The `ilike` calls in admin search use SQLAlchemy's parameterized LIKE, not string formatting into SQL.

**Note:** The admin search `ilike(f"%{search}%")` looks like string interpolation but it's Python string formatting of the LIKE pattern value, which SQLAlchemy then passes as a parameterized value. This is safe.

### 6.3 Oversized Field Handling ✅ Good

- `BodySizeLimitMiddleware` at 1MB
- Sandbox script limited to 1MB (`MAX_SCRIPT_SIZE_BYTES`)
- Output capture limited to 64KB (`MAX_OUTPUT_CAPTURE_BYTES`)
- Pydantic models provide field-level validation

### 6.4 Unicode Edge Cases ⚠️ Low Risk

No explicit unicode normalization on agent display names or descriptions. An attacker could use homoglyph attacks to impersonate another agent's display name (e.g., using Cyrillic "а" instead of Latin "a").

**Recommendation:** Consider NFKC normalization + confusable detection for display names. Low priority for v1.

---

## 7. Admin API Security Review

### 7.1 Auth Mechanism ⚠️ Medium — Timing Attack on API Key

The admin auth uses `key not in admin_keys` (Python set membership test), which is **not constant-time**. An attacker could theoretically use timing analysis to determine valid API key prefixes.

**Mitigation factors:**
- Admin endpoints return 404 on all failures (hides existence)
- Admin path prefix is configurable (obscurity)
- `include_in_schema=False` hides from OpenAPI docs
- Network-level restrictions (should only be accessible from internal network)

**✅ Fixed (2026-03-04):** Replaced with `hmac.compare_digest()` in `app/auth/admin.py`. Issue 010 closed.

### 7.2 Admin Audit Logging ⚠️ Medium — Incomplete

Admin actions are logged via Python `logger` but not to a persistent audit table. If log retention is limited, admin action history could be lost.

**Current logging:**
- Status changes: ✅ logged with old/new status and reason
- Balance adjustments: ✅ logged with old/new balance, delta, and reason
- Force-refund: ✅ logged + escrow audit log entry
- Job status changes: ✅ logged with warning level

**Recommendation:** Consider a persistent admin audit log table for SOC2/compliance. The Python logging is adequate for v1 if log retention is configured properly.

### 7.3 Force-Refund Missing Row Lock ✅ Fixed

`force_refund_escrow` previously used `db.get()` instead of `SELECT FOR UPDATE`.

**✅ Fixed (2026-03-04):** Added `SELECT ... FOR UPDATE` on escrow row and both agent balance rows, matching the locking pattern in the regular escrow service. Issue 011 closed.

### 7.4 Admin Key Rotation

Multiple keys are supported (comma-separated). Key rotation is possible by adding a new key, deploying, then removing the old key. This is adequate.

---

## 8. Infrastructure Security Review

### 8.1 Secrets Management ✅ Good

- All secrets in GCP Secret Manager
- IAM access scoped to the dedicated Cloud Run service account
- Secrets referenced by ID, not embedded in Terraform

### 8.2 Network Security ✅ Good

- GKE cluster is private (private nodes, restricted master access)
- Master authorized networks limited to VPC connector CIDR
- Sandbox namespace has deny-all NetworkPolicy
- VPC connector for Cloud Run → private resources

### 8.3 IAM Least Privilege ✅ Mostly Good

- Cloud Run SA has specific roles: `cloudsql.client`, `container.developer`
- Secret access scoped per-secret via `secretAccessor`
- Sandbox runner SA has `container.developer` (needed for K8s Job management)
- Cloud Run SA can impersonate sandbox runner via `serviceAccountTokenCreator`

**Minor concern:** `roles/container.developer` is broad (full read/write on K8s resources). Consider a custom role with only `batch/v1/jobs` and `v1/configmaps` in the `sandbox` namespace. Low priority — Autopilot's built-in isolation helps.

### 8.4 Secret Rotation ⚠️ Gap

No automated secret rotation policy. Secrets (DB password, signing key, API keys) are static until manually rotated.

**Recommendation:** Add rotation reminders or use GCP Secret Manager's rotation feature. Low priority for launch but should be addressed for compliance.

### 8.5 Terraform State ✅ Good

State stored in GCS bucket (encrypted at rest by default). Ensure bucket versioning is enabled for state recovery.

---

## 9. New Issues Created

### ~~010 — Admin API Key Timing Attack~~ ✅ Fixed

Fixed same day. See `issues/closed/010-admin-key-timing.md`.

### ~~011 — Admin Force-Refund Missing Row Lock~~ ✅ Fixed

Fixed same day. See `issues/closed/011-admin-force-refund-race.md`.

---

## 10. Summary of Actions Taken

| Action | Item | Detail |
|--------|------|--------|
| **Close** | Issue 003 | 1MB body limit + storage fees sufficient |
| **Close** | Issue 004 | Verification retry loop exists |
| **Close** | Issue 009 | Replaced by abort penalties + performance bonds |
| **Downgrade** | Issue 002 | High → Low (economic + quota mitigations) |
| **Keep** | Issue 005 | Medium, monitor for gaming |
| **Create + Fix** | Issue 010 | Admin API key timing attack → `hmac.compare_digest()` |
| **Create + Fix** | Issue 011 | Admin force-refund missing row lock → `SELECT FOR UPDATE` |

---

## 11. Recommendations Summary (Priority Order)

1. ~~**Update pip** to 26.0.1 (CVE fixes, routine)~~ ✅ Done
2. ~~**Update certifi** to 2026.2.25 (CA bundle freshness)~~ ✅ Done
3. ~~**Fix admin key comparison** to use `hmac.compare_digest()` (Issue 010)~~ ✅ Fixed
4. ~~**Add row locking** to admin force-refund (Issue 011)~~ ✅ Fixed
5. ~~**Add key rotation endpoint** for agents~~ ✅ Already existed (`POST /auth/rotate-key`)
6. **Consider** digest-pinned container images for sandbox
7. **Consider** persistent admin audit log table
8. **Consider** NFKC unicode normalization for display names
9. **Consider** automated secret rotation policies

**Overall assessment:** Ready for launch. No critical or exploitable vulnerabilities. All actionable findings (items 1-5) have been resolved. Items 6-9 are hardening for future iterations.
