import io
import uuid

import pytest

from app.core.exceptions import NotFoundError, ProcessingError
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ProcessingStage, ProcessingStatus
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
