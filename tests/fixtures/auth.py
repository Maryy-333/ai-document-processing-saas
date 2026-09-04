"""
Shared authentication helper for tests.

Generates a real, valid JWT using the same app.core.security functions and
the same Settings the running app will use to decode it — this exercises
the actual auth path end-to-end rather than bypassing it via a dependency
override, which is more valuable coverage for something this security
sensitive.
"""

from uuid import UUID

from app.core.config import get_settings
from app.core.security import create_access_token


def auth_headers(user_id: UUID) -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        user_id,
        secret_key=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_access_token_expire_minutes,
    )
    return {"Authorization": f"Bearer {token}"}
