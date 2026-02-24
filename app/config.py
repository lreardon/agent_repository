from decimal import Decimal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "development"
    database_url: str = "postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry"
    test_database_url: str = "postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry_test"
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

    # Blockchain / Wallet
    blockchain_network: str = "base_sepolia"  # "base_sepolia" or "base_mainnet"
    blockchain_rpc_url: str = ""  # Auto-set from network if empty
    treasury_wallet_private_key: str = ""  # Required for withdrawal processing
    hd_wallet_master_seed: str = ""  # BIP-39 mnemonic for per-agent deposit addresses
    usdc_contract_address: str = ""  # Auto-set from network if empty
    min_deposit_amount: Decimal = Decimal("1.00")
    min_withdrawal_amount: Decimal = Decimal("1.00")
    max_withdrawal_amount: Decimal = Decimal("100000.00")
    withdrawal_flat_fee: Decimal = Decimal("0.50")  # Covers L2 gas
    deposit_confirmations_required: int = 12
    chain_monitor_poll_interval_seconds: int = 15

    @property
    def resolved_rpc_url(self) -> str:
        if self.blockchain_rpc_url:
            return self.blockchain_rpc_url
        return {
            "base_sepolia": "https://sepolia.base.org",
            "base_mainnet": "https://mainnet.base.org",
        }[self.blockchain_network]

    @property
    def resolved_usdc_address(self) -> str:
        if self.usdc_contract_address:
            return self.usdc_contract_address
        return {
            "base_sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC on Base Sepolia
            "base_mainnet": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
        }[self.blockchain_network]

    @property
    def chain_id(self) -> int:
        return {"base_sepolia": 84532, "base_mainnet": 8453}[self.blockchain_network]

    # Webhook
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 5

    # Test runner
    test_runner_timeout_per_test: int = 60
    test_runner_timeout_per_suite: int = 300
    test_runner_memory_limit_mb: int = 256

    # Demo only — not used by the app, but must be accepted from .env
    demo_wallet_private_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
