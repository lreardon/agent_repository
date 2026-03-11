# Problems Found During End-to-End Agent Testing

**Date:** 2026-03-11
**Tester:** Clob (OpenClaw AI assistant, agent ID `72f44844-fc49-4b0c-abdc-fe01ac14705e`)
**Environment:** Staging (`api.staging.arcoa.ai`)

## Bug 1: Job Creation Returns 500 (FIXED)

**Severity:** Critical — blocks entire job lifecycle
**Status:** ✅ Fixed and deployed (commit `9f474b3`)

**Symptom:** `POST /jobs` returns HTTP 500, even though the job is successfully created in the database.

**Root Cause:** `notify_job_event()` → `enqueue_webhook()` calls `db.commit()`, which expires all SQLAlchemy ORM instances in the session. When the router then serializes the response with `JobResponse.model_validate(job)`, Pydantic accesses expired relationship attributes synchronously, triggering:

```
MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.
Was IO attempted in an unexpected place?
```

**Fix:** Added `await db.refresh(job)` after every `notify_job_event()` call in `app/routers/jobs.py`. This pattern existed in 12+ locations across the jobs router.

**Impact:** Every job lifecycle endpoint (propose, counter, accept, fund, start, deliver, verify, fail, abort) was affected. The bug was masked in tests because the test DB session uses `expire_on_commit=False` or doesn't go through the webhook path.

---

## Problem 2: Hosted Hello-Agent Not Waking Up

**Severity:** High — blocks end-to-end job completion
**Status:** 🔴 Open

**Symptom:** When a job is proposed to the hello-world agent (`579ed914`), the platform tries to wake the hosted agent but fails:

```
"Deployment agent-579ed914 not ready after 30s"
"Failed to wake agent 579ed914: TypeError: object NoneType can't be used in 'await' expression"
```

The hello-agent was registered with `hosting_mode: "hosted"` and has a listing, but it never comes online to accept/process jobs. The scaler's `wake_agent()` function returns None (not awaitable), and even if it didn't error, the Cloud Run deployment for the agent doesn't start.

**Impact:** No end-to-end job completion is possible. A buyer agent can register, fund, and propose jobs, but the seller never responds.

**Suggested Fix:** 
- Fix the `wake_agent()` async/await bug in `app/services/hosting/scaler.py`
- Ensure the hello-agent's Cloud Run service is properly configured and can cold-start
- Consider adding a timeout/fallback for hosted agents that don't respond

---

## Problem 3: Deposit Watcher ImportError on Staging

**Severity:** Medium — deposit watcher crashes but deposits still work via manual notify
**Status:** 🟡 Open (not blocking)

**Symptom:** The background deposit watcher logs:
```
"Deposit watcher error: cannot import name 'ContractEvent' from 'web3.contract'"
```

This is a web3.py version incompatibility. The deposit watcher can't auto-detect on-chain deposits. Manual `POST /wallet/deposit-notify` still works as a workaround.

---

## Problem 4: Error Reporting Permission Denied

**Severity:** Low
**Status:** 🟡 Open

**Symptom:**
```
"Failed to report exception to Cloud Error Reporting: PermissionDenied('Error Reporting API has not been used in project 413175605742')"
```

The Cloud Error Reporting API needs to be enabled in the GCP project, or the Cloud Run service account needs the `errorreporting.writer` role.

---

## Test Flow Summary

What I (Clob) was able to do as an end-user agent:

1. ✅ `POST /v1/auth/signup` — received verification email
2. ✅ Verified email via link — received registration token
3. ✅ `POST /agents` — registered with Ed25519 keypair
4. ✅ `GET /listings` — found hello-world listing
5. ✅ `GET /agents/{seller_id}` — inspected seller profile
6. ✅ `GET /wallet/deposit-address` — got Base Sepolia USDC address
7. ✅ Sent 2.00 USDC on-chain, notified platform, balance credited
8. ✅ `POST /jobs` — proposed job (after fix deployed)
9. ❌ `POST /jobs/{id}/fund` — blocked: job needs seller acceptance first
10. ❌ Seller never responds — hosted agent won't wake up

**Conclusion:** The platform works up through job proposal. The blocking issue is that hosted agents can't be woken to accept/execute jobs. This makes end-to-end marketplace transactions impossible on staging without a live external agent.
