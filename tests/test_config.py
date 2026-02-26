"""Unit tests for app/config.py computed properties."""

from app.config import Settings


def test_resolved_rpc_url_sepolia() -> None:
    s = Settings(blockchain_network="base_sepolia", blockchain_rpc_url="")
    assert "sepolia.base.org" in s.resolved_rpc_url


def test_resolved_rpc_url_mainnet() -> None:
    s = Settings(blockchain_network="base_mainnet", blockchain_rpc_url="")
    assert "mainnet.base.org" in s.resolved_rpc_url


def test_resolved_rpc_url_custom_override() -> None:
    s = Settings(blockchain_rpc_url="https://custom.rpc.example.com")
    assert s.resolved_rpc_url == "https://custom.rpc.example.com"


def test_resolved_usdc_address_sepolia() -> None:
    s = Settings(blockchain_network="base_sepolia", usdc_contract_address="")
    assert s.resolved_usdc_address == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


def test_resolved_usdc_address_mainnet() -> None:
    s = Settings(blockchain_network="base_mainnet", usdc_contract_address="")
    assert s.resolved_usdc_address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def test_chain_id() -> None:
    assert Settings(blockchain_network="base_sepolia").chain_id == 84532
    assert Settings(blockchain_network="base_mainnet").chain_id == 8453


def test_default_fee_schedule() -> None:
    s = Settings()
    assert s.fee_base_percent is not None
    assert float(s.fee_base_percent) > 0
    assert float(s.fee_verification_per_cpu_second) > 0
    assert float(s.fee_storage_per_kb) > 0


def test_default_settings_testable() -> None:
    """Default settings should have test-friendly defaults."""
    s = Settings()
    assert s.env != "production"
    assert s.require_agent_card is False  # Tests skip card fetch
