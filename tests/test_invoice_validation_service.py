from decimal import Decimal

import pytest

from app.models import Document, Invoice, InvoiceLineItem, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ProcessingStage, ProcessingStatus
from app.services.invoice_validation_service import (
    MissingDraftInvoiceError,
    run_result_validation,
)


def make_org_user(db_session):
    org = Organization(name="Acme")
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


def make_document_at_validating_result(db_session, org, user):
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=f"{org.id}/doc.pdf",
        content_type="application/pdf",
        file_size_bytes=100,
        status=DocumentStatus.VALIDATING_RESULT,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def test_successful_validation_reaches_review_required(db_session):
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    db_session.add(
        Invoice(document_id=doc.id, vendor_name="Acme", subtotal=Decimal("10.00"))
    )
    db_session.commit()

    result = run_result_validation(db_session, doc)

    assert result.status == DocumentStatus.REVIEW_REQUIRED


def test_validation_reaches_review_required_even_with_findings(db_session):
    """A document with validation errors still reaches REVIEW_REQUIRED —
    it is never silently marked FAILED just because the invoice looks
    wrong. FAILED is reserved for system-level failures."""
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    invoice = Invoice(
        document_id=doc.id,
        subtotal=Decimal("10.00"),
        tax=Decimal("1.00"),
        total=Decimal("999.00"),  # deliberately inconsistent
    )
    db_session.add(invoice)
    db_session.commit()

    result = run_result_validation(db_session, doc)

    assert result.status == DocumentStatus.REVIEW_REQUIRED


def test_processing_job_created_with_result_validation_stage(db_session):
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    db_session.add(Invoice(document_id=doc.id, vendor_name="Acme"))
    db_session.commit()

    run_result_validation(db_session, doc)

    jobs = (
        db_session.query(ProcessingJob)
        .filter(
            ProcessingJob.document_id == doc.id,
            ProcessingJob.stage == ProcessingStage.RESULT_VALIDATION,
        )
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].status == ProcessingStatus.SUCCESS
    assert jobs[0].started_at is not None
    assert jobs[0].completed_at is not None
    assert jobs[0].duration_ms is not None


def test_missing_draft_invoice_raises_and_marks_failed(db_session):
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    # No Invoice row created — simulates a system-level inconsistency.

    with pytest.raises(MissingDraftInvoiceError):
        run_result_validation(db_session, doc)

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


def test_validation_with_line_items_loaded_from_db(db_session):
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    invoice = Invoice(document_id=doc.id, vendor_name="Acme")
    db_session.add(invoice)
    db_session.flush()
    db_session.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            line_order=0,
            description="Widget",
            quantity=Decimal("2"),
            unit_price=Decimal("5.00"),
            amount=Decimal("10.00"),
        )
    )
    db_session.commit()

    result = run_result_validation(db_session, doc)
    assert result.status == DocumentStatus.REVIEW_REQUIRED


def test_validation_does_not_touch_approved_at(db_session):
    org, user = make_org_user(db_session)
    doc = make_document_at_validating_result(db_session, org, user)
    invoice = Invoice(document_id=doc.id, vendor_name="Acme")
    db_session.add(invoice)
    db_session.commit()

    run_result_validation(db_session, doc)

    db_session.refresh(invoice)
    assert invoice.approved_at is None
    assert invoice.approved_by_user_id is None
