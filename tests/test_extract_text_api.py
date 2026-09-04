import io
import uuid

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus
from app.storage.base import StorageService
from tests.fixtures.auth import auth_headers
from tests.fixtures.pdf_fixtures import make_blank_pdf, make_corrupted_pdf, make_text_pdf


def make_org_user(db_session, org_name="Acme", email="user@example.com"):
    org = Organization(name=org_name)
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
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


_NO_OVERRIDE = object()


def client_with_overrides(
    db_session, storage: StorageService, ocr_engine=_NO_OVERRIDE, ai_provider=None
) -> TestClient:
    from app.api.v1.documents import get_ai_provider, get_ocr_engine, get_storage_service

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_storage_service] = lambda: storage
    if ocr_engine is not _NO_OVERRIDE:
        app.dependency_overrides[get_ocr_engine] = lambda: ocr_engine
    # ALWAYS overridden (never falls through to the real dependency), and
    # defaults to None (AI extraction disabled). This file's tests predate
    # the AI provider (Phase 7) and were written to assert behavior that
    # stops at TEXT_EXTRACTED/OCR_REQUIRED-continuation. Without this
    # override, a real ANTHROPIC_API_KEY present in the developer's local
    # .env would cause these tests to make a real network call and fail
    # non-deterministically depending on account credits — the test suite
    # must never depend on real external AI APIs regardless of local
    # environment configuration.
    app.dependency_overrides[get_ai_provider] = lambda: ai_provider
    return TestClient(app)


def teardown_overrides():
    app.dependency_overrides.clear()


def test_extract_text_success_returns_text_extracted(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "TEXT_EXTRACTED"
        assert body["id"] == str(doc.id)
        assert "extracted_text_storage_key" not in body
        assert "storage_key" not in body
    finally:
        teardown_overrides()


def test_extract_text_without_auth_rejected(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(f"/api/v1/documents/{doc.id}/extract-text")
        assert response.status_code == 401
        db_session.refresh(doc)
        assert doc.status == DocumentStatus.UPLOADED
    finally:
        teardown_overrides()


def test_extract_text_textless_pdf_returns_ocr_required(db_session, local_storage):
    """
    Phase 5 behavior, preserved in isolation: native extraction correctly
    identifies textless/scanned content and routes to OCR_REQUIRED. OCR is
    explicitly disabled (ocr_engine=None) for this test so it verifies only
    the native-extraction detection step, not Phase 6's continuation into
    OCR — that continuation is covered separately in test_ocr_pipeline_api.py.
    """
    org, user = make_org_user(db_session)
    key = f"{org.id}/blank.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_blank_pdf()))

    client = client_with_overrides(db_session, local_storage, ocr_engine=None)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "OCR_REQUIRED"
    finally:
        teardown_overrides()


def test_extract_text_corrupted_pdf_fails_safely(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/corrupt.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_corrupted_pdf()))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert response.status_code >= 400
        body = response.json()
        assert set(body.keys()) == {"error"}
        assert "Traceback" not in body["error"]

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.FAILED
    finally:
        teardown_overrides()


def test_extract_text_missing_document_returns_404(db_session, local_storage):
    _, user = make_org_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{uuid.uuid4()}/extract-text",
            headers=auth_headers(user.id),
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_extract_text_cross_org_access_returns_404(db_session, local_storage):
    """
    Phase 10: a document must not be extractable by a user authenticated
    into a different organization than the one the document belongs to.
    Replaces the old client-supplied-organization_id mismatch test — there
    is no such field anymore; the equivalent (and now stronger) test
    authenticates as a genuinely different org's real user.
    """
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    _, user_b = make_org_user(db_session, "Org B", "b@example.com")
    key = f"{org_a.id}/doc.pdf"
    doc = make_document(db_session, org_a, user_a, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user_b.id),
        )
        assert response.status_code == 404

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.UPLOADED
    finally:
        teardown_overrides()


def test_extract_text_creates_processing_job(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
        assert len(jobs) == 1
    finally:
        teardown_overrides()


def test_extract_text_persists_storage_pointer_in_db(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        db_session.refresh(doc)
        assert doc.extracted_text_storage_key == f"{org.id}/{doc.id}/extracted_text.txt"
        assert local_storage.exists(doc.extracted_text_storage_key)
    finally:
        teardown_overrides()


def test_extract_text_original_pdf_untouched_after_success(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    original_bytes = make_text_pdf("INVOICE Total: $500.00")
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(original_bytes))

    client = client_with_overrides(db_session, local_storage)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert local_storage.read(key) == original_bytes
    finally:
        teardown_overrides()


def test_extract_text_original_pdf_untouched_after_failure(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/corrupt.pdf"
    original_bytes = make_corrupted_pdf()
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(original_bytes))

    client = client_with_overrides(db_session, local_storage)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert local_storage.read(key) == original_bytes
    finally:
        teardown_overrides()


def test_extract_text_never_calls_real_ai_provider_by_default(
    db_session, local_storage, monkeypatch
):
    """
    Proves this file's tests are immune to real Anthropic calls regardless
    of local environment configuration — reproduces exactly the failure
    condition reported (a real ANTHROPIC_API_KEY present in the local .env)
    and confirms AI extraction is never even attempted, let alone over a
    real network connection.
    """
    monkeypatch.setenv("AI_API_KEY", "sk-ant-fake-key-simulating-a-real-configured-key")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")

    org, user = make_org_user(db_session)
    key = f"{org.id}/doc.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    # No ai_provider argument passed — relies entirely on this file's
    # client_with_overrides default (ai_provider=None), NOT on whatever
    # AI_API_KEY happens to be set in the environment.
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            headers=auth_headers(user.id),
        )
        assert response.status_code == 200
        # Stops at TEXT_EXTRACTED, not REVIEW_REQUIRED — proves AI
        # extraction (and therefore Phase 8 validation) never ran.
        assert response.json()["status"] == "TEXT_EXTRACTED"

        db_session.refresh(doc)
        ai_jobs = (
            db_session.query(ProcessingJob)
            .filter(ProcessingJob.document_id == doc.id, ProcessingJob.provider_name.isnot(None))
            .all()
        )
        assert ai_jobs == []
    finally:
        teardown_overrides()
