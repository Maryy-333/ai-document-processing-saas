"""
Text extraction orchestration (Phase 5).

ORGANIZATION ISOLATION: get_org_scoped_document() is the ONLY way this
module looks up a Document. It filters by (id AND organization_id) in a
single query — there is no code path here that fetches a Document by ID
alone. A document that exists but belongs to a different organization is
indistinguishable from a document that doesn't exist (both return
NotFoundError), which avoids leaking cross-organization existence.

organization_id here is the SAME temporary, client-supplied,
pre-authentication field established in Phase 4 — not an authorization
mechanism. See app/services/document_service.py's module docstring for the
full caveat; it applies identically here. Phase 12 must derive this from the
authenticated request rather than accept it as a request parameter.
"""

import io
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, NotFoundError, ProcessingError
from app.core.logging import get_logger
from app.models import Document, ProcessingJob
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.processing.pdf_extraction import (
    PdfOpenError,
    extract_text_from_pdf_bytes,
    has_meaningful_text,
)
from app.storage.base import StorageService

logger = get_logger(__name__)


def get_org_scoped_document(
    db: Session, document_id: uuid.UUID, organization_id: uuid.UUID
) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.organization_id == organization_id)
        .first()
    )
    if document is None:
        raise NotFoundError("Document not found.")
    return document


def generate_extracted_text_storage_key(organization_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """Server-generated, never derived from client input. Fixed shape per document."""
    return f"{organization_id}/{document_id}/extracted_text.txt"


def _now() -> datetime:
    return datetime.now(UTC)


def _mark_failed(
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
    job.completed_at = _now()
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


def extract_document_text(
    db: Session,
    storage: StorageService,
    *,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    min_extractable_text_chars: int,
) -> Document:
    document = get_org_scoped_document(db, document_id, organization_id)

    job = ProcessingJob(
        document_id=document.id,
        stage=ProcessingStage.TEXT_EXTRACTION,
        status=ProcessingStatus.RUNNING,
        started_at=_now(),
    )
    db.add(job)
    document.status = DocumentStatus.EXTRACTING_TEXT
    db.commit()
    db.refresh(job)
    db.refresh(document)

    started_at = job.started_at
    perf_start = time.monotonic()

    # --- Load the PDF through the existing storage abstraction ---
    try:
        pdf_bytes = storage.read(document.storage_key)
    except Exception:
        logger.error("Storage read failed for document_id=%s", document.id)
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="Stored file could not be read.",
            started_at=started_at,
        )
        raise ProcessingError("Stored file could not be read.") from None

    # --- Extract text ---
    try:
        result = extract_text_from_pdf_bytes(pdf_bytes)
    except PdfOpenError:
        logger.error("PDF open/parse failed for document_id=%s", document.id)
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="The stored file could not be read as a PDF.",
            started_at=started_at,
        )
        raise

    duration_ms = int((time.monotonic() - perf_start) * 1000)

    if has_meaningful_text(result, min_extractable_text_chars):
        storage_key = generate_extracted_text_storage_key(document.organization_id, document.id)

        try:
            storage.save(storage_key, io.BytesIO(result.combined_text.encode("utf-8")))
        except Exception:
            logger.error("Extracted-text artifact write failed for document_id=%s", document.id)
            _mark_failed(
                db,
                document,
                job,
                category=ErrorCategory.PROCESSING_ERROR,
                message="Failed to store extracted text.",
                started_at=started_at,
            )
            raise DatabaseError("Failed to store extracted text.") from None

        try:
            document.extracted_text_storage_key = storage_key
            document.status = DocumentStatus.TEXT_EXTRACTED
            job.status = ProcessingStatus.SUCCESS
            job.completed_at = _now()
            job.duration_ms = duration_ms
            db.commit()
            db.refresh(document)
        except Exception:
            db.rollback()
            logger.error(
                "DB update failed after extracted-text artifact write for document_id=%s",
                document.id,
            )
            # Best-effort cleanup of the artifact just written — do NOT
            # touch the original uploaded PDF.
            storage.delete(storage_key)
            _mark_failed(
                db,
                document,
                job,
                category=ErrorCategory.DATABASE_ERROR,
                message="Failed to save extraction result.",
                started_at=started_at,
            )
            raise DatabaseError("Failed to save extraction result.") from None
    else:
        # Extraction itself succeeded — it correctly determined there isn't
        # enough usable text. This is a successful processing attempt with
        # an OCR_REQUIRED routing outcome, not a failure.
        document.status = DocumentStatus.OCR_REQUIRED
        job.status = ProcessingStatus.SUCCESS
        job.completed_at = _now()
        job.duration_ms = duration_ms
        try:
            db.commit()
            db.refresh(document)
        except Exception:
            db.rollback()
            logger.error(
                "DB update failed while recording OCR_REQUIRED for document_id=%s", document.id
            )
            _mark_failed(
                db,
                document,
                job,
                category=ErrorCategory.DATABASE_ERROR,
                message="Failed to save extraction result.",
                started_at=started_at,
            )
            raise DatabaseError("Failed to save extraction result.") from None

    return document
