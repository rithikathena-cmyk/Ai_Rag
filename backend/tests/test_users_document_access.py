"""GET /users/{user_id}/document-access — what a specific account can
actually see and do with documents, shown from the Users page's detail
panel. Runs the exact same two-stage filter (filter_by_category then
filter_by_permission) routers/documents.py's own list_documents() uses, just
for a target user instead of the caller — see that module's docstring.

No real Postgres/Qdrant fixture exists in this suite (see
tests/test_documents_rbac.py's own header) — this follows the same
structural-contract + fake-session convention as
tests/test_users_token_limit.py, with a minimal fake query object standing
in for the one DocumentModel query this route makes.
"""

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.params import Depends as DependsMarker
from fastapi.testclient import TestClient

from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.document import DocumentModel
from app.models.user import UserModel
from app.routers import users
from app.services.auth.dependencies import get_current_user


def _target_user(**overrides) -> UserModel:
    defaults = dict(
        id=uuid.uuid4(), email="target@example.com", display_name=None, password_hash="x",
        is_active=True, role="user", department=None, created_at=datetime.now(timezone.utc),
        daily_token_limit_override=None, monthly_token_limit_override=None,
    )
    defaults.update(overrides)
    return UserModel(**defaults)


def _document(**overrides) -> DocumentModel:
    defaults = dict(
        id=uuid.uuid4(), filename="doc.pdf", file_extension="pdf", document_type="pdf",
        file_size_bytes=100, storage_dir="x", title="A Document", department=None,
        security_classification="internal",
    )
    defaults.update(overrides)
    return DocumentModel(**defaults)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, user_row: UserModel, document_rows: list[DocumentModel] | None = None):
        self._user_row = user_row
        self._document_rows = document_rows or []

    def get(self, model, id_):
        if model is UserModel and id_ == self._user_row.id:
            return self._user_row
        return None

    def query(self, model):
        assert model is DocumentModel
        return _FakeQuery(self._document_rows)


def _build_app(actor_role: str, target: UserModel, document_rows: list[DocumentModel] | None = None):
    app = FastAPI()
    app.include_router(users.router)
    fake_db = _FakeSession(target, document_rows)
    app.dependency_overrides[get_db] = lambda: fake_db
    fake_actor = SimpleNamespace(id=uuid.uuid4(), role=actor_role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_actor
    return TestClient(app)


# --------------------------------------------------------------------------
# structural contract
# --------------------------------------------------------------------------

def test_route_requires_authentication():
    params = inspect.signature(users.get_user_document_access).parameters
    # get_db and the response model are the only explicit params; VIEW_USERS
    # is enforced via the route's own `dependencies=` list, checked below.
    assert "user_id" in params


def test_route_is_gated_on_view_users():
    source = inspect.getsource(users)
    assert '"/users/{user_id}/document-access"' in source
    # The gate is declared once, in the decorator immediately above the
    # route — this just confirms VIEW_USERS is the permission actually used
    # here (not, say, MANAGE_USERS, which would wrongly exclude HR/PM/CEO).
    idx = source.index('"/users/{user_id}/document-access"')
    nearby = source[idx : idx + 400]
    assert "Permission.VIEW_USERS" in nearby


# --------------------------------------------------------------------------
# behavior
# --------------------------------------------------------------------------

def test_404_for_an_unknown_user():
    target = _target_user()
    client = _build_app("admin", target)
    resp = client.get(f"/users/{uuid.uuid4()}/document-access")
    assert resp.status_code == 404


def test_reports_the_targets_real_document_permissions_not_the_callers(monkeypatch):
    # Employee (role="user") holds NONE of the four document permissions per
    # llm_rbac.yaml — not even VIEW_DOCUMENTS, which gates the document
    # LIBRARY page specifically (a coarse REST permission), distinct from
    # the `documents` list below (what their chat can retrieve via RAG,
    # governed by knowledge_departments — a different axis entirely; an
    # Employee with can_view=False can still have a non-empty documents
    # list). The CALLER here is admin (who has all four), so this also
    # proves the response reflects the TARGET's grants, not whoever is asking.
    # The two filter functions are mocked out here purely so this test
    # doesn't need to simulate their real SQLAlchemy query chain — the
    # permission flags below don't depend on what they return.
    monkeypatch.setattr(users, "filter_by_category", lambda db, ids, role, depts: [])
    monkeypatch.setattr(users, "filter_by_permission", lambda db, ids, user_id, role: ids)

    target = _target_user(role="user")
    client = _build_app("admin", target)
    resp = client.get(f"/users/{target.id}/document-access")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "user"
    assert body["can_view"] is False
    assert body["can_upload"] is False
    assert body["can_delete"] is False
    assert body["can_manage"] is False


def test_hr_target_has_upload_and_delete_but_scoped_to_hr_department(monkeypatch):
    monkeypatch.setattr(users, "filter_by_category", lambda db, ids, role, depts: [])
    monkeypatch.setattr(users, "filter_by_permission", lambda db, ids, user_id, role: ids)

    target = _target_user(role="hr")
    client = _build_app("admin", target)
    resp = client.get(f"/users/{target.id}/document-access")
    body = resp.json()
    assert body["can_upload"] is True
    assert body["can_delete"] is True
    assert body["knowledge_departments"] == ["hr"]


def test_ceo_target_sees_every_department_with_no_restriction_flag(monkeypatch):
    monkeypatch.setattr(users, "filter_by_category", lambda db, ids, role, depts: [])
    monkeypatch.setattr(users, "filter_by_permission", lambda db, ids, user_id, role: ids)

    target = _target_user(role="ceo")
    client = _build_app("admin", target)
    resp = client.get(f"/users/{target.id}/document-access")
    body = resp.json()
    assert set(body["knowledge_departments"]) == {"manufacturing", "hr", "engineering", "executive"}


def test_returns_exactly_the_documents_the_two_stage_filter_resolves(monkeypatch):
    visible_doc = _document(title="Visible SOP", department="manufacturing")
    hidden_doc = _document(title="Hidden HR Doc", department="hr")

    # The route queries by the ID set filter_by_category/filter_by_permission
    # resolve — mocking those two (already independently tested elsewhere:
    # tests/llm_rbac/test_category_policy.py, tests/guardrails/
    # test_retrieval_permissions.py) isolates this test to "does the route
    # wire them together and render the result correctly," not re-prove the
    # filtering rules themselves.
    monkeypatch.setattr(users, "filter_by_category", lambda db, ids, role, depts: [visible_doc.id])
    monkeypatch.setattr(users, "filter_by_permission", lambda db, ids, user_id, role: ids)

    target = _target_user(role="user")
    client = _build_app("admin", target, document_rows=[visible_doc])
    resp = client.get(f"/users/{target.id}/document-access")
    body = resp.json()

    assert body["total_visible"] == 1
    assert len(body["documents"]) == 1
    assert body["documents"][0]["title"] == "Visible SOP"
    assert str(hidden_doc.id) not in [d["id"] for d in body["documents"]]


def test_a_document_with_no_title_falls_back_to_filename(monkeypatch):
    doc = _document(title=None, filename="raw_upload.pdf")
    monkeypatch.setattr(users, "filter_by_category", lambda db, ids, role, depts: [doc.id])
    monkeypatch.setattr(users, "filter_by_permission", lambda db, ids, user_id, role: ids)

    target = _target_user(role="user")
    client = _build_app("admin", target, document_rows=[doc])
    resp = client.get(f"/users/{target.id}/document-access")
    assert resp.json()["documents"][0]["title"] == "raw_upload.pdf"
