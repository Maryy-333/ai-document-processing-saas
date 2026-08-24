import io
import uuid

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus
from app.storage.base import StorageService
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


def client_with_overrides(db_session, storage: StorageService) -> TestClient:
    from app.api.v1.documents import get_storage_service

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_storage_service] = lambda: storage
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
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "TEXT_EXTRACTED"
        assert body["id"] == str(doc.id)
        assert "extracted_text_storage_key" not in body
        assert "storage_key" not in body
    finally:
        teardown_overrides()


def test_extract_text_textless_pdf_returns_ocr_required(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/blank.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_blank_pdf()))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
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
            json={"organization_id": str(org.id)},
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
    org, _ = make_org_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{uuid.uuid4()}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_extract_text_organization_mismatch_returns_404(db_session, local_storage):
    """
    A document must not be extractable by supplying a different
    organization_id than the one it actually belongs to — same isolation
    guarantee as Phase 4's upload identity check.
    """
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, _ = make_org_user(db_session, "Org B", "b@example.com")
    key = f"{org_a.id}/doc.pdf"
    doc = make_document(db_session, org_a, user_a, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org_b.id)},
        )
        assert response.status_code == 404

        db_session.refresh(doc)
        # Document must remain untouched by the mismatched request.
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
            json={"organization_id": str(org.id)},
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
            json={"organization_id": str(org.id)},
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
            json={"organization_id": str(org.id)},
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
            json={"organization_id": str(org.id)},
        )
        assert local_storage.read(key) == original_bytes
    finally:
        teardown_overrides()
