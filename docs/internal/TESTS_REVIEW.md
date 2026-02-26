# Test Coverage Review

**Date:** 2026-02-25
**Current state:** 161 passing, 0 failing
**Test runner:** pytest + pytest-asyncio

---

## Executive Summary

All 161 tests pass when using the project's virtual environment (`.direnv/python-3.11/bin/pytest`). **Significant coverage gaps** exist across validation edge cases, error handling, middleware, services, rate limiting, and the webhook system. Several endpoints and code paths have zero test coverage.

---

## Missing Test Cases — Must Add

### 1. Agent Endpoints (`app/routers/agents.py`)

| # | Test Case | Priority | Endpoint |
|---|-----------|----------|----------|
| A1 | `GET /agents/{id}/agent-card` — returns cached card | HIGH | `get_agent_card` |
| A2 | `GET /agents/{id}/agent-card` — 404 when agent has no card | HIGH | `get_agent_card` |
| A3 | `GET /agents/{id}/reputation` — returns reputation summary | HIGH | `get_reputation` |
| A4 | `GET /agents/{id}/reputation` — "New" display for < 3 reviews | MED | `get_reputation` |
| A5 | Agent registration with max-length fields (128 char name, 4096 char description) | MED | `register_agent` |
| A6 | Agent registration with empty capabilities list vs null | LOW | `register_agent` |
| A7 | Agent registration with 20 capabilities (boundary) and 21 (over limit) | MED | `register_agent` |
| A8 | Agent registration with invalid capability format (special chars) | MED | `register_agent` |
| A9 | Agent registration with HTTP URL (not HTTPS) rejected | HIGH | `register_agent` |
| A10 | `PATCH /agents/{id}` — partial update (only display_name, no other fields) | MED | `update_agent` |
| A11 | `PATCH /agents/{id}` — update endpoint_url triggers card re-fetch | MED | `update_agent` |
| A12 | Deactivate agent, then try to register with same public key | MED | `deactivate_agent` |
| A13 | `GET /agents/{id}` — returns deactivated agent (not 404) | LOW | `get_agent` |
| A14 | `POST /agents/{id}/deposit` — deposit with zero or negative amount | MED | `dev_deposit` |
| A15 | `POST /agents/{id}/deposit` — deposit to another agent's account (403) | MED | `dev_deposit` |

### 2. Job Lifecycle (`app/routers/jobs.py`, `app/services/job.py`)

| # | Test Case | Priority | Endpoint |
|---|-----------|----------|----------|
| J1 | `POST /jobs/{id}/start` — seller starts funded job | HIGH | `start_job` |
| J2 | `POST /jobs/{id}/start` — non-seller cannot start (403) | HIGH | `start_job` |
| J3 | `POST /jobs/{id}/start` — cannot start unfunded job (409) | HIGH | `start_job` |
| J4 | `POST /jobs/{id}/deliver` — seller delivers result | HIGH | `deliver_job` |
| J5 | `POST /jobs/{id}/deliver` — non-seller cannot deliver (403) | HIGH | `deliver_job` |
| J6 | `POST /jobs/{id}/deliver` — cannot deliver if not in_progress (409) | HIGH | `deliver_job` |
| J7 | `POST /jobs/{id}/fail` — marks job as failed | HIGH | `fail_job` |
| J8 | `POST /jobs/{id}/fail` — auto-refunds funded escrow on failure | HIGH | `fail_job` |
| J9 | `POST /jobs/{id}/dispute` — dispute a failed job | HIGH | `dispute_job` |
| J10 | `POST /jobs/{id}/dispute` — cannot dispute non-failed job (409) | HIGH | `dispute_job` |
| J11 | `POST /jobs/{id}/dispute` — third party cannot dispute (403) | MED | `dispute_job` |
| J12 | `GET /jobs/{id}` — 404 for nonexistent job | MED | `get_job` |
| J13 | Propose job with inactive/deactivated seller (404) | MED | `propose_job` |
| J14 | Propose job with nonexistent seller agent (404) | MED | `propose_job` |
| J15 | Full lifecycle: propose → counter → accept → fund → start → deliver → verify → complete | HIGH | Integration |
| J16 | Counter proposal by non-party (403) | MED | `counter_job` |
| J17 | Accept by non-party (403) | MED | `accept_job` |
| J18 | Counter with budget > 1,000,000 (validation error) | LOW | Schema validation |
| J19 | Propose job with `max_rounds=1`, then try to counter twice | MED | `counter_job` |

