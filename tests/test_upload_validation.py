import pytest

from app.processing.validation import (
    EmptyFileError,
    InvalidFilenameError,
    InvalidPdfContentError,
    OversizedFileError,
    UnsupportedFileTypeError,
    UploadValidationConfig,
    run_upload_validation,
    validate_filename,
)

CONFIG = UploadValidationConfig(max_size_bytes=1024, allowed_extensions=(".pdf",))
VALID_PDF_HEADER = b"%PDF-1.4\n%\xc2\xa5\xc2\xb1\xc3\xab\n"


def test_valid_pdf_passes():
    filename = run_upload_validation(
        filename="invoice.pdf",
        content_type="application/pdf",
        size_bytes=100,
        header_bytes=VALID_PDF_HEADER,
        config=CONFIG,
    )
    assert filename == "invoice.pdf"


def test_unusual_but_present_mime_type_does_not_cause_rejection():
    """
    Approved instruction: a valid PDF must not be rejected solely for an
    unusual client-declared MIME type (e.g. generic octet-stream).
    """
    filename = run_upload_validation(
        filename="invoice.pdf",
        content_type="application/octet-stream",
        size_bytes=100,
        header_bytes=VALID_PDF_HEADER,
        config=CONFIG,
    )
    assert filename == "invoice.pdf"


def test_wrong_extension_rejected():
    with pytest.raises(UnsupportedFileTypeError):
        run_upload_validation(
            filename="invoice.txt",
            content_type="application/pdf",
            size_bytes=100,
            header_bytes=VALID_PDF_HEADER,
            config=CONFIG,
        )


def test_oversized_file_rejected():
    with pytest.raises(OversizedFileError):
        run_upload_validation(
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=CONFIG.max_size_bytes + 1,
            header_bytes=VALID_PDF_HEADER,
            config=CONFIG,
        )


def test_empty_file_rejected():
    with pytest.raises(EmptyFileError):
        run_upload_validation(
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=0,
            header_bytes=b"",
            config=CONFIG,
        )


def test_fake_pdf_with_pdf_extension_rejected():
    """A file named invoice.pdf with non-PDF content must be rejected."""
    with pytest.raises(InvalidPdfContentError):
        run_upload_validation(
            filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=100,
            header_bytes=b"MZ\x90\x00\x03\x00\x00\x00",  # Windows EXE header
            config=CONFIG,
        )


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../../etc/passwd.pdf",
        "..\\..\\windows\\system32\\evil.pdf",
        "/etc/passwd.pdf",
        "a" * 300 + ".pdf",  # excessively long
        "invoice\x00.pdf",  # null byte
    ],
)
def test_malicious_filenames_rejected(malicious_filename):
    with pytest.raises(InvalidFilenameError):
        validate_filename(malicious_filename)


def test_missing_filename_rejected():
    with pytest.raises(InvalidFilenameError):
        validate_filename(None)


def test_blank_filename_rejected():
    with pytest.raises(InvalidFilenameError):
        validate_filename("   ")
