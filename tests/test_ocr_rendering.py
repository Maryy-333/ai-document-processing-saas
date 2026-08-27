import pytest

from app.ocr.rendering import PdfRenderError, render_pdf_pages_to_images
from tests.fixtures.pdf_fixtures import (
    make_corrupted_pdf,
    make_image_only_pdf,
    make_multipage_text_pdf,
    make_text_pdf,
)


def test_renders_single_page_pdf():
    pages = render_pdf_pages_to_images(make_text_pdf("hello"))
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].png_bytes.startswith(b"\x89PNG")


def test_renders_multipage_pdf_in_order():
    pages = render_pdf_pages_to_images(make_multipage_text_pdf(3))
    assert len(pages) == 3
    assert [p.page_number for p in pages] == [1, 2, 3]
    for p in pages:
        assert p.png_bytes.startswith(b"\x89PNG")


def test_renders_image_only_pdf():
    """The realistic scanned-page shape — no text layer, still renders fine."""
    pages = render_pdf_pages_to_images(make_image_only_pdf())
    assert len(pages) == 1
    assert len(pages[0].png_bytes) > 0


def test_corrupted_pdf_raises_render_error():
    with pytest.raises(PdfRenderError):
        render_pdf_pages_to_images(make_corrupted_pdf())


def test_dpi_affects_output_size():
    """Higher DPI should produce a larger rendered image (sanity check that
    the dpi parameter is actually being used, not ignored)."""
    low = render_pdf_pages_to_images(make_text_pdf("x"), dpi=72)
    high = render_pdf_pages_to_images(make_text_pdf("x"), dpi=300)
    assert len(high[0].png_bytes) > len(low[0].png_bytes)


def test_no_temp_files_left_behind(tmp_path, monkeypatch):
    """Rendering must be in-memory only — no temp files created anywhere."""
    import tempfile

    before = set(__import__("os").listdir(tempfile.gettempdir()))
    render_pdf_pages_to_images(make_multipage_text_pdf(2))
    after = set(__import__("os").listdir(tempfile.gettempdir()))
    assert after == before
