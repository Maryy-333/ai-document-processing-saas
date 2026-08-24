"""
Synthetic PDF fixtures for Phase 5 tests, generated with PyMuPDF (fitz)
itself — no additional dependency (e.g. reportlab) needed, since the
approved extraction library can also author minimal PDFs.

Deliberately tiny and synthetic — no real invoices or sensitive documents.
"""

import fitz


def make_text_pdf(text: str = "INVOICE #1234\nTotal: $100.00") -> bytes:
    """A single-page PDF with real, extractable text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def make_multipage_text_pdf(page_count: int = 3) -> bytes:
    """A multi-page PDF, each page with distinct extractable text."""
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} of {page_count} — invoice line items")
    data = doc.tobytes()
    doc.close()
    return data


def make_whitespace_only_pdf() -> bytes:
    """A page with only whitespace characters inserted as 'text'."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "   \n\n   \t  \n")
    data = doc.tobytes()
    doc.close()
    return data


def make_blank_pdf() -> bytes:
    """A page with no text content at all — simulates scanned/image-only."""
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def make_image_only_pdf() -> bytes:
    """
    A page containing only a rendered image (a filled rectangle), no text
    layer — the realistic shape of a scanned invoice page.
    """
    doc = fitz.open()
    page = doc.new_page()
    # Draw a filled rectangle as pixel content; no insert_text call at all,
    # so PyMuPDF's text layer is genuinely empty, same as a scanned page.
    page.draw_rect(fitz.Rect(50, 50, 400, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    return data


def make_corrupted_pdf() -> bytes:
    """Not a valid PDF at all — deliberately garbage bytes with a PDF-like header."""
    return b"%PDF-1.4\nthis is not actually a valid pdf structure\n%%GARBAGE"


def make_truncated_pdf() -> bytes:
    """A real PDF's header/start, cut off before the file is complete."""
    real = make_text_pdf()
    return real[: len(real) // 3]
