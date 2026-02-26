# Open Issues

Issues that are still open and need attention.

## Critical (🔴)

| # | Issue | Summary |
|---|-------|---------|
| 001 | [Sybil Registration](001-sybil-registration.md) | Unauthenticated registration enables reputation farming and discovery spam |
| 007 | [HD Wallet Seed Security](007-hd-wallet-seed-security.md) | BIP-39 mnemonic in plaintext .env is single point of compromise |

## High (🟠)

| # | Issue | Summary |
|---|-------|---------|
| 002 | [Verification Resource Exhaustion](002-verification-resource-exhaustion.md) | Unbounded Docker compute can DoS platform |
| 003 | [Unbounded Deliverable Size](003-unbounded-deliverable-size.md) | No explicit size limit on deliverable JSON payloads |

## Medium (🟡)

| # | Issue | Summary |
|---|-------|---------|
| 004 | [No Redelivery Mechanism](004-no-redelivery-mechanism.md) | Failed jobs offer no retry opportunity |
| 005 | [Gameable Reputation](005-gameable-reputation.md) | Reputation can be gamed with cheap jobs |

## Architecture Gaps (🏗️)

| # | Issue | Summary |
|---|-------|---------|
| 009 | [No Dispute Resolution](009-no-dispute-resolution.md) | DISPUTED status has no resolution mechanism |

## Total

- **Critical:** 2 issues
- **High:** 2 issues
- **Medium:** 2 issues
- **Architecture:** 1 issue
- **Total:** 7 open issues

## Priority Order

1. **Fix immediately:** #001, #007
2. **High priority:** #002, #003
3. **Medium priority:** #004, #005
4. **Design phase:** #009

## Related

- See [Closed Issues](../closed/) for resolved concerns
- See [CONCERNS*.md](../CONCERNS*.md) for original concern documents
