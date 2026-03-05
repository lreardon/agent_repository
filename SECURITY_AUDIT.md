# Security Audit — Agent Registry & Marketplace

**Date:** 2026-03-04  
**Auditor:** Clob  
**Scope:** Full application source code review  
**Severity Scale:** CRITICAL / HIGH / MEDIUM / LOW / INFO

---

## Executive Summary

The application has a **solid security foundation**: Ed25519 signature auth with replay protection, row-level locking for financial operations, sandboxed verification in isolated containers, SSRF protection on endpoint URLs, input validation via Pydantic, rate limiting, and security headers.

**7 findings require action before production.** Most are tightening gaps rather than fundamental design flaws.

---

## Findings

### 🔴 CRITICAL

#### C1: Nonce Replay Protection is Optional

**File:** `app/auth/middleware.py:45-49`  
**Issue:** The `X-Nonce` header is optional. If a client omits it, no replay protection is enforced. An attacker who captures a signed request can replay it within the `signature_max_age_seconds` window (30s).

```python
if nonce:  # ← Only checked if provided
    nonce_key = f"nonce:{nonce}"
    already_used = await redis.set(nonce_key, "1", nx=True, ex=settings.nonce_ttl_seconds)
    if not already_used:
        raise HTTPException(status_code=403, detail="Nonce already used")
```

**Impact:** Any intercepted request (MITM, log leak, proxy cache) can be replayed for 30 seconds. For financial endpoints (fund, withdraw, deliver), this could cause duplicate operations.

**Fix:** Make nonce **required** for all mutating endpoints (POST/PATCH/DELETE). GET requests can remain nonce-optional since they're idempotent.

```python
if request.method in ("POST", "PATCH", "PUT", "DELETE"):
    if not nonce:
        raise HTTPException(status_code=403, detail="X-Nonce header required for mutating requests")
nonce_key = f"nonce:{nonce}"
# ...
```

---

#### C2: Withdrawal Marks COMPLETED Before On-Chain Confirmation

**File:** `app/services/wallet.py:225-232`  
**Issue:** After broadcasting a withdrawal transaction, the code immediately marks it `COMPLETED` without waiting for an on-chain receipt:

```python
# Persist tx_hash IMMEDIATELY
withdrawal.tx_hash = tx_hash.hex()
await db.commit()

# Now wait for confirmation
withdrawal.status = WithdrawalStatus.COMPLETED  # ← No receipt check!
withdrawal.processed_at = datetime.now(UTC)
await db.commit()
```

**Impact:** If the transaction is dropped, replaced, or reverts, the platform has already marked it complete. The agent believes they were paid, but the USDC never arrived. The balance was already deducted.

**Fix:** Wait for the transaction receipt before marking COMPLETED:

```python
withdrawal.tx_hash = tx_hash.hex()
await db.commit()  # Persist tx_hash for crash recovery

# Wait for on-chain confirmation
receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
if receipt.status == 1:
    withdrawal.status = WithdrawalStatus.COMPLETED
else:
    withdrawal.status = WithdrawalStatus.FAILED
    withdrawal.error_message = "Transaction reverted on-chain"
    # Refund agent
    agent.balance += withdrawal.amount
withdrawal.processed_at = datetime.now(UTC)
await db.commit()
```

---

### 🟠 HIGH

#### H1: SQL Injection via `ilike` in Admin Search

**File:** `app/routers/admin.py:150, 287`  
**Issue:** The admin `search` parameter is interpolated directly into an `ilike` pattern:

```python
query = query.where(Agent.display_name.ilike(f"%{search}%"))
# Also:
query = query.where(Account.email.ilike(f"%{search}%"))
```

While SQLAlchemy parameterizes the value (preventing SQL injection), the `%` and `_` wildcard characters in ILIKE are **not escaped**. An attacker with admin access could craft searches like `%` to dump all records, or use `_` for single-character brute-forcing.

**Impact:** Low in practice (requires admin key), but violates defense-in-depth.

**Fix:** Escape ILIKE special characters:

```python
import re
def _escape_ilike(s: str) -> str:
    return re.sub(r"([%_\\])", r"\\\1", s)

search_safe = _escape_ilike(search)
query = query.where(Agent.display_name.ilike(f"%{search_safe}%"))
```

---

#### H2: `platform_signing_key` Default Value in Config

**File:** `app/config.py:8`  
**Issue:** The platform signing key has a hardcoded default:

```python
platform_signing_key: str = "dev-signing-key-not-for-production"
```

