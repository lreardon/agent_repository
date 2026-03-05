# Test Integrity Audit Report

**Date:** 2026-03-05
**Branch:** `audit/test-integrity`
**Auditor:** Claude Opus 4.6 (automated cross-reference audit)

---

## Executive Summary

Audited **37 test files** (30 server + 6 SDK + conftest) containing **~350 test functions** against their corresponding source code. Every assertion was cross-referenced against router, service, model, and schema implementations.

**No evidence of deliberately sabotaged tests was found.** However, the audit uncovered significant integrity issues: tests that pass vacuously, tests that validate manual operations instead of real code, authorization gaps hidden by missing coverage, and a global test fixture that silently weakens production security defaults.

### Findings by Severity

| Severity | Count |
|----------|-------|
| **CRITICAL** | 5 |
| **HIGH** | 16 |
| **MEDIUM** | 35 |
| **LOW** | 43 |
| **Total** | 99 |

### Top 10 Most Dangerous Findings

| # | File | Finding | Severity |
|---|------|---------|----------|
| 1 | test_escrow.py | `test_escrow_audit_log_populated` is completely vacuous -- `if logs:` guard + transaction isolation means zero assertions ever execute | CRITICAL |
| 2 | test_jobs.py | `/abort` endpoint (penalty distribution, bond forfeiture) has zero test coverage | CRITICAL |
| 3 | test_jobs.py | `/verify` endpoint (sandbox execution, escrow release/refund) has zero test coverage in test_jobs.py | CRITICAL |
| 4 | test_agents.py | Deposit endpoint missing owner check -- any agent can deposit to any other agent | CRITICAL* |
| 5 | test_agents.py | Deposit endpoint uses unvalidated `str` instead of `DepositRequest` schema -- negative amounts possible | CRITICAL* |
| 6 | test_wallet.py | `test_failed_withdrawal_refunds_balance` manually performs refund in test code -- never exercises actual refund logic | HIGH |
| 7 | test_escrow.py | Seller bond return path untested on both release and refund; `refund_escrow()` may silently lose seller bonds | HIGH |
| 8 | conftest.py | `_isolate_settings` autouse fixture forces `dev_deposit_enabled=True`, `email_verification_required=False` for ALL tests | HIGH |
| 9 | test_sandbox.py | GKE production backend (~270 lines) has zero test coverage | HIGH |
| 10 | test_auth.py | `verify_email()` doesn't filter by `purpose` -- recovery tokens usable for signup verification | HIGH* |

*Items marked with \* are source code bugs exposed by the audit, not just test issues.

---

## Detailed Findings by File

---

### `tests/conftest.py`

#### CONF-1: `_isolate_settings` fixture silently weakens production security defaults [HIGH]
- **Location:** lines 130-139
- **What's wrong:** Autouse fixture forces `dev_deposit_enabled=True` (production default: `False`), `email_verification_required=False` (production default: `True`), and `min_balance_to_propose_job=Decimal("0.00")` (production default: `1.00`) for EVERY test. This means the entire test suite runs under more permissive conditions than production.
- **Impact:** Bugs in email-verified registration, deposit gating, and anti-spam balance checks would go undetected.

---

### `tests/test_escrow.py`

#### ESC-1: `test_escrow_audit_log_populated` is completely vacuous [CRITICAL]
- **Test:** line 313
- **What's wrong:** Test takes only `db_session` (no `client`), so no escrow operations run in its transaction. The `if logs:` guard (line 323) means when the table is empty (always), zero assertions execute. The `is not None` assertions on `nullable=False` columns are tautological even if they ran.
- **Impact:** Financial audit logging could be completely broken with no test failure.

#### ESC-2: Seller bond return untested on release [HIGH]
- **Test:** `test_fund_release_flow` (line 126)
- **What's wrong:** `seller_abort_penalty` defaults to `0.00`. The seller bond return path in `release_escrow` (lines 212-213, 237-238) is never exercised by any test.
- **Impact:** If `+ escrow.seller_bond_amount` were removed from the payout calculation, no test would catch it.

