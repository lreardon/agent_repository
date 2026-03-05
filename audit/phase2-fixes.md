# Phase 2 Fixes — HIGH and MEDIUM Findings

## HIGH Priority

### H1: Add /abort endpoint tests (JOB-1)
- tests/test_jobs.py or new tests/test_abort.py
- Test client abort with penalties
- Test seller abort with bond forfeiture  
- Test abort from FUNDED state (not just IN_PROGRESS)
- Test abort rejection from invalid states

### H2: Add /verify endpoint tests (JOB-2)
- tests/test_jobs.py or tests/test_verify.py
- Test verify pass → escrow release
- Test verify fail → back to IN_PROGRESS
- Test concurrent verification lock
- Test non-client rejection
- Test non-delivered state rejection

### H3: Rewrite TestPingPong (WS-1)
- tests/test_websocket_presence.py
- Tests currently just mock WebSocket and test the mock
- Need to exercise actual server handler logic

### H4: seller bond return on release untested (ESC-2)
- tests/test_escrow.py
- Add test with seller_abort_penalty > 0 that verifies bond returned on successful release

### H5: GKE backend zero coverage (SBX-1)
- tests/test_sandbox.py
- At minimum add mocked K8s client tests

### H6: No negative/zero deposit tests (AGT-3)
- Covered by Phase 1 Bug 2

### H7: Weakened review pagination assertion (REV-1)
- tests/test_reviews.py — change `<= 1` to `== 1`

### H8: SDK — 14 untested public methods (SDK-1)
- sdk/tests/test_client.py — add basic tests

### H9: No comma-separated admin key test (ADM-2)
- tests/test_admin.py

## MEDIUM Priority

### M1: Double-release/double-refund prevention (ESC-4)
### M2: Escrow response under-validated (ESC-5)
### M3: Deliver fee-before-auth ordering (JOB-3) — covered in Phase 1
### M4: fee_charged never asserted on deliver (JOB-4)
### M5: Fail endpoint leaks criteria to third parties (JOB-5)
### M6: Same-party consecutive counters (JOB-6) — document if intentional
### M7: Withdrawal below minimum passes for wrong reason (WAL-2)
### M8: test_withdrawal_must_exceed_fee tests success not rejection (WAL-3)
### M9: No concurrent withdrawal test (WAL-4)
### M10: Dev deposit has no own-agent auth (WAL-5) — covered in Phase 1
### M11: Deactivate agent wrong-owner test missing (AGT-4)
### M12: webhook_secret leakage guard (AGT-5)
### M13: CI never runs security isolation tests (SBX-2)
### M14: test_runner.py untested (SBX-3)
### M15: No escrow audit log on admin force-refund (ADM-1)
### M16: Admin list endpoints don't validate schemas (ADM-3)
### M17: Admin stats uses >= only (ADM-4)
### M18: Weakened pagination assertion in listings (LST-2)
### M19: No unique constraint test for listings (LST-3)
### M20: Webhook ownership 403 never tested (WHK-1)
### M21: Webhook payload never validated (WHK-2)
### M22: Verify lock before auth check (VER-3)
### M23: Concurrent verification bypasses real lock (VER-4)
### M24: No verify on non-delivered job test (VER-5)
### M25: Missing status assertions in verify helpers (VER-7)
### M26: Nonce-optional for GETs (AUTH-4) — document as design decision
### M27: MoltBook low karma enforcement dead code (MOL-1)
### M28: MoltBook error tests only test framework (MOL-2)
### M29: Schema test documents zero-amount deposit as OK (SCH-1)
### M30: E2E loose float assertions (E2E-1)
### M31: ILIKE wildcard edge cases (DSC-2)
### M32: Weakened discover online assertions (DSC-1)
### M33: Review pagination doesn't test pagination (REV-2)
### M34: Review on CANCELLED job not tested (REV-4)
### M35: SDK request bodies never validated (SDK-2)
