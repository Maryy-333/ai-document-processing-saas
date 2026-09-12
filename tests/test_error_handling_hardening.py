import logging
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.error_handlers import register_error_handlers
from app.core.exceptions import NotFoundError
from app.main import app as real_app


def _make_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/known-error")
    def known_error():
        raise NotFoundError("Widget not found.")

    @app.get("/unexpected-error")
    def unexpected_error():
        raise KeyError("internal_secret_key_name")

    return app


def test_known_apperror_handler_still_works():
    """Requirement 1: existing AppError-specific handlers continue working
    unchanged after adding the catch-all handler."""
    client = TestClient(_make_test_app(), raise_server_exceptions=False)
    response = client.get("/known-error")
    assert response.status_code == 404
    assert response.json() == {"error": "Widget not found."}


def test_unexpected_exception_returns_500():
    client = TestClient(_make_test_app(), raise_server_exceptions=False)
    response = client.get("/unexpected-error")
    assert response.status_code == 500


def test_unexpected_exception_does_not_expose_message():
    client = TestClient(_make_test_app(), raise_server_exceptions=False)
    response = client.get("/unexpected-error")
    assert "internal_secret_key_name" not in response.text
    assert "KeyError" not in response.text
    assert "Traceback" not in response.text
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"] == "An unexpected error occurred."


def test_unexpected_exception_is_logged(caplog):
    client = TestClient(_make_test_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        client.get("/unexpected-error")

    assert any(
        "Unhandled exception" in record.message and "/unexpected-error" in record.message
        for record in caplog.records
    )


def test_security_headers_present_on_response():
    client = TestClient(real_app)
    response = client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_security_headers_present_on_error_response():
    client = TestClient(real_app)
    response = client.get(f"/api/v1/documents/{uuid.uuid4()}/review")  # 401, no auth
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"


def test_debug_setting_wired_to_fastapi():
    settings = get_settings()
    assert real_app.debug == settings.debug
