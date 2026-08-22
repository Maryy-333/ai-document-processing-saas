"""
User: authentication subject and audit attribution (who edited/approved what).

Belongs to exactly one Organization (many-to-one), per the approved
correction to Phase 1 — NOT a 1:1 relationship.

Note: this model defines the schema shape only. No authentication logic
(password hashing, login, token issuance) is implemented in Phase 3 — that
is explicitly Phase 12 scope. `hashed_password` exists as a column now so
the schema doesn't need to change later.
"""

from typing import TYPE_CHECKING
from uuid import UUID as UUIDType

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    organization_id: Mapped[UUIDType] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped["Organization"] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} org_id={self.organization_id}>"
