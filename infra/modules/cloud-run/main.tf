variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "image" {
  type = string
}

variable "vpc_connector_id" {
  type = string
}

variable "cloud_sql_connection" {
  type = string
}

variable "db_password_secret_id" {
  type = string
}

variable "signing_key_secret_id" {
  type = string
}

variable "redis_host" {
  type = string
}

variable "redis_port" {
  type = number
}

variable "redis_auth_string" {
  type      = string
  sensitive = true
}

variable "service_account_email" {
  description = "Dedicated service account email for the Cloud Run service"
  type        = string
}

variable "base_url" {
  description = "Public base URL for the API"
  type        = string
}

variable "min_instances" {
  type = number
}

variable "max_instances" {
  type = number
}

variable "sandbox_gke_cluster" {
  description = "GKE cluster name for sandbox verification"
  type        = string
  default     = ""
}

variable "sandbox_gke_location" {
  description = "GKE cluster location (region)"
  type        = string
  default     = ""
}

variable "sandbox_service_account" {
  description = "Service account email for sandbox runner (impersonation)"
  type        = string
  default     = ""
}

# --------------------------------------------------------------------------
# Cloud Run service
# --------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name     = "agent-registry-${var.environment}"
  location = var.region

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      connector = var.vpc_connector_id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      # --- Environment variables ---
      env {
        name  = "ENV"
        value = var.environment
      }

      env {
        name  = "REDIS_URL"
        value = "redis://:${var.redis_auth_string}@${var.redis_host}:${var.redis_port}/0"
      }

      env {
        name  = "BASE_URL"
        value = var.base_url
      }

      # Components for DATABASE_URL (assembled by docker-entrypoint.sh)
      env {
        name  = "CLOUD_SQL_CONNECTION"
        value = var.cloud_sql_connection
      }

      env {
        name  = "DB_NAME"
        value = "agent_registry"
      }

      env {
        name  = "DB_USER"
        value = "api_user"
      }

      env {
        name  = "RUN_MIGRATIONS"
        value = "true"
      }

      env {
        name  = "SECRETS_BACKEND"
        value = "gcp_secrets"
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "SANDBOX_GKE_CLUSTER"
        value = var.sandbox_gke_cluster
      }

      env {
        name  = "SANDBOX_GKE_LOCATION"
        value = var.sandbox_gke_location
      }

      env {
        name  = "SANDBOX_NAMESPACE"
        value = "sandbox"
      }

      env {
        name  = "SANDBOX_SERVICE_ACCOUNT"
        value = var.sandbox_service_account
      }

      # --- Secrets mounted as env vars ---
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.db_password_secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "PLATFORM_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = var.signing_key_secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 3
      }

      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds = 30
      }
    }

    # Cloud SQL proxy sidecar
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection]
      }
    }
  }

  # Allow unauthenticated access (public API)
  ingress = "INGRESS_TRAFFIC_ALL"
}

# --------------------------------------------------------------------------
# IAM — allow public access
# --------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "url" {
  value = google_cloud_run_v2_service.api.uri
}

output "service_name" {
  value = google_cloud_run_v2_service.api.name
}
