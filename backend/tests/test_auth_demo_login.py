"""routers/auth.py's demo-login endpoints (/auth/demo-users, /auth/demo-login)
— a one-click way to sign in as a real seeded account per role, built on top
of the exact same token-issuance functions (create_access_token/
create_refresh_token) as normal password login (POST /auth/login), so a demo
session carries identical downstream authorization from that point on. Real
Postgres session (matches test_traces.py's convention), since /auth/demo-login
legitimately reads real users rows — the whole point is picking a REAL seeded
account, never a fake/mocked one.

Each test computes its own "expected account" via the exact same
_demo_account_for() helper the endpoint uses, rather than hardcoding an
expected email — this keeps the tests correct regardless of what real seed
data (scripts/seed_users.py) happens to already be loaded in the test
database, while the seeded_demo_accounts fixture guarantees at least one
active account exists per role so no test is skipped for lack of data.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import new_session
from app.models.audit_event import AuditEventModel
from app.models.user import UserModel
from app.routers import auth
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.jwt import create_access_token, decode_token
from app.services.auth.password import hash_password

ALL_DEMO_ROLES = ["employee", "hr", "project_manager", "ceo", "admin"]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app)


@pytest.fixture
def seeded_demo_accounts():
    """One throwaway active account per demo role — guarantees
    _demo_account_for() never returns None during these tests, regardless of
    whether scripts/seed_users.py has been run against this test database."""
    created_ids = []
    db = new_session()
    for demo_role, role in auth._DEMO_ROLE_MAP.items():
        user = UserModel(
            email=f"demo-login-test-{demo_role}-{uuid.uuid4().hex[:8]}@example.com",
            display_name=f"Demo Login Test ({demo_role})",
            password_hash=hash_password("Throwaway-Pass-1!"),
            is_active=True,
            role=role,
            department="manufacturing" if role == "user" else None,
        )
        db.add(user)
        db.commit()
        created_ids.append(user.id)
    db.close()
    try:
        yield
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id.in_(created_ids)).delete(synchronize_session=False)
        db.commit()
        db.close()


def _expected_account(role: str) -> UserModel:
    db = new_session()
    try:
        account = auth._demo_account_for(db, role)
        assert account is not None
        db.expunge(account)
        return account
    finally:
        db.close()


# ---- GET /auth/demo-users --------------------------------------------


def test_demo_users_lists_every_configured_role(seeded_demo_accounts):
    client = _client()

    response = client.get("/auth/demo-users")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    demo_roles = {tile["demo_role"] for tile in body["users"]}
    assert demo_roles == set(ALL_DEMO_ROLES)


def test_demo_user_tiles_have_display_name_and_description_and_correct_privilege_flag(seeded_demo_accounts):
    client = _client()

    body = client.get("/auth/demo-users").json()

    for tile in body["users"]:
        assert tile["display_name"]
        assert tile["description"]
        assert tile["is_privileged"] == (tile["demo_role"] == "admin")


def test_demo_user_tiles_expose_the_expected_account_email(seeded_demo_accounts):
    """The tile's email is what the login page types into the email field
    for the fill effect — must match whichever account demo-login would
    actually authenticate as, not just be present."""
    client = _client()

    body = client.get("/auth/demo-users").json()

    for tile in body["users"]:
        role = auth._DEMO_ROLE_MAP[tile["demo_role"]]
        expected = _expected_account(role)
        assert tile["email"] == expected.email


def test_demo_users_reports_disabled_when_the_feature_flag_is_off(monkeypatch):
    monkeypatch.setattr(settings, "demo_login_enabled", False)
    client = _client()

    response = client.get("/auth/demo-users")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "users": []}


# ---- POST /auth/demo-login ---------------------------------------------


@pytest.mark.parametrize("demo_role", ALL_DEMO_ROLES)
def test_demo_login_authenticates_as_the_expected_seeded_account(demo_role, seeded_demo_accounts):
    role = auth._DEMO_ROLE_MAP[demo_role]
    expected = _expected_account(role)
    client = _client()

    response = client.post("/auth/demo-login", json={"demo_role": demo_role})

    assert response.status_code == 200
    tokens = response.json()
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(expected.id)
    assert me.json()["email"] == expected.email
    assert me.json()["role"] == role


def test_demo_login_token_is_structurally_identical_to_a_normal_login_token(seeded_demo_accounts):
    """The actual security property: a demo-issued access token carries
    exactly the same claim shape as one create_access_token() would issue for
    that same real account directly — no extra "this is a demo session"
    marker is embedded in the JWT that downstream code could special-case."""
    role = auth._DEMO_ROLE_MAP["hr"]
    expected = _expected_account(role)
    client = _client()

    demo_tokens = client.post("/auth/demo-login", json={"demo_role": "hr"}).json()
    demo_claims = decode_token(demo_tokens["access_token"], expected_type="access")
    direct_claims = decode_token(create_access_token(expected.id, expected.role), expected_type="access")

    assert set(demo_claims.keys()) == set(direct_claims.keys())
    assert demo_claims["sub"] == direct_claims["sub"] == str(expected.id)
    assert demo_claims["role"] == direct_claims["role"] == expected.role


def test_demo_login_rejects_an_unconfigured_role_with_422(seeded_demo_accounts):
    client = _client()

    response = client.post("/auth/demo-login", json={"demo_role": "superadmin"})

    assert response.status_code == 422


def test_demo_login_is_disabled_when_the_feature_flag_is_off(monkeypatch, seeded_demo_accounts):
    monkeypatch.setattr(settings, "demo_login_enabled", False)
    client = _client()

    response = client.post("/auth/demo-login", json={"demo_role": "employee"})

    assert response.status_code == 404


def test_demo_login_writes_an_audit_event_with_a_distinguishing_reason_code(seeded_demo_accounts):
    role = auth._DEMO_ROLE_MAP["ceo"]
    expected = _expected_account(role)
    client = _client()

    client.post("/auth/demo-login", json={"demo_role": "ceo"})

    db = new_session()
    try:
        row = (
            db.query(AuditEventModel)
            .filter(AuditEventModel.actor_id == expected.id, AuditEventModel.reason_code == "demo_login")
            .order_by(AuditEventModel.created_at.desc())
            .first()
        )
    finally:
        db.close()
    assert row is not None
    assert row.event_type == AuditEventType.LOGIN_SUCCESS.value
    assert row.outcome == AuditOutcome.SUCCESS.value
    assert row.actor_role == role
