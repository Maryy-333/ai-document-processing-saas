"""
Shared helpers for processing-stage services (text extraction, OCR, AI
extraction). Extracted from text_extraction_service.py in Phase 7 so
invoice_extraction_service.py can reuse the exact same failure-recording
behavior rather than duplicating it — the "Document + ProcessingJob both
marked FAILED, safely, even if that commit itself fails" pattern must stay
identical across every processing stage.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError
from app.core.logging import get_logger
from app.models import Document, ProcessingJob
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStatus

logger = get_logger(__name__)


def now() -> datetime:
    return datetime.now(UTC)


def mark_failed(
    db: Session,
    document: Document,
    job: ProcessingJob,
    *,
    category: ErrorCategory,
    message: str,
    started_at: datetime,
) -> None:
    """
    Marks both the Document and its ProcessingJob as failed and commits.
    Never leaves the document in a state that implies success.
    """
    document.status = DocumentStatus.FAILED
    job.status = ProcessingStatus.FAILED
    job.completed_at = now()
    job.duration_ms = int((job.completed_at - started_at).total_seconds() * 1000)
    job.error_category = category
    job.error_message = message
    try:
        db.commit()
    except Exception:
        # If even the failure-marking commit fails, there is nothing further
        # we can safely do here beyond surfacing a DatabaseError — this is a
        # documented, accepted limitation (see Known Limitations).
        db.rollback()
        logger.error("Failed to persist FAILED status for document_id=%s", document.id)
        raise DatabaseError("Failed to record processing failure.") from None
