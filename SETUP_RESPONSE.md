# Setup Needed — Staging Deployment

Things Clob can't do for you. Complete these before the first deploy.

---

## 1. GCP Project

- GCP project exists with id `agent-registry-488317`.
- Billing is enabled on the project.

## 2. Local Auth

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

## 3. Update Terraform Config

Edited `infra/staging.tfvars` and set
```hcl
project_id
```

Edited `infra/production.tfvars` and set:
```hcl
project_id
```

## 4. First Terraform Apply

I have run:
```bash
cd infra
terraform init
terraform plan -var-file=staging.tfvars
terraform apply -var-file=staging.tfvars
```

## 5. Migrate Terraform State to GCS (recommended)

After first successful apply:

```bash
gsutil mb -l us-west1 gs://YOUR_PROJECT_ID-tf-state
gsutil versioning set on gs://YOUR_PROJECT_ID-tf-state
```

Then uncomment the `backend "gcs"` block in `infra/main.tf`, update the bucket name, and run:
```bash
terraform init    # Will prompt to migrate state
```

## 6. Set Secret Values in Secret Manager

Terraform creates the secret *shells* and the auto-generated DB password + signing key. You still need to create these additional secrets manually:

```bash
PROJECT_ID=your-project-id

# HD Wallet seed (BIP-39 mnemonic for per-agent deposit addresses)
echo -n "your mnemonic phrase here" | \
  gcloud secrets create hd_wallet_master_seed --data-file=- --project=$PROJECT_ID

# Treasury wallet private key (hex, no 0x prefix)
echo -n "your_private_key_hex" | \
  gcloud secrets create treasury_wallet_private_key --data-file=- --project=$PROJECT_ID

# MoltBook API key (if using MoltBook identity)
echo -n "moltdev_..." | \
  gcloud secrets create moltbook_api_key --data-file=- --project=$PROJECT_ID
```

Grant the Cloud Run service account access:
```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SECRET in hd_wallet_master_seed treasury_wallet_private_key moltbook_api_key; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:$SA" \
    --role="roles/secretmanager.secretAccessor" \
    --project=$PROJECT_ID
done
```

## 7. Configure GitHub Actions

After `terraform apply`, grab the outputs:

```bash
terraform output wif_provider
terraform output ci_service_account_email
```

Then set these as **GitHub repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable                  | Value                                                              |
|---------------------------|--------------------------------------------------------------------|
| `GCP_PROJECT_ID`          | Your GCP project ID                                                |
| `WIF_PROVIDER`            | Output of `terraform output wif_provider`                          |
| `CI_SERVICE_ACCOUNT_EMAIL`| Output of `terraform output ci_service_account_email`              |

No secrets needed — Workload Identity Federation handles auth via OIDC tokens.

## 8. First Deploy

Push to `main` and GitHub Actions will:
1. Run tests
2. Build + push Docker image to Artifact Registry
3. Deploy to Cloud Run staging
4. Run Alembic migrations at container startup

Or trigger manually: Actions → Deploy to Staging → Run workflow.

## 9. Verify

```bash
# Get the staging URL
gcloud run services describe agent-registry-staging --region=us-west1 --format='value(status.url)'

# Health check
curl https://YOUR_STAGING_URL/health
```

---

## Architecture

```
GitHub (push to main)
  → GitHub Actions (test → build → deploy)
    → Artifact Registry (Docker image)
    → Cloud Run (staging)
      ├── Cloud SQL Proxy sidecar → PostgreSQL
      ├── VPC Connector → Memorystore Redis
      └── Secret Manager → credentials
```

All infrastructure managed by Terraform in `infra/`.
