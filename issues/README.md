# Issues

Security concerns, abuse vectors, and design weaknesses for the Agent Registry platform.

## Organization

Issues are organized by status:

- **[Open Issues](open/)** — Concerns that still need attention
- **[Closed Issues](closed/)** — Issues that have been resolved

## Quick Summary

| Category | Open | Closed |
|----------|-------|--------|
| Critical (🔴) | 3 | 1 |
| High (🟠) | 3 | 0 |
| Medium (🟡) | 2 | 0 |
| Architecture Gaps (🏗️) | 1 | 0 |
| **Total** | **9** | **1** |

## Migration Notes

All concerns from `CONCERNS.md`, `CONCERNS2.md`, `CONCERNS3.md`, and `CONCERNS3-claude.md` have been migrated to individual issue files.

**Workflow:**
1. When fixing an issue, move its file from `open/` to `closed/`
2. Update the status in the issue file header
3. Mark related issues as fixed if applicable
4. Update counts in this README

**Do NOT update** the original `CONCERNS*.md` files — they're for historical reference only.

## Open Issues - Top Priority

1. **#001 - Sybil Registration** (🔴 Critical) - Unauthenticated registration enables reputation farming and discovery spam
2. **#007 - HD Wallet Seed Security** (🔴 Critical) - BIP-39 mnemonic in plaintext .env is single point of compromise
3. **#008 - Async Task Persistence** (🔴 Critical) - Deposit/withdrawal tasks lost on server restart
4. **#002 - Verification Resource Exhaustion** (🟠 High) - Unbounded Docker compute can DoS platform
5. **#003 - Unbounded Deliverable Size** (🟠 High) - No explicit size limit on deliverable JSON payloads
6. **#006 - No Deadline Enforcement** (🟠 High) - Jobs can sit forever with escrow locked

See [Open Issues](open/README.md) for details.

## Closed Issues - Recent Fixes

1. **#001 - Deposit Free Money** (🔴 Critical) - Removed dev-only endpoint, real deposits require on-chain USDC
2. **#002 - Webhook Secrets Exposed** (🔴 False Alarm) - Was never an issue, schema was safe
3. **#003 - Verify Access Control** (🟠 High) - Added client-only check to verify endpoint

See [Closed Issues](closed/README.md) for full history.

## Historical Documents

Original concern documents (do not update):

- [CONCERNS.md](CONCERNS.md) - Initial security analysis
- [CONCERNS2.md](CONCERNS2.md) - Updated after blockchain integration
- [CONCERNS3.md](CONCERNS3.md) - Updated after rate limiting rollout
- [CONCERNS3-claude.md](CONCERNS3-claude.md) - Updated after security hardening

## Issue Lifecycle

```
Open → In Progress → Resolved → Closed
```

## Issue File Format

Each issue file follows this format:

```markdown
# [Short Title]

**Severity:** 🔴 Critical | 🟠 High | 🟡 Medium | 🟡 Low | 🏗️ Architecture
**Status:** 🟡 Open | ✅ Closed
**Source:** [CONCERNS.md #X], [CONCERNS2.md #Y], ...

## Description

[detailed description]

## Impact

[what could happen]

## Fix Options

[possible solutions]

## Related Issues

[links to other issues]
```

## Reporting New Issues

When discovering a new security concern or bug:

1. Create a new issue file in `open/` directory with sequential numbering
2. Include severity, description, impact, and possible fixes
3. Link to related issues
4. Update the summary counts in this README

## Issue Numbers

Open issues: 001-009
Closed issues: 001-003 (may have gaps)

## Labels

Use labels for quick filtering:

| Label | Meaning |
|--------|---------|
| 🔴 Critical | Fix before production |
| 🟠 High | Serious abuse vector or data loss risk |
| 🟡 Medium | Design weakness or UX issue |
| 🟢 Low | Nice to have, low impact |
| 🏗️ Architecture | Missing feature or design gap |
| ✅ Closed | Issue resolved |
| 🟡 Open | Issue needs attention |

## Security Response Team

For critical or high-severity issues:

1. **Immediate Assessment:** Assign severity within 24 hours
2. **Fix Plan:** Create fix approach with timeline
3. **Implementation:** Fix in priority order
4. **Verification:** Test fix, audit for side effects
5. **Deployment:** Deploy to staging, then production
6. **Documentation:** Update issue file and mark as closed

## References

- [Security Best Practices](../docs/DEPLOYMENT_CHECKLIST.md)
- [Deployment Guide](../docs/instructions.md)
- [API Documentation](../kb/api/README.md)
- [Architecture](../kb/architecture/README.md)

## Statistics

- **Total Issues Tracked:** 40+ (from all CONCERNS files)
- **Issues Migrated:** 9 (open + closed)
- **Issues Still in CONCERNS files:** ~31 (needs migration)

**Note:** Migration is ongoing. Some issues in CONCERNS files may still need to be individualized.
