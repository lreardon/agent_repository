variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

# --------------------------------------------------------------------------
# Use the default VPC. For production, consider a custom VPC.
# --------------------------------------------------------------------------
data "google_compute_network" "default" {
  name    = "default"
  project = var.project_id
}

# --------------------------------------------------------------------------
# Private service access (required for Cloud SQL private IP)
# --------------------------------------------------------------------------
resource "google_compute_global_address" "private_ip_range" {
  name          = "agent-registry-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = data.google_compute_network.default.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = data.google_compute_network.default.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

# --------------------------------------------------------------------------
# Serverless VPC connector (Cloud Run → Cloud SQL / Redis)
# --------------------------------------------------------------------------
resource "google_vpc_access_connector" "connector" {
  name          = "agent-registry-conn"
  region        = var.region
  ip_cidr_range = "10.8.0.0/28"
  network       = data.google_compute_network.default.name

  min_instances = 2
  max_instances = 3
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "vpc_id" {
  value = data.google_compute_network.default.id
}

output "vpc_connector_id" {
  value = google_vpc_access_connector.connector.id
}
