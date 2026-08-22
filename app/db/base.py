"""
Declarative base and shared mixins for ORM models.

Kept separate from app/db/session.py (engine/session) so that Alembic can
import metadata without pulling in engine-creation side effects, and so
models can import Base without importing the session module.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


class UUIDPrimaryKeyMixin:
    """
    UUID primary keys instead of sequential integers.

    Chosen deliberately: document/invoice IDs may be referenced in URLs,
    exports, and (eventually) external client-facing APIs. UUIDs avoid
    leaking record counts/growth rate and avoid cross-organization ID
    guessing games, which matters given the organization-isolation
    requirement — an incrementing integer ID is a bad fit for a multi-tenant
    resource identifier even before authorization is checked.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """created_at / updated_at columns, set at the database level."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
