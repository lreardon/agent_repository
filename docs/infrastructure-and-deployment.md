# Infrastructure & Deployment Reference

> **Audience**: This document is reference material for both the developer (Leland) and the OpenClaw agent building this project. Sections marked 🧑 are human-only tasks. Sections marked 🤖 are things the agent should know and reference when writing code.

---

## 🧑 Prerequisites (Human — Do Before Giving OpenClaw Any Tasks)

These are manual setup steps. Do not automate these. Do not ask OpenClaw to run these.

### GCP Project Setup

```bash
export PROJECT_ID="agent-registry"
export REGION="us-west1"

gcloud projects create $PROJECT_ID
gcloud config set project $PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  vpcaccess.googleapis.com \
  cloudtasks.googleapis.com \
  compute.googleapis.com
```

### Artifact Registry

```bash
gcloud artifacts repositories create agent-registry \
  --repository-format=docker \
  --location=$REGION
```

### VPC Connector

```bash
gcloud compute networks vpc-access connectors create agent-registry-connector \
  --region=$REGION \
  --range=10.8.0.0/28
```

### Cloud SQL (Staging)

```bash
gcloud sql instances create agent-registry-staging \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region=$REGION \
  --storage-auto-increase \
  --backup-start-time=03:00 \
  --availability-type=zonal

gcloud sql databases create agent_registry --instance=agent-registry-staging

gcloud sql users create api_user \
  --instance=agent-registry-staging \
  --password=$(openssl rand -base64 32)
```

### Memorystore Redis (Staging)

```bash
gcloud redis instances create agent-registry-redis-staging \
  --size=1 \
  --region=$REGION \
  --redis-version=redis_7_2 \
  --tier=basic
```

### Secret Manager

```bash
# Store DB password
echo -n "<generated-password>" | gcloud secrets create db-password-staging --data-file=-

# Platform webhook signing key
openssl rand -base64 32 | gcloud secrets create platform-signing-key-staging --data-file=-
```

### Cloud Tasks Queue

```bash
gcloud tasks queues create webhook-delivery \
  --location=$REGION \
  --max-dispatches-per-second=10 \
  --max-attempts=5 \
  --min-backoff=1s \
  --max-backoff=1800s
```

### Checklist

- [x] GCP project created with billing
- [x] All APIs enabled
- [x] Cloud SQL instance running
- [x] Memorystore Redis instance running
- [x] VPC connector created
- [x] Artifact Registry repo created
- [x] Secrets stored in Secret Manager
- [x] Cloud Tasks queue created
- [x] Docker Desktop running locally
- [x] `gcloud` CLI authenticated

---

## 🤖 GCP Architecture (Agent Reference)

The agent should understand this architecture when writing deployment configs, environment handling, and database connection code.

### Topology

```
Internet ──(HTTPS)──► Cloud Run (FastAPI, min 1, max 10)
                          │
                     VPC Connector
                      ┌───┴───┐
                      │       │
               Cloud SQL    Memorystore
              (Postgres 16)  (Redis 7.2)
                      │
                 Cloud Tasks
              (webhook delivery)
```

- Cloud Run connects to Cloud SQL and Redis over **private IP** via VPC connector.
- Cloud Run has a **public HTTPS endpoint** for inbound API traffic.
- Outbound webhook delivery goes through **Cloud Tasks** (not Celery, not arq).
- Secrets are in **Secret Manager**, accessed via environment variable references in the Cloud Run service config.

### Environments

| Environment | Cloud SQL Tier   | Redis     | Cloud Run     |
| ----------- | ---------------- | --------- | ------------- |
| staging     | db-f1-micro      | Basic 1GB | min 0, max 2  |
| production  | db-custom-1-3840 | Basic 1GB | min 1, max 10 |

### Connection Patterns

**Database** (Cloud SQL):

- Local dev: `postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry`
- Cloud Run: Connect via Unix socket provided by the Cloud SQL proxy sidecar. The connection string uses `/cloudsql/PROJECT:REGION:INSTANCE/.s.PGSQL.5432` as the host.
- Always use `asyncpg`. Never `psycopg2`.

**Redis** (Memorystore):

- Local dev: `redis://localhost:6379/0`
- Cloud Run: `redis://PRIVATE_IP:6379/0` (private IP from Memorystore, accessed via VPC connector). No auth on basic tier.

**Secrets**:

- Local dev: loaded from `.env` file via Pydantic `BaseSettings`.
- Cloud Run: injected as environment variables via `--set-secrets` flag referencing Secret Manager.
- The `config.py` module must work with both patterns. Use `BaseSettings` with `env_file` for local and plain env vars for Cloud Run. No conditional logic needed — `BaseSettings` reads env vars by default and `.env` as fallback.

