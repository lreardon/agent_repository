# Staging Infrastructure Postmortem — 2026-02-28

## Summary

Adding Firebase Hosting to the existing Terraform-managed staging infrastructure required ~1 hour of debugging across three categories of issues: state drift, provider dependencies, and Firebase project initialization.

## Issues Encountered

### 1. Terraform State Drift (Database, GKE, Redis)

**Symptom:** `terraform apply` failed with `409 Already Exists` errors for Cloud SQL, GKE, and Redis — resources Terraform tried to create that already existed in GCP.

**Root Cause:** These resources were originally provisioned by Terraform but had fallen out of state (likely from a state file reset or partial backend migration). Terraform had no record of them, so it treated them as new resources to create.

**Resolution:** Imported each resource into state manually:
```bash
terraform import -var-file=staging.tfvars module.database.google_sql_database_instance.main agent-registry-staging
terraform import -var-file=staging.tfvars module.gke.google_container_cluster.sandbox projects/agent-registry-488317/locations/us-west1/clusters/agent-registry-sandbox-staging
terraform import -var-file=staging.tfvars module.redis.google_redis_instance.main projects/agent-registry-488317/locations/us-west1/instances/agent-registry-staging
terraform import -var-file=staging.tfvars module.database.google_sql_database.app agent-registry-488317/agent-registry-staging/agent_registry
```

**Note on import ID formats:** Each resource type has its own import ID syntax. Cloud SQL instances use just the instance name (`agent-registry-staging`), not `project:instance`. GKE and Redis use full resource paths. Always check the Terraform provider docs for the correct format.

### 2. Kubernetes Provider Chicken-and-Egg

**Symptom:** `terraform import` commands for *any* resource failed with: `The configuration for provider["kubernetes"] depends on values that cannot be determined until apply.`

**Root Cause:** The Kubernetes provider is configured using outputs from the GKE module (`module.gke.cluster_endpoint`, `module.gke.cluster_ca_certificate`). When GKE isn't in state, these values are unknown, and Terraform refuses to configure the provider — even for operations that don't involve Kubernetes resources.

**Resolution:** Temporarily hardcoded the Kubernetes provider to dummy values during imports:
```hcl
provider "kubernetes" {
  host  = "https://127.0.0.1"
  token = "dummy"
}
```
Reverted to the dynamic configuration after all imports completed.

### 3. Firebase Project Initialization

**Symptom:** `google_firebase_hosting_site` creation failed with `404: Requested entity was not found`.

**Root Cause:** Creating a Firebase Hosting site requires the GCP project to be initialized as a Firebase project first. Simply enabling `firebasehosting.googleapis.com` is not enough — `firebase.googleapis.com` must also be enabled and the project must be registered with Firebase via `firebase projects:addfirebase`.

**Resolution:**
```bash
gcloud services enable firebase.googleapis.com --project=agent-registry-488317
firebase projects:addfirebase agent-registry-488317
```

**Additional gotcha:** We initially removed `firebase.googleapis.com` from the Terraform API enables (thinking only `firebasehosting.googleapis.com` was needed), and Terraform *destroyed* it. This broke Firebase Hosting. Both APIs are required.

### 4. Firebase CLI Reading `.firebaserc` Literals

**Symptom:** `firebase projects:addfirebase agent-registry-488317` failed with `Invalid project id: $PROJECT_ID`.

**Root Cause:** `.firebaserc` contains `$PROJECT_ID` as a placeholder (designed to be `sed`-substituted in CI). The Firebase CLI reads this file from the working directory and tried to use the literal string.

**Resolution:** Pass `--project` explicitly when running Firebase CLI locally, or run from a directory without `.firebaserc`.

## Production Deployment Plan

To avoid these issues when deploying to production:

### Pre-requisites (one-time, before `terraform apply`)

1. **Initialize Firebase on the production project:**
   ```bash
   gcloud services enable firebase.googleapis.com --project=<PROD_PROJECT_ID>
   firebase projects:addfirebase --project <PROD_PROJECT_ID>
   ```

2. **Create `production.tfvars`** (if not already complete) with all required variables.

3. **Verify state is clean:** Run `terraform plan -var-file=production.tfvars` and confirm no unexpected creates for resources that already exist. If any show as `+ create` but already exist in GCP, import them *before* applying.

### Deployment

```bash
cd infra
terraform plan -var-file=production.tfvars    # Review — should show only the firebase hosting site as new
terraform apply -var-file=production.tfvars
```

### CI/CD

Ensure the production deployment workflow derives the correct site ID:
```bash
SITE_ID="${PROJECT_ID}-production"
```

### If State Drift Occurs

1. Temporarily hardcode the Kubernetes provider (if GKE is missing from state)
2. Import resources using correct ID formats (check provider docs)
3. Revert the Kubernetes provider
4. Run `terraform plan` to verify no destructive changes before applying
