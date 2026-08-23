"""
Document upload endpoint.

See app/services/document_service.py module docstring for the important
caveat about organization_id/uploaded_by_user_id being temporary,
client-supplied, pre-authentication fields — NOT an authorization mechanism.
"""

import io
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.processing.validation import OversizedFileError, UploadValidationConfig
from app.schemas.document import DocumentUploadResponse
from app.services.document_service import UploadFileInput, upload_document
from app.storage.base import StorageService
from app.storage.local import LocalStorageService

router = APIRouter(prefix="/documents", tags=["documents"])

# Read in fixed-size chunks and abort as soon as the configured max is
# exceeded, rather than buffering an arbitrarily large upload into memory
# first and rejecting it afterward.
_READ_CHUNK_SIZE = 64 * 1024
# Only the header bytes needed for the PDF magic-byte check.
_HEADER_PEEK_SIZE = 8


def get_storage_service(settings: Settings = Depends(get_settings)) -> StorageService:
    return LocalStorageService(settings.storage_dir)


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise OversizedFileError()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentUploadResponse, status_code=201)
async def upload(
    organization_id: uuid.UUID = Form(...),
    uploaded_by_user_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
):
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await _read_capped(file, max_bytes)

    if not content:
        # 0-byte upload — let the validation pipeline produce the standard
        # EmptyFileError rather than special-casing it here.
        header = b""
    else:
        header = content[:_HEADER_PEEK_SIZE]

    upload_input = UploadFileInput(
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(content),
        header_bytes=header,
        stream=io.BytesIO(content),
    )

    validation_config = UploadValidationConfig(
        max_size_bytes=max_bytes,
        allowed_extensions=settings.allowed_upload_extensions,
    )

    document = upload_document(
        db,
        storage,
        organization_id=organization_id,
        uploaded_by_user_id=uploaded_by_user_id,
        upload=upload_input,
        validation_config=validation_config,
    )
    return document
