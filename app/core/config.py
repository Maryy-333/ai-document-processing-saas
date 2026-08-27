"""
Application configuration.

All configuration is loaded from environment variables (optionally via a .env
file for local development). Nothing here is hard-coded, and nothing here
should ever contain a real secret value — see .env.example for the variable
contract that a deployment must satisfy.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central settings object. Instantiated once via get_settings() and injected
    wherever configuration is needed, rather than imported as a global.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "AI Invoice Extraction Platform"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False

    # --- API ---
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    # Full SQLAlchemy connection URL, e.g.
    # postgresql+psycopg://user:password@localhost:5432/invoice_extractor
    # Not connected to yet in Phase 2 — this is configuration foundation only;
    # ORM models and migrations are introduced in Phase 3.
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/invoice_extractor"
    )

    # --- Auth (JWT) ---
    # Must be overridden via environment variable in every real deployment.
    # The insecure default is intentionally obvious and only usable for local dev.
    jwt_secret_key: str = Field(default="INSECURE-DEV-ONLY-CHANGE-ME")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # --- File storage (local filesystem for MVP) ---
    storage_dir: str = "./data/uploads"
    max_upload_size_mb: int = 15
    # PDF only for now. Phase 2 originally included .png/.jpg as a
    # placeholder; Phase 4 explicitly scopes upload support to PDF only and
    # image formats have not been separately approved, so this was
    # corrected here rather than carried forward silently.
    allowed_upload_extensions: tuple[str, ...] = (".pdf",)

    # --- Text extraction (Phase 5) ---
    # Minimum non-whitespace characters across a PDF's combined extracted
    # text for it to be considered "usable" digital text rather than a
    # scanned/image-only document requiring OCR (Phase 6).
    min_extractable_text_chars: int = 20

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- AI provider (implementation deferred to Phase 7) ---
    # Reserved now so the config contract is stable; no provider is called yet.
    ai_provider: str = "unset"
    ai_model_name: str = "unset"
    ai_api_key: str = ""

    # --- PDF text extraction (Phase 5) ---
    # Minimum non-whitespace character count across the whole document for
    # extracted text to be considered "meaningful". Below this, the document
    # is treated as textless/scanned and routed to OCR_REQUIRED (Phase 6).
    meaningful_text_min_chars: int = 20

    # --- OCR (Phase 6) ---
    ocr_enabled: bool = True
    ocr_language: str = "en"
    # Separate threshold from meaningful_text_min_chars (the same
    # has_meaningful_text() function is reused, only the numeric value
    # differs) since OCR output is noisier and may warrant a different bar.
    ocr_min_text_chars: int = 20
    ocr_dpi: int = 200

    @field_validator("jwt_secret_key")
    @classmethod
    def warn_on_insecure_secret(cls, value: str) -> str:
        # Deliberately not raising here — local/test environments are allowed
        # to run with the placeholder. Startup logging (app/core/logging.py +
        # main.py) is responsible for surfacing a loud warning if this default
        # is still in place outside of "local"/"test".
        return value


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings accessor. Use as a FastAPI dependency:
        settings: Settings = Depends(get_settings)
    or directly via get_settings() outside of request handling.
    """
    return Settings()
