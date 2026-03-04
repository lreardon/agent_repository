# Database Backup & Recovery Strategy

**Last updated:** 2026-03-03
**Applies to:** Cloud SQL PostgreSQL 16 (`agent-registry-{environment}`)

---

## Backup Configuration (Terraform-managed)

| Setting | Staging | Production |
|---------|---------|------------|
| Automated backups | ✅ Enabled | ✅ Enabled |
| Backup window | 03:00 UTC | 03:00 UTC |
| Retained backups | 7 | 30 |
| Point-in-time recovery (PITR) | ✅ Enabled | ✅ Enabled |
| WAL retention (for PITR) | 2 days | 7 days |
| Availability | Zonal | Regional (HA) |
| Disk | PD-SSD, autoresize | PD-SSD, autoresize |
| Deletion protection | ✅ On | ✅ On |

Source: `infra/modules/database/main.tf`

---

## What's Backed Up

- **Automated backups:** Full instance snapshot daily at 03:00 UTC. Managed by GCP — no action needed.
- **PITR (production only):** Write-ahead logs (WAL) are continuously archived. You can restore to any point within the last 7 days, down to the second.
- **On-demand backups:** Can be triggered manually via `gcloud` (see below). These don't count against the retention limit.

## What's NOT Backed Up

- **Redis:** Memorystore data (rate limit counters, deposit watcher cursor, deadline sorted sets). All are reconstructable — Redis is a cache/coordination layer, not source of truth.
- **GCP Secret Manager:** Secrets are versioned by GCP natively. No separate backup needed.
- **Terraform state:** Stored in GCS bucket `agent-registry-tf-state` with versioning (managed separately).

---

## Recovery Procedures

### 1. Restore from Automated Backup (Full Instance)

Use when: catastrophic data loss, corruption, or need to roll back to a known-good daily snapshot.

```bash
# List available backups
gcloud sql backups list --instance=agent-registry-production

# Find the backup ID you want, then restore
# WARNING: This OVERWRITES the target instance completely
gcloud sql backups restore BACKUP_ID \
  --restore-instance=agent-registry-production \
  --backup-instance=agent-registry-production
```

**Downtime:** Instance is unavailable during restore (~5-15 min depending on size).

**Post-restore checklist:**
1. Verify app connectivity: `curl https://api.arcoa.ai/health`
2. Check alembic migration version matches: `SELECT version_num FROM alembic_version;`
3. Verify escrow balances: `SELECT SUM(amount) FROM escrow WHERE status = 'held';`
4. Check treasury balance matches on-chain state
5. Restart Cloud Run to clear any stale connection pools: `gcloud run services update agent-registry-api-production --region=us-west1`

### 2. Point-in-Time Recovery

Use when: you know the exact moment something went wrong (accidental DELETE, bad migration, etc.) and want to recover to just before it. Available on both staging (2-day window) and production (7-day window).

```bash
# Restore to a specific timestamp (UTC)
# This creates a NEW instance — does not overwrite the existing one
gcloud sql instances clone agent-registry-production agent-registry-recovery \
  --point-in-time="2025-07-27T14:30:00Z"
```

**After cloning:**
1. Verify data on the recovery instance
2. Export the needed data or swap the instance:
   - **Option A (surgical):** Connect to recovery instance, export specific tables/rows, import into production
   - **Option B (full swap):** Update Cloud Run to point at recovery instance, rename instances

```bash
# Option B — instance swap (causes brief downtime)
# 1. Stop Cloud Run traffic
gcloud run services update agent-registry-api-production \
  --region=us-west1 --max-instances=0

# 2. Rename instances
gcloud sql instances patch agent-registry-production --activation-policy=NEVER
gcloud sql instances patch agent-registry-recovery --activation-policy=ALWAYS

# 3. Update Terraform state or Cloud Run connection string to point to recovery instance
# 4. Restore Cloud Run traffic
gcloud run services update agent-registry-api-production \
  --region=us-west1 --max-instances=2
```

**Important:** PITR clone inherits the same password. No credential changes needed unless you rotate.

### 3. Create On-Demand Backup

Use before: risky migrations, manual data changes, or any operation you might need to undo.

```bash
# Create an on-demand backup (does not affect automated schedule)
gcloud sql backups create --instance=agent-registry-production \
  --description="Pre-migration backup $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### 4. Export to GCS (Cold Archive)

For long-term retention beyond the 30-day window, or for compliance:

```bash
# Export entire database to GCS as SQL dump
gcloud sql export sql agent-registry-production \
  gs://agent-registry-backups/manual/$(date -u +%Y-%m-%d).sql.gz \
  --database=agent_registry
```

**Note:** The GCS bucket must grant `objectCreator` to the Cloud SQL service account. Create the bucket manually if it doesn't exist:

```bash
gsutil mb -l us-west1 gs://agent-registry-backups
SA=$(gcloud sql instances describe agent-registry-production --format='value(serviceAccountEmailAddress)')
gsutil iam ch serviceAccount:${SA}:objectCreator gs://agent-registry-backups
```

---

## Monitoring & Alerts

### Verify Backups Are Running

```bash
# Check last backup status
gcloud sql backups list --instance=agent-registry-production --limit=5
```

Expected: one backup per day, status `SUCCESSFUL`.

### Recommended Alert (Cloud Monitoring)

Create an alert policy for backup failures:
- **Metric:** `cloudsql.googleapis.com/database/auto_failover_request_count` (for HA failovers)
- **Log-based metric:** Filter on `resource.type="cloudsql_database" AND textPayload:"backup"` with severity `ERROR`
- **Notification:** Email + PagerDuty/Slack

---

## Disaster Recovery Scenarios

| Scenario | Action | RTO | RPO |
|----------|--------|-----|-----|
| Accidental row deletion | PITR clone to just before the DELETE | ~15 min | seconds |
| Bad migration | PITR or restore pre-migration on-demand backup | ~10 min | seconds–minutes |
| Instance corruption | Restore latest automated backup | ~15 min | ≤24h (last backup) |
| Zone outage (prod) | Automatic HA failover (Regional availability) | ~seconds | 0 (synchronous replication) |
| Region outage | Restore from GCS export in another region | ~30 min+ | depends on export frequency |
| Ransomware / compromised credentials | Restore backup + rotate all credentials | ~30 min | ≤24h |

---

## Runbook: Quarterly Backup Test

**Frequency:** Every 3 months (add to team calendar).

1. Create PITR clone of production to a test instance
2. Connect to test instance, run: `SELECT COUNT(*) FROM agents; SELECT COUNT(*) FROM jobs; SELECT version_num FROM alembic_version;`
3. Compare counts against production (should match within the PITR window)
4. Run the API test suite against the cloned database (update `DATABASE_URL` env)
5. Delete test instance: `gcloud sql instances delete agent-registry-recovery`
6. Log results in this document (append to section below)

### Test Log

| Date | Tester | Result | Notes |
|------|--------|--------|-------|
| _TBD_ | — | — | Initial setup — first test pending |

---

## Pre-Migration Checklist

Before running `alembic upgrade head` on production:

1. [ ] Create on-demand backup (see §3)
2. [ ] Note the current timestamp (for PITR if needed)
3. [ ] Run migration on staging first, verify
4. [ ] Run migration on production
5. [ ] Verify: `SELECT version_num FROM alembic_version;`
6. [ ] Smoke test critical paths: register → list → bid → escrow → verify
7. [ ] If anything fails: restore from on-demand backup or PITR
