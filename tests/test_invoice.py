from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Document, Invoice, InvoiceLineItem, Organization, User


def make_org_user_doc(session):
    org = Organization(name="Acme")
    session.add(org)
    session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    session.add(user)
    session.flush()
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=f"orgs/{org.id}/invoice.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
    )
    session.add(doc)
    session.flush()
    return org, user, doc


def test_invoice_can_be_created_with_all_fields_null(db_session):
    """
    Per Phase 1 §4/§11: missing/uncertain fields must be representable as
    null, never a fabricated placeholder value.
    """
    _, _, doc = make_org_user_doc(db_session)
    invoice = Invoice(document_id=doc.id)
    db_session.add(invoice)
    db_session.commit()

    db_session.refresh(invoice)
    assert invoice.vendor_name is None
    assert invoice.total is None
    assert invoice.invoice_date is None
    assert invoice.approved_at is None


def test_invoice_is_one_to_one_with_document(db_session):
    _, _, doc = make_org_user_doc(db_session)
    db_session.add(Invoice(document_id=doc.id))
    db_session.flush()

    duplicate = Invoice(document_id=doc.id)
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_invoice_money_fields_preserve_decimal_precision(db_session):
    _, _, doc = make_org_user_doc(db_session)
    invoice = Invoice(
        document_id=doc.id,
        subtotal=Decimal("100.10"),
        tax=Decimal("8.51"),
        total=Decimal("108.61"),
    )
    db_session.add(invoice)
    db_session.commit()

    db_session.refresh(invoice)
    assert invoice.subtotal == Decimal("100.10")
    assert invoice.subtotal + invoice.tax == invoice.total


def test_invoice_line_items_relationship_and_ordering(db_session):
    _, _, doc = make_org_user_doc(db_session)
    invoice = Invoice(document_id=doc.id)
    db_session.add(invoice)
    db_session.flush()

    db_session.add_all(
        [
            InvoiceLineItem(
                invoice_id=invoice.id,
                line_order=1,
                description="Widget",
                quantity=Decimal("2"),
                unit_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            InvoiceLineItem(
                invoice_id=invoice.id,
                line_order=0,
                description="Gadget",
                quantity=Decimal("1"),
                unit_price=Decimal("5.00"),
                amount=Decimal("5.00"),
            ),
        ]
    )
    db_session.commit()

    db_session.refresh(invoice)
    assert len(invoice.line_items) == 2
    # order_by="InvoiceLineItem.line_order" — Gadget (0) before Widget (1)
    assert [li.description for li in invoice.line_items] == ["Gadget", "Widget"]


def test_deleting_invoice_cascades_line_items(db_session):
    _, _, doc = make_org_user_doc(db_session)
    invoice = Invoice(document_id=doc.id)
    db_session.add(invoice)
    db_session.flush()
    db_session.add(InvoiceLineItem(invoice_id=invoice.id, description="Widget"))
    db_session.commit()

    db_session.delete(invoice)
    db_session.commit()

    assert db_session.query(InvoiceLineItem).count() == 0
