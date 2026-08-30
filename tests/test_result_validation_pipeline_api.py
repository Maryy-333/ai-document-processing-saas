import io

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import ProcessingStage
from app.storage.base import StorageService
from tests.fixtures.fake_ai_provider import FakeAIProvider
from tests.fixtures.pdf_fixtures import make_text_pdf

_NO_OVERRIDE = object()

CONSISTENT_INVOICE_RESULT = {
    "vendor_name": "ABC Supplies Ltd",
    "invoice_number": "INV-1001",
    "subtotal": "100.00",
    "tax": "15.00",
    "total": "115.00",
    "line_items": [
        {"description": "Office chairs", "quantity": 5, "unit_price": "20.00", "amount": "100.00"}
    ],
}

INCONSISTENT_INVOICE_RESULT = {
    "vendor_name": "ABC Supplies Ltd",
    "subtotal": "100.00",
    "tax": "15.00",
    "total": "999.00",
    "line_items": [],
}


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


def client_with_overrides(
    db_session, storage: StorageService, ocr_engine=_NO_OVERRIDE, ai_provider=_NO_OVERRIDE
) -> TestClient:
    from app.api.v1.documents import get_ai_provider, get_ocr_engine, get_storage_service
    from app.main import app

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_storage_service] = lambda: storage
    if ocr_engine is not _NO_OVERRIDE:
        app.dependency_overrides[get_ocr_engine] = lambda: ocr_engine
    if ai_provider is not _NO_OVERRIDE:
        app.dependency_overrides[get_ai_provider] = lambda: ai_provider
    return TestClient(app)


def teardown_overrides():
    from app.main import app

    app.dependency_overrides.clear()


def test_full_pipeline_reaches_review_required_with_no_findings(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(
        db_session,
        local_storage,
        ocr_engine=None,
        ai_provider=FakeAIProvider(CONSISTENT_INVOICE_RESULT),
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "REVIEW_REQUIRED"
        assert body["validation"] is not None
        assert body["validation"]["is_valid"] is True
        assert body["validation"]["errors"] == []
    finally:
        teardown_overrides()


def test_full_pipeline_reaches_review_required_with_findings_surfaced(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $999.00")))

    client = client_with_overrides(
        db_session,
        local_storage,
        ocr_engine=None,
        ai_provider=FakeAIProvider(INCONSISTENT_INVOICE_RESULT),
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "REVIEW_REQUIRED"
        assert body["validation"]["is_valid"] is False
        codes = {e["code"] for e in body["validation"]["errors"]}
        assert "TOTAL_MISMATCH" in codes
    finally:
        teardown_overrides()


def test_validation_field_absent_when_status_not_review_required(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(db_session, local_storage, ocr_engine=None, ai_provider=None)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "TEXT_EXTRACTED"
        assert body["validation"] is None
    finally:
        teardown_overrides()


def test_result_validation_processing_job_created_via_api(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(
        db_session,
        local_storage,
        ocr_engine=None,
        ai_provider=FakeAIProvider(CONSISTENT_INVOICE_RESULT),
    )
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        jobs = (
            db_session.query(ProcessingJob)
            .filter(
                ProcessingJob.document_id == doc.id,
                ProcessingJob.stage == ProcessingStage.RESULT_VALIDATION,
            )
            .all()
        )
        assert len(jobs) == 1
    finally:
        teardown_overrides()
