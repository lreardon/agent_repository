variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "tier" {
  type = string
}

variable "database_version" {
  type = string
}

variable "private_network_id" {
  type = string
}

# --------------------------------------------------------------------------
# Cloud SQL instance
# --------------------------------------------------------------------------
resource "google_sql_database_instance" "main" {
  name             = "agent-registry-${var.environment}"
  database_version = var.database_version
  region           = var.region

  # Force Enterprise edition so shared-core tiers (db-f1-micro) work with PG16
  settings {
    tier              = var.tier
    edition           = "ENTERPRISE"
    availability_type = var.environment == "production" ? "REGIONAL" : "ZONAL"

    disk_autoresize = true
    disk_size       = 10
    disk_type       = "PD_SSD"

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = var.environment == "production"
      transaction_log_retention_days = var.environment == "production" ? 7 : 1
    }

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.private_network_id
      enable_private_path_for_google_cloud_services = true
    }

    database_flags {
      name  = "log_min_duration_statement"
      value = "1000" # Log queries slower than 1s
    }
  }

  deletion_protection = var.environment == "production"
}

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
resource "google_sql_database" "app" {
  name     = "agent_registry"
  instance = google_sql_database_instance.main.name
}

# --------------------------------------------------------------------------
# User with generated password
# --------------------------------------------------------------------------
resource "random_password" "db_password" {
  length  = 32
  special = false # Avoid URL-encoding issues in connection strings
}

resource "google_sql_user" "api_user" {
  name     = "api_user"
  instance = google_sql_database_instance.main.name
  password = random_password.db_password.result
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "private_ip" {
  value = google_sql_database_instance.main.private_ip_address
}

output "database_name" {
  value = google_sql_database.app.name
}

output "user_name" {
  value = google_sql_user.api_user.name
}

output "user_password" {
  value     = random_password.db_password.result
  sensitive = true
}
