import io
from decimal import Decimal

import pytest

from app.core.exceptions import ProcessingError
from app.models import Document, Invoice, InvoiceLineItem, Organization, ProcessingJob, User
from app.models.enums import DocumentStatus, ErrorCategory, ProcessingStage, ProcessingStatus
from app.processing.ai.provider import (
    AIProviderAuthError,
    AIProviderResponseError,
    AIProviderTimeoutError,
)
from app.services.invoice_extraction_service import (
    EmptyExtractedTextError,
    extract_invoice_from_document,
)
from tests.fixtures.fake_ai_provider import (
    AuthFailureAIProvider,
    EmptyResponseAIProvider,
    FakeAIProvider,
    MalformedResponseAIProvider,
    TimeoutAIProvider,
    UnexpectedErrorAIProvider,
)

VALID_INVOICE_RESULT = {
    "vendor_name": "ABC Supplies Ltd",
    "vendor_address": "123 Main Street",
    "customer_name": "Example Company",
    "invoice_number": "INV-1001",
    "invoice_date": "2026-08-20",
    "due_date": "2026-09-20",
    "currency": "USD",
    "subtotal": "100.00",
    "tax": "15.00",
    "total": "115.00",
    "payment_terms": "Net 30",
    "line_items": [
        {"description": "Office chairs", "quantity": 5, "unit_price": "20.00", "amount": "100.00"}
    ],
}


def make_org_user(db_session):
    org = Organization(name="Acme")
    db_session.add(org)
    db_session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    return org, user


def make_text_extracted_document(
    db_session, local_storage, org, user, text="INVOICE #1001 Total: $115.00"
):
    key = f"{org.id}/doc.pdf"
    text_key = f"{org.id}/doc/extracted_text.txt"
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=key,
        content_type="application/pdf",
        file_size_bytes=100,
        status=DocumentStatus.TEXT_EXTRACTED,
        extracted_text_storage_key=text_key,
    )
    db_session.add(doc)
    db_session.commit()
    local_storage.save(text_key, io.BytesIO(text.encode("utf-8")))
    return doc


def test_valid_extraction_produces_invoice_draft(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)
    provider = FakeAIProvider(VALID_INVOICE_RESULT)

    result = extract_invoice_from_document(db_session, local_storage, doc, provider=provider)

    # Phase 8: extract_invoice_from_document now continues automatically
    # into result validation within the same call, so the terminal status
    # is REVIEW_REQUIRED, not VALIDATING_RESULT (which is now only a
    # transient intermediate state). This test's actual purpose — a
    # correctly-populated, unapproved draft Invoice — is unaffected.
    assert result.status == DocumentStatus.REVIEW_REQUIRED
    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    assert invoice is not None
    assert invoice.vendor_name == "ABC Supplies Ltd"
    assert invoice.invoice_number == "INV-1001"
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.approved_at is None
    assert invoice.approved_by_user_id is None


def test_missing_fields_become_none_in_persisted_invoice(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)
    provider = FakeAIProvider({"vendor_name": "Only Vendor Known"})

    extract_invoice_from_document(db_session, local_storage, doc, provider=provider)

    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    assert invoice.vendor_name == "Only Vendor Known"
    assert invoice.invoice_number is None
    assert invoice.due_date is None
    assert invoice.total is None
    assert invoice.line_items == []


def test_multiple_line_items_persisted_in_order(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)
    result = {
        "line_items": [
            {"description": "A", "quantity": 1, "unit_price": "1.00", "amount": "1.00"},
            {"description": "B", "quantity": 2, "unit_price": "2.00", "amount": "4.00"},
            {"description": "C", "quantity": 3, "unit_price": "3.00", "amount": "9.00"},
        ]
    }
    extract_invoice_from_document(db_session, local_storage, doc, provider=FakeAIProvider(result))

    invoice = db_session.query(Invoice).filter(Invoice.document_id == doc.id).first()
    items = (
        db_session.query(InvoiceLineItem)
        .filter(InvoiceLineItem.invoice_id == invoice.id)
        .order_by(InvoiceLineItem.line_order)
        .all()
    )
    assert [i.description for i in items] == ["A", "B", "C"]
    assert items[1].amount == Decimal("4.00")


