import pytest

from app.processing.pdf_extraction import (
    PdfOpenError,
    extract_text_from_pdf_bytes,
    has_meaningful_text,
)
from tests.fixtures.pdf_fixtures import (
    make_blank_pdf,
    make_corrupted_pdf,
    make_image_only_pdf,
    make_multipage_text_pdf,
    make_text_pdf,
    make_truncated_pdf,
    make_whitespace_only_pdf,
)

THRESHOLD = 20


def test_extracts_text_from_digital_pdf():
    result = extract_text_from_pdf_bytes(make_text_pdf("INVOICE #1234\nTotal: $100.00"))
    assert result.page_count == 1
    assert "INVOICE" in result.combined_text
    assert result.non_whitespace_char_count > 0
    assert has_meaningful_text(result, THRESHOLD) is True


def test_extracts_text_from_multipage_pdf():
    result = extract_text_from_pdf_bytes(make_multipage_text_pdf(page_count=3))
    assert result.page_count == 3
    assert "Page 1" in result.combined_text
    assert "Page 2" in result.combined_text
    assert "Page 3" in result.combined_text


def test_whitespace_only_text_is_not_meaningful():
    result = extract_text_from_pdf_bytes(make_whitespace_only_pdf())
    assert result.non_whitespace_char_count == 0
    assert has_meaningful_text(result, THRESHOLD) is False


def test_blank_pdf_is_not_meaningful():
    result = extract_text_from_pdf_bytes(make_blank_pdf())
    assert has_meaningful_text(result, THRESHOLD) is False


def test_image_only_pdf_detected_as_textless():
    """Simulates a scanned invoice page — must route toward OCR, not be
    treated as successful digital extraction."""
    result = extract_text_from_pdf_bytes(make_image_only_pdf())
    assert result.non_whitespace_char_count == 0
    assert has_meaningful_text(result, THRESHOLD) is False


def test_meaningful_text_threshold_is_configurable():
    result = extract_text_from_pdf_bytes(make_text_pdf("short"))
    # "short" -> 5 non-whitespace chars. Below a high threshold...
    assert has_meaningful_text(result, 100) is False
    # ...but above a low one.
    assert has_meaningful_text(result, 3) is True


def test_meaningful_text_exact_boundary():
    result = extract_text_from_pdf_bytes(make_text_pdf("a" * 20))
    assert result.non_whitespace_char_count == 20
    assert has_meaningful_text(result, 20) is True  # >= threshold, inclusive
    assert has_meaningful_text(result, 21) is False


def test_corrupted_pdf_raises_pdf_open_error():
    with pytest.raises(PdfOpenError):
        extract_text_from_pdf_bytes(make_corrupted_pdf())


def test_truncated_pdf_raises_pdf_open_error():
    with pytest.raises(PdfOpenError):
        extract_text_from_pdf_bytes(make_truncated_pdf())


def test_empty_bytes_raises_pdf_open_error():
    with pytest.raises(PdfOpenError):
        extract_text_from_pdf_bytes(b"")


def test_non_pdf_bytes_raises_pdf_open_error():
    with pytest.raises(PdfOpenError):
        extract_text_from_pdf_bytes(b"just some random bytes that are not a pdf at all")
