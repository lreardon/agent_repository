from decimal import Decimal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "development"
    database_url: str = "postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry"
    redis_url: str = "redis://localhost:6379/0"
    platform_signing_key: str = "dev-signing-key-not-for-production"
    platform_fee_percent: Decimal = Decimal("0.025")

    # Rate limiting defaults
    rate_limit_discovery_capacity: int = 60
    rate_limit_discovery_refill_per_min: int = 20
    rate_limit_read_capacity: int = 120
    rate_limit_read_refill_per_min: int = 60
    rate_limit_write_capacity: int = 30
    rate_limit_write_refill_per_min: int = 10

    # A2A
    require_agent_card: bool = True  # Set False in tests to skip Agent Card fetch

    # Auth
    signature_max_age_seconds: int = 30
    nonce_ttl_seconds: int = 60

    # Webhook
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 5

    # Test runner
    test_runner_timeout_per_test: int = 60
    test_runner_timeout_per_suite: int = 300
    test_runner_memory_limit_mb: int = 256

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
