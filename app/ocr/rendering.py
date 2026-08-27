"""
PDF page rendering for OCR.

Uses PyMuPDF (already the project's approved PDF library) to rasterize each
page to a PNG image entirely in memory — no temporary files, no filesystem
paths. Operates only on bytes already retrieved through StorageService (see
app/ocr/ocr_extraction.py); this module never touches a filesystem path or
client-supplied filename itself.

Does not execute embedded PDF JavaScript/actions: rendering a page to a
pixmap only rasterizes visual content, it never invokes any action/script
API.
"""

from dataclasses import dataclass

import fitz

from app.core.exceptions import ProcessingError

# 200 DPI is a reasonable default for OCR accuracy vs. memory/time cost on
# typical invoice scans; configurable via Settings.ocr_dpi rather than
# hard-coded at the call site.
_DEFAULT_DPI = 200


class PdfRenderError(ProcessingError):
    """A PDF page could not be rendered to an image."""

    message = "The stored file could not be rendered for OCR."


@dataclass(frozen=True)
class RenderedPage:
    page_number: int  # 1-indexed, for readable logging/debugging
    png_bytes: bytes


def render_pdf_pages_to_images(pdf_bytes: bytes, dpi: int = _DEFAULT_DPI) -> list[RenderedPage]:
    """
    Renders every page of the given PDF to an in-memory PNG image.

    Raises PdfRenderError if the PDF cannot be opened or a page fails to
    render. Unlike native text extraction (which tolerates a single bad
    page), a rendering failure here is treated as fatal for the whole
    document — a missing page image would silently drop that page's content
    from OCR with no way to detect it later, which is worse than failing
    loudly and letting the document be retried.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PdfRenderError() from exc

    matrix = fitz.Matrix(dpi / 72, dpi / 72)

    try:
        pages: list[RenderedPage] = []
        for index, page in enumerate(doc):
            try:
                pixmap = page.get_pixmap(matrix=matrix)
                png_bytes = pixmap.tobytes("png")
            except Exception as exc:
                raise PdfRenderError() from exc
            pages.append(RenderedPage(page_number=index + 1, png_bytes=png_bytes))
        return pages
    finally:
        doc.close()
