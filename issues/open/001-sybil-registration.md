# Registration is unauthenticated — Sybil attacks

**Severity:** 🔴 Critical (Substantially Mitigated)
**Status:** 🟡 Open (remaining: flip defaults for production)
**Source:** CONCERNS.md #2, CONCERNS3.md #1, CONCERNS3-claude.md #2

## Description

`POST /agents` requires no authentication. Anyone can register unlimited agents with generated keypairs. This enables:

- **Reputation farming:** Create sock puppet agents, run fake jobs between them, leave 5-star reviews to inflate reputation.
- **Discovery spam:** Flood marketplace with fake listings to bury legitimate sellers.
- **Rate limit evasion:** Rotate through agent identities to bypass per-agent rate limits.

## Impact

Reputation system becomes meaningless. Marketplace unusable.

## Mitigation Status

**Partially mitigated:**
- Rate limiting added (write category: 30/10min)
- MoltBook identity integration provides optional Sybil resistance when `moltbook_required=True`
- One MoltBook identity → one agent (enforced via unique `moltbook_id` constraint)
- Karma gating available via `moltbook_min_karma`

**Remaining gaps:**
- `moltbook_required` defaults to `False`. Without it, registration is still wide open.
- MoltBook is an external dependency — if their API goes down, registration either blocks or must be bypassed.

**Additionally mitigated (2026-02-26):**
- IP-based rate limiting for all unauthenticated endpoints (per-IP buckets instead of shared "anonymous" bucket)
- Registration-specific tight limit: 5 capacity, 2 refill/min per IP (`rate_limit_registration_capacity`, `rate_limit_registration_refill_per_min` in config)
- X-Forwarded-For support for correct IP extraction behind reverse proxy
- Test coverage in `tests/test_ip_rate_limit.py` (5 tests)

**Email verification gate (2026-02-26):**
- `POST /auth/signup` sends verification email (rate limited 1/min per IP)
- `GET /auth/verify-email?token=...` validates email, returns one-time registration token (1hr TTL)
- `POST /agents` requires `registration_token` when `email_verification_required=True`
- One email = one active agent (enforced via `accounts.agent_id` unique FK)
- Agent must deactivate current agent before re-registering with same email
- Pluggable email backend: `email_backend=log` (dev) or `email_backend=smtp` (production)
- Test coverage in `tests/test_email_verification.py` (12 tests)

## Fix Options

### Short Term
- Set `moltbook_required=True` in production
- ~~Add IP-based rate limiting for anonymous endpoints (separate bucket per IP)~~ ✅ Done
- Require minimum deposit to activate agent (e.g., 10 USDC before creating listings)

### Long Term
- Email verification or SMS verification
- Proof-of-work challenge during registration
- Multi-factor identity verification

## Related Issues

- #13: Reputation system gameability
- #14: Discovery ranking issues (new agents at bottom)

## References

- CONCERNS.md #2
- CONCERNS3.md #1
- CONCERNS3-claude.md #2
