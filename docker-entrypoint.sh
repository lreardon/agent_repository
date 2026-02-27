#!/bin/sh
set -e

# Construct DATABASE_URL from Cloud Run environment if not already set
if [ -z "$DATABASE_URL" ] && [ -n "$DB_PASSWORD" ]; then
  DB_USER="${DB_USER:-api_user}"
  DB_NAME="${DB_NAME:-agent_registry}"
  if [ -n "$CLOUD_SQL_CONNECTION" ]; then
    # Cloud SQL Auth Proxy via Unix socket
    export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@/${DB_NAME}?host=/cloudsql/${CLOUD_SQL_CONNECTION}"
  else
    # Direct TCP (local dev or non-Cloud-Run environments)
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  fi
fi

# Run Alembic migrations at startup if requested
if [ "$RUN_MIGRATIONS" = "true" ] && [ -n "$DATABASE_URL" ]; then
  echo "Running Alembic migrations..."
  python -m alembic upgrade head
  echo "Migrations complete."
fi

exec "$@"
