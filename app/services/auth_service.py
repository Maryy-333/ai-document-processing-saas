"""
Authentication service (Phase 10).

ORGANIZATION-ASSIGNMENT DECISION (flagged, not silently decided — see
implementation report): a newly-registered user gets a brand-new
Organization created for them, named from their email. This is the
smallest architecture-consistent choice available — no organization-
management endpoint exists in this project, so there is no way for a
self-registering user to join an existing one. It reuses exactly the same
`Organization(name=...)` construction pattern already used throughout this
project's own tests, not a new mechanism.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import AuthenticationError, UserError
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Organization, User


class EmailAlreadyRegisteredError(UserError):
    message = "An account with this email already exists."


def register_user(db: Session, email: str, password: str) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        raise EmailAlreadyRegisteredError()

    organization = Organization(name=f"{email}'s Organization")
    db.add(organization)
    db.flush()  # assign organization.id before creating the user

    user = User(
        organization_id=organization.id,
        email=email,
        hashed_password=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """Raises AuthenticationError for any failure — deliberately the SAME
    error for "no such user" and "wrong password", so a caller can't use
    login failures to enumerate which emails are registered."""
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthenticationError("Incorrect email or password.")
    if not user.is_active:
        raise AuthenticationError("Incorrect email or password.")
    return user


def create_token_for_user(
    user_id: UUID, *, secret_key: str, algorithm: str, expire_minutes: int
) -> str:
    return create_access_token(
        user_id, secret_key=secret_key, algorithm=algorithm, expire_minutes=expire_minutes
    )
