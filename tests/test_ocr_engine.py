from io import BytesIO
from unittest.mock import MagicMock, patch

from PIL import Image

from app.ocr.engine import OCREngine, PaddleOCREngine


def _tiny_png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buf, format="PNG")
    return buf.getvalue()


def test_paddle_ocr_engine_satisfies_protocol():
    engine: OCREngine = PaddleOCREngine()
    assert hasattr(engine, "run")


def test_construction_does_not_load_model():
    """Constructing the engine must not touch PaddleOCR at all — the model
    is only built lazily on first .run() call (Phase 6 §3 requirement)."""
    engine = PaddleOCREngine(language="en")
    assert engine._model is None


def test_model_is_constructed_lazily_on_first_run():
    engine = PaddleOCREngine(language="en")
    png_bytes = _tiny_png_bytes()

    fake_model = MagicMock()
    fake_model.predict.return_value = [{"rec_texts": ["hello", "world"]}]

    with patch("paddleocr.PaddleOCR", return_value=fake_model) as mock_ctor:
        result = engine.run(png_bytes)
        assert mock_ctor.call_count == 1
        assert "hello" in result
        assert "world" in result

        # A second call must reuse the already-constructed model, not
        # re-instantiate it.
        engine.run(png_bytes)
        assert mock_ctor.call_count == 1
