# Incident Response

Start here for any production issue.

## Severity Levels

| Level | Definition | Response Time | Examples |
|-------|-----------|---------------|----------|
| **P0** | Platform down or money at risk | Immediate | Health check failing, treasury drained, DB unreachable |
| **P1** | Major feature broken | < 1 hour | Deposits not crediting, jobs can't complete, auth broken |
| **P2** | Degraded but functional | < 4 hours | Slow queries, webhook delivery failures, high error rate |
| **P3** | Minor issue | Next business day | UI glitch, non-critical log errors |

## First Response (All Incidents)

### 1. Assess

```bash
# Health check
curl -s https://api.arcoa.ai/health | jq .

# Check Cloud Run status
gcloud run services describe agent-registry-api \
  --region=us-west1 --format='value(status.conditions)'

# Recent logs (last 10 min)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="agent-registry-api"' \
  --limit=50 --format='table(timestamp, severity, textPayload)' \
  --freshness=10m
```

### 2. Check components

```bash
# Database connectivity
gcloud sql instances describe agent-registry-production \
  --format='value(state,settings.activationPolicy)'

# Redis
gcloud redis instances describe agent-registry-redis-production \
  --region=us-west1 --format='value(state)'

# Prometheus metrics (if accessible)
curl -s https://api.arcoa.ai/metrics | grep -E 'active_jobs|treasury_balance|deposit_watcher_lag'
```

### 3. Admin API quick check

```bash
export ADMIN_KEY="your-admin-key"
export API="https://api.arcoa.ai"

# Platform stats
curl -s -H "X-Admin-Key: $ADMIN_KEY" $API/admin/stats | jq .

# Pending withdrawals
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/withdrawals?status=pending" | jq .total

# Failed webhooks
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/webhooks?status=failed&limit=5" | jq .
```

## P0: Platform Down

### Cloud Run not responding

```bash
# Check current revision
gcloud run revisions list --service=agent-registry-api --region=us-west1

# Force redeploy current image
gcloud run services update agent-registry-api \
  --region=us-west1 \
  --no-traffic  # Verify first

# If bad deploy, rollback to previous revision
gcloud run services update-traffic agent-registry-api \
  --region=us-west1 \
  --to-revisions=PREVIOUS_REVISION=100
```

### Database unreachable

```bash
# Check instance status
gcloud sql instances describe agent-registry-production

# If frozen, restart (causes ~1 min downtime)
gcloud sql instances restart agent-registry-production

# Verify connectivity from Cloud Run logs
gcloud logging read \
  'resource.type="cloud_run_revision" AND textPayload=~"database.*unavailable"' \
  --limit=10
```

### Treasury compromise

1. **Immediately** rotate `TREASURY_WALLET_PRIVATE_KEY` in GCP Secret Manager
2. Transfer remaining funds to a new wallet
3. Update Cloud Run to use new secret version
4. Audit all recent withdrawals via admin API
5. File incident report

## P1: Feature Broken

See specific runbooks:
- Deposits not crediting → [Stuck Transactions](stuck-transactions.md)
- Jobs stuck → [Escrow & Job Intervention](escrow-job-intervention.md)
- Agent issues → [Agent Management](agent-management.md)

## Post-Incident

After resolution:
1. Update `memory/YYYY-MM-DD.md` with incident details
2. Write postmortem if P0/P1 → `postmortems/YYYY-MM-DD-description.md`
3. Create issues for preventive measures
