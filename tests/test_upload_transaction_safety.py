import io
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import DatabaseError
from app.models import Document, Organization, User
from app.processing.validation import UploadValidationConfig
from app.services.document_service import UploadFileInput, upload_document

VALID_PDF = b"%PDF-1.4\n%fake-but-signature-valid\n%%EOF"
CONFIG = UploadValidationConfig(max_size_bytes=10_000, allowed_extensions=(".pdf",))


def make_org_and_user(db_session):
    org = Organization(name="Acme")
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


def make_upload_input() -> UploadFileInput:
    return UploadFileInput(
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=len(VALID_PDF),
        header_bytes=VALID_PDF[:8],
        stream=io.BytesIO(VALID_PDF),
    )


def test_storage_succeeds_db_fails_triggers_cleanup(db_session, local_storage, monkeypatch):
    org, user = make_org_and_user(db_session)

    # Force db.commit() to fail after the file has already been written.
    original_commit = db_session.commit

    def failing_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", failing_commit)

    with pytest.raises(DatabaseError):
        upload_document(
            db_session,
            local_storage,
            organization_id=org.id,
            uploaded_by_user_id=user.id,
            upload=make_upload_input(),
            validation_config=CONFIG,
        )

    monkeypatch.setattr(db_session, "commit", original_commit)

    # No Document row should exist...
    assert db_session.query(Document).count() == 0
    # ...and the file that was written before the DB failure must have been
    # cleaned up (best-effort delete), not left orphaned.
    uploads_root = local_storage._root  # test-only introspection
    leftover_files = [
        f for f in (list(uploads_root.rglob("*")) if uploads_root.exists() else []) if f.is_file()
    ]
    assert leftover_files == [], f"orphaned files left on disk: {leftover_files}"


def test_storage_fails_no_document_row_created(db_session, local_storage):
    org, user = make_org_and_user(db_session)

    failing_storage = MagicMock()
    failing_storage.save.side_effect = OSError("simulated disk failure")
    failing_storage.delete = MagicMock()

    with pytest.raises(DatabaseError):
        upload_document(
            db_session,
            failing_storage,
            organization_id=org.id,
            uploaded_by_user_id=user.id,
            upload=make_upload_input(),
            validation_config=CONFIG,
        )

    assert db_session.query(Document).count() == 0
    # delete() must not be called for a save that never succeeded — nothing
    # to clean up, and calling it anyway would be misleading in logs.
    failing_storage.delete.assert_not_called()
