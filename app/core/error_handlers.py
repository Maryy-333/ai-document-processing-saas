"""
Maps the existing app.core.exceptions.AppError hierarchy (defined in Phase 2)
to HTTP responses. This is not a parallel error system — it's the first
place that hierarchy is actually wired into FastAPI, since Phase 2 only
established the category contract and nothing raised these yet.

Response body is always {"error": "<safe message>"} — never a stack trace,
internal path, or raw exception string from a third-party library.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AppError,
    AuthorizationError,
    DatabaseError,
    InvalidStateTransitionError,
    NotFoundError,
    UserError,
    ValidationError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_STATUS_BY_EXCEPTION: list[tuple[type[AppError], int]] = [
    (NotFoundError, 404),
    (AuthorizationError, 403),
    (InvalidStateTransitionError, 409),
    (ValidationError, 400),
    (UserError, 400),
    (DatabaseError, 500),
    # AppError itself (and any category without a more specific mapping,
    # e.g. ProcessingError/OCRError/AIProviderError) falls through to 500.
]


def _status_for(exc: AppError) -> int:
    for exc_type, status in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return status
    return 500


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _status_for(exc)
        if status >= 500:
            logger.error("Unhandled AppError on %s: %s", request.url.path, type(exc).__name__)
        return JSONResponse(status_code=status, content={"error": exc.message})
