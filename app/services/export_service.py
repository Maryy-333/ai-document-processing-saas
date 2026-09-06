"""
CSV/Excel export for approved invoices (Phase 11).

Only APPROVED documents may be exported — enforced here, not left to the
route layer. Organization scoping reuses get_org_scoped_document() exactly
as every other endpoint since Phase 4; no new scoping mechanism.

Column order (invoice-level, then line-item) is a single source of truth
(_COLUMNS) shared by both CSV and XLSX generation, so the two formats can
never drift out of sync with each other.
"""

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidStateTransitionError
from app.models import Document, Invoice
from app.models.enums import DocumentStatus
from app.services.text_extraction_service import get_org_scoped_document

# Single source of truth for column order — CSV and XLSX both derive their
# header row and cell order from this, so they can't silently diverge.
_COLUMNS = (
    "invoice_id",
    "document_id",
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
    "approved_at",
    "approved_by_user_id",
    "line_item_description",
    "line_item_quantity",
    "line_item_unit_price",
    "line_item_amount",
)


class InvoiceNotApprovedError(InvalidStateTransitionError):
    message = "Only approved invoices can be exported."


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    media_type: str


def _safe_filename_component(value: uuid.UUID) -> str:
    """
    UUIDs are already filesystem/URL-safe (hex digits and hyphens only), but
    this function exists as the single place filenames are built from any
    value, so nothing user-controlled (e.g. vendor_name) can ever be routed
    through it by a future change without deliberately going through here.
    """
    return str(value)


def _format_value(value) -> str:
    """
    Deterministic, lossless-for-money string representation for every cell.
    None -> "" (consistent across every column, never "None" or "null").
    Decimal -> str() (exact, never routed through float).
    date/datetime -> ISO 8601 (unambiguous, sortable, stable).
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _invoice_base_row(invoice: Invoice) -> dict:
    return {
        "invoice_id": invoice.id,
        "document_id": invoice.document_id,
        "vendor_name": invoice.vendor_name,
        "vendor_address": invoice.vendor_address,
        "customer_name": invoice.customer_name,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax": invoice.tax,
        "total": invoice.total,
        "payment_terms": invoice.payment_terms,
        "approved_at": invoice.approved_at,
        "approved_by_user_id": invoice.approved_by_user_id,
    }


def flatten_invoices_to_rows(invoices: list[Invoice]) -> list[dict]:
    """
    One row per line item; invoice-level fields repeated on every row. An
    approved invoice with zero line items still produces exactly one row,
    with blank line-item fields — it is never silently omitted.
    """
    rows: list[dict] = []
    for invoice in invoices:
        base = _invoice_base_row(invoice)
        line_items = invoice.line_items  # already ordered by line_order (model relationship)

        if not line_items:
            rows.append(
                {
                    **base,
                    "line_item_description": None,
                    "line_item_quantity": None,
                    "line_item_unit_price": None,
                    "line_item_amount": None,
                }
            )
            continue

        for item in line_items:
            rows.append(
                {
                    **base,
                    "line_item_description": item.description,
                    "line_item_quantity": item.quantity,
                    "line_item_unit_price": item.unit_price,
                    "line_item_amount": item.amount,
                }
            )
    return rows


def generate_csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    # QUOTE_MINIMAL (csv module default) correctly quotes/escapes any field
    # containing a comma, quote character, or newline per RFC 4180 —
    # exactly the "commas/quotes/newlines" requirement, with no custom
    # escaping logic needed.
    writer = csv.writer(buffer)
    writer.writerow(_COLUMNS)
    for row in rows:
        writer.writerow([_format_value(row[col]) for col in _COLUMNS])
    return buffer.getvalue().encode("utf-8")


def generate_xlsx(rows: list[dict]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Invoices"

    sheet.append(list(_COLUMNS))
    for row in rows:
        # Every value written as its formatted string — Decimal is
        # deliberately NOT passed through as a native Excel number, since
        # openpyxl would otherwise store it as a binary float, which is
        # exactly the precision loss this phase's requirements forbid.
        sheet.append([_format_value(row[col]) for col in _COLUMNS])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def get_approved_invoice_for_export(
    db: Session, document_id: uuid.UUID, organization_id: uuid.UUID
) -> Invoice:
    """
    Org-scoped lookup (never by document_id alone), then requires the
    document to be APPROVED and to actually have a linked Invoice. A
    REVIEW_REQUIRED/REJECTED/UPLOADED/etc. document raises
    InvoiceNotApprovedError (409) rather than silently exporting a draft.
    """
    document = get_org_scoped_document(db, document_id, organization_id)
    if document.status != DocumentStatus.APPROVED:
        raise InvoiceNotApprovedError()

    invoice = db.query(Invoice).filter(Invoice.document_id == document.id).first()
    if invoice is None:
        # Should not happen for a genuinely APPROVED document (approval
        # requires an Invoice row to exist), but treated as the same
        # not-exportable condition rather than assumed impossible.
        raise InvoiceNotApprovedError()

    return invoice


def list_organization_approved_invoices(db: Session, organization_id: uuid.UUID) -> list[Invoice]:
    """
    All APPROVED invoices belonging to the given organization, ordered
    deterministically (approved_at ascending, then invoice_id ascending) —
    never relying on unspecified database row order.
    """
    return (
        db.query(Invoice)
        .join(Document, Invoice.document_id == Document.id)
        .filter(Document.organization_id == organization_id)
        .filter(Document.status == DocumentStatus.APPROVED)
        .order_by(Invoice.approved_at.asc(), Invoice.id.asc())
        .all()
    )


def export_single_invoice(
    db: Session, document_id: uuid.UUID, organization_id: uuid.UUID, *, fmt: str
) -> ExportFile:
    invoice = get_approved_invoice_for_export(db, document_id, organization_id)
    rows = flatten_invoices_to_rows([invoice])
    filename_id = _safe_filename_component(invoice.id)

    if fmt == "csv":
        return ExportFile(
            content=generate_csv(rows),
            filename=f"invoice-{filename_id}.csv",
            media_type="text/csv",
        )
    return ExportFile(
        content=generate_xlsx(rows),
        filename=f"invoice-{filename_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def export_organization_invoices(
    db: Session, organization_id: uuid.UUID, *, fmt: str
) -> ExportFile:
    invoices = list_organization_approved_invoices(db, organization_id)
    rows = flatten_invoices_to_rows(invoices)  # [] in, [] out — still a valid empty export

    if fmt == "csv":
        return ExportFile(
            content=generate_csv(rows),
            filename="invoices.csv",
            media_type="text/csv",
        )
    return ExportFile(
        content=generate_xlsx(rows),
        filename="invoices.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
