# Admin API key comparison is not constant-time

**Severity:** 🟡 Medium
**Status:** ✅ Fixed (2026-03-04)
**Source:** Security Audit 2026-03-04

## Description

The admin API key authentication in `app/auth/admin.py` uses Python set membership (`key not in admin_keys`) for API key validation. Set lookups use hash comparison which is not constant-time — an attacker could theoretically use timing analysis to determine valid key prefixes or narrow down the key space.

## Impact

Potential for timing-based key extraction. Mitigated by:
- All admin auth failures return 404 (hides endpoint existence)
- Admin path prefix is configurable
- Endpoints excluded from OpenAPI schema
- Should be network-restricted in production

## Fix

Replace the set membership check with `hmac.compare_digest()`:

```python
import hmac

def _check_key(candidate: str, valid_keys: set[str]) -> bool:
    return any(hmac.compare_digest(candidate, k) for k in valid_keys)
```

## Effort

~15 minutes.