def test_provider_timeout_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)

    with pytest.raises(AIProviderTimeoutError):
        extract_invoice_from_document(db_session, local_storage, doc, provider=TimeoutAIProvider())

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED
    job = (
        db_session.query(ProcessingJob)
        .filter(
            ProcessingJob.document_id == doc.id,
            ProcessingJob.stage == ProcessingStage.AI_EXTRACTION,
        )
        .first()
    )
    assert job.status == ProcessingStatus.FAILED
    assert job.error_category == ErrorCategory.AI_PROVIDER_ERROR
    assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 0


def test_provider_auth_failure_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)

    with pytest.raises(AIProviderAuthError):
        extract_invoice_from_document(
            db_session, local_storage, doc, provider=AuthFailureAIProvider()
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


def test_provider_empty_response_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)

    with pytest.raises(AIProviderResponseError):
        extract_invoice_from_document(
            db_session, local_storage, doc, provider=EmptyResponseAIProvider()
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


def test_malformed_structured_response_marks_document_failed(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)

    with pytest.raises(AIProviderResponseError):
        extract_invoice_from_document(
            db_session, local_storage, doc, provider=MalformedResponseAIProvider()
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED
    assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 0


def test_unexpected_provider_exception_is_translated_safely(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)

    with pytest.raises(ProcessingError):
        extract_invoice_from_document(
            db_session, local_storage, doc, provider=UnexpectedErrorAIProvider()
        )

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED
    job = (
        db_session.query(ProcessingJob)
        .filter(
            ProcessingJob.document_id == doc.id,
            ProcessingJob.stage == ProcessingStage.AI_EXTRACTION,
        )
        .first()
    )
    # The raw "Some unexpected SDK-internal failure." message must never
    # leak into the stored error_message.
    assert "SDK-internal" not in job.error_message


def test_empty_extracted_text_does_not_call_provider(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user, text="   \n\n  ")
    provider = FakeAIProvider(VALID_INVOICE_RESULT)

    with pytest.raises(EmptyExtractedTextError):
        extract_invoice_from_document(db_session, local_storage, doc, provider=provider)

    assert provider.call_count == 0
    db_session.refresh(doc)
    assert doc.status == DocumentStatus.FAILED


def test_ai_extraction_creates_processing_job_with_ai_extraction_stage(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)
    extract_invoice_from_document(
        db_session, local_storage, doc, provider=FakeAIProvider(VALID_INVOICE_RESULT)
    )

    job = (
        db_session.query(ProcessingJob)
        .filter(
            ProcessingJob.document_id == doc.id,
            ProcessingJob.stage == ProcessingStage.AI_EXTRACTION,
        )
        .first()
    )
    assert job is not None
    assert job.status == ProcessingStatus.SUCCESS
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.duration_ms is not None
    assert job.provider_name == "anthropic"


def test_repeated_extraction_does_not_call_provider_again(db_session, local_storage):
    """Cost control: an already-processed document must not trigger a
    second paid AI call."""
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(db_session, local_storage, org, user)
    provider = FakeAIProvider(VALID_INVOICE_RESULT)

    extract_invoice_from_document(db_session, local_storage, doc, provider=provider)
    assert provider.call_count == 1

    # Call again — a draft Invoice already exists for this document.
    extract_invoice_from_document(db_session, local_storage, doc, provider=provider)
    assert provider.call_count == 1  # unchanged — provider was not called again

    assert db_session.query(Invoice).filter(Invoice.document_id == doc.id).count() == 1


def test_document_text_is_passed_to_provider(db_session, local_storage):
    org, user = make_org_user(db_session)
    doc = make_text_extracted_document(
        db_session, local_storage, org, user, text="INVOICE #999 unique-marker-xyz"
    )
    provider = FakeAIProvider(VALID_INVOICE_RESULT)
    extract_invoice_from_document(db_session, local_storage, doc, provider=provider)

    assert "unique-marker-xyz" in provider.last_document_text
