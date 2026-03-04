"""Google Cloud Error Reporting integration."""

import logging
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazy-init the Error Reporting client."""
    global _client
    if _client is None:
        try:
            from google.cloud import error_reporting
            from app.config import settings
            project = settings.gcp_project_id or None
            _client = error_reporting.Client(project=project)
        except Exception:
            logger.warning("Cloud Error Reporting client init failed — errors will only be logged")
            _client = False  # sentinel: don't retry
    return _client if _client else None


def report_exception(request: Request | None = None) -> None:
    """Report the current exception to Cloud Error Reporting."""
    client = _get_client()
    if client is None:
        return
    try:
        http_context = None
        if request is not None:
            from google.cloud.error_reporting import HTTPContext
            http_context = HTTPContext(
                method=request.method,
                url=str(request.url),
                user_agent=request.headers.get("user-agent", ""),
                remote_ip=request.headers.get("x-forwarded-for", request.client.host if request.client else ""),
            )
        client.report_exception(http_context=http_context)
    except Exception:
        logger.warning("Failed to report exception to Cloud Error Reporting", exc_info=True)


class ErrorReportingMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and report to Cloud Error Reporting."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        try:
            return await call_next(request)
        except Exception:
            report_exception(request)
            logger.exception("Unhandled exception")
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
