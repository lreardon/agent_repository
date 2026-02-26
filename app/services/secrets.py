"""Pluggable secrets backend for sensitive credentials.

Supports:
- env: Read from environment variables / .env file (default, dev only)
- aws_secrets: AWS Secrets Manager
- gcp_secrets: GCP Secret Manager

Configure via SECRETS_BACKEND and SECRETS_PREFIX in settings.
Secrets are fetched once at startup and cached in memory.
"""

import json
import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


def _fetch_from_env(key: str) -> str:
    """Read secret from settings (env var / .env file)."""
    mapping = {
        "hd_wallet_master_seed": settings.hd_wallet_master_seed,
        "treasury_wallet_private_key": settings.treasury_wallet_private_key,
    }
    value = mapping.get(key, "")
    if not value:
        raise ValueError(f"Secret '{key}' not found in environment")
    return value


def _fetch_from_aws(key: str) -> str:
    """Read secret from AWS Secrets Manager.

    Expects SECRETS_PREFIX to be the secret name (e.g., 'agent-registry/prod').
    The secret value should be a JSON object with keys matching the secret names.
    """
    import boto3

    client = boto3.client("secretsmanager", region_name=settings.aws_region)
    secret_id = f"{settings.secrets_prefix}/{key}" if settings.secrets_prefix else key

    try:
        response = client.get_secret_value(SecretId=secret_id)
    except client.exceptions.ResourceNotFoundException:
        # Try as a JSON bundle under the prefix
        if settings.secrets_prefix:
            response = client.get_secret_value(SecretId=settings.secrets_prefix)
            bundle = json.loads(response["SecretString"])
            if key not in bundle:
                raise ValueError(f"Secret '{key}' not found in AWS secret '{settings.secrets_prefix}'")
            return bundle[key]
        raise ValueError(f"Secret '{key}' not found in AWS Secrets Manager")

    return response["SecretString"]


def _fetch_from_gcp(key: str) -> str:
    """Read secret from GCP Secret Manager.

    Expects SECRETS_PREFIX as the project ID (e.g., 'my-project').
    Secret name is constructed as: projects/{prefix}/secrets/{key}/versions/latest
    """
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    project = settings.secrets_prefix or settings.gcp_project_id
    name = f"projects/{project}/secrets/{key}/versions/latest"

    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


_BACKENDS = {
    "env": _fetch_from_env,
    "aws_secrets": _fetch_from_aws,
    "gcp_secrets": _fetch_from_gcp,
}


@lru_cache(maxsize=16)
def get_secret(key: str) -> str:
    """Fetch a secret by key using the configured backend.

    Results are cached for the lifetime of the process.
    """
    backend = settings.secrets_backend
    fetcher = _BACKENDS.get(backend)
    if fetcher is None:
        raise ValueError(
            f"Unknown secrets backend: '{backend}'. "
            f"Valid options: {', '.join(_BACKENDS.keys())}"
        )

    logger.info("Fetching secret '%s' via %s backend", key, backend)
    value = fetcher(key)

    if not value:
        raise ValueError(f"Secret '{key}' is empty")

    return value


def get_wallet_seed() -> str:
    """Get the HD wallet master seed."""
    return get_secret("hd_wallet_master_seed")


def get_treasury_key() -> str:
    """Get the treasury wallet private key."""
    return get_secret("treasury_wallet_private_key")
