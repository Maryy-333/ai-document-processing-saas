import io
import uuid

import pytest

from app.core.exceptions import DatabaseError, NotFoundError, ProcessingError
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.services.text_extraction_service import (
    extract_document_text,
    generate_extracted_text_storage_key,
    get_org_scoped_document,
)
from tests.fixtures.pdf_fixtures import (
    make_blank_pdf,
    make_corrupted_pdf,
    make_multipage_text_pdf,
    make_text_pdf,
)

MIN_CHARS = 20


def make_org_user(db_session):
    org = Organization(name="Acme")
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    return org, user


def make_document(db_session, org, user, storage_key):
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=storage_key,
        content_type="application/pdf",
        file_size_bytes=100,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def test_generate_extracted_text_storage_key_format():
    org_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    key = generate_extracted_text_storage_key(org_id, doc_id)
    assert key == f"{org_id}/{doc_id}/extracted_text.txt"


def test_get_org_scoped_document_returns_matching_document(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, f"{org.id}/a.pdf")

    found = get_org_scoped_document(db_session, doc.id, org.id)
    assert found.id == doc.id


def test_get_org_scoped_document_rejects_cross_org_access(db_session):
    org_a, user_a = make_org_user(db_session)
    org_b = Organization(name="Other Org")
    db_session.add(org_b)
    db_session.commit()

    doc = make_document(db_session, org_a, user_a, f"{org_a.id}/a.pdf")

    with pytest.raises(NotFoundError):
        get_org_scoped_document(db_session, doc.id, org_b.id)


def test_get_org_scoped_document_rejects_unknown_id(db_session):
    org, _ = make_org_user(db_session)
    with pytest.raises(NotFoundError):
        get_org_scoped_document(db_session, uuid.uuid4(), org.id)


def test_extraction_succeeds_and_stores_artifact(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    result = extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
    )

    assert result.status == DocumentStatus.TEXT_EXTRACTED
    assert result.extracted_text_storage_key is not None
    assert local_storage.exists(result.extracted_text_storage_key)
    stored_text = local_storage.read(result.extracted_text_storage_key).decode("utf-8")
    assert "INVOICE" in stored_text

    # Original PDF must remain untouched.
    assert local_storage.exists(key)


def test_extraction_multipage_succeeds(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/multi.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_multipage_text_pdf(3)))

    result = extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
    )
    assert result.status == DocumentStatus.TEXT_EXTRACTED


def test_extraction_routes_textless_pdf_to_ocr_required(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/blank.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_blank_pdf()))

    result = extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
    )

    assert result.status == DocumentStatus.OCR_REQUIRED
    assert result.extracted_text_storage_key is None
    # Original PDF must remain untouched.
    assert local_storage.exists(key)


def test_extraction_fails_for_corrupted_pdf(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/corrupt.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_corrupted_pdf()))

    with pytest.raises(ProcessingError):
        extract_document_text(
            db_session,
            local_storage,
            document_id=doc.id,
            organization_id=org.id,
            min_extractable_text_chars=MIN_CHARS,
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED
    assert doc.extracted_text_storage_key is None
    # Original PDF must remain untouched even on failure.
    assert local_storage.exists(key)


def test_extraction_fails_when_storage_object_missing(db_session, local_storage):
    org, user = make_org_user(db_session)
    # storage_key points at a file that was never written to local_storage.
    doc = make_document(db_session, org, user, f"{org.id}/missing.pdf")

    with pytest.raises(ProcessingError):
        extract_document_text(
            db_session,
            local_storage,
            document_id=doc.id,
            organization_id=org.id,
            min_extractable_text_chars=MIN_CHARS,
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


def test_processing_job_created_with_correct_stage_and_success_status(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
    )

    jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.stage == ProcessingStage.TEXT_EXTRACTION
    assert job.status == ProcessingStatus.SUCCESS
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.duration_ms >= 0


def test_processing_job_recorded_as_failed_on_corrupted_pdf(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/corrupt.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_corrupted_pdf()))

    with pytest.raises(ProcessingError):
        extract_document_text(
            db_session,
            local_storage,
            document_id=doc.id,
            organization_id=org.id,
            min_extractable_text_chars=MIN_CHARS,
        )

    jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
    assert len(jobs) == 1
    assert jobs[0].status == ProcessingStatus.FAILED
    assert jobs[0].error_category is not None
    assert jobs[0].error_message is not None


# ==================================================
# Phase 6: OCR continuation from extract_document_text
# ==================================================


def test_extract_document_text_continues_into_ocr_on_textless_pdf(db_session, local_storage):
    from tests.fixtures.fake_ocr_engine import FakeOCREngine
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    result = extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
        ocr_engine=FakeOCREngine("INVOICE Total Amount Due: $77.00"),
        ocr_enabled=True,
        ocr_min_text_chars=MIN_CHARS,
        ocr_dpi=150,
    )
    assert result.status == DocumentStatus.TEXT_EXTRACTED
    assert result.extracted_text_storage_key is not None


def test_extract_document_text_stops_at_ocr_required_when_disabled(db_session, local_storage):
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    result = extract_document_text(
        db_session,
        local_storage,
        document_id=doc.id,
        organization_id=org.id,
        min_extractable_text_chars=MIN_CHARS,
        ocr_engine=None,
        ocr_enabled=False,
    )
    assert result.status == DocumentStatus.OCR_REQUIRED


