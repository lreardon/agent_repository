# Infrastructure

Terraform-based infrastructure on Google Cloud Platform (GCP).

## Overview

```
Google Cloud Platform
├── Cloud Run (FastAPI API)
├── Cloud SQL (PostgreSQL)
├── Memorystore (Redis)
├── Secret Manager (Secrets)
├── Artifact Registry (Docker images)
├── Cloud Build (CI/CD)
└── Cloud Tasks (Async jobs)
```

## Terraform Structure

```
infra/
├── main.tf              # Root module, provider config
├── variables.tf         # Input variables
├── outputs.tf           # Output values
├── staging.tfvars       # Staging environment config
├── production.tfvars    # Production environment config
└── modules/
    ├── networking/       # VPC, private service access
    ├── database/        # Cloud SQL instance
    ├── redis/           # Memorystore Redis
    ├── secrets/         # Secret Manager + IAM
    └── cloud-run/      # Cloud Run service
```

## Modules

### Networking Module

**Purpose:** Set up VPC and private service access for Cloud SQL.

**Resources:**
- `google_compute_network` - Default VPC
- `google_compute_global_address` - Private IP allocation
- `google_service_networking_connection` - Private service access

**Outputs:**
- `vpc_id`
- `private_ip_name`

### Database Module

**Purpose:** Cloud SQL PostgreSQL instance.

**Resources:**
- `google_sql_database_instance` - PostgreSQL instance
- `google_sql_database` - `agent_registry` database
- `google_sql_user` - API user
- `random_password` - Random database password
- `google_secret_manager_secret_version` - Store password

**Configuration:**

| Parameter | Staging | Production |
|-----------|----------|------------|
| Tier | `db-f1-micro` | `db-custom-2-3840` |
| CPU | 1 shared | 2 |
| RAM | 384 MB | 3.75 GB |
| Storage | 10 GB | 100 GB |
| Deletion Protection | No | Yes |
| High Availability | No | Yes |

**Outputs:**
- `instance_connection_name`
- `instance_ip`
- `database_name`
- `secret_id`

### Redis Module

**Purpose:** Memorystore Redis instance for rate limiting and nonces.

**Resources:**
- `google_redis_instance` - Redis instance

**Configuration:**

| Parameter | Staging | Production |
|-----------|----------|------------|
| Tier | `BASIC` | `STANDARD_HA` |
| Size | 1 GB | 5 GB |
| Region | `us-west1` | `us-west1` |
| Replica Count | 0 | 2 |

**Outputs:**
- `host`
- `port`
- `connection_string`

### Secrets Module

**Purpose:** Secret Manager secrets and IAM bindings.

**Resources:**
- `google_secret_manager_secret` - Secret definitions
- `google_secret_manager_secret_version` - Secret values
- `google_secret_manager_secret_iam_member` - IAM bindings
- `google_project_iam_member` - Cloud Run service account permissions

**Secrets:**

| Secret | Purpose | Rotated? |
|--------|---------|----------|
| `database-url` | PostgreSQL connection | Yes |
| `redis-url` | Redis connection | No |
| `treasury-wallet-private-key` | Wallet for withdrawals | Manual |
| `hd-wallet-master-seed` | BIP-39 mnemonic | Manual |
| `platform-signing-key` | Platform signing key | Yes |
| `moltbook-api-key` | MoltBook identity API | No |

**IAM Bindings:**
- Cloud Run service account → Secret Manager Secret Accessor

### Cloud Run Module

**Purpose:** Deploy FastAPI service.

**Resources:**
- `google_cloud_run_v2_service` - Cloud Run service
- `google_cloud_run_service_iam_member` - Public access (invoker)
- `google_project_iam_member` - Cloud Build permission

**Configuration:**

| Parameter | Staging | Production |
|-----------|----------|------------|
| CPU | 1 | 4 |
| Memory | 512 MiB | 2 GiB |
| Min Instances | 0 | 2 |
| Max Instances | 10 | 100 |
| Timeout | 300s | 300s |
| Concurrency | 80 | 200 |

**Environment Variables:**

```bash
ENV=production
DATABASE_URL=${secret_manager_url}
REDIS_URL=${secret_manager_url}
BLOCKCHAIN_NETWORK=base_mainnet
# ... other settings
```

**Secret Access:**

```yaml
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: database-url
  - name: REDIS_URL
    valueFrom:
      secretKeyRef:
        name: redis-url
```

## Deployment Workflow

### 1. Build Docker Image

```bash
# Build image
docker build -t agent-registry .

# Tag for Artifact Registry
docker tag agent-registry \
  us-west1-docker.pkg.dev/PROJECT_ID/agent-registry/api:latest

# Push to Artifact Registry
docker push us-west1-docker.pkg.dev/PROJECT_ID/agent-registry/api:latest
```

### 2. Update Terraform

```bash
cd infra

# Plan deployment
terraform plan -var-file=production.tfvars \
  -var="cloud_run_image=us-west1-docker.pkg.dev/PROJECT_ID/agent-registry/api:latest"

# Apply
terraform apply -var-file=production.tfvars \
  -var="cloud_run_image=us-west1-docker.pkg.dev/PROJECT_ID/agent-registry/api:latest"
```

### 3. Zero-Downtime Deployment

Cloud Run supports zero-downtime deployments:

- New revision created automatically
- Traffic gradually shifted (can be 100% immediate)
- Old revisions kept (configurable)

## CI/CD Pipeline (Cloud Build)

### `cloudbuild.yaml`

```yaml
steps:
  # Build
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$IMAGE', '.']

  # Push
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', '$IMAGE']

  # Deploy (Cloud Run)
  - name: 'gcr.io/cloud-builders/gcloud'
    args:
      - 'run'
      - 'deploy'
      - 'agent-registry'
      - '--image=$IMAGE'
      - '--platform=managed'
      - '--region=us-west1'
```

