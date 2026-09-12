"""
Maps the existing app.core.exceptions.AppError hierarchy (defined in Phase 2)
to HTTP responses. This is not a parallel error system — it's the first
place that hierarchy is actually wired into FastAPI, since Phase 2 only
established the category contract and nothing raised these yet.

Response body is always {"error": "<safe message>"} — never a stack trace,
internal path, or raw exception string from a third-party library. This flat
shape (not a nested {"error": {"code": ..., "message": ...}} object) is
deliberately preserved from the existing convention rather than replaced —
changing it would break every existing test asserting on this shape, for no
functional benefit (Phase 12 inspection finding).

Phase 12 addition: a catch-all handler for exceptions that are NOT AppError
subclasses — e.g. a raw KeyError/AttributeError from a bug, or an unwrapped
third-party exception. Before this handler existed, such an exception fell
through to Starlette's default error handling, which returns a plain-text
"Internal Server Error" body (not JSON, inconsistent with the rest of this
API) and is not logged through this project's structured logger at all —
confirmed by direct testing, not assumed.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AppError,
    AuthenticationError,
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
    (AuthenticationError, 401),
    (AuthorizationError, 403),
    (InvalidStateTransitionError, 409),
    (ValidationError, 400),
    (UserError, 400),
    (DatabaseError, 500),
    # AppError itself (and any category without a more specific mapping,
    # e.g. ProcessingError/OCRError/AIProviderError) falls through to 500.
]

_GENERIC_INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


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

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        # Full type/traceback goes to the server-side log only (logger.exception
        # captures the traceback in the log record) — never to the client.
        # The client only ever sees a generic, safe message; no exception
        # string, no traceback, no filesystem/database detail.
        logger.exception(
            "Unhandled exception on %s: %s", request.url.path, type(exc).__name__
        )
        return JSONResponse(
            status_code=500, content={"error": _GENERIC_INTERNAL_ERROR_MESSAGE}
        )