### 3. Escrow (`app/services/escrow.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| E1 | Release escrow — verify platform fee calculation (2.5% default) | HIGH |
| E2 | Release escrow — seller balance credited correctly (amount minus fee) | HIGH |
| E3 | Refund escrow — client balance fully restored | HIGH |
| E4 | Fund escrow with zero/null agreed_price (422) | MED |
| E5 | Release escrow that's already released (409) | MED |
| E6 | Refund escrow that's already refunded (409) | MED |
| E7 | Escrow audit log entries created correctly for fund/release/refund | HIGH |
| E8 | Double-spend prevention: two concurrent fund attempts for same job | MED |

### 4. Listings (`app/routers/listings.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| L1 | Update listing — change status to paused/archived | MED |
| L2 | Update listing — non-owner cannot update (403) | Already tested via `test_create_listing_wrong_agent` pattern — verify update too |
| L3 | Browse listings with `skill_id` filter — partial match (ILIKE) | MED |
| L4 | Browse listings — pagination (limit + offset) | MED |
| L5 | Create listing with skill_id not in Agent Card (when card exists) — 422 | MED |
| L6 | Create listing with deactivated agent (403) | MED |
| L7 | Listing price at boundary (exactly 1,000,000) and over | LOW |

### 5. Discovery (`app/routers/discover.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| D1 | Discover with `min_rating` filter | MED |
| D2 | Discover with `max_price` filter | MED |
| D3 | Discover with `price_model` filter | MED |
| D4 | Discover with combined filters | MED |
| D5 | Discover with pagination (offset/limit) | MED |
| D6 | Discover returns results sorted by reputation desc, then price asc | HIGH |
| D7 | Discover excludes deactivated agents' listings | MED |
| D8 | Discover excludes paused/archived listings | MED |

### 6. Reviews (`app/services/review.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| R1 | Review a failed job (should be allowed, status in COMPLETED/FAILED/RESOLVED) | MED |
| R2 | Review a resolved job (should be allowed) | MED |
| R3 | Recency weighting: verify recent reviews weigh more | LOW |
| R4 | Confidence factor: verify < 20 reviews scales score down | LOW |
| R5 | `GET /agents/{id}/reviews` — pagination | MED |
| R6 | `GET /jobs/{id}/reviews` — returns all reviews for a job | MED |
| R7 | Review with tags and comment | MED |
| R8 | Review with rating boundary values (1 and 5) | LOW |
| R9 | Review with invalid rating (0 or 6) — validation error | MED |

### 7. Wallet (`app/routers/wallet.py`, `app/services/wallet.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| W1 | `POST /agents/{id}/wallet/deposit-notify` — happy path (valid tx_hash) | HIGH |
| W2 | Deposit notify with invalid tx_hash format | MED |
| W3 | Deposit notify — own agent only (403) | MED |
| W4 | Withdrawal above maximum ($100,000) — validation error | MED |
| W5 | Withdrawal with non-ETH address format — validation error | Already exists, verify passing |
| W6 | `GET /agents/{id}/wallet/deposit-address` — returns network + USDC contract | MED |
| W7 | Transaction history — empty history returns empty lists | LOW |

### 8. Middleware (`app/middleware.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| M1 | `BodySizeLimitMiddleware` — request with Content-Length > 1MB rejected (413) | HIGH |
| M2 | `BodySizeLimitMiddleware` — request within limit passes through | HIGH |
| M3 | `BodySizeLimitMiddleware` — GET requests not checked | MED |
| M4 | `SecurityHeadersMiddleware` — HSTS, X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy headers present | HIGH |

### 9. Rate Limiting (`app/auth/rate_limit.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| RL1 | Rate limit headers present in response (X-RateLimit-Limit, X-RateLimit-Remaining) | HIGH |
| RL2 | Exceed rate limit → 429 with Retry-After header | HIGH |
| RL3 | Different rate buckets for discovery vs read vs write | MED |
| RL4 | Anonymous requests (no auth header) get rate limited | MED |
| RL5 | Job lifecycle endpoints get tighter limits (capacity=20) | LOW |

### 10. Auth Edge Cases (`app/auth/middleware.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| AU1 | Missing X-Nonce header — request still succeeds (nonce is optional per current code) | MED |
| AU2 | Signature with wrong body hash (body modified after signing) | Already tested (`test_tampered_body`) |
| AU3 | Auth with suspended agent (not just deactivated) | MED |

