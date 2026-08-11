import uuid
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission


def _fake_get_db():
    yield None


def _make_app(role: str):
    app = FastAPI()

    @app.get("/documents/upload")
    def upload(user=Depends(require_permission("UPLOAD_DOCUMENTS"))):
        return {"ok": True}

    @app.get("/settings")
    def settings(user=Depends(require_permission("SYSTEM_SETTINGS"))):
        return {"ok": True}

    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def test_role_with_granted_permission_is_allowed():
    client = TestClient(_make_app("project_manager"))
    response = client.get("/documents/upload")
    assert response.status_code == 200


def test_role_without_granted_permission_is_forbidden():
    # {"error": {"code", "message"}} is main.py's exception-handler shape,
    # not registered on this bare test app — matches tests/test_rbac.py's
    # convention of asserting status_code only, not response body shape.
    client = TestClient(_make_app("user"))
    response = client.get("/documents/upload")
    assert response.status_code == 403


def test_admin_wildcard_allows_every_permission():
    client = TestClient(_make_app("admin"))
    assert client.get("/documents/upload").status_code == 200
    assert client.get("/settings").status_code == 200


def test_ceo_does_not_get_system_settings():
    # The whole point of splitting CEO out from admin — CEO has broad
    # document/analytics access but not System Settings.
    client = TestClient(_make_app("ceo"))
    assert client.get("/documents/upload").status_code == 200
    response = client.get("/settings")
    assert response.status_code == 403


def test_employee_gets_neither_upload_nor_settings():
    client = TestClient(_make_app("user"))
    assert client.get("/documents/upload").status_code == 403
    assert client.get("/settings").status_code == 403


def test_unauthenticated_request_is_401():
    app = FastAPI()

    @app.get("/documents/upload")
    def upload(user=Depends(require_permission("UPLOAD_DOCUMENTS"))):
        return {"ok": True}

    app.dependency_overrides[get_db] = _fake_get_db
    client = TestClient(app)
    response = client.get("/documents/upload")
    assert response.status_code == 401


def test_accepts_permission_enum_or_plain_string():
    from app.core.permissions import Permission

    app = FastAPI()

    @app.get("/enum-form")
    def enum_form(user=Depends(require_permission(Permission.CHAT))):
        return {"ok": True}

    fake_user = SimpleNamespace(id=uuid.uuid4(), role="user", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    client = TestClient(app)
    assert client.get("/enum-form").status_code == 200
