variable "project_id" {
  type = string
}

variable "environment" {
  type = string
}

# --------------------------------------------------------------------------
# Enable Firebase Hosting API
# --------------------------------------------------------------------------
resource "google_project_service" "firebase" {
  project            = var.project_id
  service            = "firebasehosting.googleapis.com"
  disable_on_destroy = false
}

# --------------------------------------------------------------------------
# Firebase Site
# --------------------------------------------------------------------------
resource "firebase_hosting_site" "default" {
  project_id = var.project_id
  site_id    = "${var.project_id}-${var.environment}"

  depends_on = [google_project_service.firebase]
}

# --------------------------------------------------------------------------
# Firebase Site Config
# --------------------------------------------------------------------------
resource "firebase_hosting_default_config" "default" {
  project = var.project_id
  site_id = firebase_hosting_site.default.site_id

  # Public directory relative to repo root
  public_root = "web"

  # Clean URLs (remove .html extensions)
  clean_urls = true

  # Single-page app routing (not needed, but good practice)
  # Not using rewrites since this is a multi-section doc site

  # Headers for security
  headers {
    source = "**"
    values {
      key   = "X-Content-Type-Options"
      value = "nosniff"
    }
    values {
      key   = "X-Frame-Options"
      value = "SAMEORIGIN"
    }
    values {
      key   = "X-XSS-Protection"
      value = "1; mode=block"
    }
  }

  depends_on = [google_hosting_default_config_version.default]
}

# --------------------------------------------------------------------------
# Firebase Hosting Version
# --------------------------------------------------------------------------
# This will be managed by the CI/CD pipeline via firebase-tools
# We create an initial version, but future versions come from GitHub Actions
resource "google_storage_bucket" "hosting" {
  name          = "${firebase_hosting_site.default.site_id}.web.app"
  location      = "US"
  force_destroy = false
  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page  = "404.html"
  }

  depends_on = [google_project_service.firebase]
}

resource "google_storage_bucket_iam_binding" "public_read" {
  bucket = google_storage_bucket.hosting.name
  role   = "roles/storage.objectViewer"
  members = ["allUsers"]
}

# --------------------------------------------------------------------------
# Firebase Hosting Default Config Version
# --------------------------------------------------------------------------
resource "google_hosting_default_config_version" "default" {
  parent = firebase_hosting_site.default.id

  # Empty initial version - will be replaced by deployments
  config {
    headers {
      glob = "**"
      headers {
        key   = "X-Content-Type-Options"
        value = "nosniff"
      }
    }
  }

  depends_on = [google_storage_bucket.hosting]
}

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
output "site_id" {
  description = "Firebase Hosting site ID"
  value       = firebase_hosting_site.default.site_id
}

output "default_url" {
  description = "Default URL for the site"
  value       = firebase_hosting_site.default.default_url
}
