import pytest

from app.ocr.engine import OCREngineError
from app.ocr.ocr_extraction import extract_text_from_pdf_with_ocr
from app.ocr.rendering import PdfRenderError
from app.processing.pdf_extraction import has_meaningful_text
from tests.fixtures.fake_ocr_engine import EmptyOCREngine, FailingOCREngine, FakeOCREngine
from tests.fixtures.pdf_fixtures import (
    make_corrupted_pdf,
    make_image_only_pdf,
    make_multipage_text_pdf,
)


def test_ocr_combines_text_from_single_page():
    engine = FakeOCREngine("INVOICE #42 Total: $99.00")
    result = extract_text_from_pdf_with_ocr(make_image_only_pdf(), engine, dpi=150)
    assert result.page_count == 1
    assert "INVOICE #42" in result.combined_text
    assert result.non_whitespace_char_count > 0
    assert engine.call_count == 1


def test_ocr_combines_text_from_multiple_pages_deterministically():
    engine = FakeOCREngine("PAGE TEXT")
    # multipage_text_pdf pages have no images, but rendering doesn't care —
    # it rasterizes whatever is on the page, text or not.
    result = extract_text_from_pdf_with_ocr(make_multipage_text_pdf(3), engine, dpi=150)
    assert result.page_count == 3
    assert engine.call_count == 3
    # Combined text should contain the page text 3 times, joined deterministically.
    assert result.combined_text.count("PAGE TEXT") == 3


def test_ocr_result_is_meaningful_when_engine_finds_text():
    engine = FakeOCREngine("Real invoice content here")
    result = extract_text_from_pdf_with_ocr(make_image_only_pdf(), engine, dpi=150)
    assert has_meaningful_text(result, min_chars=20) is True


def test_ocr_result_not_meaningful_when_engine_finds_nothing():
    engine = EmptyOCREngine()
    result = extract_text_from_pdf_with_ocr(make_image_only_pdf(), engine, dpi=150)
    assert result.non_whitespace_char_count == 0
    assert has_meaningful_text(result, min_chars=20) is False


def test_ocr_engine_failure_propagates():
    engine = FailingOCREngine()
    with pytest.raises(OCREngineError):
        extract_text_from_pdf_with_ocr(make_image_only_pdf(), engine, dpi=150)


def test_ocr_rendering_failure_propagates_before_engine_is_called():
    engine = FakeOCREngine()
    with pytest.raises(PdfRenderError):
        extract_text_from_pdf_with_ocr(make_corrupted_pdf(), engine, dpi=150)
    assert engine.call_count == 0


def test_ocr_unicode_text_handled():
    engine = FakeOCREngine("Café Résumé 発票 €100.00")
    result = extract_text_from_pdf_with_ocr(make_image_only_pdf(), engine, dpi=150)
    assert "Café" in result.combined_text
    assert "発票" in result.combined_text
    # Must be safely UTF-8 encodable (this is what gets written to storage).
    result.combined_text.encode("utf-8")
