import io

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ProcessingStage, ProcessingStatus
from app.storage.base import StorageService
from tests.fixtures.fake_ocr_engine import EmptyOCREngine, FailingOCREngine, FakeOCREngine
from tests.fixtures.pdf_fixtures import (
    make_corrupted_pdf,
    make_image_only_pdf,
    make_text_pdf,
)

_NO_OVERRIDE = object()


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
    db_session, storage: StorageService, ocr_engine=_NO_OVERRIDE, ai_provider=None
) -> TestClient:
    from app.api.v1.documents import get_ai_provider, get_ocr_engine, get_storage_service
    from app.main import app

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_storage_service] = lambda: storage
    if ocr_engine is not _NO_OVERRIDE:
        app.dependency_overrides[get_ocr_engine] = lambda: ocr_engine
    # ALWAYS overridden, defaults to None (disabled) — see identical
    # reasoning in tests/test_extract_text_api.py's client_with_overrides.
    # This file's tests predate the AI provider (Phase 7); without this,
    # a real local ANTHROPIC_API_KEY makes these tests hit the real API.
    app.dependency_overrides[get_ai_provider] = lambda: ai_provider
    return TestClient(app)


def teardown_overrides():
    from app.main import app

    app.dependency_overrides.clear()


def test_scanned_pdf_routes_through_ocr_to_text_extracted(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    fake_engine = FakeOCREngine("INVOICE #555 Total: $1,200.00")
    client = client_with_overrides(db_session, local_storage, ocr_engine=fake_engine)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "TEXT_EXTRACTED"
        assert fake_engine.call_count == 1

        db_session.refresh(doc)
        assert doc.extracted_text_storage_key is not None
        stored = local_storage.read(doc.extracted_text_storage_key).decode("utf-8")
        assert "INVOICE #555" in stored

        assert local_storage.exists(key)
    finally:
        teardown_overrides()


def test_digital_pdf_never_invokes_ocr(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/digital.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_text_pdf("INVOICE Total: $500.00")))

    fake_engine = FakeOCREngine()
    client = client_with_overrides(db_session, local_storage, ocr_engine=fake_engine)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "TEXT_EXTRACTED"
        assert fake_engine.call_count == 0

        jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
        assert len(jobs) == 1
        assert jobs[0].stage == ProcessingStage.TEXT_EXTRACTION
    finally:
        teardown_overrides()


def test_ocr_creates_processing_job_with_ocr_stage(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    fake_engine = FakeOCREngine("INVOICE #555 Total: $1,200.00")
    client = client_with_overrides(db_session, local_storage, ocr_engine=fake_engine)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        jobs = (
            db_session.query(ProcessingJob)
            .filter(ProcessingJob.document_id == doc.id)
            .order_by(ProcessingJob.created_at)
            .all()
        )
        assert len(jobs) == 2
        assert jobs[0].stage == ProcessingStage.TEXT_EXTRACTION
        assert jobs[0].status == ProcessingStatus.SUCCESS
        assert jobs[1].stage == ProcessingStage.OCR
        assert jobs[1].status == ProcessingStatus.SUCCESS
        assert jobs[1].started_at is not None
        assert jobs[1].completed_at is not None
        assert jobs[1].duration_ms is not None
    finally:
        teardown_overrides()


def test_ocr_engine_failure_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    original_bytes = make_image_only_pdf()
    local_storage.save(key, io.BytesIO(original_bytes))

    client = client_with_overrides(db_session, local_storage, ocr_engine=FailingOCREngine())
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
        assert doc.extracted_text_storage_key is None

        jobs = (
            db_session.query(ProcessingJob)
            .filter(
                ProcessingJob.document_id == doc.id,
                ProcessingJob.stage == ProcessingStage.OCR,
            )
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == ProcessingStatus.FAILED
        assert jobs[0].error_category.value == "OCR_ERROR"
        assert jobs[0].error_message is not None
        assert "Traceback" not in jobs[0].error_message

        assert local_storage.read(key) == original_bytes
    finally:
        teardown_overrides()


def test_ocr_empty_result_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    original_bytes = make_image_only_pdf()
    local_storage.save(key, io.BytesIO(original_bytes))

    client = client_with_overrides(db_session, local_storage, ocr_engine=EmptyOCREngine())
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code >= 400

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.FAILED
        assert doc.extracted_text_storage_key is None

        jobs = (
            db_session.query(ProcessingJob)
            .filter(
                ProcessingJob.document_id == doc.id,
                ProcessingJob.stage == ProcessingStage.OCR,
            )
            .all()
        )
        assert len(jobs) == 1
        # PROVISIONAL decision — see implementation report. Not OCR_ERROR:
        # the engine succeeded, it just found nothing.
        assert jobs[0].status == ProcessingStatus.FAILED
        assert jobs[0].error_category.value == "PROCESSING_ERROR"

        assert local_storage.read(key) == original_bytes
    finally:
        teardown_overrides()


def test_ocr_organization_isolation(db_session, local_storage):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, _ = make_org_user(db_session, "Org B", "b@example.com")
    key = f"{org_a.id}/scanned.pdf"
    doc = make_document(db_session, org_a, user_a, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    client = client_with_overrides(db_session, local_storage, ocr_engine=FakeOCREngine())
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org_b.id)},
        )
        assert response.status_code == 404

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.UPLOADED
    finally:
        teardown_overrides()


