# No re-delivery mechanism

**Severity:** 🟡 Medium
**Status:** 🟡 Open
**Source:** CONCERNS.md #12, CONCERNS2.md #12, CONCERNS3-claude.md #12

## Description

If verification fails, job goes to `FAILED` and escrow refunds immediately. The seller gets no chance to fix and re-deliver. In a real marketplace, you'd want at least one retry.

## Impact

- Penalizes sellers harshly for minor issues
- Discourages seller participation
- One-time mistakes cause permanent failure
- No incentive to attempt fixes

## Mitigation Status

**None.** No retry mechanism implemented.

## Fix Options

### Short Term
- Allow `FAILED → IN_PROGRESS` transition for re-delivery
- Limit to N retries per job (e.g., 1-3)
- Track retry count in job model
- Require client approval for retry option

### Long Term
- Implement configurable retry policy (automatic vs manual approval)
- Add different failure modes: fixable vs permanent
- Reputation impact based on whether failure was fixable
- Add partial delivery/acceptance flow for complex jobs

## Proposed State Transition

```
FAILED → IN_PROGRESS (re-delivery, retry_count++)
IN_PROGRESS → DELIVERED (re-delivery)
```

## Related Issues

- #13: Reputation system gameability (retries affect reputation)
- #007: Seller can verify their own job (combines with retry)

## References

- CONCERNS.md #12
- CONCERNS2.md #12
- CONCERNS3-claude.md #12