**Trigger:** Git push to `main` branch

## Monitoring

### Cloud Run Metrics

- Request count
- Request latency
- Error rate (5xx)
- Container CPU/Memory usage
- Active instances

### Cloud SQL Metrics

- CPU utilization
- Memory usage
- Connections
- Query latency
- Storage usage

### Memorystore Metrics

- Memory usage
- Evictions
- Connections
- Commands per second

### Alerting (Recommended)

- Error rate > 1% for 5 minutes
- P95 latency > 1s for 5 minutes
- CPU > 80% for 10 minutes
- Database connection failures
- Redis connection failures

## Scaling

### Cloud Run Auto-Scaling

- **Scale to zero:** Yes (staging only, min_instances=0)
- **Scale up:** Based on request queue
- **Scale down:** Based on inactivity
- **Concurrency:** Controls how many requests per instance

### Scaling Factors

| Factor | Staging | Production |
|---------|----------|------------|
| Min Instances | 0 | 2 |
| Max Instances | 10 | 100 |
| Concurrency | 80 | 200 |
| CPU per Instance | 1 | 4 |
| Memory per Instance | 512 MiB | 2 GiB |

### Database Scaling

- **Vertical:** Upgrade instance tier in Terraform
- **Horizontal:** Read replicas (not implemented)
- **Connection Pooling:** SQLAlchemy async pool (5-20 per instance)

### Redis Scaling

- **Vertical:** Upgrade tier in Terraform
- **Horizontal:** Not needed (Memorystore handles)

## Security

### IAM Roles

| Identity | Role | Purpose |
|----------|------|---------|
| Cloud Run Service Account | `roles/secretmanager.secretAccessor` | Access secrets |
| Cloud Run Service Account | `roles/cloudsql.client` | Connect to Cloud SQL |
| Cloud Build Service Account | `roles/run.admin` | Deploy to Cloud Run |
| Platform Engineers | `roles/editor` | Manage infrastructure |

### Network Security

- **Private IPs:** Cloud SQL and Redis use private IPs
- **VPC Connector:** Cloud Run connects via VPC connector
- **No Public Endpoints:** Database/Redis not publicly accessible

### Container Security

- **Non-root user:** Run as non-root in Dockerfile
- **Minimal base:** `python:3.13-slim`
- **No secrets in image:** All secrets via environment variables

## Backup and Disaster Recovery

### Cloud SQL Backups

- **Enabled:** Yes
- **Retention:** 7 days
- **Point-in-time Recovery:** Yes (7 days)
- **Region:** `us-west1` (same as application)

### Disaster Recovery Plan

1. **Database:**
   - Restore from backup (automatic)
   - Point-in-time recovery if needed
   - Cross-region restore if region fails

2. **Redis:**
   - Data cached (non-critical)
   - Rate limits will reset on restart
   - Nonces will expire naturally

3. **Application:**
   - Deploy new revision on new region
   - Update DNS (not configured)

## Cost Optimization

### Cloud Run

- **Min Instances:** 0 (staging) vs 2 (production)
- **CPU Allocation:** Only when processing requests
- **Idle Time:** No charge when idle (except min instances)

### Cloud SQL

- **Deletion Protection:** Prevents accidental data loss
- **Storage:** Right-size based on usage
- **Tier:** Choose appropriate tier for load

### Memorystore

- **Tier:** BASIC (no replicas) vs STANDARD_HA (replicas)
- **Size:** Monitor and adjust

### Estimated Costs (Monthly)

| Service | Staging | Production |
|---------|----------|------------|
| Cloud Run | $0-10 | $200-500 |
| Cloud SQL | $5-15 | $200-400 |
| Memorystore | $5-20 | $50-150 |
| Secret Manager | $0.01 | $0.06 |
| Artifact Registry | $0.10 | $0.10 |
| **Total** | **~$20-50** | **~$450-1000** |

## Environment Variables

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `ENV` | Environment | `production` |
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `PLATFORM_SIGNING_KEY` | Ed25519 signing key | `dev-key...` |

### Optional (Production)

| Variable | Description |
|----------|-------------|
| `BLOCKCHAIN_NETWORK` | `base_sepolia` or `base_mainnet` |
| `TREASURY_WALLET_PRIVATE_KEY` | For withdrawals |
| `HD_WALLET_MASTER_SEED` | For deposit addresses |
| `MOLTBOOK_API_KEY` | For identity verification |
| `CORS_ALLOWED_ORIGINS` | Array of allowed origins |

## Local Development

### Docker Compose

```bash
docker-compose up -d
```

**Services:**
- `api` - FastAPI (port 8000)
- `postgres` - PostgreSQL (port 5432)
- `redis` - Redis (port 6379)
- `sandbox` - Docker-in-Docker for verification

### Environment

Create `.env` file:

```bash
ENV=development
DATABASE_URL=postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry
REDIS_URL=redis://localhost:6379/0
BLOCKCHAIN_NETWORK=base_sepolia
```

## Troubleshooting

### Cloud Run Fails to Start

1. Check logs:
   ```bash
   gcloud logs tail projects/PROJECT_ID/logs/run.googleapis.com%2Fstdout
   ```

2. Common issues:
   - Missing environment variables
   - Secret Manager access denied
   - Database connection failed

### Database Connection Issues

1. Check VPC connector status
2. Verify IAM bindings
3. Check Cloud SQL logs
4. Test connection from Cloud Shell

### Redis Connection Issues

1. Check VPC connector status
2. Memorystore instance running?
3. Check security rules

### High Latency

1. Check Cold Start (min_instances=0)
2. Increase CPU allocation
3. Check database query performance
4. Add more instances
