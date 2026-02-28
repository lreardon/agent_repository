# Firebase Hosting Module

This module sets up Firebase Hosting for the Agent Registry documentation site.

## Resources Created

- **Firebase Hosting Site**: A public-facing site for hosting static documentation
- **Cloud Storage Bucket**: Storage backend for the hosting site
- **Public Access**: IAM binding allowing all users to read the site
- **Security Headers**: Default security headers for the site

## Usage

The module is automatically included in the main Terraform configuration. After applying:

```bash
cd infra
terraform plan -var-file=staging.tfvars
terraform apply -var-file=staging.tfvars
```

The documentation site URL will be available in the Terraform outputs:

```bash
terraform output docs_url
```

## How It Works

1. **Infrastructure**: Terraform provisions Firebase Hosting resources
2. **Deployment**: GitHub Actions deploys the `web/` directory to Firebase
3. **Access**: The site is publicly available at `https://<PROJECT_ID>-<environment>.web.app`

## Deployment Process

When you push to the `main` branch:

1. Tests run (Postgres + Redis services)
2. API deploys to Cloud Run
3. Documentation deploys to Firebase Hosting (parallel to API deployment)

The documentation deployment uses the Firebase CLI via Node.js, which is installed in the GitHub Actions workflow.

## Updating Documentation

To update the documentation:

1. Edit files in `web/` directory
2. Commit and push to `main` branch
3. GitHub Actions automatically deploys changes

## Local Development

To preview changes locally:

```bash
# Install Firebase CLI
npm install -g firebase-tools

# Serve locally
firebase serve --only hosting

# Or use the simple Python server
./scripts/serve-docs.sh
```

## URL Structure

The site will be available at:

- **Staging**: `https://<PROJECT_ID>-staging.web.app`
- **Production**: `https://<PROJECT_ID>-production.web.app`

## Configuration

- **Public Directory**: `web/` (relative to repo root)
- **Config File**: `firebase.json` at repo root
- **Environment Handling**: `.firebaserc` is dynamically updated by GitHub Actions

## Notes

- Firebase Hosting automatically provisions SSL certificates
- Global CDN is included for fast content delivery
- The site is configured with security headers (X-Content-Type-Options, X-Frame-Options, etc.)
- Static assets (CSS, JS, fonts) have long cache times for performance
