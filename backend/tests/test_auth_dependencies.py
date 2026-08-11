"""get_current_user() itself (backend/app/services/auth/dependencies.py) —
tests/test_rbac.py exercises require_role() with get_current_user overridden
to a fake, so no existing test actually calls the real function with a
deactivated user. Follows the same fake-DB-session convention as
tests/test_audit_logging.py.
"""

import uuid

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core.errors import AppError
from app.services.auth.dependencies import get_current_user
from app.services.auth.jwt import create_access_token


class _FakeUser:
    def __init__(self, id_, is_active):
        self.id = id_
        self.is_active = is_active


class _FakeSession:
    def __init__(self, user):
        self._user = user

    def get(self, model, user_id):
        return self._user if user_id == self._user.id else None


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_deactivated_user_is_rejected_with_403():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "user")
    db = _FakeSession(_FakeUser(user_id, is_active=False))

    with pytest.raises(AppError) as exc_info:
        get_current_user(credentials=_bearer(token), db=db)
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "user_inactive"


def test_active_user_is_accepted():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "user")
    db = _FakeSession(_FakeUser(user_id, is_active=True))

    user = get_current_user(credentials=_bearer(token), db=db)
    assert user.id == user_id


def test_missing_credentials_is_rejected_with_401():
    db = _FakeSession(_FakeUser(uuid.uuid4(), is_active=True))
    with pytest.raises(AppError) as exc_info:
        get_current_user(credentials=None, db=db)
    assert exc_info.value.status_code == 401


def test_token_for_a_deleted_user_is_rejected_with_401():
    real_id = uuid.uuid4()
    deleted_user_token = create_access_token(uuid.uuid4(), "user")
    db = _FakeSession(_FakeUser(real_id, is_active=True))  # token's subject isn't this user

    with pytest.raises(AppError) as exc_info:
        get_current_user(credentials=_bearer(deleted_user_token), db=db)
    assert exc_info.value.status_code == 401
