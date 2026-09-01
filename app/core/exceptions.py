"""
Base application exception hierarchy.

Per the project's error-handling requirement, errors are categorized so that
API responses can be predictable and user-friendly without ever leaking
internal implementation details (stack traces, raw exception messages from
third-party libraries, etc.).

Concrete usage (raising these from services, translating to HTTP responses in
API routes/exception handlers) is introduced in later phases as the relevant
subsystems are built. This module only establishes the category contract now,
as part of the application foundation.
"""


class AppError(Exception):
    """Base class for all application-raised errors."""

    #: Safe, user-facing message. Must never contain internal details.
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        if message:
            self.message = message
        super().__init__(self.message)


class UserError(AppError):
    """Bad input from the end user (e.g. malformed request)."""

    message = "Invalid request."


class ValidationError(AppError):
    """Data failed schema or business-rule validation."""

    message = "Validation failed."


class ProcessingError(AppError):
    """Generic document processing pipeline failure."""

    message = "Document processing failed."


class OCRError(ProcessingError):
    """OCR stage failed."""

    message = "Text recognition failed."


class AIProviderError(ProcessingError):
    """The AI/LLM provider call failed or returned an unusable response."""

    message = "AI extraction failed."


class DatabaseError(AppError):
    """A database operation failed."""

    message = "A database error occurred."


class NotFoundError(AppError):
    """A requested resource does not exist (or is not visible to the caller)."""

    message = "Resource not found."


class AuthorizationError(AppError):
    """The caller is authenticated but not permitted to perform this action."""

    message = "You do not have permission to perform this action."


class InvalidStateTransitionError(AppError):
    """
    The requested action is not valid given the resource's current state
    (e.g. approving a document that isn't REVIEW_REQUIRED). Distinct from
    UserError (malformed input) and NotFoundError (resource doesn't exist)
    — the request is well-formed and the resource exists, but the action
    conflicts with its current state. Maps to HTTP 409, not 400.
    """

    message = "This action cannot be performed in the resource's current state."
