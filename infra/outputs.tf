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
