variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "memory_size_gb" {
  type = number
}

variable "authorized_network" {
  type = string
}

# --------------------------------------------------------------------------
# Memorystore Redis instance
# --------------------------------------------------------------------------
resource "google_redis_instance" "main" {
  name               = "agent-registry-${var.environment}"
  region             = var.region
  memory_size_gb     = var.memory_size_gb
  tier               = "STANDARD_HA"
  redis_version      = "REDIS_7_2"
  authorized_network = var.authorized_network
  auth_enabled       = true

  display_name = "Agent Registry Redis (${var.environment})"
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "host" {
  value = google_redis_instance.main.host
}

output "port" {
  value = google_redis_instance.main.port
}

output "auth_string" {
  value     = google_redis_instance.main.auth_string
  sensitive = true
}
