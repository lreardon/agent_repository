terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
  }

  # After first apply, uncomment and run `terraform init` to migrate state to GCS:
  backend "gcs" {
    bucket = "agent-registry-tf-state"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# Kubernetes provider — uses Connect Gateway to reach the private GKE cluster.
# This avoids needing direct IP access to the master (works from any network).
# Requires: gkehub.googleapis.com + connectgateway.googleapis.com APIs enabled,
# cluster registered as a Fleet member, and roles/gkehub.gatewayEditor on the caller.
data "google_client_config" "default" {}

data "google_project" "current" {
  project_id = var.project_id
}

provider "kubernetes" {
  host  = "https://${var.region}-connectgateway.googleapis.com/v1/projects/${data.google_project.current.number}/locations/${var.region}/gkeMemberships/${try(module.gke.cluster_name, "placeholder")}"
  token = data.google_client_config.default.access_token
}

# --------------------------------------------------------------------------
# Enable required APIs
# --------------------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "vpcaccess.googleapis.com",
    "cloudtasks.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "gkehub.googleapis.com",
    "connectgateway.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------
module "networking" {
  source = "./modules/networking"

  project_id = var.project_id
  region     = var.region

  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------
# Cloud SQL (PostgreSQL)
# --------------------------------------------------------------------------
module "database" {
  source = "./modules/database"

  project_id         = var.project_id
  region             = var.region
  environment        = var.environment
  tier               = var.db_tier
  database_version   = var.db_version
  private_network_id = module.networking.vpc_id

  depends_on = [module.networking]
}

# --------------------------------------------------------------------------
# Memorystore (Redis)
# --------------------------------------------------------------------------
module "redis" {
  source = "./modules/redis"

  project_id       = var.project_id
  region           = var.region
  environment      = var.environment
  memory_size_gb   = var.redis_memory_size_gb
  authorized_network = module.networking.vpc_id

  depends_on = [module.networking]
}

# --------------------------------------------------------------------------
# Secret Manager
# --------------------------------------------------------------------------
module "secrets" {
  source = "./modules/secrets"

  project_id                = var.project_id
  environment               = var.environment
  db_password               = module.database.user_password
  cloud_run_service_account = google_service_account.cloud_run_api.email
}

# --------------------------------------------------------------------------
# Dedicated Cloud Run service account (least-privilege)
# --------------------------------------------------------------------------
resource "google_service_account" "cloud_run_api" {
  account_id   = "agent-registry-api-${var.environment}"
  display_name = "Agent Registry API (${var.environment})"
  description  = "Dedicated service account for the Cloud Run API service"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# Cloud SQL client — required for Cloud SQL Auth Proxy sidecar
resource "google_project_iam_member" "api_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_run_api.email}"
}

# --------------------------------------------------------------------------
# Artifact Registry
# --------------------------------------------------------------------------
resource "google_artifact_registry_repository" "api" {
  location      = var.region
  repository_id = "agent-registry"
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------
# Cloud Tasks queue
# --------------------------------------------------------------------------
resource "google_cloud_tasks_queue" "webhook_delivery" {
  name     = "webhook-delivery"
  location = var.region

  rate_limits {
    max_dispatches_per_second = 10
  }

  retry_config {
    max_attempts       = 5
    min_backoff        = "1s"
    max_backoff        = "1800s"
    max_retry_duration = "0s"
  }

  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------
# CI/CD — Workload Identity Federation for GitHub Actions
# --------------------------------------------------------------------------
module "ci_cd" {
  source = "./modules/ci-cd"

  project_id  = var.project_id
  github_repo = "lreardon/agent_repository"

  depends_on = [google_project_service.apis]
}

# --------------------------------------------------------------------------
# GKE Autopilot (sandbox for verification scripts)
# --------------------------------------------------------------------------
module "gke" {
  source = "./modules/gke"

  project_id                = var.project_id
  region                    = var.region
  environment               = var.environment
  network                   = "default"
  network_id                = module.networking.vpc_id
  master_authorized_cidr    = "10.8.0.0/28"
  cloud_run_service_account = google_service_account.cloud_run_api.email
}

# --------------------------------------------------------------------------
# Fleet membership (Connect Gateway access to private GKE cluster)
# --------------------------------------------------------------------------
resource "google_gke_hub_membership" "sandbox" {
  membership_id = module.gke.cluster_name
  location      = var.region

  endpoint {
    gke_cluster {
      resource_link = "//container.googleapis.com/projects/${var.project_id}/locations/${var.region}/clusters/${module.gke.cluster_name}"
    }
  }

  authority {
    issuer = "https://container.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/clusters/${module.gke.cluster_name}"
  }

  depends_on = [module.gke, google_project_service.apis]
}

# --------------------------------------------------------------------------
# Cloud Run
# --------------------------------------------------------------------------
module "cloud_run" {
  source = "./modules/cloud-run"

  project_id            = var.project_id
  region                = var.region
  environment           = var.environment
  image                 = var.cloud_run_image
  service_account_email = google_service_account.cloud_run_api.email
  vpc_connector_id      = module.networking.vpc_connector_id
  cloud_sql_connection  = module.database.connection_name
  db_password_secret_id = module.secrets.db_password_secret_id
  signing_key_secret_id = module.secrets.signing_key_secret_id
  redis_host            = module.redis.host
  redis_port            = module.redis.port
  redis_auth_string     = module.redis.auth_string
  base_url              = var.base_url
  min_instances            = var.cloud_run_min_instances
  max_instances            = var.cloud_run_max_instances
  sandbox_gke_cluster      = module.gke.cluster_name
  sandbox_gke_location     = var.region
  sandbox_service_account  = module.gke.sandbox_service_account_email

  depends_on = [
    module.database,
    module.redis,
    module.secrets,
    module.gke,
    google_artifact_registry_repository.api,
  ]
}

# --------------------------------------------------------------------------
# Firebase Hosting (Documentation Site)
# --------------------------------------------------------------------------
resource "google_firebase_hosting_site" "docs" {
  provider = google-beta
  project  = var.project_id
  site_id  = "${var.project_id}-${var.environment}"

  depends_on = [google_project_service.apis]
}