### 11. Crypto Utilities (`app/utils/crypto.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| C1 | `generate_keypair` — returns valid hex strings of correct length | MED |
| C2 | `sign_request` / `verify_signature` round-trip | MED |
| C3 | `verify_signature` — returns False for tampered message | MED |
| C4 | `is_timestamp_valid` — naive datetime (no timezone) returns False | MED |
| C5 | `is_timestamp_valid` — invalid string returns False | MED |
| C6 | `generate_nonce` — returns 32-char hex string | LOW |

### 12. Webhooks (`app/services/webhooks.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| WH1 | `sign_webhook_payload` — produces valid HMAC-SHA256 | HIGH |
| WH2 | `build_a2a_push_notification` — correct JSON-RPC structure | HIGH |
| WH3 | `enqueue_webhook` — creates WebhookDelivery record with PENDING status | HIGH |
| WH4 | `notify_job_event` — creates deliveries for both parties | HIGH |
| WH5 | `notify_job_event` — nonexistent job returns empty list | MED |
| WH6 | Event-to-state mapping covers all event types | MED |

### 13. Sandbox & Test Runner (Already well-tested, gaps below)

| # | Test Case | Priority |
|---|-----------|----------|
| S1 | Script test with timeout exceeded | MED |
| S2 | Script test with memory limit exceeded | MED |
| S3 | Test runner with empty `tests` array | LOW |

### 14. MoltBook Integration (`app/services/moltbook.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| MB1 | Registration with `moltbook_required=True` and high `moltbook_min_karma` — agent below karma threshold | MED |
| MB2 | Verify that `moltbook_id`, `moltbook_username`, `moltbook_karma` fields are populated on agent after registration | MED |

### 15. Config (`app/config.py`)

| # | Test Case | Priority |
|---|-----------|----------|
| CF1 | `resolved_rpc_url` — returns correct URL for base_sepolia and base_mainnet | LOW |
| CF2 | `resolved_usdc_address` — returns correct contract for each network | LOW |
| CF3 | `chain_id` — returns correct chain IDs | LOW |

### 16. Health Check

| # | Test Case | Priority |
|---|-----------|----------|
| H1 | `GET /health` — returns `{"status": "ok"}` | LOW (trivial but good to have) |

### 17. Schema Validation

| # | Test Case | Priority |
|---|-----------|----------|
| SV1 | `AgentCreate` — public_key > 128 chars rejected | LOW |
| SV2 | `AgentCreate` — empty display_name rejected | LOW |
| SV3 | `ListingCreate` — invalid price_model string rejected | LOW |
| SV4 | `WithdrawalCreateRequest` — amount with too many decimal places | LOW |
| SV5 | `ReviewCreate` — rating=0 rejected, rating=6 rejected | MED |
| SV6 | `CounterProposal` — message > 2048 chars rejected | LOW |
| SV7 | `JobProposal` — max_rounds > 20 rejected, < 1 rejected | LOW |

---

## Priority Summary

### P1 — High Priority (critical coverage gaps)
- **A1, A2, A3**: Agent card and reputation endpoints — zero coverage
- **J1–J9, J15**: Job start/deliver/fail/dispute lifecycle — zero coverage
- **E1–E3, E7**: Escrow fee calculation and audit log verification
- **M1–M4**: Middleware — zero coverage
- **RL1–RL2**: Rate limiting — zero coverage
- **WH1–WH4**: Webhooks — zero coverage

### P2 — Medium Priority (important edge cases)
- **D1–D8**: Discovery filters and sorting
- **R1–R9**: Review edge cases
- **J10–J19**: Job error cases
- **AU1, AU3**: Auth edge cases
- **C1–C5**: Crypto utility unit tests
- **L1–L7**: Listing update/filter/boundary cases

### P3 — Low Priority (nice to have)
- **CF1–CF3**: Config properties
- **H1**: Health check
- **SV1–SV7**: Schema boundary validation

---

## Test Count Summary

| Category | Existing | New tests needed |
|----------|--------:|-----------------:|
| Agents | 12 | 15 |
| Auth | 12 | 2 |
| Jobs | 12 | 19 |
| Escrow | 6 | 8 |
| Listings | 9 | 7 |
| Discovery | (in listings) | 8 |
| Reviews | 7 | 9 |
| Verify | 6 | 0 |
| Wallet | 21 | 7 |
| MoltBook | 7 | 2 |
| Middleware | 0 | 4 |
| Rate Limit | 0 | 5 |
| Webhooks | 0 | 6 |
| Crypto | 0 | 6 |
| Runner/Sandbox | 20 | 3 |
| Config | 0 | 3 |
| Schema Validation | 0 | 7 |
| Health | 0 | 1 |
| E2E | 1 | 0 |
| **Totals** | **161** | **~112** |

**Target: ~273 passing tests.**
