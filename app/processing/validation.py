"""
Upload validation for Phase 4.

Validates, in order, short-circuiting on first failure:
  1. filename safety/length
  2. extension
  3. declared MIME type (supporting evidence only, never authoritative)
  4. file size
  5. empty-file check
  6. PDF magic-byte check

IMPORTANT: the magic-byte check only proves the file begins with the PDF
signature (%PDF-). It does NOT prove the file is a structurally valid,
fully-parseable PDF — that's Phase 5's job (PyMuPDF will be the real test).
A file could pass this check and still fail real PDF parsing later; that is
expected and acceptable at this phase.
"""

import re
from dataclasses import dataclass

from app.core.exceptions import UserError, ValidationError

PDF_MAGIC_BYTES = b"%PDF-"

_MAX_FILENAME_LENGTH = 255
# Reject anything containing a path separator, null byte, or leading dot-dot —
# a safe filename should just be a bare display name, never a path fragment.
_UNSAFE_FILENAME_PATTERN = re.compile(r"[/\\\x00]")


class UnsupportedFileTypeError(UserError):
    message = "Unsupported file type. Only PDF is currently supported."


class OversizedFileError(UserError):
    message = "File exceeds the maximum allowed size."


class EmptyFileError(UserError):
    message = "Uploaded file is empty."


class InvalidFilenameError(UserError):
    message = "Filename is invalid or unsafe."


class InvalidPdfContentError(ValidationError):
    message = "File content does not match a valid PDF."


@dataclass(frozen=True)
class UploadValidationConfig:
    max_size_bytes: int
    allowed_extensions: tuple[str, ...]


def validate_filename(filename: str | None) -> str:
    """
    Validates the client-supplied filename as METADATA ONLY. This value is
    never used to construct a filesystem path — see app/storage/local.py and
    document_service.py, which always use a server-generated storage key.
    """
    if not filename or not filename.strip():
        raise InvalidFilenameError("Filename is required.")

    if len(filename) > _MAX_FILENAME_LENGTH:
        raise InvalidFilenameError("Filename is too long.")

    if _UNSAFE_FILENAME_PATTERN.search(filename):
        raise InvalidFilenameError("Filename contains unsafe characters.")

    # ".." anywhere (not just as a full path segment) is rejected — this is
    # intentionally stricter than "is it a real path traversal", because the
    # filename should never be treated as path-like at all.
    if ".." in filename:
        raise InvalidFilenameError("Filename contains unsafe characters.")

    return filename


def validate_extension(filename: str, config: UploadValidationConfig) -> None:
    lowered = filename.lower()
    if not any(lowered.endswith(ext) for ext in config.allowed_extensions):
        raise UnsupportedFileTypeError()


def validate_declared_content_type(content_type: str | None) -> None:
    """
    Supporting evidence only, per the approved instructions: an unusual or
    generic client-declared MIME type (e.g. application/octet-stream) must
    NOT by itself cause rejection. Only used for informational/logging
    purposes at this phase; the magic-byte check is what actually gates
    acceptance.
    """
    return None


def validate_size(size_bytes: int, config: UploadValidationConfig) -> None:
    if size_bytes <= 0:
        raise EmptyFileError()
    if size_bytes > config.max_size_bytes:
        raise OversizedFileError()


def validate_pdf_magic_bytes(header: bytes) -> None:
    if not header.startswith(PDF_MAGIC_BYTES):
        raise InvalidPdfContentError()


def run_upload_validation(
    *,
    filename: str | None,
    content_type: str | None,
    size_bytes: int,
    header_bytes: bytes,
    config: UploadValidationConfig,
) -> str:
    """
    Runs the full validation pipeline in order, short-circuiting on the first
    failure. Returns the validated filename on success.
    """
    safe_filename = validate_filename(filename)
    validate_extension(safe_filename, config)
    validate_declared_content_type(content_type)
    validate_size(size_bytes, config)
    validate_pdf_magic_bytes(header_bytes)
    return safe_filename
