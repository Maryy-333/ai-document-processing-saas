"""
ProcessingJob: system processing history for a Document.

Per the approved correction to Phase 1 §12/§13: this table records SYSTEM
pipeline execution — which stage ran, its status, how long it took, what
failed and why, and (later) its cost. It does NOT record user actions
(who edited which field, who clicked approve). A true user-action AuditLog
remains explicitly out of scope unless a later phase requires it — do not
overload this table to serve that purpose.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID as UUIDType

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ErrorCategory, ProcessingStage, ProcessingStatus

if TYPE_CHECKING:
    from app.models.document import Document


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "processing_jobs"

    document_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    stage: Mapped[ProcessingStage] = mapped_column(
        Enum(ProcessingStage, name="processing_stage"), nullable=False
    )
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_category: Mapped[ErrorCategory | None] = mapped_column(
        Enum(ErrorCategory, name="error_category"), nullable=True
    )
    # Safe, high-level diagnostic text only — never raw stack traces or
    # document content. Enforced by convention at the point this is written
    # (later phase), not by the schema itself.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reserved for Phase 20 cost tracking; unused until an AI provider exists.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=10, scale=6), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="processing_jobs")

    def __repr__(self) -> str:
        return f"<ProcessingJob id={self.id} stage={self.stage} status={self.status}>"
