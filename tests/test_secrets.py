"""Tests for the pluggable secrets backend."""

import pytest
from unittest.mock import patch, MagicMock

from app.services.secrets import get_secret, get_wallet_seed, get_treasury_key


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear lru_cache between tests."""
    get_secret.cache_clear()
    yield
    get_secret.cache_clear()


def test_env_backend_reads_wallet_seed():
    with patch("app.services.secrets.settings") as mock_settings:
        mock_settings.secrets_backend = "env"
        mock_settings.hd_wallet_master_seed = "test seed phrase here"
        mock_settings.treasury_wallet_private_key = ""

        result = get_secret("hd_wallet_master_seed")
        assert result == "test seed phrase here"


def test_env_backend_reads_treasury_key():
    with patch("app.services.secrets.settings") as mock_settings:
        mock_settings.secrets_backend = "env"
        mock_settings.hd_wallet_master_seed = ""
        mock_settings.treasury_wallet_private_key = "0xdeadbeef"

        result = get_secret("treasury_wallet_private_key")
        assert result == "0xdeadbeef"


def test_env_backend_raises_on_missing():
    with patch("app.services.secrets.settings") as mock_settings:
        mock_settings.secrets_backend = "env"
        mock_settings.hd_wallet_master_seed = ""
        mock_settings.treasury_wallet_private_key = ""

        with pytest.raises(ValueError, match="not found"):
            get_secret("hd_wallet_master_seed")


def test_unknown_backend_raises():
    with patch("app.services.secrets.settings") as mock_settings:
        mock_settings.secrets_backend = "magic"

        with pytest.raises(ValueError, match="Unknown secrets backend"):
            get_secret("anything")


def test_get_secret_caches_result():
    with patch("app.services.secrets.settings") as mock_settings:
        mock_settings.secrets_backend = "env"
        mock_settings.hd_wallet_master_seed = "cached seed"

        result1 = get_secret("hd_wallet_master_seed")
        # Mutate the setting — should still return cached value
        mock_settings.hd_wallet_master_seed = "different seed"
        result2 = get_secret("hd_wallet_master_seed")

        assert result1 == result2 == "cached seed"


def test_get_wallet_seed_convenience():
    with patch("app.services.secrets.get_secret", return_value="my seed"):
        assert get_wallet_seed() == "my seed"


def test_get_treasury_key_convenience():
    with patch("app.services.secrets.get_secret", return_value="0xkey"):
        assert get_treasury_key() == "0xkey"


def test_aws_backend_fetches_single_secret():
    """Test AWS Secrets Manager integration (mocked)."""
    import sys

    mock_boto3 = MagicMock()
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "aws-seed-value"}
    mock_boto3.client.return_value = mock_client

    with patch("app.services.secrets.settings") as mock_settings, \
         patch.dict(sys.modules, {"boto3": mock_boto3}):
        mock_settings.secrets_backend = "aws_secrets"
        mock_settings.secrets_prefix = ""
        mock_settings.aws_region = "us-east-1"

        result = get_secret("hd_wallet_master_seed")
        assert result == "aws-seed-value"
        mock_client.get_secret_value.assert_called_once_with(SecretId="hd_wallet_master_seed")


def test_aws_backend_with_prefix():
    import sys

    mock_boto3 = MagicMock()
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "prefixed-value"}
    mock_boto3.client.return_value = mock_client

    with patch("app.services.secrets.settings") as mock_settings, \
         patch.dict(sys.modules, {"boto3": mock_boto3}):
        mock_settings.secrets_backend = "aws_secrets"
        mock_settings.secrets_prefix = "agent-registry/prod"
        mock_settings.aws_region = "us-east-1"

        result = get_secret("hd_wallet_master_seed")
        assert result == "prefixed-value"
        mock_client.get_secret_value.assert_called_once_with(
            SecretId="agent-registry/prod/hd_wallet_master_seed"
        )
