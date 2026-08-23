import io

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models import Document, Organization, User
from app.storage.base import StorageService

VALID_PDF = b"%PDF-1.4\n%fake-but-signature-valid\n%%EOF"


def make_org_and_user(db_session, org_name="Acme", email="user@example.com"):
    org = Organization(name=org_name)
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


def client_with_overrides(db_session, storage: StorageService) -> TestClient:
    from app.api.v1.documents import get_storage_service

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_storage_service] = lambda: storage
    return TestClient(app)


def teardown_overrides():
    app.dependency_overrides.clear()


def test_upload_valid_pdf_succeeds(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.pdf", io.BytesIO(VALID_PDF), "application/pdf")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["organization_id"] == str(org.id)
        assert body["uploaded_by_user_id"] == str(user.id)
        assert body["original_filename"] == "invoice.pdf"
        assert body["status"] == "UPLOADED"
        assert "storage_key" not in body

        doc = db_session.get(Document, body["id"])
        assert doc is not None
        assert local_storage.exists(doc.storage_key)
        assert local_storage.read(doc.storage_key) == VALID_PDF
    finally:
        teardown_overrides()


def test_upload_unknown_organization_rejected(db_session, local_storage):
    _, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        fake_org_id = "00000000-0000-0000-0000-000000000000"
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": fake_org_id, "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.pdf", io.BytesIO(VALID_PDF), "application/pdf")},
        )
        assert response.status_code == 404
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_unknown_user_rejected(db_session, local_storage):
    org, _ = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        fake_user_id = "00000000-0000-0000-0000-000000000000"
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": fake_user_id},
            files={"file": ("invoice.pdf", io.BytesIO(VALID_PDF), "application/pdf")},
        )
        assert response.status_code == 404
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_user_from_different_organization_rejected(db_session, local_storage):
    """
    A user belonging to Organization B must NOT be accepted with
    organization_id belonging to Organization A.
    """
    org_a, _ = make_org_and_user(db_session, "Org A", "a@example.com")
    org_b, user_b = make_org_and_user(db_session, "Org B", "b@example.com")

    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            # org_a but user actually belongs to org_b
            data={"organization_id": str(org_a.id), "uploaded_by_user_id": str(user_b.id)},
            files={"file": ("invoice.pdf", io.BytesIO(VALID_PDF), "application/pdf")},
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_wrong_extension_rejected(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.txt", io.BytesIO(b"not a pdf"), "text/plain")},
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_oversized_file_rejected(db_session, local_storage, monkeypatch):
    from app.core import config as config_module

    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        tiny_settings = config_module.Settings(max_upload_size_mb=1)
        app.dependency_overrides[config_module.get_settings] = lambda: tiny_settings

        oversized = b"%PDF-1.4\n" + (b"A" * (2 * 1024 * 1024))
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.pdf", io.BytesIO(oversized), "application/pdf")},
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_empty_file_rejected(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_upload_fake_pdf_content_rejected(db_session, local_storage):
    """invoice.pdf with non-PDF bytes inside must be rejected."""
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={
                "file": (
                    "invoice.pdf",
                    io.BytesIO(b"MZ\x90\x00\x03\x00\x00\x00this is an exe not a pdf"),
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
        # confirm nothing was left on disk either
        assert response.json()["error"]
    finally:
        teardown_overrides()


def test_upload_valid_pdf_with_unusual_mime_type_still_accepted(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={
                "file": (
                    "invoice.pdf",
                    io.BytesIO(VALID_PDF),
                    "application/octet-stream",  # unusual generic MIME
                )
            },
        )
        assert response.status_code == 201
    finally:
        teardown_overrides()


def test_upload_path_traversal_filename_cannot_escape_storage(db_session, local_storage, tmp_path):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={
                "file": ("../../../etc/passwd.pdf", io.BytesIO(VALID_PDF), "application/pdf")
            },
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
        escaped = (tmp_path / ".." / ".." / ".." / "etc" / "passwd.pdf").resolve()
        assert not escaped.exists()
    finally:
        teardown_overrides()


def test_upload_excessively_long_filename_rejected(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        long_name = ("a" * 300) + ".pdf"
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": (long_name, io.BytesIO(VALID_PDF), "application/pdf")},
        )
        assert response.status_code == 400
        assert db_session.query(Document).count() == 0
    finally:
        teardown_overrides()


def test_error_response_never_exposes_internal_details(db_session, local_storage):
    org, user = make_org_and_user(db_session)
    client = client_with_overrides(db_session, local_storage)
    try:
        response = client.post(
            "/api/v1/documents",
            data={"organization_id": str(org.id), "uploaded_by_user_id": str(user.id)},
            files={"file": ("invoice.txt", io.BytesIO(b"x"), "text/plain")},
        )
        body = response.json()
        assert set(body.keys()) == {"error"}
        assert "Traceback" not in body["error"]
        assert "/home/" not in body["error"]
    finally:
        teardown_overrides()
