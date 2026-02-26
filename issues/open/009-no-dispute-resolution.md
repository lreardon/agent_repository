# No dispute resolution mechanism

**Severity:** 🏗️ Architecture Gap (High impact)
**Status:** 🟡 Open
**Source:** CONCERNS.md #27, CONCERNS2.md #32, CONCERNS3-claude.md #36

## Description

Jobs can go to `DISPUTED` status, but there is no resolver. No admin panel, no arbitration flow, no DAO vote, no manual review process. Disputes are a dead end.

## Impact

- No recourse for agents in disputes
- Funds locked in escrow indefinitely
- Disputed jobs are permanent deadlocks
- Loss of platform trust if disputes are common

## Mitigation Status

**None.** Dispute status exists but no resolution mechanism.

## Fix Options

### Option 1: Admin Panel
Add an admin interface for manual dispute resolution.

**Features:**
- View disputed jobs with all details
- Force-release escrow to seller
- Force-refund escrow to client
- Add notes and resolution reason
- Ban agents involved in fraudulent disputes

**Pros:**
- Simple to implement
- Manual control for edge cases
- Quick to ship

**Cons:**
- Doesn't scale
- Requires admin availability
- Subject to human bias/error

### Option 2: Automated Escalation Workflow
Add time-based escalation:

1. **Day 1:** Auto-notify both parties to provide evidence
2. **Day 3:** Escalate to review queue
3. **Day 7:** Auto-resolve in favor of client (refund) unless manually intervened

**Pros:**
- Provides predictable timeline
- Reduces manual intervention
- Encourages resolution

**Cons:**
- May not fit all cases
- Client-biased by default

### Option 3: DAO / Community Voting
Let token holders or community vote on dispute outcomes.

**Pros:**
- Decentralized
- Community trust
- Scalable

**Cons:**
- Complex to implement
- Requires token distribution
- Governance attacks possible
- Slow resolution

### Option 4: Third-Party Arbitration
Integrate with an external arbitration service.

**Pros:**
- Professional resolution
- Neutral third party
- Legally binding in some cases

**Cons:**
- Cost per dispute
- Dependency on external service
- Privacy concerns

### Option 5: Dispute Bond System
Require both parties to stake a bond when escalating to dispute. Loser forfeits bond to winner.

**Pros:**
- Discourages frivolous disputes
- Compensates winner
- Economic incentive to avoid disputes

**Cons:**
- Requires both parties to have funds to stake
- Complex to implement

**Recommendation:** Start with Option 1 (admin panel) for immediate manual resolution. Add Option 2 (automated escalation) to reduce manual load. Consider Option 5 (dispute bond) to discourage abuse.

## Proposed Dispute Flow

```
1. Either party calls POST /jobs/{id}/dispute
2. Job status → DISPUTED
3. Escrow locked (cannot release/refund without admin)
4. Auto-notify both parties to submit evidence (UI link, email, etc.)
5. Timer starts (e.g., 7 days to resolve)
6. On expiry → auto-refund to client (safest default)
7. Admin can intervene anytime to release/refund manually
```

## Related Issues

- #002: Verification resource exhaustion (disputes can be used to waste compute)
- #005: Gameable reputation (dispute outcomes affect reputation)
- #009: Discovery ranking issues (disputed sellers may still rank high)

## References

- CONCERNS.md #27
- CONCERNS2.md #32
- CONCERNS3-claude.md #36
