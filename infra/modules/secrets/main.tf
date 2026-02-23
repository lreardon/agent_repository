variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

# --------------------------------------------------------------------------
# Database password
# --------------------------------------------------------------------------
resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password-${var.environment}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

# --------------------------------------------------------------------------
# Platform webhook signing key
# --------------------------------------------------------------------------
resource "random_password" "signing_key" {
  length  = 44 # ~32 bytes base64
  special = false
}

resource "google_secret_manager_secret" "signing_key" {
  secret_id = "platform-signing-key-${var.environment}"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "signing_key" {
  secret      = google_secret_manager_secret.signing_key.id
  secret_data = random_password.signing_key.result
}

# --------------------------------------------------------------------------
# IAM — grant Cloud Run service account access to secrets
# --------------------------------------------------------------------------
data "google_project" "current" {
  project_id = var.project_id
}

locals {
  compute_sa = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "db_password_access" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_sa}"
}

resource "google_secret_manager_secret_iam_member" "signing_key_access" {
  secret_id = google_secret_manager_secret.signing_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.compute_sa}"
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "db_password_secret_id" {
  value = google_secret_manager_secret.db_password.secret_id
}

output "signing_key_secret_id" {
  value = google_secret_manager_secret.signing_key.secret_id
}
