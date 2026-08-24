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


class DocumentProcessingResponse(BaseModel):
    """
    Response for processing-stage endpoints (e.g. extract-text). Deliberately
    does not include extracted_text_storage_key or storage_key — those are
    internal server-side pointers, not client-facing data, consistent with
    the Phase 4 decision to keep storage_key out of API responses.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    status: DocumentStatus
    updated_at: datetime


class ExtractTextRequest(BaseModel):
    """
    organization_id here is the SAME temporary, client-supplied,
    pre-authentication field established in Phase 4 — NOT an authorization
    mechanism. See app/services/text_extraction_service.py module docstring.
    """

    organization_id: UUID
