"""
Fake OCR engines for tests. Satisfy the OCREngine Protocol without requiring
PaddleOCR or its model weights — tests must be deterministic and must not
require network access (Phase 6 §14.1).
"""

from app.ocr.engine import OCREngineError


class FakeOCREngine:
    """Returns a fixed string for every page, regardless of image content."""

    def __init__(self, text: str = "INVOICE Total: $100.00") -> None:
        self._text = text
        self.call_count = 0

    def run(self, image_bytes: bytes) -> str:
        self.call_count += 1
        return self._text


class EmptyOCREngine:
    """Simulates OCR running successfully but finding no usable text."""

    def run(self, image_bytes: bytes) -> str:
        return "   "


class FailingOCREngine:
    """Simulates a genuine OCR engine/inference failure."""

    def run(self, image_bytes: bytes) -> str:
        raise OCREngineError("Simulated OCR engine failure.")