def test_ocr_disabled_stops_at_ocr_required(db_session, local_storage):
    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    client = client_with_overrides(db_session, local_storage, ocr_engine=None)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "OCR_REQUIRED"

        jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
        assert len(jobs) == 1
        assert jobs[0].stage == ProcessingStage.TEXT_EXTRACTION
    finally:
        teardown_overrides()


def test_ocr_storage_read_failure(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, f"{org.id}/missing.pdf")
    key = doc.storage_key
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    class DeleteAfterFirstRead:
        """Lets native extraction read the file once, then makes it
        disappear before the OCR stage tries to read it again."""

        def __init__(self, inner):
            self._inner = inner
            self._reads = 0

        def read(self, k):
            self._reads += 1
            data = self._inner.read(k)
            if self._reads == 1:
                self._inner.delete(k)
            return data

        def save(self, k, d):
            return self._inner.save(k, d)

        def exists(self, k):
            return self._inner.exists(k)

        def delete(self, k):
            return self._inner.delete(k)

    wrapped_storage = DeleteAfterFirstRead(local_storage)
    client = client_with_overrides(db_session, wrapped_storage, ocr_engine=FakeOCREngine())
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


def test_corrupted_pdf_fails_at_native_stage_before_ocr_is_reached(db_session, local_storage):
    """
    A corrupted PDF fails native extraction's PdfOpenError immediately, so
    it never reaches OCR_REQUIRED — confirms corrupted input never silently
    reaches the OCR engine.
    """
    org, user = make_org_user(db_session)
    key = f"{org.id}/corrupt.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_corrupted_pdf()))

    client = client_with_overrides(db_session, local_storage, ocr_engine=FakeOCREngine())
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code >= 400
        db_session.refresh(doc)
        assert doc.status == DocumentStatus.FAILED

        jobs = db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id).all()
        assert len(jobs) == 1
        assert jobs[0].stage == ProcessingStage.TEXT_EXTRACTION
        assert jobs[0].status == ProcessingStatus.FAILED
    finally:
        teardown_overrides()


def test_ocr_pipeline_never_calls_real_ai_provider_by_default(
    db_session, local_storage, monkeypatch
):
    """
    Reproduces the reported failure condition (a real ANTHROPIC_API_KEY
    present in the local .env) and confirms OCR-path tests in this file
    remain immune to it — AI extraction is never attempted after OCR
    succeeds, unless a test explicitly injects a provider.
    """
    monkeypatch.setenv("AI_API_KEY", "sk-ant-fake-key-simulating-a-real-configured-key")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    fake_engine = FakeOCREngine("INVOICE #777 Total: $300.00")
    # ai_provider not passed — relies on this file's client_with_overrides
    # default (ai_provider=None), not on the environment.
    client = client_with_overrides(db_session, local_storage, ocr_engine=fake_engine)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        # Stops at TEXT_EXTRACTED (OCR ran), not REVIEW_REQUIRED — proves AI
        # extraction never ran, even though OCR succeeded with usable text.
        assert response.json()["status"] == "TEXT_EXTRACTED"

        ai_jobs = (
            db_session.query(ProcessingJob)
            .filter(ProcessingJob.document_id == doc.id, ProcessingJob.provider_name.isnot(None))
            .all()
        )
        assert ai_jobs == []
    finally:
        teardown_overrides()


def test_ocr_pipeline_with_explicit_fake_ai_provider_reaches_review_required(
    db_session, local_storage
):
    """
    Confirms the fix doesn't just disable AI extraction unconditionally —
    this file's DI wiring correctly honors an explicitly-injected fake
    provider too, preserving Phase 8's full chain (AI_EXTRACTION →
    RESULT_VALIDATION → REVIEW_REQUIRED) when a test actually wants it,
    using the existing AIProvider abstraction rather than a parallel one.
    """
    from tests.fixtures.fake_ai_provider import FakeAIProvider

    org, user = make_org_user(db_session)
    key = f"{org.id}/scanned.pdf"
    doc = make_document(db_session, org, user, key)
    local_storage.save(key, io.BytesIO(make_image_only_pdf()))

    fake_engine = FakeOCREngine("INVOICE #888 Total: $450.00")
    fake_ai_provider = FakeAIProvider({"vendor_name": "Acme Corp", "total": "450.00"})
    client = client_with_overrides(
        db_session, local_storage, ocr_engine=fake_engine, ai_provider=fake_ai_provider
    )
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/extract-text",
            json={"organization_id": str(org.id)},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REVIEW_REQUIRED"
        assert fake_ai_provider.call_count == 1

        stages = {
            j.stage
            for j in db_session.query(ProcessingJob).filter(ProcessingJob.document_id == doc.id)
        }
        assert ProcessingStage.OCR in stages
        assert ProcessingStage.AI_EXTRACTION in stages
        assert ProcessingStage.RESULT_VALIDATION in stages
    finally:
        teardown_overrides()
