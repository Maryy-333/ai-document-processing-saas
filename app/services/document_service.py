"""
Document upload orchestration.

IMPORTANT — TEMPORARY PRE-AUTHENTICATION IDENTITY HANDLING:
organization_id and uploaded_by_user_id are currently supplied directly by
the API caller as request fields. This is NOT an authorization mechanism —
it is a placeholder used only because authentication/authorization is not
implemented until Phase 12. Any caller can currently claim any
organization_id/uploaded_by_user_id combination that passes the consistency
checks below; nothing here proves the caller actually IS that user.

What IS enforced here (and must remain true after Phase 12):
  - organization_id must reference a real Organization
  - uploaded_by_user_id must reference a real User
  - that User must belong to that Organization
    (a user from Org B must be rejected if organization_id = Org A)

When Phase 12 lands, organization_id and uploaded_by_user_id must be DERIVED
from the authenticated request (JWT), not accepted as trusted request
fields. This entire identity-resolution step should be replaced, not
extended, at that point.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import DatabaseError, NotFoundError, UserError
from app.core.logging import get_logger
from app.models import Document, Organization, User
from app.processing.validation import UploadValidationConfig, run_upload_validation
from app.storage.base import StorageService

logger = get_logger(__name__)


class OrganizationUserMismatchError(UserError):
    message = "The specified user does not belong to the specified organization."


@dataclass(frozen=True)
class UploadFileInput:
    filename: str | None
    content_type: str | None
    size_bytes: int
    header_bytes: bytes
    stream: object  # file-like, positioned at 0; passed through to StorageService.save


def resolve_and_validate_identity(
    db: Session, organization_id: uuid.UUID, uploaded_by_user_id: uuid.UUID
) -> tuple[Organization, User]:
    """
    See module docstring: this is explicitly NOT an authorization check. It
    only confirms the supplied IDs are internally consistent with each other
    and reference real rows.
    """
    organization = db.get(Organization, organization_id)
    if organization is None:
        raise NotFoundError("organization_id does not reference an existing organization.")

    user = db.get(User, uploaded_by_user_id)
    if user is None:
        raise NotFoundError("uploaded_by_user_id does not reference an existing user.")

    if user.organization_id != organization.id:
        raise OrganizationUserMismatchError()

    return organization, user


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
    # 1. Identity consistency (not authorization — see module docstring)
    organization, user = resolve_and_validate_identity(
        db, organization_id, uploaded_by_user_id
    )

    # 2. File validation (no I/O to storage or DB yet)
    safe_filename = run_upload_validation(
        filename=upload.filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        header_bytes=upload.header_bytes,
        config=validation_config,
    )

    # 3. Generate safe server-side storage key
    storage_key = generate_storage_key(organization.id)

    # 4. Store the file
    try:
        storage.save(storage_key, upload.stream)
    except Exception as exc:
        logger.error("Storage write failed for key=%s: %s", storage_key, type(exc).__name__)
        raise DatabaseError("Failed to store the uploaded file.") from exc

    # 5. Persist the Document row; clean up the stored file if this fails so
    #    a normal success path never leaves an orphaned file behind. Note:
    #    a process crash between step 4 and this cleanup can still orphan a
    #    file — accepted Phase 4 limitation, not solved here.
    try:
        document = Document(
            organization_id=organization.id,
            uploaded_by_user_id=user.id,
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
