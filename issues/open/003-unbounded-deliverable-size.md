# Deliverable size is unbounded

**Severity:** 🟠 High (Partially Mitigated)
**Status:** 🟡 Open
**Source:** CONCERNS.md #10, CONCERNS3-claude.md #10

## Description

`DeliverPayload.result` accepts any `dict | list` with no explicit size limit. While `BodySizeLimitMiddleware` caps request bodies at 1MB, this is a generous upper bound for deliverables. A seller could deliver a 500MB+ JSON blob that gets:

- Stored in PostgreSQL (JSONB column)
- Serialized to disk for sandbox verification
- Loaded into memory by the verification script

## Impact

- Database bloat from large deliverable storage
- OOM kills on database workers
- Disk exhaustion from repeated large deliverables
- Verification timeouts from loading/processing large data

## Mitigation Status

**Partially mitigated:**
- `BodySizeLimitMiddleware` caps request bodies at 1MB

**Remaining gaps:**
- 1MB is still very large for typical JSON deliverables
- No explicit size validation on the `DeliverPayload.result` field
- No per-user storage quota
- JSONB column grows without cleanup mechanism

## Fix Options

### Short Term
- Add explicit `max_size` validator on `DeliverPayload.result` (e.g., 1MB or 500KB)
- Add storage fee scaling that charges more for larger deliverables
- Add per-user deliverable storage quota

### Long Term
- Implement deliverable compression before storage
- Store large deliverables in object storage (e.g., GCS) instead of database
- Implement deliverable lifecycle policy (auto-delete after N days)
- Add delivery size preview/quota in listing creation

## Related Issues

- #002: Verification resource exhaustion (large payloads make this worse)

## References

- CONCERNS.md #10
- CONCERNS3-claude.md #10
