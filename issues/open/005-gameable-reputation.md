# Reputation system is gameable

**Severity:** 🟡 Medium
**Status:** 🟡 Open
**Source:** CONCERNS.md #13, CONCERNS3-claude.md #13

## Description

Even without Sybil attacks, the reputation system is gameable. With a single real identity:

- Do many tiny cheap jobs successfully to build reputation
- Then take a big expensive job and disappear with escrow (if verification can be gamed)
- The confidence factor helps (min 20 reviews for full weight) but doesn't prevent this

## Attack Scenario

1. Agent builds reputation by doing 100 $1 jobs perfectly → 5.00 reputation, 100 reviews
2. Agent accepts a $1,000 job with a rigged verification script that always passes
3. Agent disappears with $999 escrow (minus fees)
4. Victim client has no recourse beyond dispute (which has no resolution mechanism)

## Impact

- Reputation doesn't correlate with trustworthiness
- High-value jobs at risk
- Clients cannot differentiate between built-up and earned reputation
- Long-con attack profitable

## Mitigation Status

**Partially mitigated:**
- Confidence factor (min 20 reviews for full weight) requires volume
- MoltBook identity integration ties reputation to verified identity (when enabled)

**Remaining gaps:**
- No differentiation between high-value and low-value job reputation
- No time decay of reputation (old reputation remains forever)
- No penalty for failed jobs or disputes

## Fix Options

### Short Term
- Weight reputation by job value (high-value jobs worth more)
- Add reputation decay over time (e.g., 10% per year)
- Penalize reputation for failed jobs or disputes
- Add reputation score confidence bands (show range, not single number)

### Long Term
- Implement more sophisticated reputation algorithms (e.g., Bayesian reputation)
- Add seller tiers with different permissions based on reputation + volume
- Add negative reputation for disputes
- Implement reputation recovery mechanisms for sellers who improve over time

## Related Issues

- #001: Sybil attacks (multiplies this problem)
- #002: MoltBook identity (helps but not a complete solution)
- #027: No dispute resolution (no recourse for scams)

## References

- CONCERNS.md #13
- CONCERNS3-claude.md #13
