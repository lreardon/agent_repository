output "cloud_run_url" {
  description = "Public URL of the Cloud Run service"
  value       = module.cloud_run.url
}

output "db_connection_name" {
  description = "Cloud SQL connection name (for Cloud Run --add-cloudsql-instances)"
  value       = module.database.connection_name
}

output "db_private_ip" {
  description = "Cloud SQL private IP address"
  value       = module.database.private_ip
}

output "redis_host" {
  description = "Redis private IP"
  value       = module.redis.host
}

output "redis_port" {
  description = "Redis port"
  value       = module.redis.port
}

output "artifact_registry_repo" {
  description = "Docker image registry URL"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.api.repository_id}"
}

output "webhook_queue_name" {
  description = "Cloud Tasks queue name for webhook delivery"
  value       = google_cloud_tasks_queue.webhook_delivery.name
}

output "gke_cluster_name" {
  description = "GKE Autopilot cluster name for sandbox verification"
  value       = module.gke.cluster_name
}

output "gke_cluster_endpoint" {
  description = "GKE cluster API endpoint"
  value       = module.gke.cluster_endpoint
}

output "sandbox_service_account" {
  description = "Sandbox runner service account email"
  value       = module.gke.sandbox_service_account_email
}

output "wif_provider" {
  description = "Workload Identity Federation provider (for GitHub Actions auth action)"
  value       = module.ci_cd.workload_identity_provider
}

output "ci_service_account_email" {
  description = "CI/CD service account email (for GitHub Actions auth action)"
  value       = module.ci_cd.service_account_email
}

# output "docs_url" {
#   description = "Public URL of the documentation site (Firebase Hosting)"
#   value       = module.firebase_hosting.default_url
# }
