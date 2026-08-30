"""
Invoice structured-data extraction orchestration (Phase 7).

Reads the extracted-text artifact for a document (already produced by
text_extraction_service.py, via native extraction or OCR), sends it to the
configured AIProvider, validates the structured response, and persists it
as an UNAPPROVED DRAFT into the existing Invoice/InvoiceLineItem tables.

approved_at is NEVER set here — it stays NULL, which is what makes this
"not yet trusted business data" per Phase 1 §3's workflow rule. This reuses
Invoice exactly as its own docstring already described back in Phase 3
("a row can exist here as a draft under review before approval"); Phase 7
does not introduce any new persistence mechanism.

SCOPE BOUNDARY: this module does basic schema/type parsing only (via
Pydantic). It does NOT perform semantic/business validation — it does not
check subtotal+tax≈total, does not flag contradictions, does not compute a
confidence score. If the AI returns internally-inconsistent numbers that
still conform to the schema, they are stored as-is. That judgment is
explicitly Phase 8's job.

COST CONTROL: if a draft Invoice already exists for this document, the AI
provider is NOT called again — the existing draft is returned unchanged.
Re-running the pipeline on an already-processed document must never trigger
a second paid API call.
"""

import time

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, ProcessingError
from app.core.logging import get_logger
from app.models import Document, Invoice, InvoiceLineItem, ProcessingJob
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.processing.ai.provider import (
    AIProvider,
    AIProviderAuthError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.schemas.invoice_extraction import ExtractedInvoice
from app.services.invoice_validation_service import run_result_validation
from app.services.processing_helpers import mark_failed, now
from app.storage.base import StorageService

logger = get_logger(__name__)


class EmptyExtractedTextError(ProcessingError):
    """There is no usable extracted-text artifact to send to the AI provider."""

    message = "No extracted text is available for AI extraction."


def extract_invoice_from_document(
    db: Session,
    storage: StorageService,
    document: Document,
    *,
    provider: AIProvider,
) -> Document:
    """
    Runs AI structured extraction for a document already at TEXT_EXTRACTED.
    Mirrors the ProcessingJob lifecycle and transaction/failure pattern
    established by text_extraction_service.py's native-extraction and OCR
    paths (same shape, different stage).
    """
    # --- Cost control: never re-call the provider for a document that
    # already has a draft Invoice. If a prior run got as far as creating
    # the draft but never completed validation (e.g. crashed in between),
    # resume from validation rather than silently leaving it stuck. ---
    existing_invoice = db.query(Invoice).filter(Invoice.document_id == document.id).first()
    if existing_invoice is not None:
        logger.info(
            "Skipping AI extraction for document_id=%s — draft Invoice already exists.",
            document.id,
        )
        if document.status == DocumentStatus.VALIDATING_RESULT:
            return run_result_validation(db, document)
        return document

    job = ProcessingJob(
        document_id=document.id,
        stage=ProcessingStage.AI_EXTRACTION,
        status=ProcessingStatus.RUNNING,
        started_at=now(),
    )
    db.add(job)
    document.status = DocumentStatus.AI_PROCESSING
    db.commit()
    db.refresh(job)
    db.refresh(document)

    started_at = job.started_at
    perf_start = time.monotonic()

    # --- Load the extracted-text artifact through the existing storage
    # abstraction — never a direct filesystem path. ---
    if not document.extracted_text_storage_key:
        logger.error("No extracted_text_storage_key set for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="No extracted text is available for this document.",
            started_at=started_at,
        )
        raise EmptyExtractedTextError()

    try:
        text_bytes = storage.read(document.extracted_text_storage_key)
        document_text = text_bytes.decode("utf-8")
    except Exception:
        logger.error("Failed to read extracted text for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="Stored extracted text could not be read.",
            started_at=started_at,
        )
        raise ProcessingError("Stored extracted text could not be read.") from None

    if not document_text.strip():
        logger.error("Extracted text is empty for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.PROCESSING_ERROR,
            message="No extracted text is available for this document.",
            started_at=started_at,
        )
        raise EmptyExtractedTextError()

    # --- Call the AI provider ---
    try:
        raw_result = provider.extract_invoice_fields(document_text)
    except AIProviderTimeoutError:
        logger.error("AI provider timed out for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.AI_PROVIDER_ERROR,
            message="AI extraction timed out.",
            started_at=started_at,
        )
        raise
    except AIProviderAuthError:
        logger.error("AI provider auth/config failure for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.AI_PROVIDER_ERROR,
            message="AI provider is not configured correctly.",
            started_at=started_at,
        )
        raise
    except AIProviderResponseError:
        logger.error("AI provider returned an unusable response for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.AI_PROVIDER_ERROR,
            message="AI provider returned an unusable response.",
            started_at=started_at,
        )
        raise
    except Exception:
        # Any other provider-adapter failure that wasn't translated into one
        # of the specific exceptions above — never let a raw SDK exception
        # or its message (which could contain request/response details)
        # propagate further.
        logger.error("Unexpected AI provider failure for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.AI_PROVIDER_ERROR,
            message="AI extraction failed.",
            started_at=started_at,
        )
        raise ProcessingError("AI extraction failed.") from None

    # --- Basic schema/type parsing only (Pydantic). No semantic validation
    # — see module docstring. ---
    try:
        extracted = ExtractedInvoice.model_validate(raw_result)
    except PydanticValidationError:
        logger.error("AI response failed schema validation for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.AI_PROVIDER_ERROR,
            message="AI provider returned data that did not match the expected schema.",
            started_at=started_at,
        )
        raise AIProviderResponseError(
            "AI provider returned data that did not match the expected schema."
        ) from None

    duration_ms = int((time.monotonic() - perf_start) * 1000)

    # --- Persist as an unapproved draft (approved_at stays NULL) ---
    try:
        invoice = Invoice(
            document_id=document.id,
            vendor_name=extracted.vendor_name,
            vendor_address=extracted.vendor_address,
            customer_name=extracted.customer_name,
            invoice_number=extracted.invoice_number,
            invoice_date=extracted.invoice_date,
            due_date=extracted.due_date,
            currency=extracted.currency,
            subtotal=extracted.subtotal,
            tax=extracted.tax,
            total=extracted.total,
            payment_terms=extracted.payment_terms,
            # approved_at / approved_by_user_id intentionally left unset
            # (NULL) — this is a draft, not trusted business data.
        )
        db.add(invoice)
        db.flush()  # assign invoice.id before creating line items

        for index, line in enumerate(extracted.line_items):
            db.add(
                InvoiceLineItem(
                    invoice_id=invoice.id,
                    line_order=index,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    amount=line.amount,
                )
            )

        document.status = DocumentStatus.VALIDATING_RESULT
        job.status = ProcessingStatus.SUCCESS
        job.completed_at = now()
        job.duration_ms = duration_ms
        job.provider_name = "anthropic"
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        logger.error("DB persistence failed after AI extraction for document_id=%s", document.id)
        mark_failed(
            db,
            document,
            job,
            category=ErrorCategory.DATABASE_ERROR,
            message="Failed to save extraction result.",
            started_at=started_at,
        )
        raise DatabaseError("Failed to save extraction result.") from None

    # Phase 8: continue straight into result validation within the same
    # call, mirroring the Phase 6/7 chaining pattern (native → OCR → AI →
    # validation, all in one request where possible). A failure here
    # propagates as-is; run_result_validation is responsible for its own
    # ProcessingJob/document-status failure handling.
    return run_result_validation(db, document)
