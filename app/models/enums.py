"""
Enum types shared across models.

DocumentStatus mirrors the state machine defined in Phase 1 §7 exactly.
ProcessingStage/ProcessingStatus back the ProcessingJob history table.
"""

import enum


class DocumentStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    STORED = "STORED"
    EXTRACTING_TEXT = "EXTRACTING_TEXT"
    OCR_REQUIRED = "OCR_REQUIRED"
    OCR_PROCESSING = "OCR_PROCESSING"
    AI_PROCESSING = "AI_PROCESSING"
    VALIDATING_RESULT = "VALIDATING_RESULT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class ProcessingStage(str, enum.Enum):
    """Which pipeline stage a given ProcessingJob row represents."""

    VALIDATION = "VALIDATION"
    TEXT_EXTRACTION = "TEXT_EXTRACTION"
    OCR = "OCR"
    AI_EXTRACTION = "AI_EXTRACTION"
    RESULT_VALIDATION = "RESULT_VALIDATION"


class ProcessingStatus(str, enum.Enum):
    """Outcome of a single ProcessingJob attempt."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ErrorCategory(str, enum.Enum):
    """
    Matches the error categories called out in Phase 1 §14, so a
    ProcessingJob's failure can be classified consistently once error
    handling is implemented (later phase).
    """

    USER_ERROR = "USER_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    OCR_ERROR = "OCR_ERROR"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
