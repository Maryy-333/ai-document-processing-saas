"""
Document: an uploaded file and its processing state.

Carries organization_id directly (not nullable) — this is the field every
authorization check must filter on to enforce organization-level isolation.
Do not add any query path that reads Document without scoping by
organization_id once auth exists (Phase 12) — that would be an isolation
violation, not a valid convenience shortcut.
"""

from typing import TYPE_CHECKING
from uuid import UUID as UUIDType

from sqlalchemy import BigInteger, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentStatus

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.organization import Organization
    from app.models.processing_job import ProcessingJob
    from app.models.user import User


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    # --- Tenant isolation boundary ---
    organization_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    uploaded_by_user_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --- File metadata ---
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Key/path under the configured storage backend (StorageService), not a
    # user-controlled path — never derived directly from original_filename.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
        index=True,
    )

    # Pointer to the extracted-text artifact under StorageService — NOT the
    # text itself. The extracted text is a derived processing artifact, not
    # authoritative business data, and can be regenerated from the original
    # PDF if needed. Populated by Phase 5 text extraction; null until then.
    extracted_text_storage_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="documents")
    uploaded_by: Mapped["User"] = relationship()

    invoice: Mapped["Invoice | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ProcessingJob.created_at",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} status={self.status} org_id={self.organization_id}>"
