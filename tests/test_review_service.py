import uuid
from decimal import Decimal

import pytest

from app.core.exceptions import InvalidStateTransitionError, NotFoundError
from app.models import Document, Invoice, InvoiceLineItem, Organization, User
from app.models.enums import DocumentStatus
from app.schemas.invoice_extraction import ExtractedLineItem
from app.services.invoice_validation_service import MissingDraftInvoiceError
from app.services.review_service import (
    ApprovalBlockedByValidationError,
    approve_document,
    get_review,
    reject_document,
    update_invoice,
)


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


def make_invoice(db_session, doc, **kwargs):
    invoice = Invoice(document_id=doc.id, **kwargs)
    db_session.add(invoice)
    db_session.commit()
    return invoice


def make_consistent_invoice(db_session, doc):
    invoice = make_invoice(
        db_session,
        doc,
        vendor_name="Acme",
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("110.00"),
    )
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
    db_session.refresh(invoice)
    return invoice


# ---------- GET review ----------


def test_get_review_returns_invoice_and_validation(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    document, invoice, result = get_review(db_session, doc.id, org.id)
    assert document.id == doc.id
    assert invoice.vendor_name == "Acme"
    assert result.is_valid is True


def test_get_review_missing_document_raises_not_found(db_session):
    org, _ = make_org_user(db_session)
    with pytest.raises(NotFoundError):
        get_review(db_session, uuid.uuid4(), org.id)


def test_get_review_cross_org_access_raises_not_found(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, _ = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    with pytest.raises(NotFoundError):
        get_review(db_session, doc.id, org_b.id)


def test_get_review_missing_invoice_raises(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    with pytest.raises(MissingDraftInvoiceError):
        get_review(db_session, doc.id, org.id)


# ---------- PATCH invoice ----------


def test_update_invoice_partial_scalar_field(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    document, invoice, result = update_invoice(
        db_session,
        doc.id,
        org.id,
        user.id,
        updates={"vendor_name": "New Vendor Name"},
        fields_set={"vendor_name"},
    )
    assert invoice.vendor_name == "New Vendor Name"
    assert invoice.subtotal == Decimal("100.00")


def test_update_invoice_line_item_replacement(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    new_items = [
        ExtractedLineItem(
            description="New item",
            quantity=Decimal("2"),
            unit_price=Decimal("5.00"),
            amount=Decimal("10.00"),
        )
    ]
    document, invoice, result = update_invoice(
        db_session,
        doc.id,
        org.id,
        user.id,
        updates={"line_items": [i.model_dump() for i in new_items]},
        fields_set={"line_items"},
    )
    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].description == "New item"


def test_update_invoice_explicit_null_clears_field(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    document, invoice, result = update_invoice(
        db_session,
        doc.id,
        org.id,
        user.id,
        updates={"due_date": None},
        fields_set={"due_date"},
    )
    assert invoice.due_date is None


def test_update_invoice_omitted_field_untouched(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    update_invoice(
        db_session,
        doc.id,
        org.id,
        user.id,
        updates={"vendor_name": "Changed"},
        fields_set={"vendor_name"},
    )
    db_session.refresh(doc)
    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    assert invoice.total == Decimal("110.00")


def test_update_invoice_recalculates_validation(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    document, invoice, result = update_invoice(
        db_session,
        doc.id,
        org.id,
        user.id,
        updates={"total": Decimal("999999.00")},
        fields_set={"total"},
    )
    assert result.is_valid is False
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_update_invoice_blocked_when_not_review_required(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, status=DocumentStatus.UPLOADED)
    make_invoice(db_session, doc, vendor_name="Acme")

    with pytest.raises(InvalidStateTransitionError):
        update_invoice(
            db_session,
            doc.id,
            org.id,
            user.id,
            updates={"vendor_name": "X"},
            fields_set={"vendor_name"},
        )


def test_update_invoice_blocked_after_approval(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)
    approve_document(db_session, doc.id, org.id, user.id)

    with pytest.raises(InvalidStateTransitionError):
        update_invoice(
            db_session,
            doc.id,
            org.id,
            user.id,
            updates={"vendor_name": "X"},
            fields_set={"vendor_name"},
        )


def test_update_invoice_does_not_leave_partial_state_on_failure(local_storage, monkeypatch):
    """
    Uses a real standalone session rather than the shared db_session
    fixture: with that fixture's transaction-wrapping pattern, several real
    sequential commits followed by a rollback is fragile in a way specific
    to the test harness (SQLAlchemy session/connection interaction), not
    the production code — same issue and same fix as Phase 6's analogous
    DB-failure-cleanup test.
    """
    from tests.conftest import _SessionFactory

    db = _SessionFactory()
    try:
        org, user = make_org_user(db)
        doc = make_document(db, org, user)
        invoice = make_consistent_invoice(db, doc)
        original_vendor = invoice.vendor_name

        def failing_commit():
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(db, "commit", failing_commit)

        from app.core.exceptions import DatabaseError

        with pytest.raises(DatabaseError):
            update_invoice(
                db,
                doc.id,
                org.id,
                user.id,
                updates={"vendor_name": "Should not stick"},
                fields_set={"vendor_name"},
            )

        monkeypatch.undo()
        db.rollback()
        fresh = db.query(Invoice).filter(Invoice.document_id == doc.id).first()
        assert fresh.vendor_name == original_vendor
    finally:
        db.rollback()
        db.query(InvoiceLineItem).filter(
            InvoiceLineItem.invoice_id.in_(
                db.query(Invoice.id).filter(Invoice.document_id == doc.id)
            )
        ).delete(synchronize_session=False)
        db.query(Invoice).filter(Invoice.document_id == doc.id).delete()
        db.query(Document).filter(Document.id == doc.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.query(Organization).filter(Organization.id == org.id).delete()
        db.commit()
        db.close()


# ---------- Approve ----------


def test_approve_success(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    result = approve_document(db_session, doc.id, org.id, user.id)
    assert result.status == DocumentStatus.APPROVED

    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    assert invoice.approved_at is not None
    assert invoice.approved_by_user_id == user.id


def test_approve_warning_only_findings_do_not_block(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_invoice(
        db_session,
        doc,
        vendor_name="Acme",
        subtotal=Decimal("10.00"),
        tax=Decimal("1.00"),
        total=Decimal("11.00"),
    )

    result = approve_document(db_session, doc.id, org.id, user.id)
    assert result.status == DocumentStatus.APPROVED


def test_approve_blocked_by_error_findings(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_invoice(
        db_session,
        doc,
        vendor_name="Acme",
        subtotal=Decimal("10.00"),
        tax=Decimal("1.00"),
        total=Decimal("999.00"),
    )

    with pytest.raises(ApprovalBlockedByValidationError) as exc_info:
        approve_document(db_session, doc.id, org.id, user.id)
    assert any(e.code == "TOTAL_MISMATCH" for e in exc_info.value.validation_result.errors)

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.REVIEW_REQUIRED


def test_approve_missing_invoice_raises(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    with pytest.raises(MissingDraftInvoiceError):
        approve_document(db_session, doc.id, org.id, user.id)


@pytest.mark.parametrize(
    "status",
    [
        DocumentStatus.UPLOADED,
        DocumentStatus.TEXT_EXTRACTED,
        DocumentStatus.FAILED,
        DocumentStatus.APPROVED,
        DocumentStatus.REJECTED,
    ],
)
def test_approve_invalid_state_transitions_rejected(db_session, status):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, status=status)
    make_invoice(db_session, doc, vendor_name="Acme")

    with pytest.raises(InvalidStateTransitionError):
        approve_document(db_session, doc.id, org.id, user.id)


def test_approve_cross_org_raises_not_found(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    with pytest.raises(NotFoundError):
        approve_document(db_session, doc.id, org_b.id, user_b.id)


def test_repeated_approval_fails(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)
    approve_document(db_session, doc.id, org.id, user.id)

    with pytest.raises(InvalidStateTransitionError):
        approve_document(db_session, doc.id, org.id, user.id)


# ---------- Reject ----------


def test_reject_success_persists_reason(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)

    result = reject_document(db_session, doc.id, org.id, user.id, "Vendor address is wrong.")
    assert result.status == DocumentStatus.REJECTED

    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    assert invoice.rejection_reason == "Vendor address is wrong."
    assert invoice.rejected_at is not None
    assert invoice.rejected_by_user_id == user.id


def test_reject_cross_org_raises_not_found(db_session):
    org_a, user_a = make_org_user(db_session, "Org A", "a@example.com")
    org_b, user_b = make_org_user(db_session, "Org B", "b@example.com")
    doc = make_document(db_session, org_a, user_a)
    make_consistent_invoice(db_session, doc)

    with pytest.raises(NotFoundError):
        reject_document(db_session, doc.id, org_b.id, user_b.id, "reason")


def test_reject_invalid_state_transition(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user, status=DocumentStatus.UPLOADED)
    make_invoice(db_session, doc, vendor_name="Acme")

    with pytest.raises(InvalidStateTransitionError):
        reject_document(db_session, doc.id, org.id, user.id, "reason")


def test_repeated_rejection_fails(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)
    reject_document(db_session, doc.id, org.id, user.id, "first reason")

    with pytest.raises(InvalidStateTransitionError):
        reject_document(db_session, doc.id, org.id, user.id, "second reason")


def test_reject_then_approve_fails(db_session):
    org, user = make_org_user(db_session)
    doc = make_document(db_session, org, user)
    make_consistent_invoice(db_session, doc)
    reject_document(db_session, doc.id, org.id, user.id, "reason")

    with pytest.raises(InvalidStateTransitionError):
        approve_document(db_session, doc.id, org.id, user.id)