#### ESC-3: `refund_escrow()` may silently lose seller bonds [HIGH]
- **Test:** `test_refund_restores_full_balance` (line 262)
- **What's wrong:** `refund_escrow()` does NOT return `seller_bond_amount` to the seller. When a job fails through `/fail` (not `/abort`), a seller's performance bond is silently lost. No test sets `seller_abort_penalty > 0` on the refund path.
- **Impact:** Potential real financial bug -- sellers lose bonds on legitimate job failures.

#### ESC-4: Missing double-release/double-refund prevention tests [MEDIUM]
- **What's wrong:** `test_double_fund_prevention` exists, but no corresponding tests for double-release or double-refund.

#### ESC-5: Response schema under-validated [MEDIUM]
- **Test:** `test_fund_job` (line 64)
- **What's wrong:** Only checks `status` and `amount` of 8 `EscrowResponse` fields. `escrow_id`, `client_agent_id`, `seller_agent_id`, `funded_at` never verified.

#### ESC-6: Weakened "Insufficient balance" assertion [LOW]
- **Test:** `test_fund_insufficient_balance` (line 92)
- **What's wrong:** Substring check `"Insufficient balance" in detail` instead of verifying the actual vs. required amounts.

---

### `tests/test_jobs.py`

#### JOB-1: `/abort` endpoint has zero test coverage [CRITICAL]
- **What's wrong:** The abort endpoint (router lines 279-305) handles penalty distribution, bond forfeiture, and state validation. It is financially critical and completely untested in this file.

#### JOB-2: `/verify` endpoint has zero test coverage [CRITICAL]
- **What's wrong:** The verify endpoint (router lines 150-252) handles sandbox execution, fee charging, escrow release/refund, and concurrent locking. The most complex endpoint in the router with zero tests.

#### JOB-3: Deliver endpoint charges fee before authorization check [MEDIUM]
- **Test:** `test_non_seller_cannot_deliver` (line 468)
- **What's wrong:** Router charges storage fee via `charge_fee()` BEFORE `deliver_job()` checks seller identity. Rolled back by transaction semantics, but wrong ordering.

#### JOB-4: `fee_charged` response field never asserted [MEDIUM]
- **Test:** `test_deliver_job` (line 433)
- **What's wrong:** Deliver returns `{**JobResponse, "fee_charged": ...}` but no test checks `fee_charged`.

#### JOB-5: Fail endpoint leaks criteria existence to third parties [MEDIUM]
- **What's wrong:** `/fail` checks criteria before party membership. Third party gets 409 ("use /verify") instead of 403, revealing job configuration.

#### JOB-6: Same-party consecutive counters allowed without enforcement [MEDIUM]
- **What's wrong:** `counter_job` uses `allowed="both"` -- a party can counter their own proposal. No test documents whether this is intentional.

#### JOB-7: Misleading `test_deliver_and_fail_flow` docstring [LOW]
- **Test:** line 249
- **What's wrong:** Claims to test "propose -> accept -> fund -> start -> deliver -> fail -> dispute" but only tests that agreed->start fails.

---

### `tests/test_wallet.py`

#### WAL-1: `test_failed_withdrawal_refunds_balance` is tautological [HIGH]
- **Test:** line 528
- **What's wrong:** Manually sets `withdrawal.status = FAILED` and `agent.balance += withdrawal.amount` in the test itself, then asserts the manual operation worked. Never exercises actual `_process_withdrawal` refund logic.
- **Impact:** Real refund logic could be completely broken with no test failure.

#### WAL-2: `test_withdrawal_below_minimum` passes for wrong reason [MEDIUM]
- **Test:** line 153
- **What's wrong:** Docstring claims "$1.00 minimum" but actual minimum is $0.01. Test passes because $0.50 - $0.50 fee = $0.00 net, triggering fee-exceeds-amount guard, not minimum check.

#### WAL-3: `test_withdrawal_must_exceed_fee` tests success, not rejection [MEDIUM]
- **Test:** line 167
- **What's wrong:** Named as testing fee-guard rejection but actually tests successful withdrawal with fee subtraction.

