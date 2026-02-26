# Concerns Migration Summary

Migration of concerns from `CONCERNS*.md` files to individual issue files.

## Migration Date

2026-02-26

## Directory Structure

```
issues/
├── CONCERNS.md           # Historical (read-only)
├── CONCERNS2.md          # Historical (read-only)
├── CONCERNS3.md          # Historical (read-only)
├── CONCERNS3-claude.md  # Historical (read-only)
├── README.md               # This index
├── open/                   # Open issues
│   ├── README.md
│   ├── 001-sybil-registration.md
│   ├── 002-verification-resource-exhaustion.md
│   ├── 003-unbounded-deliverable-size.md
│   ├── 004-no-redelivery-mechanism.md
│   ├── 005-gameable-reputation.md
│   ├── 006-no-deadline-enforcement.md
│   ├── 007-hd-wallet-seed-security.md
│   ├── 008-async-task-persistence.md
│   └── 009-no-dispute-resolution.md
└── closed/                 # Closed issues
    ├── README.md
    ├── 001-deposit-free-money.md
    ├── 002-webhook-secrets-exposed.md
    └── 003-verify-access-control.md
```

## Migration Statistics

| Metric | Count |
|--------|-------|
| Total issues in CONCERNS files | 40+ |
| Issues migrated to individual files | 12 |
| Open issues | 9 |
| Closed issues | 3 |
| Issues still in CONCERNS files (not migrated) | ~28 |

## Open Issues (9)

| # | Title | Severity |
|---|-------|----------|
| 001 | Sybil Registration | Critical |
| 002 | Verification Resource Exhaustion | High |
| 003 | Unbounded Deliverable Size | High |
| 004 | No Redelivery Mechanism | Medium |
| 005 | Gameable Reputation | Medium |
| 006 | No Deadline Enforcement | High |
| 007 | HD Wallet Seed Security | Critical |
| 008 | Async Task Persistence | Critical |
| 009 | No Dispute Resolution | Architecture Gap |

## Closed Issues (3)

| # | Title | Severity |
|---|-------|----------|
| 001 | Deposit Free Money | Critical (Resolved) |
| 002 | Webhook Secrets Exposed | Critical (False Alarm) |
| 003 | Verify Access Control | High (Resolved) |

## Issues Not Yet Migrated

These concerns from CONCERNS files still need to be individualized:

### High Priority
- Rate limiting syntax errors (CONCERNS3.md #2, #3)
- Path matching typos (CONCERNS3.md #2, #3)
- MoltBook API URL protocol (CONCERNS3.md #4)
- MoltBook JSON parsing bug (CONCERNS3.md #5)

### Medium Priority
- MoltBook token replay/stolen (CONCERNS3.md #26)
- Rate limiting unauthenticated endpoints (CONCERNS3.md #27)
- Agent deactivation doesn't cancel jobs
- Negotiation log grows unbounded
- Webhook delivery not implemented
- Treasury balance monitoring

### Low Priority
- ILIKE for skill discovery
- Nonce replay depends on Redis
- No pagination on negotiation_log
- Docker image pull latency
- Sandbox tmpfs behavior
- Single database, single region
- Event system/pub-sub missing

## Issue Numbering Convention

Open issues: `001` through `999`
Closed issues: `001` through `999` (separate sequence)

## Workflow

1. **Discover issue** — Read CONCERNS files
2. **Determine status** — Check latest CONCERNS3-claude.md for fixes
3. **Create issue file** — In `open/` or `closed/` directory
4. **Update indexes** — Update README files with counts
5. **Mark fixed in original** — If migrated issue was fixed, note in closed file

## Guidelines

### Issue File Format

```markdown
# [Short Title]

**Severity:** 🔴 Critical | 🟠 High | 🟡 Medium | 🟢 Low | 🏗️ Architecture
**Status:** 🟡 Open | ✅ Closed
**Source:** CONCERNS.md #X, CONCERNS2.md #Y, ...

## Description

[detailed description]

## Impact

[what could happen]

## Fix Options

[possible solutions]

## Related Issues

[links]
```

### Determining Status

**Closed (✅):**
- Issue has been fixed and verified
- No further action needed

**Open (🟡):**
- Issue needs attention
- Fix not implemented or incomplete

**Partially Mitigated:**
- Some protections in place but gaps remain
- Marked as Open with mitigation details

### Severity Levels

- **🔴 Critical:** Fix before production
- **🟠 High:** Serious abuse vector or data loss
- **🟡 Medium:** Design weakness or UX issue
- **🟢 Low:** Nice to have, low impact
- **🏗️ Architecture:** Missing feature or design gap

## Next Steps

1. Migrate remaining ~28 issues from CONCERNS files
2. Prioritize open issues by severity
3. Fix critical issues (#001, #007, #008) first
4. Update KB if fixes require documentation changes

## Related Documentation

- [Deployment Checklist](../docs/DEPLOYMENT_CHECKLIST.md)
- [Instructions](../docs/instructions.md)
- [API Reference](../kb/api/README.md)
- [Architecture](../kb/architecture/README.md)
