variable "project_id" {
  type = string
}

variable "github_repo" {
  description = "GitHub repository in owner/repo format"
  type        = string
}

# --------------------------------------------------------------------------
# Workload Identity Pool for GitHub Actions OIDC
# --------------------------------------------------------------------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC identity pool for GitHub Actions CI/CD"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# --------------------------------------------------------------------------
# CI/CD Service Account
# --------------------------------------------------------------------------
resource "google_service_account" "github_actions" {
  account_id   = "github-actions-ci"
  display_name = "GitHub Actions CI/CD"
  description  = "Service account used by GitHub Actions for deploy workflows"
}

# Roles for the CI/CD service account
locals {
  ci_roles = [
    "roles/run.developer",
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.editor",
    "roles/iam.serviceAccountUser",
    "roles/firebasehosting.admin",
  ]
}

resource "google_project_iam_member" "ci_roles" {
  for_each = toset(local.ci_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

# --------------------------------------------------------------------------
# IAM — allow GitHub Actions to impersonate the service account
# --------------------------------------------------------------------------
resource "google_service_account_iam_member" "github_actions_wif" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "workload_identity_provider" {
  description = "Full resource name of the Workload Identity Provider (for GitHub Actions auth)"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "service_account_email" {
  description = "Email of the CI/CD service account"
  value       = google_service_account.github_actions.email
}
