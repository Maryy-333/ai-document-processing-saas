"""
Application entrypoint / factory.

Run locally with:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.api.v1.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="AI-assisted invoice data extraction with mandatory human review.",
    )

    app.include_router(health_router, prefix=settings.api_v1_prefix)

    logger.info(
        "Application configured. environment=%s api_prefix=%s",
        settings.environment,
        settings.api_v1_prefix,
    )
    return app


app = create_app()
