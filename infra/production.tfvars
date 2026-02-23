project_id  = "agent-registry"
region      = "us-west1"
environment = "production"

# Database — dedicated CPU for production
db_tier    = "db-custom-1-3840"
db_version = "POSTGRES_16"

# Redis
redis_memory_size_gb = 1

# Cloud Run — always-on in production
cloud_run_image         = "us-west1-docker.pkg.dev/agent-registry/agent-registry/api:latest"
cloud_run_min_instances = 1
cloud_run_max_instances = 10
