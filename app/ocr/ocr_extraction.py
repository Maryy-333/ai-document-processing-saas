"""
OCR orchestration.

Combines rendering (rendering.py) and a swappable OCREngine (engine.py) into
a single OCRResult, mirroring the shape of Phase 5's ExtractionResult so the
existing has_meaningful_text() threshold function can be reused as-is rather
than duplicating threshold logic here.
"""

from dataclasses import dataclass

from app.ocr.engine import OCREngine
from app.ocr.rendering import render_pdf_pages_to_images

# Pages are joined the same way Phase 5 joins native-extraction pages, for
# consistent downstream handling regardless of which path produced the text.
_PAGE_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class OCRResult:
    """
    Deliberately shaped like app.processing.pdf_extraction.ExtractionResult
    (same field names) so app.processing.pdf_extraction.has_meaningful_text()
    can be called with either result type without modification or
    duplicated threshold logic.
    """

    combined_text: str
    page_count: int
    non_whitespace_char_count: int


def extract_text_from_pdf_with_ocr(pdf_bytes: bytes, engine: OCREngine, dpi: int) -> OCRResult:
    """
    Renders every page of the given PDF and runs OCR on each via the
    injected engine. Raises PdfRenderError (page rendering failed) or
    OCREngineError (OCR engine failed) — both ProcessingError subclasses,
    handled by the caller (text_extraction_service.py) the same way native
    extraction failures are handled.
    """
    pages = render_pdf_pages_to_images(pdf_bytes, dpi=dpi)

    page_texts = [engine.run(page.png_bytes) for page in pages]
    combined_text = _PAGE_SEPARATOR.join(page_texts)
    non_whitespace_count = len("".join(combined_text.split()))

    return OCRResult(
        combined_text=combined_text,
        page_count=len(pages),
        non_whitespace_char_count=non_whitespace_count,
    )
