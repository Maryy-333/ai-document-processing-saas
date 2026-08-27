"""
OCR engine abstraction.

OCREngine is the swappable boundary between OCR orchestration
(ocr_extraction.py) and a concrete OCR backend. This mirrors the existing
StorageService pattern (Protocol + one concrete implementation) already used
elsewhere in the project.

PaddleOCREngine lazily constructs the underlying PaddleOCR model on first use,
NOT at import time or __init__ time — importing this module (and even
instantiating PaddleOCREngine) never triggers a model download. The model is
only loaded the first time .run() is actually called, and that instance is
then reused for subsequent calls on the same engine object.
"""

from typing import Protocol

from app.core.exceptions import ProcessingError


class OCREngineError(ProcessingError):
    """A genuine OCR engine/inference failure (model load, crash, etc.)."""

    message = "OCR processing failed."


class OCREngine(Protocol):
    def run(self, image_bytes: bytes) -> str:
        """
        Runs OCR on a single rendered page image (PNG bytes) and returns the
        recognized text for that page. Raises OCREngineError on failure.
        """
        ...


class PaddleOCREngine:
    """
    Wraps PaddleOCR. The `paddleocr` import itself is safe (no network call),
    but constructing `paddleocr.PaddleOCR(...)` loads model weights and, per
    verified behavior in this environment, requires reaching one of
    HuggingFace / ModelScope / AIStudio / BOS to fetch them on first use.
    That network dependency is real and is documented as a manual
    verification / deployment requirement, not something this class can or
    should hide.
    """

    def __init__(self, language: str = "en") -> None:
        self._language = language
        self._model = None  # constructed lazily in _get_model()

    def _get_model(self):
        if self._model is None:
            import paddleocr

            try:
                self._model = paddleocr.PaddleOCR(lang=self._language)
            except Exception as exc:
                raise OCREngineError("Failed to initialize OCR engine.") from exc
        return self._model

    def run(self, image_bytes: bytes) -> str:
        model = self._get_model()

        try:
            import io as _io

            import numpy as np
            from PIL import Image

            image_array = np.array(Image.open(_io.BytesIO(image_bytes)).convert("RGB"))
        except Exception as exc:
            raise OCREngineError("Failed to decode rendered page image.") from exc

        try:
            result = model.predict(image_array)
        except Exception as exc:
            raise OCREngineError("OCR inference failed.") from exc

        try:
            texts: list[str] = []
            for page_result in result:
                texts.extend(page_result.get("rec_texts", []))
            return " ".join(texts)
        except Exception as exc:
            raise OCREngineError("Failed to parse OCR engine output.") from exc