#### WAL-4: No concurrent withdrawal race condition test [MEDIUM]
- **What's wrong:** Service uses `SELECT FOR UPDATE` for serialization, but only sequential tests exist.

#### WAL-5: Dev deposit has no own-agent authorization check [MEDIUM]
- **What's wrong:** Any authenticated agent can deposit to any other agent's balance (source code bug).

---

### `tests/test_agents.py`

#### AGT-1: Deposit endpoint missing owner check [CRITICAL*]
- **Test:** `test_deposit_and_balance` (line ~64)
- **What's wrong:** `dev_deposit` endpoint never verifies `auth.agent_id == agent_id`. Any authenticated agent can deposit credits into any other agent's balance. Test only tests happy path with self-deposit.
- **Impact:** Authorization bypass on financial endpoint.

#### AGT-2: Deposit endpoint uses unvalidated `str` instead of `DepositRequest` schema [CRITICAL*]
- **What's wrong:** Router defines inline `DevDepositRequest(amount: str)` with no validation. The proper `DepositRequest` schema (with `gt=0`, `max_digits=12`, max $1M) exists in `app/schemas/agent.py` but is never imported.
- **Impact:** Negative deposits could drain balances. Malformed strings could cause `InvalidOperation` exceptions.

#### AGT-3: No test for negative/zero deposit amounts [HIGH]
- **What's wrong:** Combined with AGT-2, this means negative deposits are both possible and untested.

#### AGT-4: Missing `test_deactivate_agent_wrong_owner` [MEDIUM]
- **What's wrong:** `test_update_agent_wrong_owner` covers PATCH, but DELETE has no wrong-owner test.

#### AGT-5: No regression guard against `webhook_secret` leakage [MEDIUM]
- **Test:** `test_register_agent`
- **What's wrong:** No assertion that `"webhook_secret" not in body`.

#### AGT-6: 403 vs 401 for missing authentication [LOW]
- **Tests:** `test_update_agent_no_auth`, `test_balance_no_auth`
- **What's wrong:** Missing auth returns 403 instead of 401 per HTTP semantics.

---

### `tests/test_verify.py` + `tests/test_verify_script.py`

#### VER-1: All Docker sandbox tests skipped in CI [HIGH]
- **Tests:** `test_script_verify_pass_releases_escrow`, `test_script_verify_fail_refunds_escrow`
- **What's wrong:** The only tests that exercise real sandbox execution + escrow release/refund never run in CI.

#### VER-2: GKE backend cannot capture stderr -- tests assert stderr content [MEDIUM]
- **Test:** `test_script_verify_fail_refunds_escrow` (line 168)
- **What's wrong:** GKE backend puts everything in stdout, stderr is always empty. Test passes against Docker but would fail on GKE. Production verification failure messages would be lost.

#### VER-3: Lock acquired before authorization check [HIGH]
- **Test:** `test_verify_rejects_non_client` (line 112)
- **What's wrong:** Redis lock is acquired BEFORE auth check. No test verifies lock is released after unauthorized attempt.

#### VER-4: `test_verify_rejects_concurrent_verification` bypasses real lock mechanism [MEDIUM]
- **Test:** line 145
- **What's wrong:** Test manually pre-sets Redis lock instead of testing that the endpoint sets it. If `nx=True` were removed from the source, this test would still pass.

#### VER-5: No test for verify on non-delivered job [MEDIUM]
- **What's wrong:** Source returns 409 for non-delivered jobs. No test covers this state guard.

#### VER-6: Loose seller balance assertion [LOW]
- **Test:** `test_script_verify_pass_releases_escrow` (line 132)
- **What's wrong:** `assert seller_balance > 100.0` -- would pass even if seller got paid double or fees weren't deducted.

