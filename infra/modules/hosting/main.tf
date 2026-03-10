# Hosted Agent Infrastructure
#
# Reuses the existing GKE Autopilot cluster but creates a separate
# namespace with its own NetworkPolicy for agent isolation.

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-west1"
}

variable "environment" {
  type = string
}

variable "gke_cluster_name" {
  description = "Name of the existing GKE Autopilot cluster"
  type        = string
}

variable "gke_cluster_location" {
  description = "Location of the existing GKE Autopilot cluster"
  type        = string
}

variable "api_service_account_email" {
  description = "Cloud Run API service account email (needs GKE access)"
  type        = string
}

# ---------------------------------------------------------------------------
# Artifact Registry — hosted agent images
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "hosted_agents" {
  repository_id = "hosted-agents-${var.environment}"
  location      = var.region
  format        = "DOCKER"
  description   = "Container images for Arcoa hosted agents"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

# ---------------------------------------------------------------------------
# GKE Namespace + NetworkPolicy (native Terraform kubernetes resources)
# ---------------------------------------------------------------------------

resource "kubernetes_namespace" "hosted_agents" {
  metadata {
    name = "hosted-agents"
    labels = {
      app     = "arcoa"
      purpose = "hosted-agents"
    }
  }
}

resource "kubernetes_network_policy" "hosted_agent_isolation" {
  metadata {
    name      = "hosted-agent-isolation"
    namespace = kubernetes_namespace.hosted_agents.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        "arcoa-role" = "hosted-agent"
      }
    }

    policy_types = ["Ingress", "Egress"]

    # No ingress block = deny all inbound (Ingress is in policy_types)

    # Allow DNS
    egress {
      to {
        namespace_selector {}
        pod_selector {
          match_labels = {
            "k8s-app" = "kube-dns"
          }
        }
      }
      ports {
        protocol = "UDP"
        port     = "53"
      }
      ports {
        protocol = "TCP"
        port     = "53"
      }
    }

    # Allow outbound HTTPS/HTTP to public internet only
    egress {
      to {
        ip_block {
          cidr = "0.0.0.0/0"
          except = [
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
          ]
        }
      }
      ports {
        protocol = "TCP"
        port     = "443"
      }
      ports {
        protocol = "TCP"
        port     = "80"
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Service Account for hosted agents (minimal permissions)
# ---------------------------------------------------------------------------

resource "google_service_account" "hosted_agent_runner" {
  account_id   = "hosted-agent-runner-${var.environment}"
  display_name = "Hosted Agent Runner (${var.environment})"
  project      = var.project_id
}

# Allow the API service account to create deployments in the hosted-agents namespace
resource "google_project_iam_member" "api_gke_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${var.api_service_account_email}"
}

# Allow the API to push images to the hosted agents registry (project-level)
resource "google_project_iam_member" "api_ar_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${var.api_service_account_email}"
}

# Allow GKE runner SA to pull images (project-level)
resource "google_project_iam_member" "runner_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.hosted_agent_runner.email}"
}

# ---------------------------------------------------------------------------
# Cloud Build — for building agent images in CI
# ---------------------------------------------------------------------------

resource "google_project_iam_member" "api_cloud_build" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${var.api_service_account_email}"
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "hosted_agents_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.hosted_agents.repository_id}"
}

output "hosted_agent_runner_sa" {
  value = google_service_account.hosted_agent_runner.email
}
