"""
Result validation orchestration (Phase 8).

Runs after invoice_extraction_service.py has committed a draft Invoice and
set Document.status = VALIDATING_RESULT. This module's only job is to run
the deterministic checks in app/processing/invoice_validation.py against
that draft and record the outcome — it does not call the AI provider, does
not touch StorageService, and never sets approved_at.

IMPORTANT: a "successful" validation run here does NOT mean the invoice is
correct — it means the validation STAGE completed without a system-level
failure. Whether the invoice has 0 findings or 10, the document always
transitions to REVIEW_REQUIRED, because Phase 1's workflow requires human
review before any AI output becomes trusted business data. FAILED is
reserved for genuine system/processing failures (e.g. the draft Invoice
row is unexpectedly missing, or the DB write itself fails) — never for
"the invoice looks wrong", which is exactly what ValidationResult.errors
is for.

Validation findings are NOT persisted anywhere (no new table, no new
column — see design decision in the implementation report). They are
recomputed on demand wherever needed, since validate_invoice() is a pure,
cheap, deterministic function of already-persisted data.
"""

import time

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, ProcessingError
from app.core.logging import get_logger
from app.models import Document, Invoice, ProcessingJob
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.processing.invoice_validation import extracted_invoice_from_orm, validate_invoice
from app.services.processing_helpers import mark_failed, now

logger = get_logger(__name__)


class MissingDraftInvoiceError(ProcessingError):
    """No draft Invoice exists to validate — a system-level inconsistency,
    not a content-level validation finding."""

    message = "No extracted invoice data is available to validate."


def run_result_validation(db: Session, document: Document) -> Document:
    job = ProcessingJob(
        document_id=document.id,
        stage=ProcessingStage.RESULT_VALIDATION,
        status=ProcessingStatus.RUNNING,
        started_at=now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    started_at = job.started_at
    perf_start = time.monotonic()

    invoice = db.query(Invoice).filter(Invoice.document_id == document.id).first()
    if invoice is None:
        logger.error("No draft Invoice found for document_id=%s during validation.", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="No extracted invoice data is available to validate.",
            started_at=started_at,
        )
        raise MissingDraftInvoiceError()

    try:
        extracted = extracted_invoice_from_orm(invoice)
        validate_invoice(extracted)  # result itself isn't persisted; see module docstring
    except Exception:
        logger.error("Validation logic failed unexpectedly for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="Validation failed unexpectedly.",
            started_at=started_at,
        )
        raise ProcessingError("Validation failed unexpectedly.") from None

    duration_ms = int((time.monotonic() - perf_start) * 1000)

    try:
        document.status = DocumentStatus.REVIEW_REQUIRED
        job.status = ProcessingStatus.SUCCESS
        job.completed_at = now()
        job.duration_ms = duration_ms
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        logger.error("DB update failed after validation for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.DATABASE_ERROR,
            message="Failed to save validation result.",
            started_at=started_at,
        )
        raise DatabaseError("Failed to save validation result.") from None

    return document
