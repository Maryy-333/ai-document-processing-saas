"""
Document upload endpoint.

See app/services/document_service.py module docstring for the important
caveat about organization_id/uploaded_by_user_id being temporary,
client-supplied, pre-authentication fields — NOT an authorization mechanism.
"""

import io
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Invoice
from app.models.enums import DocumentStatus
from app.ocr.engine import OCREngine, PaddleOCREngine
from app.processing.ai.anthropic_provider import AnthropicProvider
from app.processing.ai.provider import AIProvider
from app.processing.invoice_validation import extracted_invoice_from_orm, validate_invoice
from app.processing.validation import OversizedFileError, UploadValidationConfig
from app.schemas.document import (
    DocumentProcessingResponse,
    DocumentUploadResponse,
    ExtractTextRequest,
)
from app.schemas.review import (
    ApproveRequest,
    InvoiceReviewResponse,
    InvoiceUpdateRequest,
    RejectRequest,
)
from app.services.document_service import UploadFileInput, upload_document
from app.services.review_service import (
    ApprovalBlockedByValidationError,
    approve_document,
    get_review,
    reject_document,
    update_invoice,
)
from app.services.text_extraction_service import extract_document_text
from app.storage.base import StorageService
from app.storage.local import LocalStorageService

router = APIRouter(prefix="/documents", tags=["documents"])

# Read in fixed-size chunks and abort as soon as the configured max is
# exceeded, rather than buffering an arbitrarily large upload into memory
# first and rejecting it afterward.
_READ_CHUNK_SIZE = 64 * 1024
# Only the header bytes needed for the PDF magic-byte check.
_HEADER_PEEK_SIZE = 8


def get_storage_service(settings: Settings = Depends(get_settings)) -> StorageService:
    return LocalStorageService(settings.storage_dir)


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise OversizedFileError()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentUploadResponse, status_code=201)
async def upload(
    organization_id: uuid.UUID = Form(...),
    uploaded_by_user_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
):
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await _read_capped(file, max_bytes)

    if not content:
        # 0-byte upload — let the validation pipeline produce the standard
        # EmptyFileError rather than special-casing it here.
        header = b""
    else:
        header = content[:_HEADER_PEEK_SIZE]

    upload_input = UploadFileInput(
        filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(content),
        header_bytes=header,
        stream=io.BytesIO(content),
    )

    validation_config = UploadValidationConfig(
        max_size_bytes=max_bytes,
        allowed_extensions=settings.allowed_upload_extensions,
    )

    document = upload_document(
        db,
        storage,
        organization_id=organization_id,
        uploaded_by_user_id=uploaded_by_user_id,
        upload=upload_input,
        validation_config=validation_config,
    )
    return document


def get_ocr_engine(settings: Settings = Depends(get_settings)) -> OCREngine | None:
    """
    Returns None (OCR skipped) when disabled via configuration. Otherwise
    returns a PaddleOCREngine — construction itself is cheap and does not
    load any model (lazy-loaded on first .run() call), per Phase 6 §3.
    """
    if not settings.ocr_enabled:
        return None
    return PaddleOCREngine(language=settings.ocr_language)


def get_ai_provider(settings: Settings = Depends(get_settings)) -> AIProvider | None:
    """
    Returns None (AI extraction skipped) when no API key is configured —
    that absence IS the "disabled" signal, rather than a separate toggle.
    Construction itself makes no network call.
    """
    if not settings.ai_api_key:
        return None
    return AnthropicProvider(
        api_key=settings.ai_api_key,
        model=settings.ai_model_name,
        timeout_seconds=settings.ai_timeout_seconds,
    )


@router.post("/{document_id}/extract-text", response_model=DocumentProcessingResponse)
async def extract_text(
    document_id: uuid.UUID,
    request: ExtractTextRequest,
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
    ocr_engine: OCREngine | None = Depends(get_ocr_engine),
    ai_provider: AIProvider | None = Depends(get_ai_provider),
):
    document = extract_document_text(
        db,
        storage,
        document_id=document_id,
        organization_id=request.organization_id,
        min_extractable_text_chars=settings.meaningful_text_min_chars,
        ocr_engine=ocr_engine,
        ocr_enabled=settings.ocr_enabled,
        ocr_min_text_chars=settings.ocr_min_text_chars,
        ocr_dpi=settings.ocr_dpi,
        ai_provider=ai_provider,
    )

    validation_result = None
    if document.status == DocumentStatus.REVIEW_REQUIRED:
        invoice = db.query(Invoice).filter(Invoice.document_id == document.id).first()
        if invoice is not None:
            validation_result = validate_invoice(extracted_invoice_from_orm(invoice))

    return DocumentProcessingResponse(
        id=document.id,
        organization_id=document.organization_id,
        status=document.status,
        updated_at=document.updated_at,
        validation=validation_result,
    )


@router.get("/{document_id}/review", response_model=InvoiceReviewResponse)
async def review(
    document_id: uuid.UUID,
    organization_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
):
    document, invoice, validation_result = get_review(db, document_id, organization_id)
    return InvoiceReviewResponse(
        document_id=document.id,
        organization_id=document.organization_id,
        status=document.status,
        invoice=invoice,
        validation=validation_result,
    )


@router.patch("/{document_id}/invoice", response_model=InvoiceReviewResponse)
async def edit_invoice(
    document_id: uuid.UUID,
    request: InvoiceUpdateRequest,
    db: Session = Depends(get_db),
):
    document, invoice, validation_result = update_invoice(
        db,
        document_id,
        request.organization_id,
        request.edited_by_user_id,
        updates=request.model_dump(),
        fields_set=request.model_fields_set,
    )
    return InvoiceReviewResponse(
        document_id=document.id,
        organization_id=document.organization_id,
        status=document.status,
        invoice=invoice,
        validation=validation_result,
    )


@router.post("/{document_id}/approve", response_model=DocumentProcessingResponse)
async def approve(
    document_id: uuid.UUID,
    request: ApproveRequest,
    db: Session = Depends(get_db),
):
    try:
        document = approve_document(
            db, document_id, request.organization_id, request.approved_by_user_id
        )
    except ApprovalBlockedByValidationError as exc:
        # Richer than the generic {"error": ...} shape — includes the
        # blocking findings so the caller knows exactly what to fix.
        return JSONResponse(
            status_code=409,
            content={
                "error": exc.message,
                "validation": exc.validation_result.model_dump(mode="json"),
            },
        )
    return DocumentProcessingResponse(
        id=document.id,
        organization_id=document.organization_id,
        status=document.status,
        updated_at=document.updated_at,
        validation=None,
    )


@router.post("/{document_id}/reject", response_model=DocumentProcessingResponse)
async def reject(
    document_id: uuid.UUID,
    request: RejectRequest,
    db: Session = Depends(get_db),
):
    document = reject_document(
        db, document_id, request.organization_id, request.rejected_by_user_id, request.reason
    )
    return DocumentProcessingResponse(
        id=document.id,
        organization_id=document.organization_id,
        status=document.status,
        updated_at=document.updated_at,
        validation=None,
    )
