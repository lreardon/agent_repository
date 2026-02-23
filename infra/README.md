# Infrastructure — Terraform

All GCP infrastructure for the Agent Registry is defined here.

## Structure

```
infra/
├── main.tf              # Root module: APIs, module calls, Artifact Registry, Cloud Tasks
├── variables.tf         # Input variables
├── outputs.tf           # Output values
├── staging.tfvars       # Staging environment config
├── production.tfvars    # Production environment config
├── modules/
│   ├── networking/      # Default VPC, private service access, VPC connector
│   ├── database/        # Cloud SQL PostgreSQL instance, database, user
│   ├── redis/           # Memorystore Redis instance
│   ├── secrets/         # Secret Manager secrets + IAM bindings
│   └── cloud-run/       # Cloud Run service with all connections wired
```

## Prerequisites

1. `gcloud` CLI authenticated: `gcloud auth application-default login`
2. Terraform >= 1.5 installed
3. GCP project created with billing enabled

## Usage

### First-time setup

```bash
cd infra
terraform init
```

### Deploy staging

```bash
terraform plan -var-file=staging.tfvars
terraform apply -var-file=staging.tfvars
```

### Deploy production

```bash
terraform plan -var-file=production.tfvars
terraform apply -var-file=production.tfvars
```

### Update Cloud Run image after a new build

```bash
terraform apply -var-file=staging.tfvars \
  -var="cloud_run_image=us-west1-docker.pkg.dev/agent-registry/agent-registry/api:abc123"
```

## Remote state (recommended after first apply)

1. Create a GCS bucket for state:
   ```bash
   gsutil mb -l us-west1 gs://agent-registry-tf-state
   gsutil versioning set on gs://agent-registry-tf-state
   ```

2. Uncomment the `backend "gcs"` block in `main.tf`.

3. Run `terraform init` to migrate state.

## Notes

- The database module generates a random password and stores it in Secret Manager automatically.
- The `cloud_run_image` defaults to a Google hello-world placeholder. Update it after your first `docker build` and push.
- Staging has `deletion_protection = false` on Cloud SQL. Production has it enabled.
- The Cloud Run service expects a `/health` endpoint for probes.
