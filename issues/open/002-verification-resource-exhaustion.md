# Verification script resource exhaustion attack

**Severity:** 🟠 High (Partially Mitigated)
**Status:** 🟡 Open
**Source:** CONCERNS.md #9, CONCERNS3-claude.md #9

## Description

The sandbox limits are 300s timeout, 512MB memory, 1 CPU. But running Docker containers is expensive. A malicious client could:

- Submit jobs with scripts that always take max time (300s of compute per verify call)
- Do this across many jobs simultaneously
- The platform eats the compute cost

## Impact

Denial of service via compute exhaustion. Platform costs become unbounded.

## Mitigation Status

**Partially mitigated:**
- Rate limiting on job lifecycle endpoints (20 capacity, 5 refill/min)
- Verification fee charged to client (disincentivizes waste)

**Remaining gaps:**
- A determined attacker with multiple agents (if MoltBook is off) can still run many 300s containers simultaneously
- No concurrency limit on sandbox containers
- Fee doesn't fully offset Docker costs

## Fix Options

### Short Term
- Add concurrency limit on sandbox containers (e.g., max 5 containers per server)
- Increase verification fee to better offset Docker costs
- Account-level rate limiting in addition to per-agent

### Long Term
- Move verification to separate pool of workers with strict resource quotas
- Implement verification queue with priority scheduling
- Use cheaper compute for verification (e.g., spot instances)

## Related Issues

- #001: Sybil attacks (enables multi-agent attacks)
- #002: MoltBook dependency (if off, this is worse)

## References

- CONCERNS.md #9
- CONCERNS3-claude.md #9