def test_ocr_storage_save_failure_does_not_leave_orphaned_success(db_session, local_storage):
    """StorageService.save() failure during the OCR artifact write must be
    recorded as a failure, not silently swallowed."""
    from unittest.mock import MagicMock

    from tests.fixtures.fake_ocr_engine import FakeOCREngine
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    original_bytes = make_image_only_pdf()
    local_storage.save(key, io.BytesIO(original_bytes))

    def save_that_fails_for_artifact(k, data):
        raise OSError("simulated disk full")

    failing_storage = MagicMock()
    failing_storage.read = local_storage.read
    failing_storage.exists = local_storage.exists
    failing_storage.delete = MagicMock(wraps=local_storage.delete)
    failing_storage.save = MagicMock(side_effect=save_that_fails_for_artifact)

    with pytest.raises(DatabaseError):
        extract_document_text(
            db_session,
            failing_storage,
            document_id=doc.id,
            organization_id=org.id,
            min_extractable_text_chars=MIN_CHARS,
            ocr_engine=FakeOCREngine("INVOICE Total Amount Due: $500.00"),
            ocr_enabled=True,
            ocr_min_text_chars=MIN_CHARS,
            ocr_dpi=150,
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED
    assert doc.extracted_text_storage_key is None
    # Original PDF must remain untouched.
    assert local_storage.read(key) == original_bytes


def test_ocr_db_failure_after_artifact_write_triggers_cleanup(local_storage, monkeypatch):
    """
    Uses a real standalone session (not the shared db_session fixture)
    rather than the transaction-wrapped one used elsewhere in this file.
    Reason: the db_session fixture wraps each test in one outer transaction
    via connection.begin(), under which Session.commit() only soft-commits
    (flushes within that still-open outer transaction) while Session.
    rollback() performs a REAL rollback of that entire outer transaction —
    so a rollback after multiple prior commits wipes out all of them, not
    just the most recent one. That's a property of the test harness, not of
    real production sessions (which don't run inside an externally-managed
    outer transaction), so this test uses a real session to exercise the
    actual commit/rollback/re-commit sequence _run_ocr relies on, with
    manual row cleanup afterward since nothing auto-rolls-back here.
    """
    from tests.conftest import _SessionFactory
    from tests.fixtures.fake_ocr_engine import FakeOCREngine
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    db = _SessionFactory()
    try:
        org, user = make_org_user(db)
        key = f"{org.id}/scanned.pdf"
        doc = make_document(db, org, user, key)
        doc.status = DocumentStatus.OCR_REQUIRED
        db.commit()

        expected_artifact_key = f"{org.id}/{doc.id}/extracted_text.txt"
        original_bytes = make_image_only_pdf()
        local_storage.save(key, io.BytesIO(original_bytes))

        original_commit = db.commit
        call_count = {"n": 0}

        def commit_fails_on_second_call():
            call_count["n"] += 1
            # commit #1 = OCR job created (RUNNING) + OCR_PROCESSING status
            # commit #2 = saves TEXT_EXTRACTED + storage key (fails)
            # commit #3+ = _mark_failed()'s own cleanup commit — must succeed.
            if call_count["n"] == 2:
                raise RuntimeError("simulated database failure")
            return original_commit()

        monkeypatch.setattr(db, "commit", commit_fails_on_second_call)

        from app.services.text_extraction_service import _run_ocr

        with pytest.raises(DatabaseError):
            _run_ocr(
                db,
                local_storage,
                doc,
                engine=FakeOCREngine("INVOICE Total Amount Due: $500.00"),
                min_text_chars=MIN_CHARS,
                dpi=150,
            )

        monkeypatch.setattr(db, "commit", original_commit)

        # The extracted-text artifact must have been cleaned up (best-effort).
        assert not local_storage.exists(expected_artifact_key)
        # Original PDF must remain untouched.
        assert local_storage.read(key) == original_bytes

        # The failure state must have actually persisted (real commit, not
        # just held in memory) — this is exactly what the fixture-wrapped
        # pattern couldn't reliably verify above.
        db.refresh(doc)
        assert doc.status == DocumentStatus.FAILED
    finally:
        # Manual cleanup since this session isn't wrapped in an
        # auto-rolled-back outer transaction like the shared fixture.
        db.rollback()
        db.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).delete()
        db.query(Document).filter(Document.id == doc.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
        db.close()


def test_ocr_rendering_failure_is_recorded(db_session, local_storage, monkeypatch):
    """
    A file that opens fine (passes native extraction) but fails specifically
    during page rendering isn't realistically constructible as a plain PDF
    fixture, since native extraction and OCR rendering both call the same
    fitz.open(). Tested via monkeypatch instead — an honest substitute for a
    scenario that can't be reached with a natural file.
    """
    from tests.fixtures.fake_ocr_engine import FakeOCREngine
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    from app.ocr.rendering import PdfRenderError

    def raise_render_error(pdf_bytes, dpi):
        raise PdfRenderError()

    monkeypatch.setattr(
        "app.ocr.ocr_extraction.render_pdf_pages_to_images", raise_render_error
    )

    with pytest.raises(PdfRenderError):
        extract_document_text(
            db_session,
            local_storage,
            document_id=doc.id,
            organization_id=org.id,
            min_extractable_text_chars=MIN_CHARS,
            ocr_engine=FakeOCREngine("INVOICE Total Amount Due: $500.00"),
            ocr_enabled=True,
            ocr_min_text_chars=MIN_CHARS,
            ocr_dpi=150,
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED

    jobs = (
        db_session.query(ProcessingJob)
        .filter(ProcessingJob.document_id == doc.id, ProcessingJob.stage == ProcessingStage.OCR)
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].status == ProcessingStatus.FAILED
    assert jobs[0].error_category == ErrorCategory.PROCESSING_ERROR
