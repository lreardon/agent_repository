# Database Operations

## Connection

### Cloud SQL Proxy (recommended for local access)

```bash
# Install if needed
gcloud components install cloud-sql-proxy

# Start proxy
cloud-sql-proxy agent-registry-488317:us-west1:agent-registry-production &

# Connect
psql "postgresql://api_user:PASSWORD@localhost:5432/agent_registry"
```

### Direct via gcloud

```bash
gcloud sql connect agent-registry-production --user=api_user --database=agent_registry
```

### From Cloud Run logs

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND textPayload=~"database"' \
  --limit=20
```

## Backups

### List available backups

```bash
gcloud sql backups list --instance=agent-registry-production \
  --format='table(id, startTime, status, type)'
```

### Create on-demand backup

```bash
# Before any risky operation
gcloud sql backups create --instance=agent-registry-production \
  --description="Pre-migration backup $(date +%Y-%m-%d)"
```

### Restore from backup

**⚠️ This OVERWRITES the entire instance. Causes downtime.**

```bash
# 1. Create an on-demand backup of current state first (safety net)
gcloud sql backups create --instance=agent-registry-production \
  --description="Pre-restore safety backup"

# 2. Restore
gcloud sql backups restore BACKUP_ID \
  --restore-instance=agent-registry-production \
  --backup-instance=agent-registry-production

# 3. Verify
curl -s https://api.arcoa.ai/health | jq .
```

### Point-in-Time Recovery (Production only)

Restore to any point within the last 7 days:

```bash
# Create a NEW instance from PITR (doesn't affect running instance)
gcloud sql instances clone agent-registry-production agent-registry-pitr-restore \
  --point-in-time="2026-03-04T15:30:00Z"

# Verify data on the clone, then swap if needed
```

## Migrations

### Run migrations (staging)

```bash
# SSH into Cloud Run or run locally against staging DB
alembic upgrade head
```

### Run migrations (production)

```bash
# 1. Create pre-migration backup
gcloud sql backups create --instance=agent-registry-production \
  --description="Pre-migration $(date +%Y-%m-%d)"

# 2. Run migration
# Option A: Via Cloud SQL Proxy locally
DATABASE_URL="postgresql+asyncpg://api_user:PASS@localhost:5432/agent_registry" \
  alembic upgrade head

# Option B: Cloud Run job (if configured)
gcloud run jobs execute alembic-migrate --region=us-west1

# 3. Verify
curl -s https://api.arcoa.ai/health | jq .
```

### Rollback a migration

```bash
# Check current version
alembic current

# Downgrade one step
alembic downgrade -1

# Or downgrade to specific revision
alembic downgrade REVISION_ID
```

### Common migration issues

**Orphaned enum types:** Test teardown drops tables but not custom Postgres enum types. This causes `alembic upgrade head` to fail with "type already exists" on a fresh schema.

```sql
-- Find orphaned enums
SELECT typname FROM pg_type t
JOIN pg_namespace n ON t.typnamespace = n.oid
WHERE n.nspname = 'public'
AND typname IN ('agentstatus', 'jobstatus', 'escrowstatus', 'escrowaction',
                'depositstatus', 'withdrawalstatus', 'webhookstatus',
                'verificationpurpose');

-- Drop them if needed
DROP TYPE IF EXISTS agentstatus CASCADE;
-- etc.
```

**Stale alembic_version:** If the alembic_version table has a row pointing to a non-existent revision:

```sql
SELECT * FROM alembic_version;
-- If wrong:
DELETE FROM alembic_version;
-- Then re-stamp to current
-- alembic stamp head
```

## Connection Pool Issues

Cloud Run auto-scales containers. Each container opens its own connection pool. With many containers, you can exhaust Cloud SQL connections.

**Check connection count:**
```sql
SELECT count(*) FROM pg_stat_activity WHERE datname = 'agent_registry';
SELECT state, count(*) FROM pg_stat_activity
WHERE datname = 'agent_registry'
GROUP BY state;
```

**Kill idle connections:**
```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'agent_registry'
AND state = 'idle'
AND query_start < NOW() - INTERVAL '10 minutes';
```

**Prevention:** Set `max_instances` on Cloud Run to limit container count, and tune SQLAlchemy pool size in `database.py`.

## Table Sizes

```sql
SELECT relname AS table,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup AS row_count
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
```
