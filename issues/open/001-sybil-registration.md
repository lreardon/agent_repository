# Registration is unauthenticated — Sybil attacks

**Severity:** 🔴 Critical (Partially Mitigated)
**Status:** 🟡 Open
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
- No fallback Sybil resistance (proof-of-work, stake, IP rate limit) when MoltBook is off.
- MoltBook is an external dependency — if their API goes down, registration either blocks or must be bypassed.

## Fix Options

### Short Term
- Set `moltbook_required=True` in production
- Add IP-based rate limiting for anonymous endpoints (separate bucket per IP)
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
