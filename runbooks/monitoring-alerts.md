# Monitoring & Alerts

## Health Check

```bash
curl -s https://api.arcoa.ai/health | jq .
```

Response:
```json
{
  "status": "healthy",        // or "unhealthy"
  "components": {
    "database": "ok",          // or "unavailable"
    "redis": "ok"              // or "unavailable"
  },
  "in_flight_tasks": 2        // background wallet tasks
}
```

**Monitor this endpoint externally** (UptimeRobot, GCP Uptime Check, etc.). Alert if non-200 for > 1 minute.

## Prometheus Metrics

Available at `GET /metrics` (excluded from OpenAPI docs).

### Key metrics to watch

| Metric | Alert Threshold | Meaning |
|--------|----------------|---------|
| `treasury_balance_usdc` | < 500 | Treasury running low — fund it |
| `deposit_watcher_lag_seconds` | > 60 | Deposit scanner stuck |
| `active_jobs` | Informational | Active job count |
| `escrow_volume_usd_total` | Informational | Total escrow volume |
| `http_requests_total{status="5xx"}` | > 10/min | Server errors spiking |
| `http_request_duration_seconds` | p99 > 5s | Slow requests |

### Quick metric check

```bash
curl -s https://api.arcoa.ai/metrics | grep -E \
  'treasury_balance|deposit_watcher_lag|active_jobs|escrow_volume'
```

## Cloud Run Metrics

```bash
# Request count and latency
gcloud monitoring dashboards list --format='table(name, displayName)'

# Error rate (last hour)
gcloud logging read \
  'resource.type="cloud_run_revision" AND severity>=ERROR' \
  --limit=20 --freshness=1h
```

## Log Queries

### Recent errors

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND resource.labels.service_name="agent-registry-api"
   AND severity>=ERROR' \
  --limit=20 --format='table(timestamp, textPayload)'
```

### Wallet-related logs

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND textPayload=~"deposit|withdrawal|treasury|wallet"' \
  --limit=30
```

### Admin API usage

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND textPayload=~"Admin"' \
  --limit=20
```

### Slow requests (> 2 seconds)

```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   AND httpRequest.latency>"2s"' \
  --limit=20
```

## Alert Response

### Treasury balance low

1. Check current balance: `curl -s .../metrics | grep treasury_balance`
2. Fund the treasury wallet with USDC on Base
3. Withdrawals auto-pause below `treasury_pause_threshold_usdc` ($100 default)
4. They auto-resume once balance recovers above threshold

### Deposit watcher lag high

1. Check if the background task is running (logs)
2. Check RPC endpoint connectivity
3. If stuck, restart Cloud Run (see [Incident Response](incident-response.md))

### 5xx error spike

1. Check recent error logs (above)
2. Common causes:
   - Database connection exhaustion → see [Database Operations](database-operations.md#connection-pool-issues)
   - Redis down → check Memorystore status
   - Bad deploy → rollback (see [Deployment](deployment.md#rollback))

### High latency

1. Check database query performance:
   ```sql
   SELECT query, calls, mean_exec_time, total_exec_time
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC
   LIMIT 10;
   ```
2. Check Cloud Run container count (auto-scaling may be cold-starting)
3. Check for missing indexes on frequently queried columns

## Platform Stats (via Admin API)

Quick health snapshot beyond infrastructure:

```bash
export ADMIN_KEY="your-admin-key"
export API="https://api.arcoa.ai"

curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/stats" | jq '{
  agents: {total: .total_agents, active: .active_agents, suspended: .suspended_agents},
  jobs: {total: .total_jobs, by_status: .jobs_by_status},
  money: {escrow_held: .total_escrow_held, deposits: .total_deposits, withdrawals: .total_withdrawals},
  webhooks: {total: .total_webhook_deliveries, failed: .failed_webhook_deliveries}
}'
```
