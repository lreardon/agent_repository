variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "environment" {
  type = string
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
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  # Restrict API server access
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.master_authorized_cidr
      display_name = "VPC Connector (Cloud Run)"
    }
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "Allow kubectl (auth still required)"
    }
  }

  # Release channel
  release_channel {
    channel = "REGULAR"
  }

  # Deletion protection — disable for staging, enable for production
  deletion_protection = var.environment == "production" ? true : false

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

# Allow the default Compute Engine SA (used by Cloud Run) to impersonate the sandbox runner
data "google_project" "current" {
  project_id = var.project_id
}

locals {
  compute_sa = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_service_account_iam_member" "compute_impersonate_sandbox" {
  service_account_id = google_service_account.sandbox_runner.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${local.compute_sa}"
}

# --------------------------------------------------------------------------
# Kubernetes resources (namespace + NetworkPolicy)
# Requires the kubernetes provider to be configured by the caller.
# --------------------------------------------------------------------------

# Sandbox namespace
resource "kubernetes_namespace_v1" "sandbox" {
  metadata {
    name = "sandbox"
    labels = {
      "app.kubernetes.io/managed-by" = "terraform"
      "purpose"                      = "verification-sandbox"
    }
  }

  depends_on = [google_container_cluster.sandbox]
}

# Default-deny NetworkPolicy — block ALL ingress and egress in the sandbox namespace
resource "kubernetes_network_policy_v1" "deny_all" {
  metadata {
    name      = "deny-all"
    namespace = kubernetes_namespace_v1.sandbox.metadata[0].name
  }

  spec {
    # Select all pods in the namespace
    pod_selector {}

    # Deny all traffic directions
    policy_types = ["Ingress", "Egress"]

    # No ingress rules = deny all ingress
    # No egress rules = deny all egress
  }

  depends_on = [kubernetes_namespace_v1.sandbox]
}

# --------------------------------------------------------------------------
# Resource quota for the sandbox namespace
# --------------------------------------------------------------------------
resource "kubernetes_resource_quota_v1" "sandbox_quota" {
  metadata {
    name      = "sandbox-quota"
    namespace = kubernetes_namespace_v1.sandbox.metadata[0].name
  }

  spec {
    hard = {
      "requests.cpu"    = "4"
      "requests.memory" = "4Gi"
      "pods"            = "20"
    }
  }

  depends_on = [kubernetes_namespace_v1.sandbox]
}

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
  value     = google_container_cluster.sandbox.master_auth[0].cluster_ca_certificate
  sensitive = true
}

output "sandbox_service_account_email" {
  value = google_service_account.sandbox_runner.email
}
