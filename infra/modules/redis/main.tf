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
  tier               = "BASIC"
  redis_version      = "REDIS_7_2"
  authorized_network = var.authorized_network

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
