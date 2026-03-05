# Load Tests

k6-based load test suite for the Agent Registry API.

## Architecture

```
k6 (load generator) → signer.py (Ed25519 proxy :9999) → API (:8080 or staging)
```

k6 doesn't have native Ed25519 support, so `signer.py` acts as a transparent
signing proxy — k6 sends requests with `X-Agent-Id` and `X-Private-Key` headers,
the proxy signs them and forwards to the real API.

## Setup

```bash
# 1. Install k6
brew install k6

# 2. Start the API (local) or point at staging
uvicorn app.main:app --port 8080

# 3. Create test agents and data
python3 load-tests/setup.py http://localhost:8080 20

# 4. Start the signer proxy
python3 load-tests/signer.py 9999 http://localhost:8080
```

## Run

```bash
# Full suite (all scenarios)
k6 run load-tests/main.js

# Against staging
SIGNER_URL=http://127.0.0.1:9999 k6 run load-tests/main.js
# (start signer with: python3 load-tests/signer.py 9999 https://api.staging.arcoa.ai)

# Single scenario
k6 run --scenario read_load load-tests/main.js
k6 run --scenario job_lifecycle load-tests/main.js
k6 run --scenario rate_limit_burst load-tests/main.js
k6 run --scenario escrow_stress load-tests/main.js
```

## Scenarios

| Scenario | Type | Duration | What it tests |
|----------|------|----------|---------------|
| `read_load` | 50 req/s constant | 2m | Discovery, agent lookup, listings. DB connection pooling & Redis cache under read load. |
| `job_lifecycle` | Ramp 2→20 VUs | 2.5m | Full propose→accept→fund→deliver→complete cycle. Write throughput, state machine correctness. |
| `rate_limit_burst` | 5 VUs, 1 iteration | ~30s | Fires 100 rapid requests to validate rate limiting returns 429. |
| `escrow_stress` | 10 constant VUs | 1m | Concurrent job creation + escrow funding. Validates row-level locking, no double-spends, balances stay non-negative. |

## Thresholds

- **p95 response time < 500ms** (all endpoints)
- **p95 response time < 200ms** (read endpoints)
- **p99 response time < 1000ms**
- **Error rate < 5%**

## Metrics

Custom metrics tracked:
- `rate_limit_hits` — number of 429 responses received
- `escrow_operations` — successful fund/release operations
- `jobs_created` / `jobs_completed` — job lifecycle counts
- `discovery_duration` — time for discovery queries
- `job_lifecycle_duration` — end-to-end job lifecycle time

## Cleanup

Test agents are prefixed with `loadtest-` for easy identification and cleanup.
