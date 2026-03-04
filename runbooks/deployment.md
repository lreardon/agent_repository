# Deployment

## Staging

### Deploy via CI

Push to `main` triggers `.github/workflows/deploy-staging.yml`:
1. Runs tests
2. Builds Docker image
3. Pushes to Artifact Registry
4. Deploys to Cloud Run

### Manual deploy

```bash
export PROJECT_ID="agent-registry-488317"
export REGION="us-west1"
export REPO="$REGION-docker.pkg.dev/$PROJECT_ID/agent-registry"

# Build and push
docker build -t $REPO/api:latest .
docker push $REPO/api:latest

# Deploy
gcloud run deploy agent-registry-api \
  --image=$REPO/api:latest \
  --region=$REGION \
  --platform=managed
```

### Verify staging

```bash
curl -s https://api.staging.arcoa.ai/health | jq .

# Run smoke test
curl -s https://api.staging.arcoa.ai/fees | jq .
```

## Production

**Production deployment does not exist yet.** See `docs/internal/staging-production-audit.md` for what's needed before production goes live.

When ready, the process should be:

1. Tag a release: `git tag v1.x.x`
2. CI builds and pushes image with tag
3. Manual approval gate in CI
4. Deploy to production Cloud Run
5. Verify health check
6. Monitor for 15 minutes
7. If issues, rollback immediately

## Rollback

### Quick rollback (traffic shift)

```bash
# List recent revisions
gcloud run revisions list --service=agent-registry-api \
  --region=us-west1 --format='table(name, active, createTime)' --limit=5

# Shift 100% traffic to previous revision
gcloud run services update-traffic agent-registry-api \
  --region=us-west1 \
  --to-revisions=PREVIOUS_REVISION_NAME=100
```

### Full rollback (redeploy old image)

```bash
# Find the previous image tag
gcloud artifacts docker images list \
  $REGION-docker.pkg.dev/$PROJECT_ID/agent-registry/api \
  --format='table(version, createTime)' --limit=5

# Deploy specific image
gcloud run deploy agent-registry-api \
  --image=$REPO/api:PREVIOUS_TAG \
  --region=$REGION
```

### Database rollback

If the deploy included a migration that needs reverting:

1. Rollback the migration first (see [Database Operations](database-operations.md#rollback-a-migration))
2. Then rollback the code (traffic shift or redeploy)

**Order matters:** Old code expects old schema.

## Pre-Deploy Checklist

- [ ] All tests passing (`python3 -m pytest`)
- [ ] No uncommitted changes
- [ ] Environment variables reviewed (any new config?)
- [ ] Database migration needed? If yes, run migration before deploying new code
- [ ] On-demand backup created (for production deploys)
- [ ] Monitoring dashboard open during deploy

## Environment Variables

When adding new env vars to Cloud Run:

```bash
# Add a new env var
gcloud run services update agent-registry-api \
  --region=us-west1 \
  --update-env-vars=NEW_VAR=value

# Add a new secret reference
gcloud run services update agent-registry-api \
  --region=us-west1 \
  --update-secrets=NEW_SECRET=secret-name:latest
```

**Remember the GCP Secret Manager checklist** (from MEMORY.md):
1. Create the secret
2. Add a version with the actual value
3. Grant `secretAccessor` to the Cloud Run service account
