terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
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

# Kubernetes provider — configured after GKE cluster exists.
# On first apply (before GKE), this will use dummy values;
# run `terraform apply` again after the cluster is created.
data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${try(module.gke.cluster_endpoint, "127.0.0.1")}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = try(base64decode(module.gke.cluster_ca_certificate), "")
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
    "firebasehosting.googleapis.com",
    "firebase.googleapis.com",
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

  project_id  = var.project_id
  environment = var.environment
  db_password = module.database.user_password
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

  project_id             = var.project_id
  region                 = var.region
  environment            = var.environment
  network                = "default"
  network_id             = module.networking.vpc_id
  master_authorized_cidr = "10.8.0.0/28"
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
  vpc_connector_id      = module.networking.vpc_connector_id
  cloud_sql_connection  = module.database.connection_name
  db_password_secret_id = module.secrets.db_password_secret_id
  signing_key_secret_id = module.secrets.signing_key_secret_id
  redis_host            = module.redis.host
  redis_port            = module.redis.port
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
# TODO: Re-enable once firebase provider is available.
#       The hashicorp/firebase provider doesn't exist yet;
#       firebase_hosting_site requires the google-beta provider.
# --------------------------------------------------------------------------
# module "firebase_hosting" {
#   source = "./modules/firebase-hosting"
#
#   project_id  = var.project_id
#   environment = var.environment
#
#   depends_on = [google_project_service.apis]
# }
