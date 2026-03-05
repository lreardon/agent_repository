# Rewrite load test signer proxy in Go

**Severity:** 🟡 Medium
**Status:** 🟡 Open
**Source:** Load test results 2026-03-04

## Description

The Python `ThreadingHTTPServer` signer proxy (`load-tests/signer.py`) can't handle concurrent load from k6. Under 50+ VUs, it drops connections (`connection reset by peer`), causing 73% HTTP failure rate in load tests. This makes it impossible to get accurate API performance numbers.

## Impact

- Can't distinguish API bottlenecks from test infrastructure bottlenecks
- Load test results are unreliable for performance tuning
- Blocks meaningful p95/p99 benchmarking

## Fix

Rewrite `load-tests/signer.py` in Go:
- Use `net/http` (handles concurrency natively via goroutines)
- Ed25519 signing via `crypto/ed25519` (stdlib)
- Same interface: read `X-Agent-Id` and `X-Private-Key` headers, sign request, forward to target
- Connection pooling to target API via `http.Transport`

Alternatively: pre-generate signed requests during `setup.py` and have k6 consume them directly (eliminates the proxy entirely, but limits test flexibility).

## Effort

~2-3 hours.
