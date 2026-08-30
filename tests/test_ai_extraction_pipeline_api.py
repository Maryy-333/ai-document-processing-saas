import io

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.models import Document, Invoice, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ProcessingStage, ProcessingStatus
from app.storage.base import StorageService
from tests.fixtures.fake_ai_provider import (
    FakeAIProvider,
    MalformedResponseAIProvider,
    TimeoutAIProvider,
)
from tests.fixtures.pdf_fixtures import make_text_pdf

_NO_OVERRIDE = object()

VALID_INVOICE_RESULT = {
    "vendor_name": "ABC Supplies Ltd",
    "invoice_number": "INV-1001",
    "total": "115.00",
    "line_items": [
        {"description": "Office chairs", "quantity": 5, "unit_price": "20.00", "amount": "100.00"}
    ],
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


def test_full_pipeline_reaches_review_required(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    fake_provider = FakeAIProvider(VALID_INVOICE_RESULT)
    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=fake_provider
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        # Phase 8: the same call now continues through validation, so the
        # terminal status is REVIEW_REQUIRED (VALIDATING_RESULT is now only
        # a transient intermediate state).
        assert response.json()["status"] == "REVIEW_REQUIRED"
        assert fake_provider.call_count == 1

        invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
        assert invoice is not None
        assert invoice.vendor_name == "ABC Supplies Ltd"
        assert invoice.approved_at is None
    finally:
        teardown_overrides()


def test_ai_extraction_not_triggered_when_no_provider_configured(db_session, local_storage):
    """Mirrors the OCR-disabled pattern: no provider injected (as if
    AI_API_KEY were unset) must stop cleanly at TEXT_EXTRACTED."""
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
        assert response.json()["status"] == "TEXT_EXTRACTED"
        assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 0
    finally:
        teardown_overrides()


def test_ai_provider_failure_returns_safe_error(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=TimeoutAIProvider()
    )
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
        assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 0
    finally:
        teardown_overrides()


def test_malformed_ai_response_returns_safe_error(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=MalformedResponseAIProvider()
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code >= 400
        db_session.refresh(doc)
        assert doc.status == DocumentStatus.FAILED
    finally:
        teardown_overrides()


def test_ai_extraction_missing_document_returns_404(db_session, local_storage):
    import uuid

    org, _ = make_org_user(db_session)
    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=FakeAIProvider(VALID_INVOICE_RESULT)
    )
    try:
        response = client.post(
            f"/api/v1/documents/{uuid.uuid4()}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_ai_extraction_organization_mismatch_returns_404(db_session, local_storage):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, _ = make_org_user(db_session, "Org B", "b@example.com")
    key = f"{org_a.id}/digital.pdf"
    doc = make_document(db_session, org_a, user_a, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    fake_provider = FakeAIProvider(VALID_INVOICE_RESULT)
    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=fake_provider
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org_b.id)},
        )
        assert response.status_code == 404
        assert fake_provider.call_count == 0

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.UPLOADED
    finally:
        teardown_overrides()


def test_ai_extraction_creates_processing_job_via_api(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE #1001 Total: $115.00")))

    client = client_with_overrides(
        db_session, local_storage, ocr_engine=None, ai_provider=FakeAIProvider(VALID_INVOICE_RESULT)
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
                ProcessingJob.stage == ProcessingStage.AI_EXTRACTION,
            )
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == ProcessingStatus.SUCCESS
    finally:
        teardown_overrides()


def test_existing_phase6_ocr_behavior_remains_intact(db_session, local_storage):
    """
    Regression guard: a scanned PDF must still correctly route through OCR
    even with an AI provider configured — AI extraction should only run
    after OCR produces usable text, not instead of it.
    """
    from tests.fixtures.fake_ocr_engine import FakeOCREngine
    from tests.fixtures.pdf_fixtures import make_image_only_pdf

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    ocr_engine = FakeOCREngine("INVOICE #2002 Total Amount Due: $250.00")
    ai_provider = FakeAIProvider(VALID_INVOICE_RESULT)
    client = client_with_overrides(
        db_session, local_storage, ocr_engine=ocr_engine, ai_provider=ai_provider
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        # Phase 8: the same call now continues through validation, so the
        # terminal status is REVIEW_REQUIRED.
        assert response.json()["status"] == "REVIEW_REQUIRED"
        assert ocr_engine.call_count == 1
        assert ai_provider.call_count == 1

        jobs = (
            db_session.query(ProcessingJob)
            .filter(ProcessingJob.document_id == doc.id)
            .order_by(ProcessingJob.created_at)
            .all()
        )
        stages = [j.stage for j in jobs]
        assert ProcessingStage.TEXT_EXTRACTION in stages
        assert ProcessingStage.OCR in stages
        assert ProcessingStage.AI_EXTRACTION in stages
        assert ProcessingStage.RESULT_VALIDATION in stages
    finally:
        teardown_overrides()
