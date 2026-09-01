"""
Invoice: the structured, human-approved extraction result for a Document.

All extracted fields are nullable by design — per Phase 1 §4/§11, missing or
unconfidently-extracted data must be represented as null, never invented.
Schema-level validation of AI output (Pydantic) happens before a row is
written here; this table stores the outcome, not raw AI output.

`approved_at`/`approved_by_user_id` being non-null is what makes this data
"trusted business data" per the workflow rule in Phase 1 §3 — a row can
exist here (as a draft under review) before approval.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID as UUIDType

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.invoice_line_item import InvoiceLineItem
    from app.models.user import User

# Money fields: fixed precision, never float, to avoid rounding drift on
# financial data and to make the subtotal + tax ≈ total check meaningful.
_MONEY = Numeric(precision=14, scale=2)


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    document_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # enforces the 1:1 relationship with Document
    )

    # --- Extracted fields (all nullable — see module docstring) ---
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_address: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    subtotal: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    tax: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    total: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Review/approval state ---
    # Kept as its own explicit column rather than reusing updated_at, since
    # "last edited" and "approved" are different events.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[UUIDType | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # --- Review/rejection state (Phase 9) ---
    # Mirrors approved_at/approved_by_user_id exactly, for the reject path.
    # Deliberately NOT a generic "reviewed_*" pair — approve and reject are
    # mutually exclusive terminal outcomes, and separate explicit columns
    # make it unambiguous which happened without needing a third status
    # field on this table (Document.status is the source of truth for
    # which path was taken).
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_user_id: Mapped[UUIDType | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="invoice")
    approved_by: Mapped["User | None"] = relationship(foreign_keys=[approved_by_user_id])
    rejected_by: Mapped["User | None"] = relationship(foreign_keys=[rejected_by_user_id])
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_order",
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} document_id={self.document_id} "
            f"number={self.invoice_number!r}>"
        )
