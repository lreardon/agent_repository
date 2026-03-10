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
# GKE Namespace + NetworkPolicy (applied via kubectl)
# ---------------------------------------------------------------------------

# We use null_resource + kubectl because Terraform's kubernetes provider
# requires cluster credentials at plan time, which complicates CI.

resource "null_resource" "hosted_agents_namespace" {
  triggers = {
    namespace = "hosted-agents"
  }

  provisioner "local-exec" {
    command = <<-EOT
      gcloud container clusters get-credentials ${var.gke_cluster_name} \
        --region ${var.gke_cluster_location} \
        --project ${var.project_id}

      kubectl apply -f - <<EOF
      apiVersion: v1
      kind: Namespace
      metadata:
        name: hosted-agents
        labels:
          app: arcoa
          purpose: hosted-agents
      EOF

      kubectl apply -f - <<EOF
      apiVersion: networking.k8s.io/v1
      kind: NetworkPolicy
      metadata:
        name: hosted-agent-isolation
        namespace: hosted-agents
      spec:
        podSelector:
          matchLabels:
            arcoa-role: hosted-agent
        policyTypes:
          - Egress
          - Ingress
        ingress: []
        egress:
          - to:
              - namespaceSelector: {}
                podSelector:
                  matchLabels:
                    k8s-app: kube-dns
            ports:
              - protocol: UDP
                port: 53
              - protocol: TCP
                port: 53
          - to:
              - ipBlock:
                  cidr: 0.0.0.0/0
                  except:
                    - 10.0.0.0/8
                    - 172.16.0.0/12
                    - 192.168.0.0/16
                    - 169.254.0.0/16
            ports:
              - protocol: TCP
                port: 443
              - protocol: TCP
                port: 80
      EOF
    EOT
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

# Allow the API to push images to the hosted agents registry
resource "google_artifact_registry_repository_iam_member" "api_push" {
  repository = google_artifact_registry_repository.hosted_agents.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.api_service_account_email}"
}

# Allow GKE to pull images from the hosted agents registry
resource "google_artifact_registry_repository_iam_member" "gke_pull" {
  repository = google_artifact_registry_repository.hosted_agents.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.hosted_agent_runner.email}"
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
