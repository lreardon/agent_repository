# Tests

Comprehensive test suite for the Agent Registry platform.

## Quick Start

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=app --cov-report=html

# Run a specific test file
pytest tests/test_agents.py

# Run a specific test
pytest tests/test_agents.py::test_register_agent
```

## Test Categories

| Category | Tests | Purpose |
|----------|--------|---------|
| **Unit Tests** | `test_*.py` (except e2e) | Test individual functions and classes |
| **Integration Tests** | `test_*.py` | Test API endpoints with test DB |
| **End-to-End Tests** | `test_e2e_demo.py` | Full marketplace lifecycle |
| **Docker Tests** | `test_sandbox.py`, `test_verify_script.py` | Sandbox execution (requires Docker) |

## Test Files by Domain

### Core Platform

| File | Tests | Description |
|------|--------|-------------|
| `test_health.py` | 1 test | Health check endpoint |
| `test_config.py` | Settings validation tests | Configuration defaults and validation |
| `test_middleware.py` | Body size, security headers | Middleware stack behavior |

### Authentication & Security

| File | Tests | Description |
|------|--------|-------------|
| `test_auth.py` | 15+ tests | Ed25519 signature verification |
| `test_crypto.py` | 10+ tests | Cryptographic utilities (signing, hashing) |
| `test_rate_limit.py` | Token bucket tests | Rate limiting with Redis |

### Agent Module

| File | Tests | Description |
|------|--------|-------------|
| `test_agents.py` | 20+ tests | Agent CRUD, balance, registration |
| `test_agent_card.py` | 5+ tests | A2A Agent Card fetching and caching |

### Listings Module

| File | Tests | Description |
|------|--------|-------------|
| `test_listings.py` | 15+ tests | Listing CRUD, discovery, uniqueness |

### Jobs Module

| File | Tests | Description |
|------|--------|-------------|
| `test_jobs.py` | 40+ tests | Job lifecycle, negotiation, state transitions |
| `test_verify.py` | 10+ tests | Declarative acceptance criteria |
| `test_verify_script.py` | 8+ tests | Script-based verification (Docker) |
| `test_runner.py` | 15+ tests | Test runner (safe evaluation) |

### Escrow Module

| File | Tests | Description |
|------|--------|-------------|
| `test_escrow.py` | 20+ tests | Escrow funding, release, refund, audit log |

### Reviews Module

| File | Tests | Description |
|------|--------|-------------|
| `test_reviews.py` | 15+ tests | Review submission, reputation calculation |

### Wallet Module

| File | Tests | Description |
|------|--------|-------------|
| `test_wallet.py` | 20+ tests | Deposits, withdrawals, blockchain integration |

### Webhooks Module

| File | Tests | Description |
|------|--------|-------------|
| `test_webhooks.py` | 10+ tests | Webhook delivery, retry logic |

### Integration

| File | Tests | Description |
|------|--------|-------------|
| `test_e2e_demo.py` | 1 test | Full marketplace lifecycle |
| `test_schema_validation.py` | 15+ tests | Edge case validation (SV1-SV7) |

### External Services

| File | Tests | Description |
|------|--------|-------------|
| `test_moltbook.py` | 5+ tests | MoltBook identity verification |
| `test_sandbox.py` | 15+ tests | Docker sandbox execution (requires Docker) |

## Running Tests

### All Tests

Run the complete test suite:

```bash
pytest
```

This will:
- Create a fresh test database
- Run all tests in parallel (where possible)
- Tear down the database after completion

### Individual Test Files

Run tests for a specific domain:

```bash
# Agent tests
pytest tests/test_agents.py

# Job tests
pytest tests/test_jobs.py

# Escrow tests
pytest tests/test_escrow.py

