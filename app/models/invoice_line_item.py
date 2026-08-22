"""InvoiceLineItem: individual line items belonging to an Invoice."""

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID as UUIDType

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.invoice import Invoice

_MONEY = Numeric(precision=14, scale=2)
_QUANTITY = Numeric(precision=14, scale=4)


class InvoiceLineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Preserves source ordering from the original invoice document.
    line_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(_QUANTITY, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")

    def __repr__(self) -> str:
        return (
            f"<InvoiceLineItem id={self.id} invoice_id={self.invoice_id} "
            f"desc={self.description!r}>"
        )
