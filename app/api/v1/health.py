"""Health check endpoint — used by Docker/orchestration and deployment checks."""

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(settings: Settings = Depends(get_settings)) -> dict:
    """
    Liveness/readiness check.

    Intentionally does NOT verify database connectivity in Phase 2 (no models/
    migrations exist yet). Will be extended in a later phase to confirm DB
    reachability once that's meaningful.
    """
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "environment": settings.environment,
    }
