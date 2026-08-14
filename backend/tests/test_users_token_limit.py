"""routers/users.py's PUT /users/{user_id}/token-limit — the per-user daily/
monthly token quota override Admin/CEO can set on top of a role's default
(llm_rbac.yaml), enforced via services/llm_rbac/quotas.py::effective_quotas()
in services/llm_rbac/engine.py. Same _FakeSession-per-model convention as
tests/test_employee_pii_approval.py.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.models.user import UserModel
from app.routers import users
from app.services.auth.dependencies import get_current_user


class _FakeSession:
    def __init__(self, user_row: UserModel):
        self._user_row = user_row
        self.committed = False

    def get(self, model, id_):
        if model is UserModel and id_ == self._user_row.id:
            return self._user_row
        return None

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _target_user(**overrides) -> UserModel:
    defaults = dict(
        id=uuid.uuid4(), email="target@example.com", display_name=None, password_hash="x",
        is_active=True, role="user", department=None, created_at=datetime.now(timezone.utc),
        daily_token_limit_override=None, monthly_token_limit_override=None,
    )
    defaults.update(overrides)
    return UserModel(**defaults)


def _build_app(actor_role: str, target: UserModel):
    app = FastAPI()
    app.include_router(users.router)
    fake_db = _FakeSession(target)
    app.dependency_overrides[get_db] = lambda: fake_db
    fake_actor = SimpleNamespace(id=uuid.uuid4(), role=actor_role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_actor
    return TestClient(app), fake_db


@pytest.mark.parametrize("role", ["admin", "ceo"])
def test_admin_and_ceo_can_set_token_limit(role):
    target = _target_user()
    client, fake_db = _build_app(role, target)
    response = client.put(f"/users/{target.id}/token-limit", json={"daily_tokens": 5000, "monthly_tokens": 100000})
    assert response.status_code == 200
    body = response.json()
    assert body["daily_token_limit_override"] == 5000
    assert body["monthly_token_limit_override"] == 100000
    assert target.daily_token_limit_override == 5000
    assert target.monthly_token_limit_override == 100000
    assert fake_db.committed


@pytest.mark.parametrize("role", ["user", "hr", "project_manager"])
def test_other_roles_are_forbidden(role):
    target = _target_user()
    client, _ = _build_app(role, target)
    response = client.put(f"/users/{target.id}/token-limit", json={"daily_tokens": 5000})
    assert response.status_code == 403


def test_omitting_a_field_clears_that_override():
    # PUT semantics: the body is the full desired state, so leaving
    # daily_tokens out (implicit null) clears a previously-set override back
    # to the role default rather than leaving it untouched.
    target = _target_user(daily_token_limit_override=5000, monthly_token_limit_override=100000)
    client, _ = _build_app("admin", target)
    response = client.put(f"/users/{target.id}/token-limit", json={"monthly_tokens": 200000})
    assert response.status_code == 200
    body = response.json()
    assert body["daily_token_limit_override"] is None
    assert body["monthly_token_limit_override"] == 200000


def test_missing_user_is_404():
    target = _target_user()
    client, _ = _build_app("admin", target)
    response = client.put(f"/users/{uuid.uuid4()}/token-limit", json={"daily_tokens": 5000})
    assert response.status_code == 404


def test_rejects_non_positive_token_value():
    target = _target_user()
    client, _ = _build_app("admin", target)
    response = client.put(f"/users/{target.id}/token-limit", json={"daily_tokens": 0})
    assert response.status_code == 422


def test_get_user_response_includes_override_fields():
    target = _target_user(daily_token_limit_override=1234, monthly_token_limit_override=None)
    client, _ = _build_app("admin", target)
    response = client.get(f"/users/{target.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_token_limit_override"] == 1234
    assert body["monthly_token_limit_override"] is None
