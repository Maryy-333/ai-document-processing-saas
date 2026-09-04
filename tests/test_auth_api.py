import uuid
from decimal import Decimal

import jwt as pyjwt
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models import Document, Invoice, Organization, User
from app.models.enums import DocumentStatus
from tests.fixtures.auth import auth_headers


def client_with_db(db_session) -> TestClient:
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def teardown_overrides():
    app.dependency_overrides.clear()


def make_org_user(db_session, org_name="Acme", email="user@example.com"):
    org = Organization(name=org_name)
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


# ---------- Registration ----------


def test_register_valid_returns_201_with_user(db_session):
    client = client_with_db(db_session)
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "valid@example.com", "password": "password123"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "valid@example.com"
        assert "organization_id" in body
        assert body["is_active"] is True
    finally:
        teardown_overrides()


def test_register_password_hash_never_returned(db_session):
    client = client_with_db(db_session)
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "nohash@example.com", "password": "password123"},
        )
        body = response.json()
        assert "password" not in body
        assert "hashed_password" not in body
        assert "password_hash" not in body
    finally:
        teardown_overrides()


def test_register_duplicate_email_returns_400(db_session):
    client = client_with_db(db_session)
    try:
        client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "password123"},
        )
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "anotherpassword"},
        )
        assert response.status_code == 400
    finally:
        teardown_overrides()


def test_register_invalid_email_returns_422(db_session):
    client = client_with_db(db_session)
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "password123"},
        )
        assert response.status_code == 422
    finally:
        teardown_overrides()


def test_register_password_too_short_returns_422(db_session):
    client = client_with_db(db_session)
    try:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "shortpw@example.com", "password": "short"},
        )
        assert response.status_code == 422
    finally:
        teardown_overrides()


def test_register_plaintext_password_not_persisted(db_session):
    client = client_with_db(db_session)
    try:
        client.post(
            "/api/v1/auth/register",
            json={"email": "plaintext@example.com", "password": "mysecretpassword"},
        )
        user = db_session.query(User).filter(User.email == "plaintext@example.com").first()
        assert user.hashed_password != "mysecretpassword"
        assert "mysecretpassword" not in user.hashed_password
    finally:
        teardown_overrides()


# ---------- Login ----------


def test_login_valid_credentials_returns_token(db_session):
    client = client_with_db(db_session)
    try:
        client.post(
            "/api/v1/auth/register",
            json={"email": "login@example.com", "password": "password123"},
        )
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "password123"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
    finally:
        teardown_overrides()


def test_login_token_contains_correct_user_identity(db_session):
    client = client_with_db(db_session)
    try:
        register_response = client.post(
            "/api/v1/auth/register",
            json={"email": "identity@example.com", "password": "password123"},
        )
        user_id = register_response.json()["id"]

        login_response = client.post(
            "/api/v1/auth/login",
            json={"email": "identity@example.com", "password": "password123"},
        )
        token = login_response.json()["access_token"]
        settings = get_settings()
        payload = pyjwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        assert payload["sub"] == user_id
    finally:
        teardown_overrides()


def test_login_wrong_password_returns_401(db_session):
    client = client_with_db(db_session)
    try:
        client.post(
            "/api/v1/auth/register",
            json={"email": "wrongpw@example.com", "password": "correctpassword"},
        )
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpw@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_login_nonexistent_user_returns_401(db_session):
    client = client_with_db(db_session)
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "anypassword"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


# ---------- Authentication dependency (tested through a real protected endpoint) ----------


def test_protected_endpoint_missing_auth_header_returns_401(db_session):
    client = client_with_db(db_session)
    try:
        response = client.get(f"/api/v1/documents/{uuid.uuid4()}/review")
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_protected_endpoint_malformed_auth_header_returns_401(db_session):
    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review",
            headers={"Authorization": "not-bearer-scheme sometoken"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_protected_endpoint_invalid_token_returns_401(db_session):
    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review",
            headers={"Authorization": "Bearer completely.invalid.token"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_protected_endpoint_expired_token_returns_401(db_session):
    org, user = make_org_user(db_session, "Expired Token Org", "expired@example.com")

    settings = get_settings()
    expired_token = create_access_token(
        user.id,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=-1,
    )

    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_protected_endpoint_token_for_nonexistent_user_returns_401(db_session):
    settings = get_settings()
    token = create_access_token(
        uuid.uuid4(),  # a user ID that doesn't exist in the DB
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=30,
    )

    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_protected_endpoint_inactive_user_token_returns_401(db_session):
    org = Organization(name="Inactive User Org")
    db_session.add(org)
    db_session.flush()
    user = User(
        organization_id=org.id,
        email="inactivetoken@example.com",
        hashed_password="x",
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    settings = get_settings()
    token = create_access_token(
        user.id,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=30,
    )

    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


# ---------- Authorization: identity cannot be overridden by request fields ----------


def test_extra_organization_id_in_body_is_ignored_not_trusted(db_session):
    """
    Even if a client sends an organization_id field in the request body
    (which the schema no longer defines), it must be silently ignored —
    identity comes exclusively from the authenticated token.
    """
    org, user = make_org_user(db_session, "Real Org", "real@example.com")
    other_org, _ = make_org_user(db_session, "Other Org", "other@example.com")

    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=f"{org.id}/doc.pdf",
        content_type="application/pdf",
        file_size_bytes=100,
        status=DocumentStatus.REVIEW_REQUIRED,
    )
    db_session.add(doc)
    db_session.commit()
    invoice = Invoice(document_id=doc.id, vendor_name="Acme", subtotal=Decimal("10.00"))
    db_session.add(invoice)
    db_session.commit()

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={
                "vendor_name": "Attempted override",
                # Not a real field on InvoiceUpdateRequest anymore — must
                # be silently dropped by Pydantic, not smuggled through.
                "organization_id": str(other_org.id),
            },
        )
        assert response.status_code == 200
        assert response.json()["organization_id"] == str(org.id)  # unchanged
    finally:
        teardown_overrides()
