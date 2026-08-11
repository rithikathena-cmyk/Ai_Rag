import uuid
from datetime import timedelta

import pytest

from app.services.auth import jwt as jwt_module
from app.services.auth.jwt import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)


def test_access_token_roundtrip():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "admin")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip():
    user_id = uuid.uuid4()
    token = create_refresh_token(user_id, "user")
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "refresh"


def test_wrong_expected_type_rejected():
    user_id = uuid.uuid4()
    access = create_access_token(user_id, "user")
    with pytest.raises(TokenError):
        decode_token(access, expected_type="refresh")


def test_garbage_token_rejected():
    with pytest.raises(TokenError):
        decode_token("not-a-real-jwt", expected_type="access")


def test_expired_token_rejected():
    user_id = uuid.uuid4()
    expired = jwt_module._create_token(user_id, "user", "access", timedelta(seconds=-1))
    with pytest.raises(TokenError):
        decode_token(expired, expected_type="access")
