variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "master_ipv4_cidr_block" {
  description = "CIDR for GKE master nodes (must not overlap with other subnets)"
  type        = string
  default     = "172.16.0.0/28"
}

variable "network" {
  description = "VPC network name"
  type        = string
}

variable "network_id" {
  description = "VPC network self_link / id"
  type        = string
}

variable "master_authorized_cidr" {
  description = "CIDR block allowed to reach the K8s master (VPC connector range)"
  type        = string
  default     = "10.8.0.0/28"
}

# --------------------------------------------------------------------------
# GKE Autopilot cluster
# --------------------------------------------------------------------------
resource "google_container_cluster" "sandbox" {
  name     = "agent-registry-sandbox-${var.environment}"
  location = var.region
  project  = var.project_id

  # Autopilot mode
  enable_autopilot = true

  # Network
  network    = var.network_id
  subnetwork = "default"

  # Private cluster — pods/nodes not exposed to internet
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false  # Allow public kubectl access (restricted by master_authorized_networks)
    master_ipv4_cidr_block  = var.master_ipv4_cidr_block
  }

  # Restrict API server access
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.master_authorized_cidr
      display_name = "VPC Connector (Cloud Run)"
    }
    # Removed 0.0.0.0/0 — kubectl access restricted to VPC connector range only
  }

  # Release channel
  release_channel {
    channel = "REGULAR"
  }

  # Deletion protection — disable for staging, enable for production
  deletion_protection = false # temporarily disabled for cluster replacement

  # Timeouts
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
}

# --------------------------------------------------------------------------
# Service account for the sandbox runner (used by Cloud Run to submit Jobs)
# --------------------------------------------------------------------------
resource "google_service_account" "sandbox_runner" {
  account_id   = "sandbox-runner-${var.environment}"
  display_name = "Sandbox Runner (${var.environment})"
  project      = var.project_id
}

# Grant the SA permission to manage workloads in the cluster
resource "google_project_iam_member" "sandbox_runner_container_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.sandbox_runner.email}"
}

variable "cloud_run_service_account" {
  description = "Email of the dedicated Cloud Run service account (for sandbox impersonation)"
  type        = string
}

# Allow the dedicated Cloud Run SA to impersonate the sandbox runner
resource "google_service_account_iam_member" "api_impersonate_sandbox" {
  service_account_id = google_service_account.sandbox_runner.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${var.cloud_run_service_account}"
}

# --------------------------------------------------------------------------
# Kubernetes resources (namespace + NetworkPolicy)
# Requires the kubernetes provider to be configured by the caller.
# --------------------------------------------------------------------------

# TEMPORARILY COMMENTED OUT — need fleet membership before k8s provider works
# Uncomment after fleet membership is created and provider host is restored.

# resource "kubernetes_namespace_v1" "sandbox" {
#   metadata {
#     name = "sandbox"
#     labels = {
#       "app.kubernetes.io/managed-by" = "terraform"
#       "purpose"                      = "verification-sandbox"
#     }
#   }
#   depends_on = [google_container_cluster.sandbox]
# }

# resource "kubernetes_network_policy_v1" "deny_all" {
#   metadata {
#     name      = "deny-all"
#     namespace = kubernetes_namespace_v1.sandbox.metadata[0].name
#   }
#   spec {
#     pod_selector {}
#     policy_types = ["Ingress", "Egress"]
#   }
#   depends_on = [kubernetes_namespace_v1.sandbox]
# }

# resource "kubernetes_resource_quota_v1" "sandbox_quota" {
#   metadata {
#     name      = "sandbox-quota"
#     namespace = kubernetes_namespace_v1.sandbox.metadata[0].name
#   }
#   spec {
#     hard = {
#       "requests.cpu"    = "4"
#       "requests.memory" = "4Gi"
#       "pods"            = "20"
#     }
#   }
#   depends_on = [kubernetes_namespace_v1.sandbox]
# }

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "cluster_name" {
  value = google_container_cluster.sandbox.name
}

output "cluster_endpoint" {
  value = google_container_cluster.sandbox.endpoint
}

output "cluster_ca_certificate" {
  value     = try(google_container_cluster.sandbox.master_auth[0].cluster_ca_certificate, "")
  sensitive = true
}

output "sandbox_service_account_email" {
  value = google_service_account.sandbox_runner.email
}
