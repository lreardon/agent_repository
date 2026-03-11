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

## Problem 5: GKE Pods Have No Internet Egress (No Cloud NAT)

**Severity:** Critical — blocks all hosted agent WebSocket connections
**Status:** 🔴 Open

**Symptom:** Hosted agent pod starts successfully, authenticates `ARCOA_PRIVATE_KEY`, logs "Agent online — listening for jobs", but all WebSocket connections to `wss://api.staging.arcoa.ai/ws/agent` fail with "timed out during opening handshake".

**Root Cause:** The GKE Autopilot cluster (`agent-registry-sandbox-staging`) has **no Cloud Router or Cloud NAT** configured. GKE pods on a private cluster without NAT have no outbound internet access. The pod can't reach the external Cloud Run URL.

**Verified:**
- WebSocket endpoint works from external hosts (tested from laptop — auth succeeds, `auth_ok` returned)
- `gcloud compute routers list` returns 0 items
- Pod logs show exponential backoff on WS connection (1s → 2s → 4s → 8s → 16s → 32s → 60s)

**Fix Options (pick one):**
1. **Cloud NAT** (simplest) — Add a Cloud Router + NAT gateway for the GKE subnet. ~$30/month.
2. **Internal networking** — Route hosted agents to the Cloud Run service via Private Service Connect or internal URL, avoiding the need for internet egress entirely. More secure and zero NAT cost.
3. **Serverless VPC Connector** — If Cloud Run and GKE are on the same VPC, use a connector for internal routing.

**Recommendation:** Option 2 (internal networking) is the right long-term answer — hosted agents should never need internet access to talk to the platform. But Option 1 is the quickest unblock.

---

**Conclusion:** The platform works up through job proposal. Three separate issues prevented end-to-end completion:
1. ✅ Job serialization bug (MissingGreenlet) — fixed
2. ✅ Scaler bugs (await expire_all, missing ARCOA_PRIVATE_KEY) — fixed
3. 🔴 GKE has no internet egress — hosted agents can't reach the WebSocket endpoint
