"""
Pydantic schema for AI-extracted invoice structured output.

This is the CONTRACT between the AI provider's structured/tool-use response
and the rest of the system — the AIProvider abstraction (provider.py) must
return data that validates against this schema. It is deliberately shaped to
match app/models/invoice.py's Invoice/InvoiceLineItem field set exactly,
since Phase 7 persists into that table as an unapproved draft (approved_at
stays NULL — see invoice_extraction_service.py).

All fields are optional/nullable by design: the AI must never invent a value
it cannot support from the source text (Phase 1 §4/§11, restated for Phase 7
in the AI prompt itself — see prompts.py). Phase 7 does NOT judge whether the
values are internally consistent (e.g. subtotal + tax == total) — that is
explicitly Phase 8's job.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ExtractedLineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class ExtractedInvoice(BaseModel):
    """
    The structured result of AI extraction for a single document. Basic
    schema/type parsing only (Pydantic validation) — no semantic/business
    validation (subtotal+tax≈total, contradiction detection, etc.), which is
    explicitly Phase 8 scope.
    """

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
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
