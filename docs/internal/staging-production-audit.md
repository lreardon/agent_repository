# Staging → Production Infrastructure Audit

**Date:** 2026-03-01
**Auditor:** Clob

---

## 🔴 Critical (Must Fix Before Production)

### 1. Missing production environment variables in Cloud Run

The Cloud Run module only sets a handful of env vars (`ENV`, `REDIS_URL`, `DB_*`, `SECRETS_BACKEND`, `GCP_PROJECT_ID`, sandbox vars). Many production-critical settings are left at **app defaults** (which are dev defaults):

- `BLOCKCHAIN_NETWORK` → defaults to `base_sepolia` (testnet). **Must be `base_mainnet`.**
- `BASE_URL` → defaults to `http://localhost:8000`. Breaks email verification links, webhook URLs.
- `CORS_ALLOWED_ORIGINS` → defaults to `localhost:3000/5173`. API will reject real frontend requests.
- `EMAIL_BACKEND` → defaults to `log` (prints to console, never sends). Must be `smtp`.
- `EMAIL_VERIFICATION_REQUIRED` → defaults to `false`. No registration gate.
- `REQUIRE_AGENT_CARD` → defaults to `true` (actually correct), but it's not explicitly set — fragile.
- `MOLTBOOK_REQUIRED` / `MOLTBOOK_API_KEY` / `MOLTBOOK_API_URL` → not configured. Sybil prevention disabled.

### 2. No wallet secrets in Cloud Run

`TREASURY_WALLET_PRIVATE_KEY` and `HD_WALLET_MASTER_SEED` are not passed to Cloud Run at all. The wallet/escrow system literally can't function — no withdrawals, no deposit address derivation. These need to be GCP Secret Manager secrets mounted like `DB_PASSWORD`.

### 3. Same GCP project for staging and production

Both `staging.tfvars` and `production.tfvars` use `project_id = "agent-registry-488317"`. They share:

- The same Artifact Registry repo
- The same Workload Identity pool
- The same default VPC and VPC connector
- The same Cloud Tasks queue (`webhook-delivery` — hardcoded name, will collide)

A compromised staging environment gives lateral access to production resources. Production should be a separate GCP project.

### 4. No production CI/CD pipeline

Only `deploy-staging.yml` exists. There's no `deploy-production.yml`. No gated deployment, no manual approval step, no canary/blue-green strategy.

### 5. Committed secrets in tracked files

`.env` contains real private keys and an HD wallet mnemonic (`TREASURY_WALLET_PRIVATE_KEY`, `HD_WALLET_MASTER_SEED`). While `.env` is in `.gitignore`, the `demos/.env.staging` file **is tracked** and contains the same private key. Anyone with repo access has the treasury key.

---

## 🟠 High (Should Fix)

### 6. Redis has no auth and no HA

- `tier = "BASIC"` — no replication, single point of failure. Production should use `STANDARD_HA`.
- No `AUTH_STRING` / Redis AUTH configured. Any VPC-connected workload can read/write the cache (rate limit state, nonces).

### 7. GKE master API open to `0.0.0.0/0`

The `master_authorized_networks_config` includes `0.0.0.0/0` with comment "Allow kubectl (auth still required)". Auth-required doesn't make this safe — it expands the attack surface for credential-stuffing and API exploits. Production should restrict to known CIDRs.

### 8. Cloud Run uses default Compute Engine service account

Secrets, GKE impersonation, and all IAM are granted to the default Compute Engine SA (`PROJECT_NUMBER-compute@`). This SA has broad default permissions. Production should use a dedicated, least-privilege SA for the Cloud Run service.

### 9. No HTTPS enforcement or custom domain

Cloud Run's auto-generated `*.run.app` URL is fine for staging. Production needs a custom domain with managed SSL cert, and `BASE_URL` set accordingly.

### 10. `deletion_protection = false` for staging database

Expected, but the Terraform is configured to share state — make sure production workspace is isolated so a `terraform destroy` on staging can't accidentally target production.

---

## 🟡 Medium (Address Before or Shortly After Launch)

### 11. No Terraform workspace/state isolation

Staging and production share the same `backend "gcs"` bucket and prefix. Running `terraform apply -var-file=production.tfvars` in the wrong directory/state could be catastrophic. Use separate workspaces or separate state paths.

### 12. No observability stack

No error tracking (Sentry/etc.), no APM, no treasury balance alerting, no structured logging config. The `DEPLOYMENT_CHECKLIST.md` calls this out but nothing is implemented.

### 13. Staging image is still the hello-world placeholder

`cloud_run_image = "us-docker.pkg.dev/cloudrun/container/hello"` in `staging.tfvars`. The CI pipeline builds and deploys on push to main, but the tfvars still has the placeholder — any `terraform apply` would revert to hello-world.

### 14. SMTP credentials not in Secret Manager

When `EMAIL_BACKEND=smtp`, the SMTP username/password need to come from somewhere secure. They're not in the secrets module.

### 15. No database connection pooling

Cloud Run can scale to 10 instances, each opening direct connections. No PgBouncer or Cloud SQL connection limit config. Risk of exhausting Postgres `max_connections`.

---

## Summary

The Terraform infrastructure is well-structured for staging, but the gap to production is significant. The biggest categories:

1. **Missing env vars** — the Cloud Run module passes ~30% of what the app needs
2. **Missing secrets** — wallet keys aren't in Secret Manager or Cloud Run
3. **Blast radius** — shared project, shared VPC, shared state means staging ↔ production aren't isolated
4. **No prod deploy pipeline** — CI only covers staging
