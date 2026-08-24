"""
PDF text extraction via PyMuPDF (fitz).

This module operates ONLY on bytes already retrieved through StorageService
(see app/services/text_extraction_service.py) — it never touches the
filesystem or a client-supplied path itself. PyMuPDF opens PDFs in a
sandboxed, in-memory manner here (stream=..., no external file references
followed); embedded files/JavaScript are not executed since we only ever
call get_text(), never any action-execution API.

IMPORTANT: this module proves a PDF is READABLE and yields a text/meaningful
verdict. It is not a full PDF structural validator — a PDF that opens and
yields a page count but is otherwise malformed downstream is still handled
as best-effort (per-page extraction failures are tolerated, not fatal).
"""

from dataclasses import dataclass

import fitz

from app.core.exceptions import ProcessingError

# Pages are joined with a form-feed-style separator so page boundaries are
# still visible in the combined text without polluting normal paragraph
# breaks within a page.
_PAGE_SEPARATOR = "\n\n"


class PdfOpenError(ProcessingError):
    """The PDF could not be opened at all (corrupted/malformed/not a PDF)."""

    message = "The stored file could not be read as a PDF."


@dataclass(frozen=True)
class ExtractionResult:
    combined_text: str
    page_count: int
    non_whitespace_char_count: int


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> ExtractionResult:
    """
    Opens the given bytes as a PDF and extracts text from every page.

    Raises PdfOpenError if the document cannot be opened at all. Per-page
    extraction failures are logged-worthy but not fatal — a page that fails
    to yield text contributes an empty string rather than aborting the whole
    document, since one bad page shouldn't discard text successfully
    extracted from the rest of a multi-page invoice.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfOpenError() from exc

    try:
        page_texts: list[str] = []
        for page in doc:
            try:
                page_texts.append(page.get_text() or "")
            except Exception:
                page_texts.append("")
        page_count = doc.page_count
    finally:
        doc.close()

    combined_text = _PAGE_SEPARATOR.join(page_texts)
    non_whitespace_count = len("".join(combined_text.split()))

    return ExtractionResult(
        combined_text=combined_text,
        page_count=page_count,
        non_whitespace_char_count=non_whitespace_count,
    )


def has_meaningful_text(result: ExtractionResult, min_chars: int) -> bool:
    """
    Deterministic threshold check: is there enough non-whitespace text to
    treat this as a genuine digital-text PDF rather than a scanned/
    image-only document that needs OCR (Phase 6)?
    """
    return result.non_whitespace_char_count >= min_chars
