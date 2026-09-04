"""
Shared FastAPI dependencies (Phase 10).

get_current_user is the single source of truth for identity across every
authenticated endpoint — no route may accept organization_id/user_id as
trusted request fields once this dependency is in use.
"""

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User

# auto_error=False: a missing/malformed header returns None instead of
# FastAPI's default HTTPException(403) — we want to raise our own
# AuthenticationError (401) through the project's existing error-handler
# architecture, not a second, inconsistent error shape.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing or malformed Authorization header.")

    try:
        user_id = decode_access_token(
            credentials.credentials,
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    except jwt.InvalidTokenError:
        # Covers expired signatures, bad signatures, malformed tokens, and
        # a token missing its subject claim — all the same 401 to the
        # caller, since none of them are the caller's business to
        # distinguish.
        raise AuthenticationError("Invalid or expired token.") from None

    user = db.get(User, user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired token.") from None
    if not user.is_active:
        raise AuthenticationError("Invalid or expired token.") from None

    return user
