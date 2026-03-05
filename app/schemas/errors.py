"""Common error response schemas for OpenAPI documentation."""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error response body."""
    detail: str = Field(..., description="Human-readable error message")

    model_config = {
        "json_schema_extra": {
            "examples": [{"detail": "Not found"}]
        }
    }


# Pre-built response dicts for use in route `responses=` parameter
UNAUTHORIZED = {401: {"model": ErrorResponse, "description": "Missing or invalid authentication signature"}}
FORBIDDEN = {403: {"model": ErrorResponse, "description": "Insufficient permissions"}}
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Resource not found"}}
CONFLICT = {409: {"model": ErrorResponse, "description": "Conflict with current resource state"}}
RATE_LIMITED = {429: {"model": ErrorResponse, "description": "Rate limit exceeded"}}

# Common combos
AUTH_ERRORS = {**UNAUTHORIZED, **RATE_LIMITED}
OWNER_ERRORS = {**UNAUTHORIZED, **FORBIDDEN, **NOT_FOUND, **RATE_LIMITED}
JOB_ERRORS = {**UNAUTHORIZED, **FORBIDDEN, **NOT_FOUND, **CONFLICT, **RATE_LIMITED}
PUBLIC_ERRORS = {**NOT_FOUND, **RATE_LIMITED}
