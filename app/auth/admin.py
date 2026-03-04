"""Admin API key authentication dependency."""

from fastapi import HTTPException, Request

from app.config import settings

# All admin auth failures return 404 to avoid revealing that admin endpoints exist.
_HIDDEN_404 = HTTPException(status_code=404, detail="Not found")


def _parse_admin_keys() -> set[str]:
    """Parse comma-separated admin API keys from config."""
    raw = settings.admin_api_keys.strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


async def require_admin(request: Request) -> str:
    """FastAPI dependency: validate X-Admin-Key header.

    Returns the validated key (for audit logging).
    Returns 404 (not 401/403) on all failures to hide admin existence.
    """
    admin_keys = _parse_admin_keys()
    if not admin_keys:
        raise _HIDDEN_404

    key = request.headers.get("X-Admin-Key", "").strip()
    if not key:
        raise _HIDDEN_404

    if key not in admin_keys:
        raise _HIDDEN_404

    return key