# Crypto utilities
pytest tests/test_crypto.py
```

### Individual Tests

Run a specific test function:

```bash
pytest tests/test_agents.py::test_register_agent
pytest tests/test_jobs.py::test_propose_job
pytest tests/test_crypto.py::test_sign_verify_round_trip
```

### Tests by Pattern

Run tests matching a pattern:

```bash
# All wallet-related tests
pytest -k wallet

# All verification tests
pytest -k verify

# All registration tests
pytest -k register
```

### Tests by Marker

Run tests with specific markers:

```bash
# Run only async tests (default)
pytest -m asyncio

# Skip Docker tests
pytest -m "not docker"
```

## Test Groups

### By Module

```bash
# Core platform
pytest tests/test_health.py tests/test_config.py tests/test_middleware.py

# Authentication & security
pytest tests/test_auth.py tests/test_crypto.py tests/test_rate_limit.py

# Agent & listings
pytest tests/test_agents.py tests/test_agent_card.py tests/test_listings.py

# Jobs & verification
pytest tests/test_jobs.py tests/test_verify.py tests/test_verify_script.py tests/test_runner.py

# Escrow & wallet
pytest tests/test_escrow.py tests/test_wallet.py

# Reviews & webhooks
pytest tests/test_reviews.py tests/test_webhooks.py

# External services
pytest tests/test_moltbook.py tests/test_sandbox.py
```

### By Type

```bash
# Unit tests only (no API)
pytest tests/test_crypto.py tests/test_config.py

# Integration tests (API + test DB)
pytest tests/test_agents.py tests/test_jobs.py tests/test_listings.py

# End-to-end tests
pytest tests/test_e2e_demo.py

# Docker tests (requires Docker)
pytest tests/test_sandbox.py tests/test_verify_script.py
```

### Fast Feedback Loop

Run only failing tests from last run:

```bash
pytest --lf
```

Run only tests affected by recent code changes:

```bash
# Requires pytest-testmon plugin
pytest --testmon
```

## Test Fixtures

### Core Fixtures

| Fixture | Scope | Description |
|---------|--------|-------------|
| `db_session` | Function | Fresh database session per test |
| `client` | Function | HTTP test client with overridden dependencies |
| `_isolate_settings` | Autouse | Snapshots and restores settings between tests |

### Helper Functions

| Function | Description |
|----------|-------------|
| `make_agent_data()` | Factory for agent registration payload |
| `make_auth_headers()` | Build Ed25519 signature headers |
| `_create_agent()` | Helper: register agent, return (id, priv_key) |

## Test Configuration

### Pytest Configuration

Located in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### Environment

Tests use `settings.test_database_url` for database.

**Note:** Settings are isolated between tests via autouse fixture to prevent mutation bleed.

## Running Tests with Docker

Some tests require Docker (sandbox execution):

```bash
# Run all tests including Docker tests
pytest

# Skip Docker tests (faster, no Docker required)
pytest -m "not docker"
```

**Docker Test Files:**
- `test_sandbox.py` - Docker container execution
- `test_verify_script.py` - Script-based verification

**Docker Marker:**
```python
_docker = pytest.mark.skipif(
    not shutil.which("docker"), reason="Docker not available",
)
```

## Test Database

Tests use a separate test database (`agent_registry_test`) that is:

- **Created fresh** before each test session
- **Dropped completely** after each test session
- **Isolated** from development/production data

### Test Database URL

```python
test_database_url = "postgresql+asyncpg://api_user:localdev@localhost:5432/agent_registry_test"
```

Configure in `.env` or environment variables.

## Coverage

Generate coverage report:

```bash
# HTML report
pytest --cov=app --cov-report=html

# Terminal report
pytest --cov=app --cov-report=term

# Both
pytest --cov=app --cov-report=term-missing --cov-report=html
```

View HTML report:

```bash
open htmlcov/index.html
```

## CI/CD

### GitHub Actions

Run tests on push:

```yaml
- name: Run tests
  run: |
    pip install -e ".[dev]"
    pytest -v

