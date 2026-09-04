import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import hash_password, verify_password
from app.models import Organization, User
from app.services.auth_service import EmailAlreadyRegisteredError, authenticate_user, register_user


def test_register_user_creates_user_and_organization(db_session):
    user = register_user(db_session, "new@example.com", "password123")
    assert user.email == "new@example.com"
    assert user.organization_id is not None

    org = db_session.get(Organization, user.organization_id)
    assert org is not None


def test_register_user_hashes_password(db_session):
    user = register_user(db_session, "hashed@example.com", "plaintextpassword")
    assert user.hashed_password != "plaintextpassword"
    assert verify_password("plaintextpassword", user.hashed_password) is True


def test_register_user_duplicate_email_raises(db_session):
    register_user(db_session, "dup@example.com", "password123")
    with pytest.raises(EmailAlreadyRegisteredError):
        register_user(db_session, "dup@example.com", "differentpassword")


def test_register_user_persists_to_database(db_session):
    user = register_user(db_session, "persisted@example.com", "password123")
    fresh = db_session.query(User).filter(User.email == "persisted@example.com").first()
    assert fresh is not None
    assert fresh.id == user.id


def test_authenticate_user_valid_credentials(db_session):
    register_user(db_session, "auth@example.com", "correctpassword")
    user = authenticate_user(db_session, "auth@example.com", "correctpassword")
    assert user.email == "auth@example.com"


def test_authenticate_user_wrong_password_raises(db_session):
    register_user(db_session, "wrongpw@example.com", "correctpassword")
    with pytest.raises(AuthenticationError):
        authenticate_user(db_session, "wrongpw@example.com", "wrongpassword")


def test_authenticate_user_nonexistent_email_raises(db_session):
    with pytest.raises(AuthenticationError):
        authenticate_user(db_session, "doesnotexist@example.com", "anypassword")


def test_authenticate_user_inactive_user_rejected(db_session):
    org = Organization(name="Inactive Org")
    db_session.add(org)
    db_session.flush()

    user = User(
        organization_id=org.id,
        email="inactive@example.com",
        hashed_password=hash_password("password123"),
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    with pytest.raises(AuthenticationError):
        authenticate_user(db_session, "inactive@example.com", "password123")


def test_authenticate_user_error_message_does_not_reveal_which_field_was_wrong(db_session):
    """Wrong password and nonexistent user must raise the SAME error, so a
    caller can't enumerate registered emails via login failures."""
    register_user(db_session, "enum-test@example.com", "correctpassword")

    try:
        authenticate_user(db_session, "enum-test@example.com", "wrongpassword")
        wrong_password_message = None
    except AuthenticationError as exc:
        wrong_password_message = exc.message

    try:
        authenticate_user(db_session, "never-registered@example.com", "anypassword")
        nonexistent_user_message = None
    except AuthenticationError as exc:
        nonexistent_user_message = exc.message

    assert wrong_password_message == nonexistent_user_message