If someone deploys without setting this env var, they get a known key. The comment says "ROTATE BEFORE PRODUCTION" but there's no enforcement.

**Fix:** Refuse to start in non-development environments with the default key:

```python
@model_validator(mode="after")
def validate_production_keys(self) -> "Settings":
    if self.env not in ("development", "test"):
        if self.platform_signing_key == "dev-signing-key-not-for-production":
            raise ValueError("PLATFORM_SIGNING_KEY must be set for non-development environments")
        if not self.admin_api_keys:
            # Optional: warn if admin is disabled in production
            pass
    return self
```

---

#### H3: Body Size Limit Bypass via Missing Content-Length

**File:** `app/middleware.py:16-21`  
**Issue:** The `BodySizeLimitMiddleware` only checks the `Content-Length` header. Chunked transfer encoding (no Content-Length) bypasses this entirely.

```python
content_length = request.headers.get("content-length")
if content_length and int(content_length) > self.max_bytes:  # ← Only if header exists
    return JSONResponse(...)
```

**Impact:** An attacker can send arbitrarily large request bodies via chunked encoding, potentially causing OOM.

**Fix:** Also check the actual body size. Alternatively, use Starlette's built-in limit or read-and-check:

```python
async def dispatch(self, request, call_next):
    if request.method in ("POST", "PATCH", "PUT"):
        content_length = request.headers.get("content-length")
        if content_length:
            if int(content_length) > self.max_bytes:
                return JSONResponse(status_code=413, content={"detail": "..."})
        else:
            # For chunked encoding, read body and check
            body = await request.body()
            if len(body) > self.max_bytes:
                return JSONResponse(status_code=413, content={"detail": "..."})
    return await call_next(request)
```

Note: FastAPI/Starlette will buffer the body anyway for JSON parsing, so this doesn't add overhead. For a production deployment, configure the reverse proxy (nginx/Cloud Run) to enforce body size limits as well.

---

### 🟡 MEDIUM

#### M1: Webhook Delivery Lacks HMAC Signature Verification

**File:** `app/services/webhooks.py:15-18`  
**Issue:** The `sign_webhook_payload` function exists but is **never called**. Webhook deliveries are sent without signatures, meaning agents can't verify that notifications came from the platform.

```python
def sign_webhook_payload(secret: str, timestamp: str, body: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    # This function is defined but never used
```

**Impact:** Agents receiving webhooks can't distinguish platform notifications from spoofed requests. An attacker could fake job status changes.

**Fix:** Sign all outbound webhook deliveries and document the verification process for agents. The signing function already exists — just wire it in during delivery.

---

#### M2: Verification Lock Has No Per-Agent Cooldown

**File:** `app/routers/jobs.py:116-120`  
**Issue:** The verification endpoint uses a per-job Redis lock (10-min TTL) to prevent concurrent verification, but there's no per-agent rate limit on verification attempts. A client could repeatedly trigger verification to exhaust platform sandbox resources.

The per-endpoint rate limit (20/5min for job_lifecycle) helps but is shared across all job operations.

**Fix:** Add a per-agent verification cooldown (e.g., max 3 verification attempts per job, or a 60s cooldown between attempts):

```python
attempt_key = f"verify_attempts:{job_id}"
attempts = await redis.incr(attempt_key)
if attempts == 1:
    await redis.expire(attempt_key, 3600)  # 1hr window
if attempts > 5:
    raise HTTPExc(status_code=429, detail="Maximum verification attempts exceeded for this job")
```

---

#### M3: `DevDepositRequest.amount` is Unbounded String

**File:** `app/routers/agents.py:146-148`  
**Issue:** The dev deposit endpoint accepts `amount` as a raw string, converts to `Decimal` without validation:

```python
class DevDepositRequest(_BaseModel):
    amount: str  # ← No validation

agent = await agent_service.deposit(db, agent_id, Decimal(data.amount))
```

While this endpoint is disabled in production (`settings.env == "production"`), a staging misconfiguration could allow arbitrary balance manipulation.

**Fix:** Use `Decimal` type with Pydantic validation, or at minimum validate the string parses to a positive number.

---

### 🟢 LOW

#### L1: Error Messages Leak Internal Details

**File:** Various (e.g., `app/services/escrow.py`, `app/services/wallet.py`)  
**Issue:** Some error messages expose internal state like exact balance amounts:

```python
detail=f"Insufficient balance: {client.balance} < {amount}"
detail=f"Seller has insufficient balance for performance bond: ${seller.balance} < ${seller_bond}"
```

**Fix:** Use generic messages in production: "Insufficient balance" without revealing the exact amount. Log the details server-side.

---

#### L2: Admin Balance Adjustment Lacks Audit Trail

**File:** `app/routers/admin.py:193-214`  
**Issue:** The admin `adjust_agent_balance` endpoint logs to Python logger but doesn't create a persistent audit record (unlike escrow operations which have `EscrowAuditLog`). In production, log entries can be lost.

**Fix:** Create an `AdminAuditLog` table or reuse an existing audit mechanism to persist admin balance adjustments.

---

#### L3: WebSocket Authentication Timeout

**File:** `app/routers/ws.py:38`  
**Issue:** The WebSocket authentication timeout is 10 seconds, but there's no limit on the number of unauthenticated WebSocket connections. An attacker could open thousands of connections and hold them for 10 seconds each, exhausting server resources.

**Fix:** Apply IP-based rate limiting to WebSocket connection attempts, or use a connection semaphore.

---

### ℹ️ INFO (Defense-in-Depth Recommendations)

#### I1: CORS Allows Credentials with Multiple Origins

The CORS middleware has `allow_credentials=True` with multiple origins. This is fine with explicit origin lists (not `*`), but worth verifying that all listed origins are trusted.

#### I2: Prometheus `/metrics` Endpoint is Unauthenticated

The `/metrics` endpoint exposes operational data (request counts, latencies, in-flight tasks). In production, restrict access via network policy or add authentication.

#### I3: Static Files Served from `/docs-site`

If the `web/` directory contains user-facing documentation, ensure no sensitive information is exposed. The directory is mounted read-only, which is correct.

#### I4: Consider Adding CSP Header

The `SecurityHeadersMiddleware` adds standard security headers but not `Content-Security-Policy`. For the HTML responses (verify-email, agent-status), adding CSP would prevent XSS if user-controlled data makes it into those pages.

---

## What's Done Well

| Area | Assessment |
|------|-----------|
| **Auth system** | Ed25519 signatures with timestamp validation — strong scheme |
| **Escrow locking** | `SELECT FOR UPDATE` consistently used — prevents double-spend |
| **Sandbox isolation** | Docker: no network, read-only fs, dropped caps, non-root, memory limits — excellent |
| **GKE sandbox** | NetworkPolicy deny-all, no service account, ephemeral storage limits — production-ready |
| **Input validation** | Pydantic schemas with field validators, max lengths, regex patterns |
| **SSRF protection** | Private IP blocking on `endpoint_url` — covers RFC 1918, link-local, loopback |
| **Rate limiting** | Token bucket with Redis + Lua atomicity — good implementation |
| **Admin hiding** | Returns 404 on all auth failures — doesn't leak existence |
| **Deliverable redaction** | Result field redacted from responses until job completed — prevents work extraction |
| **Deliverable size cap** | 512KB limit via Pydantic validator (closes issue #003) |
| **State machine** | Explicit valid transitions — prevents illegal state jumps |
| **Idempotent withdrawals** | tx_hash persisted before marking complete — crash-safe (except C2 timing) |
| **Treasury safety** | Auto-pause withdrawals below threshold — prevents drain |

---

## Priority Fix Order

1. ~~**C1** — Make nonce required for mutating requests~~ ✅ **FIXED 2026-03-04** — `app/auth/middleware.py` now rejects POST/PUT/PATCH/DELETE without `X-Nonce`. Tests: `test_auth_without_nonce_rejected_for_post`, `_delete`, `_patch`.
2. ~~**C2** — Wait for tx receipt before marking withdrawal complete~~ ✅ **FIXED 2026-03-04** — `app/services/wallet.py` now calls `wait_for_transaction_receipt()` after broadcast. Reverted txs refund the agent; receipt timeouts leave PROCESSING for recovery. Tests: `test_process_withdrawal_waits_for_receipt`, `_reverted_receipt_refunds`, `_receipt_timeout_leaves_processing`.
3. **H2** — Enforce platform_signing_key in production (15 min)
4. **H3** — Fix body size limit for chunked encoding (30 min)
5. **M2** — Add per-job verification attempt limits (30 min)
6. **M1** — Wire in webhook HMAC signatures (1 hr)
7. **H1** — Escape ILIKE wildcards in admin search (15 min)
8. **L1-L3** — Lower priority cleanups (2 hrs total)
