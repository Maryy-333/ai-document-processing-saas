"""
Logging configuration.

Design goals (per project security/observability requirements):
- Structured, consistent format across the app.
- Never log secrets, credentials, or raw document/invoice content here.
  Callers are responsible for passing safe, minimal context (e.g. document_id,
  stage, duration) rather than full payloads.
- Log level configurable via environment (Settings.log_level).
"""

import logging
import sys

from app.core.config import Settings, get_settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(settings: Settings | None = None) -> None:
    """
    Configure the root logger once at application startup.
    Idempotent: safe to call multiple times (e.g. in tests).
    """
    settings = settings or get_settings()

    root = logging.getLogger()
    root.setLevel(settings.log_level)

    # Avoid duplicate handlers if configure_logging() is called more than once
    # (e.g. once by the app, once by a test fixture).
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(handler)

    if settings.environment not in ("local", "test") and settings.jwt_secret_key.startswith(
        "INSECURE-DEV-ONLY"
    ):
        logging.getLogger(__name__).warning(
            "JWT_SECRET_KEY is set to the insecure default in a non-local "
            "environment ('%s'). This MUST be overridden before real use.",
            settings.environment,
        )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
