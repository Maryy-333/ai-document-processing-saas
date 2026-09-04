"""
Document upload orchestration.

IDENTITY (Phase 10): organization_id and uploaded_by_user_id are supplied by
the caller as already-authenticated values — the API route derives them from
current_user (see app/api/deps.py's get_current_user), never from a
client-supplied request field. This function trusts them without
re-validating that they reference real, consistent rows, because they
necessarily do: current_user.id and current_user.organization_id come from a
User row already loaded from the database by the auth dependency.

(Prior to Phase 10, this module performed its own identity lookup/consistency
check here, since callers supplied raw client-controlled IDs. That check is
now redundant and has been removed — the authenticated User is the source of
truth.)
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError
from app.core.logging import get_logger
from app.models import Document
from app.processing.validation import UploadValidationConfig, run_upload_validation
from app.storage.base import StorageService

logger = get_logger(__name__)


@dataclass(frozen=True)
class UploadFileInput:
    filename: str | None
    content_type: str | None
    size_bytes: int
    header_bytes: bytes
    stream: object  # file-like, positioned at 0; passed through to StorageService.save


def generate_storage_key(organization_id: uuid.UUID) -> str:
    """
    Server-generated, never derived from client input (filename or
    otherwise). Format: {organization_id}/{uuid4}.pdf
    """
    return f"{organization_id}/{uuid.uuid4()}.pdf"


def upload_document(
    db: Session,
    storage: StorageService,
    *,
    organization_id: uuid.UUID,
    uploaded_by_user_id: uuid.UUID,
    upload: UploadFileInput,
    validation_config: UploadValidationConfig,
) -> Document:
    # 1. File validation (no I/O to storage or DB yet)
    safe_filename = run_upload_validation(
        filename=upload.filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        header_bytes=upload.header_bytes,
        config=validation_config,
    )

    # 2. Generate safe server-side storage key
    storage_key = generate_storage_key(organization_id)

    # 3. Store the file
    try:
        storage.save(storage_key, upload.stream)
    except Exception as exc:
        logger.error("Storage write failed for key=%s: %s", storage_key, type(exc).__name__)
        raise DatabaseError("Failed to store the uploaded file.") from exc

    # 4. Persist the Document row; clean up the stored file if this fails so
    #    a normal success path never leaves an orphaned file behind. Note:
    #    a process crash between step 3 and this cleanup can still orphan a
    #    file — accepted Phase 4 limitation, not solved here.
    try:
        document = Document(
            organization_id=organization_id,
            uploaded_by_user_id=uploaded_by_user_id,
            original_filename=safe_filename,
            storage_key=storage_key,
            content_type=upload.content_type or "application/octet-stream",
            file_size_bytes=upload.size_bytes,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
    except Exception as exc:
        db.rollback()
        logger.error(
            "Document persistence failed after storage write for key=%s: %s",
            storage_key,
            type(exc).__name__,
        )
        storage.delete(storage_key)  # best-effort; StorageService never raises here
        raise DatabaseError("Failed to save the document record.") from exc

    return document
