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

variable "cloud_run_service_account" {
  description = "Email of the dedicated Cloud Run service account"
  type        = string
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
# IAM — grant dedicated Cloud Run service account access to secrets
# --------------------------------------------------------------------------
resource "google_secret_manager_secret_iam_member" "db_password_access" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.cloud_run_service_account}"
}

resource "google_secret_manager_secret_iam_member" "signing_key_access" {
  secret_id = google_secret_manager_secret.signing_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.cloud_run_service_account}"
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
