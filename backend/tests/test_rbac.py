import uuid
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.roles import Role
from app.db.postgres import get_db
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_role


def _fake_get_db():
    yield None


def _make_app(role: str):
    app = FastAPI()

    @app.get("/admin-only")
    def admin_only(user=Depends(require_role(Role.ADMIN))):
        return {"ok": True}

    @app.get("/admin-or-manager")
    def admin_or_manager(user=Depends(require_role(Role.ADMIN, Role.PLANT_MANAGER))):
        return {"ok": True}

    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def test_matching_role_is_allowed():
    client = TestClient(_make_app("admin"))
    response = client.get("/admin-only")
    assert response.status_code == 200


def test_insufficient_role_is_forbidden():
    client = TestClient(_make_app("user"))
    response = client.get("/admin-only")
    assert response.status_code == 403


def test_multiple_allowed_roles():
    client = TestClient(_make_app("plant_manager"))
    response = client.get("/admin-or-manager")
    assert response.status_code == 200


def test_unauthenticated_request_is_401():
    app = FastAPI()

    @app.get("/admin-only")
    def admin_only(user=Depends(require_role(Role.ADMIN))):
        return {"ok": True}

    app.dependency_overrides[get_db] = _fake_get_db
    client = TestClient(app)
    response = client.get("/admin-only")
    assert response.status_code == 401
