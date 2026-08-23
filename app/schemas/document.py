"""
API-facing schemas for Document endpoints.

Note: storage_key is intentionally NOT included here — it's an internal
server-side detail (see Phase 4 approval), not something a client needs.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import DocumentStatus


class DocumentUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    uploaded_by_user_id: UUID
    original_filename: str
    content_type: str
    file_size_bytes: int
    status: DocumentStatus
    created_at: datetime
