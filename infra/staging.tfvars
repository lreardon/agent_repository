# TODO: Set this to your actual GCP project ID
project_id  = "agent-registry-488317"
region      = "us-west1"
environment = "staging"

# Database — cheapest Enterprise tier
db_tier    = "db-f1-micro"
db_version = "POSTGRES_16"

# Redis
redis_memory_size_gb = 1

# Cloud Run — scale to zero in staging
cloud_run_image         = "us-west1-docker.pkg.dev/agent-registry-488317/agent-registry/api:latest"
cloud_run_min_instances = 0
cloud_run_max_instances = 2

# API base URL
base_url = "https://api.staging.arcoa.ai"

# Treasury Wallet
treasury_wallet_address = "0xaa1FAF0bCfd2915d679b0b60D7A82D4379be19dD"

# Blockchain
blockchain_network = "base_sepolia"