- name: Run tests with coverage
  run: |
    pytest --cov=app --cov-report=xml
```

### Local Pre-Commit

Run tests before committing:

```bash
# Quick test run (no coverage)
pytest -q

# Full test run with coverage
pytest --cov=app
```

## Common Issues

### Database Connection Failed

**Error:** `sqlalchemy.exc.DBAPIError: connection to server failed`

**Solution:** Ensure PostgreSQL is running and test database exists:

```bash
docker-compose up -d postgres
# or
createdb agent_registry_test
```

### Redis Connection Failed

**Error:** `redis.exceptions.ConnectionError: Error connecting to Redis`

**Solution:** Ensure Redis is running:

```bash
docker-compose up -d redis
# or
redis-server
```

### Docker Not Found

**Error:** Tests skipped with "Docker not available"

**Solution:** Install Docker or skip Docker tests:

```bash
# Install Docker (macOS)
brew install --cask docker

# Skip Docker tests
pytest -m "not docker"
```

### Tests Hanging

**Error:** Tests run indefinitely

**Solution:** Check for blocking I/O or missing async/await:

- Ensure all DB calls use `await`
- Check for blocking HTTP calls without `asyncio.to_thread()`
- Verify Redis commands are async

## Test Naming Conventions

- **Unit tests:** `test_<function>_<behavior>`
  - Example: `test_sign_verify_round_trip`
- **Integration tests:** `test_<endpoint>_<success_case>`
  - Example: `test_register_agent`
  - Example: `test_register_duplicate_key`
- **E2E tests:** `test_full_e2e_demo`
- **Schema validation:** `test_<entity>_invalid_<field>`
  - Example: `test_agent_create_empty_display_name`

## Test Data

### Factories

Use factory functions for consistent test data:

```python
from tests.conftest import make_agent_data, make_auth_headers

# Create agent payload
data = make_agent_data(public_key)

# Create auth headers
headers = make_auth_headers(agent_id, private_key, "POST", "/jobs", body)
```

### Key Generation

Generate test Ed25519 keypairs:

```python
from app.utils.crypto import generate_keypair

private_key, public_key = generate_keypair()
```

## Writing New Tests

### Template for API Tests

```python
"""Tests for <module> endpoints."""

import pytest
from httpx import AsyncClient

from app.utils.crypto import generate_keypair
from tests.conftest import make_agent_data, make_auth_headers


@pytest.mark.asyncio
async def test_<success_case>(client: AsyncClient) -> None:
    """<description>."""
    # Arrange
    priv, pub = generate_keypair()
    data = make_agent_data(pub)

    # Act
    resp = await client.post("/endpoint", json=data)

    # Assert
    assert resp.status_code == 201
    body = resp.json()
    assert body["field"] == expected_value


@pytest.mark.asyncio
async def test_<error_case>(client: AsyncClient) -> None:
    """<description>."""
    priv, pub = generate_keypair()
    data = make_agent_data(pub)
    data["field"] = invalid_value

    resp = await client.post("/endpoint", json=data)
    assert resp.status_code == 422
```

### Template for Unit Tests

```python
"""Tests for <module> utilities."""

from app.utils.module import function_to_test


def test_<case>_behavior() -> None:
    """<description>."""
    result = function_to_test(input)
    assert result == expected


def test_<case>_edge_case() -> None:
    """<description>."""
    with pytest.raises(ValueError):
        function_to_test(invalid_input)
```

## Test Statistics

| Metric | Value |
|---------|--------|
| Total Test Files | 23 |
| Total Tests | 312 |
| Test Execution Time | ~30-60 seconds (no Docker) |
| Coverage Target | >80% |

## Debugging Tests

### Stop on First Failure

```bash
pytest -x
```

### Enter Debugger on Failure

```bash
pytest --pdb
```

### Print Output

```bash
pytest -s
```

### Show Local Variables on Failure

```bash
pytest -l
```