#### VER-7: Missing status code assertions in test_verify.py helpers [MEDIUM]
- **What's wrong:** `_create_agent` and `_setup_funded_job` in test_verify.py don't assert status codes (test_verify_script.py's versions do).

---

### `tests/test_auth.py` + `tests/test_email_verification.py` + `tests/test_key_recovery.py`

#### AUTH-1: `verify_email()` doesn't filter by `purpose` [HIGH*]
- **Test:** `test_verify_email_returns_registration_token` (test_email_verification.py:132)
- **What's wrong:** Source code queries `EmailVerification` by token and `used==False` but NOT `purpose==signup`. A recovery-purpose token could be used on `/auth/verify-email` to obtain a registration token.
- **Impact:** Cross-purpose token confusion -- security vulnerability in source code.

#### AUTH-2: `validate_registration_token()` doesn't filter by `purpose` [HIGH*]
- **Test:** `test_register_with_invalid_token` (test_email_verification.py:251)
- **What's wrong:** Same pattern as AUTH-1. Recovery-issued registration tokens could be used for agent registration.

#### AUTH-3: Registration token not invalidated after use [LOW]
- **Test:** `test_registration_token_cannot_be_reused` (test_email_verification.py:263)
- **What's wrong:** Reuse prevention relies on account state (agent_id not null), not token invalidation. If agent is deactivated (SET NULL), old token may become reusable.

#### AUTH-4: Nonce-optional for GETs (design risk) [MEDIUM]
- **Test:** `test_auth_without_nonce_succeeds_for_get` (test_auth.py:224)
- **What's wrong:** GET requests skip nonce checks entirely. Signed GETs can be replayed within the 30-second window.

#### AUTH-5: No test for `PUT` method nonce requirement [LOW]
- **What's wrong:** Source requires nonces for POST, PUT, PATCH, DELETE. Tests cover all except PUT.

#### AUTH-6: Duplicate deactivation tests [LOW]
- **Tests:** `test_deactivated_agent_rejected` (line 144) and `test_auth_with_deactivated_agent_rejected` (line 311)
- **What's wrong:** Functionally identical tests.

#### AUTH-7: Recovery token HTML not verified for correctness [LOW]
- **Test:** `test_verify_recovery_html_response` (test_key_recovery.py:272)
- **What's wrong:** Checks for "rotate-key" string but doesn't verify the actual token value in HTML.

---

### `tests/test_sandbox.py`

#### SBX-1: GKE production backend has zero test coverage [HIGH]
- **What's wrong:** `_run_in_gke()` (~270 lines) handles K8s job creation, polling, log collection, cleanup. Zero tests of any kind.

#### SBX-2: CI never runs security isolation tests [MEDIUM]
- **What's wrong:** All `TestSandboxExecution` tests skipped in CI. Network isolation, read-only FS, privilege escalation prevention, memory limits, timeout enforcement never validated in pipelines.

#### SBX-3: `test_runner.py` integration layer untested [MEDIUM]
- **What's wrong:** `run_script_test()` function (entry point for `/verify`) has no test coverage. 500-char stdout truncation, `SuiteResult.passed` property untested.

#### SBX-4: `returncode or 0` pattern in source [LOW]
- **What's wrong:** `process.returncode or 0` is a subtle truthiness-based assignment. Tests don't cover `returncode is None` edge case.

---

### `tests/test_admin.py`

#### ADM-1: No test for escrow audit log creation on force-refund [HIGH]
- **Test:** `test_force_refund` (line 364)
- **What's wrong:** Verifies balances and status but never checks that `EscrowAuditLog` entry was created for this sensitive operation.

#### ADM-2: No test for comma-separated admin API keys [HIGH]
- **What's wrong:** `_parse_admin_keys()` supports comma-separated keys. All tests use single key.

#### ADM-3: List endpoint tests don't validate response item schemas [MEDIUM]
- **Tests:** `test_list_agents`, `test_list_jobs`, `test_list_escrows`, `test_list_accounts`, `test_list_webhooks`
- **What's wrong:** Only check `total >= 1` and `len(items) >= 1`. Never inspect item field names or types.

#### ADM-4: Stats test uses `>=` assertions only [MEDIUM]
- **Test:** `test_stats_with_data` (line 198)
- **What's wrong:** Uses `>= 1` instead of exact values after creating known data.

#### ADM-5: Balance-below-zero test doesn't verify error detail [MEDIUM]
- **Test:** `test_adjust_balance_below_zero` (line 298)
- **What's wrong:** Only checks 400, not the error message content.

#### ADM-6: No tests for deposit/withdrawal admin endpoints [LOW]
- **What's wrong:** `GET /deposits` and `GET /withdrawals` have zero test coverage.

#### ADM-7: No pagination tests for any admin list endpoint [LOW]

---

### `tests/test_listings.py`

#### LST-1: `browse_listings` doesn't filter deactivated agents unlike `discover` [HIGH]
- **What's wrong:** `/discover` filters `Agent.status == ACTIVE`, but `/listings` doesn't join against Agent. Deactivated agents' listings appear in browse but not discover. No test covers this.

#### LST-2: Weakened pagination assertion [MEDIUM]
- **Test:** `test_discover_pagination` (line 407)
- **What's wrong:** `assert len(items) <= 2` instead of `== 2`. Would pass with 0 results.

#### LST-3: No test for unique constraint `(seller, skill, status)` [MEDIUM]
- **What's wrong:** DB constraint exists but no test verifies the application handles duplicate creation correctly.

#### LST-4: `PaginatedResponse.total` field never asserted [LOW]
- **What's wrong:** Count subquery logic untested across all paginated endpoints.

#### LST-5: No negative/zero price test [LOW]
- **What's wrong:** Schema uses `gt=0` but boundary not tested.

#### LST-6: 403 instead of 401 for missing auth [MEDIUM]

---

### `tests/test_webhooks.py`

#### WHK-1: 403 ownership check never tested [MEDIUM]
- **What's wrong:** Router raises 403 when `auth.agent_id != agent_id`. No test exercises this.

#### WHK-2: Webhook payload content never validated [MEDIUM]
- **Test:** `test_notify_job_event_creates_two_deliveries` (line 100)
- **What's wrong:** Verifies delivery count but never inspects `delivery.payload` content.

#### WHK-3: `payload` field missing from `WebhookDeliveryResponse` schema [MEDIUM]
- **What's wrong:** Agents can't see what payload was sent without DB access. No test documents this omission.

#### WHK-4: Weakened `test_redeliver_delivered_webhook` assertions [LOW]
- **What's wrong:** Doesn't verify `attempts == 0` and `last_error is None` reset.

---

### `tests/test_abort_penalties.py`

#### ABT-1: `seller_abort_penalty > max_budget` not validated [HIGH]
- **Test:** `test_propose_penalty_exceeds_budget_rejected` (line 129)
- **What's wrong:** Only validates `client_abort_penalty > max_budget`. Source code (`validate_penalties`) has no check for seller penalty exceeding budget. A seller could be required to post a bond larger than the job value.

#### ABT-2: Missing Docker skipif on `test_verify_fail_returns_to_in_progress` [MEDIUM]
- **What's wrong:** Sibling test has Docker skip guard; this one doesn't. Will fail or produce wrong results in CI.

#### ABT-3: No test for abort from FUNDED state [LOW]
- **What's wrong:** All abort tests start the job first (IN_PROGRESS). FUNDED state abort path untested.

---

### `tests/test_reviews.py`

#### REV-1: Weakened `total <= 1` assertion [HIGH]
- **Test:** `test_get_agent_reviews_pagination` (line 329)
- **What's wrong:** `assert resp.json()["total"] <= 1` instead of `== 1`. Would pass with 0 results.

#### REV-2: Pagination test doesn't test pagination [MEDIUM]
- **Test:** `test_get_agent_reviews_pagination`
- **What's wrong:** Creates 1 review, requests `limit=1&offset=0`. Never tests multiple pages.

#### REV-3: Reputation visible below display threshold [MEDIUM]
- **Test:** `test_reputation_updates` (line 157)
- **What's wrong:** `get_reputation()` returns "New" for `<3` reviews, but raw score is written to agent and returned by GET agent endpoint. Test validates the leaked score.

#### REV-4: No test for review rejection on CANCELLED jobs [MEDIUM]
- **What's wrong:** Source allows reviews on COMPLETED/FAILED/RESOLVED but not CANCELLED. No test verifies CANCELLED rejection.

#### REV-5: `test_get_reviews_for_job` only checks count, not content [LOW]

---

### `tests/test_discover_online.py`

#### DSC-1: Weakened `>= 1` assertions [MEDIUM]
- **Tests:** `test_discover_online_true`, `test_discover_online_false`
- **What's wrong:** Should be `== 1` for precise filter validation.

#### DSC-2: No ILIKE wildcard character edge case tests [MEDIUM]
- **What's wrong:** `skill_id` filter uses ILIKE without escaping `%` and `_` metacharacters.

---

### `tests/test_websocket_presence.py`

#### WS-1: TestPingPong tests are tautological [HIGH]
- **Tests:** `test_server_sends_ping_expects_pong`, `test_client_ping_gets_pong_response` (lines 314-344)
- **What's wrong:** Create a mock WebSocket, manually send messages, assert the mock recorded what was sent. Never exercise actual server ping/pong handler logic.

---

### `tests/test_moltbook.py`

#### MOL-1: Low karma allowed -- documents unimplemented enforcement [MEDIUM]
- **Test:** `test_register_moltbook_low_karma_currently_allowed` (line 156)
- **What's wrong:** `moltbook_min_karma` config exists but enforcement is dead code. Test passes and hides this gap.

#### MOL-2: Error handling tests only test framework, not source [MEDIUM]
- **Tests:** `test_register_moltbook_invalid_token`, `test_register_moltbook_api_down`, `test_register_moltbook_no_api_key_configured`
- **What's wrong:** Mock `verify_identity_token` with `HTTPException` side effects. Never exercise actual error-handling logic in source.

---

### `tests/test_config.py`

#### CFG-1: `require_agent_card` default assertion is wrong [HIGH]
- **Test:** `test_default_settings_testable` (line 48)
- **What's wrong:** Asserts `s.require_agent_card is False` but source default is `True`. Only passes due to `.env` file override.

---

### `tests/test_schema_validation.py`

#### SCH-1: Documents zero-amount deposit as expected behavior [MEDIUM]
- **Test:** `test_deposit_amount_validation` (line 121)
- **What's wrong:** Deposits `amount: "0"` and asserts 200. Documents that dev deposit has no input validation.

---

### `tests/test_e2e_demo.py`

#### E2E-1: Loose float balance assertions with contradictory comments [MEDIUM]
- **Tests:** lines 212-222
- **What's wrong:** `seller_balance > 29.00` and `469.00 < client_balance < 470.00`. Comments disagree about fee math.

#### E2E-2: Entire test skipped in CI [LOW]

---

### SDK Tests (`sdk/tests/`)

#### SDK-1: 14 public client methods have zero test coverage [HIGH]
- **What's wrong:** `counter_job`, `start_job`, `complete_job`, `fail_job`, `update_agent`, `get_agent_card`, `register_agent`, `create_listing`, `get_listing`, `update_listing`, `browse_listings`, `submit_review`, `get_agent_reviews`, `get_job_reviews` are untested.

#### SDK-2: No client tests verify request bodies [MEDIUM]
- **What's wrong:** All tests mock HTTP responses and only check return values. Request payloads (amounts, addresses) never validated.

#### SDK-3: `test_status_no_config` mocks wrong exception type [MEDIUM]
- **Test:** `test_status_no_config` (test_cli.py:216)
- **What's wrong:** Mocks generic `Exception` instead of `ArcoaConfigError`. Tests wrong code path.

#### SDK-4: No `401 Unauthorized` exception class or test [MEDIUM]
- **What's wrong:** `_STATUS_MAP` has no 401 entry. Auth-heavy system with no dedicated auth error handling.

#### SDK-5: `save_config` file permissions (0o600) untested [MEDIUM]
- **What's wrong:** Private key file security permissions never verified in tests.

#### SDK-6: WebSocket proxy `propose_job` has its own dict construction logic, untested [MEDIUM]

#### SDK-7: Config auto-load silently swallows errors [MEDIUM]
- **What's wrong:** `ArcoaClient.__init__` has `except Exception: pass` on config load. Untested.

#### SDK-8: Backoff cap at 60s not tested [LOW]
#### SDK-9: Assertions buried in mock side_effects [LOW]
- **Tests:** `test_init_passes_hosting_mode_websocket`, `test_recover_with_user_provided_key`, `test_init_with_capabilities`

---

### Clean Files (No significant findings)

The following test files were cross-referenced and found to be correct:

- `tests/test_agent_card.py` -- All assertions match source. Comprehensive.
- `tests/test_crypto.py` -- Thorough sign/verify coverage. Clean.
- `tests/test_dashboard.py` -- Login, token, data, deactivation all correct.
- `tests/test_deadline_queue.py` -- Enqueue/cancel/fail/recovery correct (one unnecessary mock).
- `tests/test_health.py` -- Minimal but correct.
- `tests/test_human_confirmation.py` -- Well-tested content negotiation and flows.
- `tests/test_ip_rate_limit.py` -- Comprehensive bucket testing. Clean.
- `tests/test_middleware.py` -- Security headers exact-match. Body limits correct.
- `tests/test_optional_endpoint.py` -- All hosting modes validated. Clean.
- `tests/test_rate_limit.py` -- Headers, 429, buckets all correct.
- `tests/test_secrets.py` -- Env/GCP backends properly tested.
- `tests/test_startup_recovery.py` -- All recovery paths tested with proper mocking.
- `tests/test_task_registry.py` -- Register/unregister/shutdown exact match. Clean.

---

## Recommendations (Priority Order)

### Immediate (Source Code Bugs)

1. **Fix deposit endpoint authorization** (AGT-1): Add `if auth.agent_id != agent_id: raise HTTPException(403)` to `dev_deposit`
2. **Fix deposit endpoint validation** (AGT-2): Replace inline `DevDepositRequest` with proper `DepositRequest` schema
3. **Fix `verify_email()` purpose filter** (AUTH-1): Add `purpose == VerificationPurpose.signup` filter
4. **Fix `validate_registration_token()` purpose filter** (AUTH-2): Same pattern
5. **Fix `seller_abort_penalty` validation** (ABT-1): Add `seller_abort_penalty > max_budget` check to `validate_penalties`
6. **Fix `refund_escrow()` seller bond** (ESC-3): Return `seller_bond_amount` to seller on refund path
7. **Fix `browse_listings` deactivated agent filter** (LST-1): Join against Agent table, filter `status == ACTIVE`

### High Priority (Test Fixes)

8. **Rewrite `test_escrow_audit_log_populated`** (ESC-1): Create escrow flow in same transaction, remove `if logs:` guard, assert specific entries
9. **Rewrite `test_failed_withdrawal_refunds_balance`** (WAL-1): Exercise actual `_process_withdrawal` refund logic
10. **Add abort endpoint tests** (JOB-1): Client abort, seller abort, penalty distribution, bond forfeiture, invalid state rejection
11. **Add verify endpoint tests to test_jobs.py** (JOB-2): Pass/fail, concurrent lock, non-client rejection, non-delivered state
12. **Fix TestPingPong tests** (WS-1): Exercise actual WebSocket handler, not mocks
13. **Fix `test_default_settings_testable`** (CFG-1): Assert `require_agent_card is True` or explicitly construct with override

### Medium Priority

14. Add GKE backend tests (SBX-1) -- at minimum mocked K8s client tests
15. Add SDK coverage for untested methods (SDK-1)
16. Tighten `>=` assertions to `==` across discover, reviews, admin stats
17. Add seller bond tests for both release and refund paths
18. Add concurrent access tests for wallet and escrow
19. Fix deliver endpoint fee-before-auth ordering (JOB-3)
20. Add webhook ownership (403) tests

---

## Methodology

Each audit agent:
1. Read the complete test file
2. Read all corresponding source files (router, service, model, schema)
3. Cross-referenced every assertion against actual source code behavior
4. Verified status codes, response schemas, error messages, and business logic
5. Checked for skips, xfails, TODO/FIXME/HACK, suspicious mocks
6. Identified missing edge case coverage for critical paths

14 parallel audit agents covered all 37 test files. Findings were deduplicated and severity-ranked.
