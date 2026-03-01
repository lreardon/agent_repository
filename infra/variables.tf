variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-west1"
}

variable "environment" {
  description = "Environment name (staging or production)"
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Environment must be 'staging' or 'production'."
  }
}

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_version" {
  description = "PostgreSQL version"
  type        = string
  default     = "POSTGRES_16"
}

# --------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------
variable "redis_memory_size_gb" {
  description = "Redis memory size in GB"
  type        = number
  default     = 1
}

# --------------------------------------------------------------------------
# Cloud Run
# --------------------------------------------------------------------------
variable "cloud_run_image" {
  description = "Container image for the API. Set to a placeholder for initial apply."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "cloud_run_min_instances" {
  description = "Minimum Cloud Run instances"
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Maximum Cloud Run instances"
  type        = number
  default     = 2
}

variable "base_url" {
  description = "Public base URL for the API (used for email verification links, webhooks, etc.)"
  type        = string
}
