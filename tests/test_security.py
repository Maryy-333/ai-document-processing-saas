import uuid

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_produces_different_hash_than_plaintext():
    hashed = hash_password("mypassword123")
    assert hashed != "mypassword123"


def test_hash_password_is_not_reversible_looking():
    hashed = hash_password("mypassword123")
    assert "mypassword123" not in hashed


def test_verify_password_correct():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed) is True


def test_verify_password_incorrect():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_malformed_hash_returns_false_not_raises():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hashing_same_password_twice_produces_different_hashes():
    """bcrypt salts each hash — two hashes of the same password must differ,
    even though both verify correctly."""
    h1 = hash_password("samepassword")
    h2 = hash_password("samepassword")
    assert h1 != h2
    assert verify_password("samepassword", h1) is True
    assert verify_password("samepassword", h2) is True


def test_create_and_decode_access_token_round_trip():
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret_key="testsecret", algorithm="HS256", expire_minutes=30
    )
    decoded = decode_access_token(token, secret_key="testsecret", algorithm="HS256")
    assert decoded == user_id


def test_decode_token_with_wrong_secret_raises():
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret_key="testsecret", algorithm="HS256", expire_minutes=30
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, secret_key="wrongsecret", algorithm="HS256")


def test_decode_expired_token_raises():
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret_key="testsecret", algorithm="HS256", expire_minutes=-1
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret_key="testsecret", algorithm="HS256")


def test_decode_malformed_token_raises():
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token("not.a.real.token", secret_key="testsecret", algorithm="HS256")


def test_token_contains_correct_user_identity():
    user_id = uuid.uuid4()
    token = create_access_token(
        user_id, secret_key="testsecret", algorithm="HS256", expire_minutes=30
    )
    payload = jwt.decode(token, "testsecret", algorithms=["HS256"])
    assert payload["sub"] == str(user_id)
