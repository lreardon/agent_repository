# Tests Index

Complete index of all test files.

## Test Files

| File | Domain | Tests | Docker Required? | Docs |
|------|---------|--------|------------------|-------|
| `test_health.py` | Health | 1 | No | [Tests](README.md) |
| `test_config.py` | Configuration | ~5 | No | [Tests](README.md) |
| `test_middleware.py` | Middleware | ~10 | No | [Tests](README.md) |
| `test_auth.py` | Authentication | 15+ | No | [Tests](README.md) |
| `test_crypto.py` | Crypto | 10+ | No | [Tests](README.md) |
| `test_rate_limit.py` | Rate Limiting | 8+ | No | [Tests](README.md) |
| `test_agents.py` | Agents | 20+ | No | [Tests](README.md) |
| `test_agent_card.py` | Agent Card | 5+ | No | [Tests](README.md) |
| `test_listings.py` | Listings | 15+ | No | [Tests](README.md) |
| `test_jobs.py` | Jobs | 40+ | No | [Tests](README.md) |
| `test_verify.py` | Verification | 10+ | No | [Tests](README.md) |
| `test_verify_script.py` | Script Verification | 8+ | Yes | [Tests](README.md) |
| `test_runner.py` | Test Runner | 15+ | No | [Tests](README.md) |
| `test_escrow.py` | Escrow | 20+ | No | [Tests](README.md) |
| `test_reviews.py` | Reviews | 15+ | No | [Tests](README.md) |
| `test_wallet.py` | Wallet | 20+ | No | [Tests](README.md) |
| `test_webhooks.py` | Webhooks | 10+ | No | [Tests](README.md) |
| `test_e2e_demo.py` | E2E | 1 | No | [Tests](README.md) |
| `test_schema_validation.py` | Validation | 15+ | No | [Tests](README.md) |
| `test_moltbook.py` | MoltBook | 5+ | No | [Tests](README.md) |
| `test_sandbox.py` | Sandbox | 15+ | Yes | [Tests](README.md) |

**Total:** 312 tests across 23 files

## Running Tests

| Command | Description |
|---------|-------------|
| `pytest` | Run all tests |
| `pytest tests/test_agents.py` | Run specific file |
| `pytest tests/test_agents.py::test_register_agent` | Run specific test |
| `pytest -k wallet` | Run tests matching "wallet" |
| `pytest -m "not docker"` | Skip Docker tests |
| `pytest --cov=app` | Run with coverage |
| `pytest -v` | Verbose output |
| `pytest -x` | Stop on first failure |

See [Tests README](README.md) for detailed documentation.

## Test Categories

### Unit Tests
- `test_crypto.py` - Cryptographic functions
- `test_config.py` - Configuration validation

### Integration Tests
- All `test_*.py` except `test_e2e_demo.py`
- Tests API endpoints with test database

### End-to-End Tests
- `test_e2e_demo.py` - Full marketplace lifecycle

### Docker Tests (Requires Docker)
- `test_sandbox.py` - Sandbox execution
- `test_verify_script.py` - Script-based verification

**Note:** Docker tests are skipped if Docker is not available.
