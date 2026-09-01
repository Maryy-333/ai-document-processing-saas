"""
Schemas for Phase 9 human review, editing, approval, and rejection.

Line items reuse app.schemas.invoice_extraction.ExtractedLineItem directly
(same optional-field shape already established in Phase 7) rather than
defining a parallel line-item schema.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DocumentStatus
from app.processing.invoice_validation import ValidationResult
from app.schemas.invoice_extraction import ExtractedLineItem


class InvoiceLineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_order: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    vendor_name: str | None
    vendor_address: str | None
    customer_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    payment_terms: str | None
    approved_at: datetime | None
    approved_by_user_id: UUID | None
    rejected_at: datetime | None
    rejected_by_user_id: UUID | None
    rejection_reason: str | None
    line_items: list[InvoiceLineItemResponse]


class InvoiceReviewResponse(BaseModel):
    """Response for GET /documents/{id}/review."""

    document_id: UUID
    organization_id: UUID
    status: DocumentStatus
    invoice: InvoiceResponse
    validation: ValidationResult


class InvoiceUpdateRequest(BaseModel):
    """
    Request for PATCH /documents/{id}/invoice.

    organization_id / edited_by_user_id are the same temporary,
    client-supplied, pre-authentication identity fields used everywhere
    else in this project (see app/services/document_service.py) — NOT an
    authorization mechanism.

    True partial-update (PATCH) semantics: a field the caller does not
    include in the request body is left untouched. A field explicitly sent
    as `null` clears it. This is why every field defaults to None but the
    service layer checks `model_fields_set` rather than truthiness — see
    review_service.py.
    """

    organization_id: UUID
    edited_by_user_id: UUID

    vendor_name: str | None = None
    vendor_address: str | None = None
    customer_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    payment_terms: str | None = None

    # Full-collection replacement, not a diff/patch of individual items —
    # matches "replacement of the complete line-item collection" in scope.
    # Omit this field entirely to leave existing line items untouched;
    # include it (even as []) to replace them.
    line_items: list[ExtractedLineItem] | None = None


class ApproveRequest(BaseModel):
    organization_id: UUID
    approved_by_user_id: UUID


class RejectRequest(BaseModel):
    organization_id: UUID
    rejected_by_user_id: UUID
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank.")
        return value
