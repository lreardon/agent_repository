# Automatic Documentation Hosting Setup

This document describes the Firebase Hosting setup for automatic deployment of the documentation site.

## Overview

The `web/` directory contains a static documentation site that is automatically deployed to Firebase Hosting when you push to the `main` branch. The site is publicly accessible and includes SSL certificates and a global CDN.

## Infrastructure

### Terraform Resources

**Module**: `infra/modules/firebase-hosting/`

Creates:
- Firebase Hosting site (named `{project_id}-{environment}`)
- Cloud Storage bucket for site content
- Public read access for the bucket
- Security headers configuration

### Configuration Files

- **`firebase.json`**: Firebase Hosting configuration (public directory, headers, rewrites)
- **`.firebaserc`**: Project configuration (dynamically set by GitHub Actions)

## Deployment Process

When you push to `main`:

1. **Tests run** → PostgreSQL + Redis services, pytest suite
2. **API deploys** → Cloud Run (parallel)
3. **Docs deploy** → Firebase Hosting (parallel)

All three steps run in parallel after tests pass.

## URLs

After deployment, the documentation site is available at:

| Environment | URL |
|-------------|-----|
| Staging | `https://<PROJECT_ID>-staging.web.app` |
| Production | `https://<PROJECT_ID>-production.web.app` |

Get the URL from Terraform outputs:
```bash
cd infra
terraform output docs_url
```

## Local Development

### Quick Preview

```bash
# Using the provided script
./scripts/serve-docs.sh

# Or manually
cd web
python3 -m http.server 8000
# Visit http://localhost:8000
```

### Firebase Preview

```bash
# Install Firebase CLI
npm install -g firebase-tools

# Serve locally with Firebase
firebase serve --only hosting
```

## Making Changes

1. Edit files in `web/` directory:
   - `index.html` - Main content
   - `css/styles.css` - Styles
   - `js/main.js` - Interactive features

2. Commit and push:
   ```bash
   git add web/
   git commit -m "Update docs: add new section"
   git push origin main
   ```

3. GitHub Actions automatically deploys changes

4. Visit the URL to see changes

## Important Notes

### Before First Deployment

Update the base API URL in `web/index.html`:

```html
<!-- Replace this placeholder -->
<p class="section-subtitle">Base URL: <code>https://api.agentregistry.example.com</code></p>

<!-- With your actual API URL -->
<p class="section-subtitle">Base URL: <code>https://api-staging.agent-registry.com</code></p>
```

### Customization

You can customize the site in `web/css/styles.css`:

```css
:root {
    --primary: #3B82F6;      /* Main color */
    --primary-dark: #2563EB; /* Darker shade */
    --success: #10B981;      /* Success color */
    --warning: #F59E0B;      /* Warning color */
    --danger: #EF4444;       /* Danger color */
    /* ... more variables */
}
```

### Adding to Production Checklist

Before going to production, verify in `DEPLOYMENT_CHECKLIST.md`:

- [ ] **Verify docs deployment** — Confirm Firebase Hosting is serving the `web/` directory
- [ ] **Update base URL in docs** — Replace placeholder API URL with production endpoint

## Troubleshooting

### Deployment Fails

Check GitHub Actions logs:
1. Go to repository → Actions tab
2. Find the failed `deploy-docs` job
3. Check error messages

Common issues:
- **Firebase auth**: Verify `FIREBASE_CLI_EXPERIMENTS=webframeworks` is set
- **Project ID**: Ensure `.firebaserc` is correctly substituted
- **Permissions**: Verify CI service account has `roles/firebasehosting.admin`

### Site Not Loading

1. Check Terraform outputs:
   ```bash
   terraform output docs_url
   ```

2. Verify Firebase site exists:
   ```bash
   firebase projects:list
   firebase hosting:sites:list
   ```

3. Check deployment status:
   ```bash
   firebase deploy --only hosting --project=${PROJECT_ID} --dry-run
   ```

### Local Preview Not Working

- Ensure port 8000 is not in use
- Try a different port: `python3 -m http.server 3000`
- Check firewall settings

## Permissions

The CI/CD service account (`github-actions-ci@...`) has been granted:
- `roles/firebasehosting.admin` — Full access to Firebase Hosting

This permission was added to `infra/modules/ci-cd/main.tf`.

## Cost

Firebase Hosting is free tier for standard usage:
- **Free**: 10 GB/month storage, 10 GB/month transfer
- **Paid**: $0.026/GB/month storage, $0.15/GB transfer

For documentation sites, free tier is typically sufficient.

## Next Steps

1. Apply the Terraform changes to create Firebase Hosting resources:
   ```bash
   cd infra
   terraform plan -var-file=staging.tfvars
   terraform apply -var-file=staging.tfvars
   ```

2. Push to `main` to trigger the first deployment

3. Visit the URL from `terraform output docs_url`

4. Update the base API URL in `web/index.html` for production
