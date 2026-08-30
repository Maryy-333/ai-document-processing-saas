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

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, NotFoundError, ProcessingError
from app.core.logging import get_logger
from app.models import Document, ProcessingJob
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.ocr.engine import OCREngine, OCREngineError
from app.ocr.ocr_extraction import extract_text_from_pdf_with_ocr
from app.ocr.rendering import PdfRenderError
from app.processing.ai.provider import AIProvider
from app.processing.pdf_extraction import (
    PdfOpenError,
    extract_text_from_pdf_bytes,
    has_meaningful_text,
)
from app.services.invoice_extraction_service import extract_invoice_from_document
from app.services.processing_helpers import mark_failed as _mark_failed
from app.services.processing_helpers import now as _now
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


def extract_document_text(
    db: Session,
    storage: StorageService,
    *,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    min_extractable_text_chars: int,
    ocr_engine: OCREngine | None = None,
    ocr_enabled: bool = True,
    ocr_min_text_chars: int = 20,
    ocr_dpi: int = 200,
    ai_provider: AIProvider | None = None,
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

        # Phase 6: continue straight into OCR within the same request/call,
        # so the existing extract-text endpoint takes the document all the
        # way to TEXT_EXTRACTED/FAILED rather than requiring a second call.
        # Preserves exact Phase 5 behavior (stop at OCR_REQUIRED) when OCR is
        # disabled or no engine was injected.
        if ocr_enabled and ocr_engine is not None:
            document = _run_ocr(
                db,
                storage,
                document,
                engine=ocr_engine,
                min_text_chars=ocr_min_text_chars,
                dpi=ocr_dpi,
            )

    # Phase 7: continue into AI extraction once text is available, via
    # either path above (native or OCR) — single continuation point so the
    # "should we proceed to AI" decision isn't duplicated in two branches.
    # Preserves exact Phase 5/6 behavior (stop at TEXT_EXTRACTED) when no
    # provider was injected (e.g. AI_API_KEY not configured).
    if document.status == DocumentStatus.TEXT_EXTRACTED and ai_provider is not None:
        document = extract_invoice_from_document(db, storage, document, provider=ai_provider)

    return document


def _run_ocr(
    db: Session,
    storage: StorageService,
    document: Document,
    *,
    engine: OCREngine,
    min_text_chars: int,
    dpi: int,
) -> Document:
    """
    Runs the OCR fallback for a document already at OCR_REQUIRED. Mirrors the
    structure of the native-extraction path in extract_document_text() above
    (same ProcessingJob lifecycle, same transaction/cleanup pattern).
    """
    job = ProcessingJob(
        document_id=document.id,
        stage=ProcessingStage.OCR,
        status=ProcessingStatus.RUNNING,
        started_at=_now(),
    )
    db.add(job)
    document.status = DocumentStatus.OCR_PROCESSING
    db.commit()
    db.refresh(job)
    db.refresh(document)

    started_at = job.started_at
    perf_start = time.monotonic()

    try:
        pdf_bytes = storage.read(document.storage_key)
    except Exception:
        logger.error("Storage read failed during OCR for document_id=%s", document.id)
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="Stored file could not be read.",
            started_at=started_at,
        )
        raise ProcessingError("Stored file could not be read.") from None

    try:
        result = extract_text_from_pdf_with_ocr(pdf_bytes, engine, dpi)
    except PdfRenderError:
        logger.error("PDF rendering failed during OCR for document_id=%s", document.id)
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="The stored file could not be rendered for OCR.",
            started_at=started_at,
        )
        raise
    except OCREngineError:
        logger.error("OCR engine failed for document_id=%s", document.id)
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.OCR_ERROR,
            message="OCR processing failed.",
            started_at=started_at,
        )
        raise

    duration_ms = int((time.monotonic() - perf_start) * 1000)

    if has_meaningful_text(result, min_text_chars):
        storage_key = generate_extracted_text_storage_key(document.organization_id, document.id)

        try:
            storage.save(storage_key, io.BytesIO(result.combined_text.encode("utf-8")))
        except Exception:
            logger.error("OCR extracted-text artifact write failed for document_id=%s", document.id)
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
                "DB update failed after OCR artifact write for document_id=%s", document.id
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
        # OCR ran successfully but found no usable text. PROVISIONAL,
        # explicitly flagged pending confirmation (see implementation
        # report): error_category=PROCESSING_ERROR rather than OCR_ERROR,
        # since the OCR engine itself did not fail — it correctly processed
        # the document and found nothing. OCR_ERROR is reserved for genuine
        # engine/inference failures (the except OCREngineError branch
        # above). job.status=FAILED because ProcessingStatus has no
        # "succeeded but produced nothing usable" value, and
        # document.status=FAILED because there is no further pipeline state
        # to route to — OCR is the last fallback in this state machine.
        _mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="OCR completed but no usable text was found.",
            started_at=started_at,
        )
        raise ProcessingError("OCR completed but no usable text was found.")

    return document
