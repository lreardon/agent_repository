# GKE Connect Gateway — Private Cluster Access

**Date:** 2026-03-01
**Status:** Active

## Background

### The Problem

Our GKE Autopilot cluster (`agent-registry-sandbox-staging`) runs verification scripts in isolated containers. As part of a security hardening pass, we locked down the Kubernetes API server's `master_authorized_networks` to only allow access from the VPC connector CIDR (`10.8.0.0/28`) — this is the range Cloud Run uses to submit sandbox jobs.

Previously, the master was open to `0.0.0.0/0` (any IP), relying solely on authentication. While GKE auth is strong, exposing the API server to the internet unnecessarily widens the attack surface for credential-stuffing, API exploits, or zero-days against the K8s API.

After removing `0.0.0.0/0`, two things broke:

1. **`kubectl` from a developer laptop** — can't reach the master IP directly
2. **`terraform apply`** — the Kubernetes provider tries to connect to the master during initialization, causing timeout errors

### Why Not Static IP Allowlists?

The obvious fix — add the developer's IP to `master_authorized_networks` — has a chicken-and-egg problem: when your IP changes (new wifi, coffee shop, tethering), you can't run `terraform apply` to update the allowlist because you're already locked out.

### The Solution: Connect Gateway

GKE Connect Gateway routes `kubectl` and Terraform traffic through Google's managed `connectgateway.googleapis.com` API instead of hitting the master IP directly. Your laptop talks to Google's API (which is always reachable), and Google proxies the request to the cluster through an internal channel.

**Benefits:**
- Works from any network (no IP restrictions)
- Authentication is IAM-based (same Google credentials you already use)
- Master API remains private (no public IP exposure)
- No VPN, bastion host, or Cloud Shell needed

## Architecture

```
Developer laptop
    │
    ▼
connectgateway.googleapis.com (Google-managed, public)
    │ (IAM auth: roles/gkehub.gatewayEditor)
    ▼
GKE Fleet Membership (registered cluster)
    │ (internal channel)
    ▼
GKE Master API (private, 10.8.0.0/28 only)
    │
    ▼
Sandbox namespace (NetworkPolicy: deny-all)
```

## Setup (What We Did)

### 1. Enable APIs

```bash
gcloud services enable gkehub.googleapis.com connectgateway.googleapis.com \
  --project=agent-registry-488317
```

These are also managed in Terraform (`main.tf` → `google_project_service.apis`).

### 2. Register Cluster as Fleet Member

```bash
gcloud container fleet memberships register agent-registry-sandbox-staging \
  --gke-uri=https://container.googleapis.com/v1/projects/agent-registry-488317/locations/us-west1/clusters/agent-registry-sandbox-staging \
  --enable-workload-identity \
  --project=agent-registry-488317
```

This is now managed in Terraform as `google_gke_hub_membership.sandbox`.

### 3. Grant Gateway Access

```bash
gcloud projects add-iam-policy-binding agent-registry-488317 \
  --member="user:leland6925@gmail.com" \
  --role="roles/gkehub.gatewayEditor"
```

This grants read/write access to cluster resources through the gateway. For read-only access, use `roles/gkehub.gatewayReader`.

### 4. Install Auth Plugin

```bash
gcloud components install gke-gcloud-auth-plugin
```

**Important:** The gcloud SDK `bin/` directory must be in `$PATH` for `kubectl` to find the plugin:

```bash
export PATH="/usr/local/share/google-cloud-sdk/bin:$PATH"
```

Add this to your shell profile (`.zshrc`, `.bashrc`, etc.) if not already present.

### 5. Get Credentials

```bash
gcloud container fleet memberships get-credentials agent-registry-sandbox-staging \
  --project=agent-registry-488317
```

This writes a kubeconfig entry that routes through Connect Gateway. You can now run:

```bash
kubectl get namespaces
kubectl get pods -n sandbox
```

### 6. Terraform Provider Configuration

The Kubernetes provider in `infra/main.tf` uses the Connect Gateway endpoint:

```hcl
provider "kubernetes" {
  host  = "https://${var.region}-connectgateway.googleapis.com/v1/projects/${data.google_project.current.number}/locations/${var.region}/gkeMemberships/${module.gke.cluster_name}"
  token = data.google_client_config.default.access_token
}
```

**Key details:**
- Uses the **regional** endpoint (`us-west1-connectgateway.googleapis.com`)
- Uses the **project number** (not project ID) — this is what the gateway expects
- No `cluster_ca_certificate` needed — Google handles TLS termination

## Day-to-Day Usage

### Access the cluster from any network

```bash
# One-time per session (or after credential expiry)
gcloud container fleet memberships get-credentials agent-registry-sandbox-staging \
  --project=agent-registry-488317

# Then use kubectl normally
kubectl get pods -n sandbox
kubectl logs -n sandbox <pod-name>
```

### Run Terraform

```bash
cd infra/
terraform plan -var-file=staging.tfvars
terraform apply -var-file=staging.tfvars
```

No special flags needed — the Kubernetes provider automatically routes through Connect Gateway.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `gke-gcloud-auth-plugin not found` | SDK bin not in PATH | `export PATH="/usr/local/share/google-cloud-sdk/bin:$PATH"` |
| `the server rejected our request for an unknown reason` | Wrong gateway URL format | Ensure using project **number** and **regional** endpoint |
| `PERMISSION_DENIED` on kubectl | Missing IAM role | Grant `roles/gkehub.gatewayEditor` to your account |
| Terraform timeout on K8s resources | Provider still using direct endpoint | Check `provider "kubernetes"` block uses connectgateway URL |

## Security Notes

- Connect Gateway access is controlled by IAM (`roles/gkehub.gatewayEditor`), not network position
- The GKE master remains private — only the VPC connector CIDR can reach it directly
- Cloud Run submits sandbox jobs via the VPC connector (direct path), not through Connect Gateway
- All gateway traffic is authenticated and encrypted by Google's infrastructure
- Audit logs for gateway access are in Cloud Logging under `connectgateway.googleapis.com`

## Terraform Resources

| Resource | Purpose |
|----------|---------|
| `google_project_service.apis["gkehub.googleapis.com"]` | Fleet/Hub API |
| `google_project_service.apis["connectgateway.googleapis.com"]` | Connect Gateway API |
| `google_gke_hub_membership.sandbox` | Fleet membership for the sandbox cluster |