### Webhook Delivery via Cloud Tasks

When a webhook needs to fire:

1. The service layer creates a Cloud Tasks task targeting `POST /internal/webhook-dispatch` on the same Cloud Run service.
2. The task payload contains: `event`, `job_id`, `target_url`, `payload`, `webhook_secret`.
3. The `/internal/webhook-dispatch` endpoint signs the payload with HMAC-SHA256 and delivers it to the agent's `endpoint_url`.
4. Cloud Tasks handles retry with exponential backoff (1s → 5s → 30s → 5min → 30min, 5 attempts).

The `/internal/webhook-dispatch` endpoint must:

- Not be exposed publicly. Use IAM or a shared internal secret to authenticate Cloud Tasks requests.
- Set a 10-second timeout on the outbound HTTP call.
- Return 2xx to Cloud Tasks on successful delivery (regardless of the remote agent's response status).
- Return 5xx to Cloud Tasks if delivery fails (triggers retry).

### Cloud Run Deployment Command

```bash
gcloud run deploy agent-registry-staging \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/agent-registry/api:latest \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --memory=512Mi \
  --cpu=1 \
  --vpc-connector=agent-registry-connector \
  --vpc-egress=private-ranges-only \
  --add-cloudsql-instances=${PROJECT_ID}:${REGION}:agent-registry-staging \
  --set-env-vars="ENV=staging" \
  --set-secrets="DB_PASSWORD=db-password-staging:latest,PLATFORM_SIGNING_KEY=platform-signing-key-staging:latest"
```

---

## 🤖 Local Development Environment (Agent Reference)

The agent should create and maintain these files.

### docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: agent_registry
      POSTGRES_USER: api_user
      POSTGRES_PASSWORD: localdev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U api_user -d agent_registry"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7.2-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  api:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - .:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

volumes:
  pgdata:
```

### .env.example

```bash
ENV=development
DATABASE_URL=postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry
REDIS_URL=redis://localhost:6379/0
PLATFORM_SIGNING_KEY=dev-signing-key-not-for-production
PLATFORM_FEE_PERCENT=0.025
```

### Dockerfile

```dockerfile
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY . .

RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### cloudbuild.yaml

```yaml
steps:
  - name: "python:3.11"
    entrypoint: "bash"
    args:
      - "-c"
      - |
        pip install .[dev]
        pytest tests/ -v --tb=short

  - name: "gcr.io/cloud-builders/docker"
    args:
      [
        "build",
        "-t",
        "${_REGION}-docker.pkg.dev/${PROJECT_ID}/agent-registry/api:${SHORT_SHA}",
        ".",
      ]

  - name: "gcr.io/cloud-builders/docker"
    args:
      [
        "push",
        "${_REGION}-docker.pkg.dev/${PROJECT_ID}/agent-registry/api:${SHORT_SHA}",
      ]

  - name: "gcr.io/google.com/cloudsdktool/cloud-sdk"
    entrypoint: "gcloud"
    args:
      - "run"
      - "deploy"
      - "agent-registry-staging"
      - "--image=${_REGION}-docker.pkg.dev/${PROJECT_ID}/agent-registry/api:${SHORT_SHA}"
      - "--region=${_REGION}"

substitutions:
  _REGION: us-west1

images:
  - "${_REGION}-docker.pkg.dev/${PROJECT_ID}/agent-registry/api:${SHORT_SHA}"
```

---

## 🤖 Configuration Module Pattern

The agent should follow this pattern for `app/config.py`:

```python
from pydantic_settings import BaseSettings
from decimal import Decimal


class Settings(BaseSettings):
    env: str = "development"
    database_url: str
    redis_url: str
    platform_signing_key: str
    platform_fee_percent: Decimal = Decimal("0.025")

    # Rate limiting defaults
    rate_limit_discovery_capacity: int = 60
    rate_limit_discovery_refill_per_min: int = 20
    rate_limit_read_capacity: int = 120
    rate_limit_read_refill_per_min: int = 60
    rate_limit_write_capacity: int = 30
    rate_limit_write_refill_per_min: int = 10

    # Auth
    signature_max_age_seconds: int = 30
    nonce_ttl_seconds: int = 60

    # Webhook
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 5

    # Test runner
    test_runner_timeout_per_test: int = 60
    test_runner_timeout_per_suite: int = 300
    test_runner_memory_limit_mb: int = 256

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
```

This works unchanged in both local dev (reads `.env`) and Cloud Run (reads injected env vars). No `if env == "production"` branching.
