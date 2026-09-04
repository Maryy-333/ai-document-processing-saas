"""
Password hashing and JWT token handling (Phase 10).

Password hashing uses bcrypt directly. JWT uses PyJWT with the algorithm/
secret/expiry already reserved in Settings since Phase 2.

Never log a raw password, a password hash, or a token — see app/core/logging
usage conventions elsewhere in this project.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

# Subject claim key. Kept as a module constant so create/decode agree on the
# exact claim name without duplicating a string literal.
_SUBJECT_CLAIM = "sub"


def hash_password(plain_password: str) -> str:
    """Returns a bcrypt hash, safe to store. Never store the plain password."""
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        # Malformed hash (shouldn't happen for rows we wrote ourselves, but
        # never let a malformed-hash edge case raise instead of just
        # reporting "doesn't match").
        return False


def create_access_token(
    user_id: UUID, *, secret_key: str, algorithm: str, expire_minutes: int
) -> str:
    now = datetime.now(UTC)
    payload = {
        _SUBJECT_CLAIM: str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_access_token(token: str, *, secret_key: str, algorithm: str) -> UUID:
    """
    Returns the user ID encoded in the token's subject claim.

    Raises jwt.InvalidTokenError (or a subclass, e.g. jwt.ExpiredSignatureError)
    on any signature, expiration, or malformed-token failure — callers (see
    app/api/deps.py) are responsible for translating that into the project's
    AuthenticationError, not this module.
    """
    payload = jwt.decode(token, secret_key, algorithms=[algorithm])
    subject = payload.get(_SUBJECT_CLAIM)
    if subject is None:
        raise jwt.InvalidTokenError("Token is missing a subject claim.")
    return UUID(subject)
