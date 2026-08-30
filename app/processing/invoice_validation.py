"""
Deterministic invoice validation (Phase 8).

This module is intentionally pure: it operates only on ExtractedInvoice /
ExtractedLineItem (Phase 7's Pydantic schema), never touches the database,
and never touches the AI provider. This is what makes it independently
testable and deterministic, per the Phase 8 requirements.

SCOPE: this checks structural completeness and deterministic arithmetic
consistency only. It never modifies the invoice data it's given — findings
are reported, not "fixed". Approval/trust decisions are explicitly out of
scope; every document that reaches this stage still requires human review
(Phase 9) regardless of how many/few findings are produced here — see
invoice_validation_service.py, which always transitions to REVIEW_REQUIRED
after a validation run completes without a system-level failure.
"""

import enum
from decimal import Decimal

from pydantic import BaseModel

from app.models import Invoice
from app.schemas.invoice_extraction import ExtractedInvoice, ExtractedLineItem

# Deterministic tolerance for comparing money values that should be equal
# in principle (e.g. subtotal + tax vs total) but may differ by a cent or
# two due to normal rounding in the source document. One cent matches the
# project's existing money precision convention (Numeric(precision=14,
# scale=2) on Invoice/InvoiceLineItem, established in Phase 3) — anything
# larger risks masking a genuine mismatch; anything smaller would flag
# ordinary rounding as an error.
ARITHMETIC_TOLERANCE = Decimal("0.01")


class ValidationSeverity(str, enum.Enum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationFinding(BaseModel):
    field: str
    code: str
    severity: ValidationSeverity
    message: str


class ValidationResult(BaseModel):
    is_valid: bool
    requires_review: bool
    errors: list[ValidationFinding]
    warnings: list[ValidationFinding]


_TOP_LEVEL_FIELDS = (
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


def _check_missing_fields(invoice: ExtractedInvoice) -> list[ValidationFinding]:
    findings = []
    for field_name in _TOP_LEVEL_FIELDS:
        if getattr(invoice, field_name) is None:
            findings.append(
                ValidationFinding(
                    field=field_name,
                    code="FIELD_MISSING",
                    severity=ValidationSeverity.WARNING,
                    message=f"{field_name} was not found in the extracted document.",
                )
            )
    if not invoice.line_items:
        findings.append(
            ValidationFinding(
                field="line_items",
                code="NO_LINE_ITEMS",
                severity=ValidationSeverity.WARNING,
                message="No line items were extracted from the document.",
            )
        )
    return findings


def _check_total_consistency(invoice: ExtractedInvoice) -> list[ValidationFinding]:
    if invoice.subtotal is None or invoice.tax is None or invoice.total is None:
        # Cannot evaluate without all three values — the missing value(s)
        # already produced their own FIELD_MISSING warning above. This is
        # not itself an additional error.
        return []

    expected_total = invoice.subtotal + invoice.tax
    if abs(expected_total - invoice.total) > ARITHMETIC_TOLERANCE:
        return [
            ValidationFinding(
                field="total",
                code="TOTAL_MISMATCH",
                severity=ValidationSeverity.ERROR,
                message=(
                    f"subtotal ({invoice.subtotal}) + tax ({invoice.tax}) = "
                    f"{expected_total}, which does not match total ({invoice.total})."
                ),
            )
        ]
    return []


def _check_line_item(index: int, item: ExtractedLineItem) -> list[ValidationFinding]:
    if item.quantity is None or item.unit_price is None or item.amount is None:
        # Cannot evaluate arithmetic without all three values. Deliberately
        # not flagged as its own finding — a line item legitimately may not
        # carry a unit price (e.g. "Shipping — included"), and per-field
        # noise here would overwhelm a reviewer without adding much value
        # beyond what the arithmetic check itself already conveys when data
        # IS present.
        return []

    expected_amount = item.quantity * item.unit_price
    if abs(expected_amount - item.amount) > ARITHMETIC_TOLERANCE:
        return [
            ValidationFinding(
                field=f"line_items[{index}].amount",
                code="LINE_ITEM_AMOUNT_MISMATCH",
                severity=ValidationSeverity.ERROR,
                message=(
                    f"line_items[{index}]: quantity ({item.quantity}) × unit_price "
                    f"({item.unit_price}) = {expected_amount}, which does not match "
                    f"amount ({item.amount})."
                ),
            )
        ]
    return []


def _check_line_items(invoice: ExtractedInvoice) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for index, item in enumerate(invoice.line_items):
        findings.extend(_check_line_item(index, item))
    return findings


def validate_invoice(invoice: ExtractedInvoice) -> ValidationResult:
    """
    Runs every deterministic check and returns a single ValidationResult.
    Never raises for content-level findings — those are reported as data,
    not exceptions. Never mutates `invoice`.
    """
    warnings = _check_missing_fields(invoice)
    errors = _check_total_consistency(invoice) + _check_line_items(invoice)

    return ValidationResult(
        is_valid=len(errors) == 0,
        requires_review=bool(errors or warnings),
        errors=errors,
        warnings=warnings,
    )


def extracted_invoice_from_orm(invoice: Invoice) -> ExtractedInvoice:
    """
    Reconstructs the Phase 7 Pydantic schema from the persisted Invoice ORM
    row, so validate_invoice() never needs to know about SQLAlchemy. Line
    items are read in their stored order (InvoiceLineItem.line_order).
    """
    return ExtractedInvoice(
        vendor_name=invoice.vendor_name,
        vendor_address=invoice.vendor_address,
        customer_name=invoice.customer_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        subtotal=invoice.subtotal,
        tax=invoice.tax,
        total=invoice.total,
        payment_terms=invoice.payment_terms,
        line_items=[
            ExtractedLineItem(
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                amount=li.amount,
            )
            for li in sorted(invoice.line_items, key=lambda li: li.line_order)
        ],
    )
