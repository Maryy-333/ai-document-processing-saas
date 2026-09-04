import uuid
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models import Document, Invoice, InvoiceLineItem, Organization, User
from app.models.enums import DocumentStatus
from tests.fixtures.auth import auth_headers


def make_org_user(db_session, org_name="Acme", email="user@example.com"):
    org = Organization(name=org_name)
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email=email, hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


def make_document(db_session, org, user, status=DocumentStatus.REVIEW_REQUIRED):
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=f"{org.id}/{uuid.uuid4()}.pdf",
        content_type="application/pdf",
        file_size_bytes=100,
        status=status,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def make_consistent_invoice(db_session, doc):
    invoice = Invoice(
        document_id=doc.id,
        vendor_name="Acme",
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("110.00"),
    )
    db_session.add(invoice)
    db_session.flush()
    db_session.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            line_order=0,
            description="Widget",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            amount=Decimal("100.00"),
        )
    )
    db_session.commit()
    return invoice


def client_with_db(db_session) -> TestClient:
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def teardown_overrides():
    app.dependency_overrides.clear()


# ---------- GET review ----------


def test_review_endpoint_returns_invoice_and_validation(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{doc.id}/review", headers=auth_headers(user.id)
        )
        assert response.status_code == 200
        body = response.json()
        assert body["invoice"]["vendor_name"] == "Acme"
        assert body["validation"]["is_valid"] is True
        assert len(body["invoice"]["line_items"]) == 1
    finally:
        teardown_overrides()


def test_review_endpoint_without_auth_returns_401(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.get(f"/api/v1/documents/{doc.id}/review")
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_review_endpoint_missing_document_returns_404(db_session):
    _, user = make_org_user(db_session)
    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{uuid.uuid4()}/review", headers=auth_headers(user.id)
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_review_endpoint_cross_org_returns_404(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    _, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.get(
            f"/api/v1/documents/{doc.id}/review", headers=auth_headers(user_b.id)
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


# ---------- PATCH invoice ----------


def test_patch_invoice_partial_update(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={"vendor_name": "Updated Vendor"},
        )
        assert response.status_code == 200
        assert response.json()["invoice"]["vendor_name"] == "Updated Vendor"
        assert response.json()["invoice"]["total"] == "110.00"
    finally:
        teardown_overrides()


def test_patch_invoice_without_auth_returns_401(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            json={"vendor_name": "Should not work"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_patch_invoice_cross_org_returns_404(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    _, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user_b.id),
            json={"vendor_name": "Hijacked"},
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_patch_invoice_line_item_replacement(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={
                "line_items": [
                    {
                        "description": "Replaced item",
                        "quantity": "3",
                        "unit_price": "2.00",
                        "amount": "6.00",
                    }
                ],
            },
        )
        assert response.status_code == 200
        items = response.json()["invoice"]["line_items"]
        assert len(items) == 1
        assert items[0]["description"] == "Replaced item"
    finally:
        teardown_overrides()


def test_patch_invoice_invalid_payload_returns_422(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={"subtotal": "not-a-decimal"},
        )
        assert response.status_code == 422
    finally:
        teardown_overrides()


def test_patch_invoice_recalculates_and_returns_validation(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={"total": "999999.00"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["validation"]["is_valid"] is False
        codes = {e["code"] for e in body["validation"]["errors"]}
        assert "TOTAL_MISMATCH" in codes
    finally:
        teardown_overrides()


def test_patch_invoice_blocked_after_approval(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        approve_response = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id)
        )
        assert approve_response.status_code == 200

        edit_response = client.patch(
            f"/api/v1/documents/{doc.id}/invoice",
            headers=auth_headers(user.id),
            json={"vendor_name": "Too late"},
        )
        assert edit_response.status_code == 409
    finally:
        teardown_overrides()


# ---------- Approve ----------


def test_approve_endpoint_success(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id)
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"
    finally:
        teardown_overrides()


def test_approve_endpoint_without_auth_returns_401(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(f"/api/v1/documents/{doc.id}/approve")
        assert response.status_code == 401
        db_session.refresh(doc)
        assert doc.status == DocumentStatus.REVIEW_REQUIRED
    finally:
        teardown_overrides()


def test_approve_endpoint_cross_org_returns_404(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    _, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user_b.id)
        )
        assert response.status_code == 404
        db_session.refresh(doc)
        assert doc.status == DocumentStatus.REVIEW_REQUIRED
    finally:
        teardown_overrides()


def test_approve_endpoint_blocked_by_errors_returns_409_with_findings(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    invoice = Invoice(
        document_id=doc.id,
        vendor_name="Acme",
        subtotal=Decimal("10.00"),
        tax=Decimal("1.00"),
        total=Decimal("999.00"),
    )
    db_session.add(invoice)
    db_session.commit()

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id)
        )
        assert response.status_code == 409
        body = response.json()
        assert "validation" in body
        codes = {e["code"] for e in body["validation"]["errors"]}
        assert "TOTAL_MISMATCH" in codes

        db_session.refresh(doc)
        assert doc.status == DocumentStatus.REVIEW_REQUIRED
    finally:
        teardown_overrides()


def test_approve_endpoint_invalid_state_returns_409(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, status=DocumentStatus.UPLOADED)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id)
        )
        assert response.status_code == 409
    finally:
        teardown_overrides()


# ---------- Reject ----------


def test_reject_endpoint_success(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/reject",
            headers=auth_headers(user.id),
            json={"reason": "Vendor name unreadable in scan."},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REJECTED"
    finally:
        teardown_overrides()


def test_reject_endpoint_without_auth_returns_401(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/reject",
            json={"reason": "reason"},
        )
        assert response.status_code == 401
    finally:
        teardown_overrides()


def test_reject_endpoint_blank_reason_returns_422(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/reject",
            headers=auth_headers(user.id),
            json={"reason": "   "},
        )
        assert response.status_code == 422
    finally:
        teardown_overrides()


def test_reject_endpoint_cross_org_returns_404(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    _, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        response = client.post(
            f"/api/v1/documents/{doc.id}/reject",
            headers=auth_headers(user_b.id),
            json={"reason": "reason"},
        )
        assert response.status_code == 404
    finally:
        teardown_overrides()


def test_repeated_transition_returns_409(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        first = client.post(
            f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id)
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/documents/{doc.id}/reject",
            headers=auth_headers(user.id),
            json={"reason": "too late"},
        )
        assert second.status_code == 409
    finally:
        teardown_overrides()


# ---------- Attribution (Phase 10) ----------


def test_approve_attribution_uses_authenticated_user(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        client.post(f"/api/v1/documents/{doc.id}/approve", headers=auth_headers(user.id))
        invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
        assert invoice.approved_by_user_id == user.id
    finally:
        teardown_overrides()


def test_reject_attribution_uses_authenticated_user(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    client = client_with_db(db_session)
    try:
        client.post(
            f"/api/v1/documents/{doc.id}/reject",
            headers=auth_headers(user.id),
            json={"reason": "reason"},
        )
        invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
        assert invoice.rejected_by_user_id == user.id
    finally:
        teardown_overrides()
