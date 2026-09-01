"""
Human review, editing, approval, and rejection orchestration (Phase 9).

Reuses rather than duplicates:
- get_org_scoped_document() (text_extraction_service.py) for org isolation
- resolve_and_validate_identity() (document_service.py) for the temporary
  pre-authentication identity pattern used everywhere else in this project
- validate_invoice() / extracted_invoice_from_orm() (Phase 8) for
  deterministic validation — re-run after edits and immediately before
  approval, never duplicated

STATE MACHINE: only REVIEW_REQUIRED -> APPROVED and REVIEW_REQUIRED ->
REJECTED are valid transitions. Editing is only permitted while
REVIEW_REQUIRED. Any other starting state raises InvalidStateTransitionError
(409) rather than silently succeeding or degrading.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, InvalidStateTransitionError
from app.core.logging import get_logger
from app.models import Document, Invoice, InvoiceLineItem
from app.models.enums import DocumentStatus
from app.processing.invoice_validation import (
    ValidationResult,
    extracted_invoice_from_orm,
    validate_invoice,
)
from app.services.document_service import resolve_and_validate_identity
from app.services.invoice_validation_service import MissingDraftInvoiceError
from app.services.text_extraction_service import get_org_scoped_document

logger = get_logger(__name__)


class ApprovalBlockedByValidationError(InvalidStateTransitionError):
    """
    Approval was attempted but the current (re-run) validation has ERROR
    findings. Carries the ValidationResult so the caller can see exactly
    what's blocking approval — routes should catch this specifically to
    include `validation` in the response body, not just the generic
    {"error": ...} shape.
    """

    message = "Cannot approve: validation found blocking errors."

    def __init__(self, validation_result: ValidationResult) -> None:
        super().__init__()
        self.validation_result = validation_result


def _now() -> datetime:
    return datetime.now(UTC)


def _get_invoice_or_raise(db: Session, document: Document) -> Invoice:
    invoice = db.query(Invoice).filter(Invoice.document_id == document.id).first()
    if invoice is None:
        raise MissingDraftInvoiceError()
    return invoice


def get_review(
    db: Session, document_id: uuid.UUID, organization_id: uuid.UUID
) -> tuple[Document, Invoice, ValidationResult]:
    document = get_org_scoped_document(db, document_id, organization_id)
    invoice = _get_invoice_or_raise(db, document)
    result = validate_invoice(extracted_invoice_from_orm(invoice))
    return document, invoice, result


def update_invoice(
    db: Session,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    edited_by_user_id: uuid.UUID,
    updates: dict,
    fields_set: set[str],
) -> tuple[Document, Invoice, ValidationResult]:
    """
    `updates` is the raw field->value mapping (e.g. request.model_dump());
    `fields_set` is request.model_fields_set — only keys present there are
    applied, giving correct partial-update (PATCH) semantics: an omitted
    field is left untouched, an explicit null clears it. Line items, if
    present in fields_set, fully replace the existing collection.
    """
    document = get_org_scoped_document(db, document_id, organization_id)
    # Confirms edited_by_user_id is a real user belonging to this
    # organization — same pre-auth identity pattern as everywhere else,
    # NOT an authorization check (see document_service.py docstring).
    resolve_and_validate_identity(db, organization_id, edited_by_user_id)

    if document.status != DocumentStatus.REVIEW_REQUIRED:
        raise InvalidStateTransitionError(
            f"Cannot edit invoice: document status is {document.status.value}, "
            "not REVIEW_REQUIRED."
        )

    invoice = _get_invoice_or_raise(db, document)

    scalar_fields = (
        "vendor_name",
        "vendor_address",
        "customer_name",
        "invoice_number",
        "invoice_date",
        "due_date",
        "currency",
        "subtotal",
        "tax",
        "total",
        "payment_terms",
    )

    try:
        for field_name in scalar_fields:
            if field_name in fields_set:
                setattr(invoice, field_name, updates[field_name])

        if "line_items" in fields_set:
            # Full-collection replacement: delete existing rows, insert the
            # new set. cascade="all, delete-orphan" on Invoice.line_items
            # means clearing the relationship deletes the old rows.
            invoice.line_items.clear()
            db.flush()
            new_items = updates["line_items"] or []
            for index, item in enumerate(new_items):
                db.add(
                    InvoiceLineItem(
                        invoice_id=invoice.id,
                        line_order=index,
                        description=item["description"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        amount=item["amount"],
                    )
                )

        db.commit()
        db.refresh(invoice)
    except Exception:
        db.rollback()
        logger.error("Invoice edit failed for document_id=%s", document.id)
        raise DatabaseError("Failed to save invoice edits.") from None

    result = validate_invoice(extracted_invoice_from_orm(invoice))
    return document, invoice, result


def approve_document(
    db: Session,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    approved_by_user_id: uuid.UUID,
) -> Document:
    document = get_org_scoped_document(db, document_id, organization_id)
    resolve_and_validate_identity(db, organization_id, approved_by_user_id)

    if document.status != DocumentStatus.REVIEW_REQUIRED:
        raise InvalidStateTransitionError(
            f"Cannot approve: document status is {document.status.value}, "
            "not REVIEW_REQUIRED."
        )

    invoice = _get_invoice_or_raise(db, document)

    # Re-run validation immediately before approval — the invoice may have
    # been edited since REVIEW_REQUIRED was first reached, and this is the
    # authoritative gate, not whatever validation ran earlier.
    result = validate_invoice(extracted_invoice_from_orm(invoice))
    if result.errors:
        raise ApprovalBlockedByValidationError(result)

    try:
        invoice.approved_at = _now()
        invoice.approved_by_user_id = approved_by_user_id
        document.status = DocumentStatus.APPROVED
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        logger.error("Approval failed for document_id=%s", document.id)
        raise DatabaseError("Failed to save approval.") from None

    return document


def reject_document(
    db: Session,
    document_id: uuid.UUID,
    organization_id: uuid.UUID,
    rejected_by_user_id: uuid.UUID,
    reason: str,
) -> Document:
    document = get_org_scoped_document(db, document_id, organization_id)
    resolve_and_validate_identity(db, organization_id, rejected_by_user_id)

    if document.status != DocumentStatus.REVIEW_REQUIRED:
        raise InvalidStateTransitionError(
            f"Cannot reject: document status is {document.status.value}, "
            "not REVIEW_REQUIRED."
        )

    invoice = _get_invoice_or_raise(db, document)

    try:
        invoice.rejected_at = _now()
        invoice.rejected_by_user_id = rejected_by_user_id
        invoice.rejection_reason = reason
        document.status = DocumentStatus.REJECTED
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        logger.error("Rejection failed for document_id=%s", document.id)
        raise DatabaseError("Failed to save rejection.") from None

    return document
