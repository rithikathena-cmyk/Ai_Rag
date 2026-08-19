"""routers/approvals.py — the generic approval queue (project submission,
document deletion). Same conventions as tests/test_projects_rbac.py."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.roles import Role
from app.db.postgres import get_db
from app.models.approval_request import ApprovalRequestModel
from app.models.document import DocumentModel
from app.models.project import ProjectModel
from app.routers import approvals, documents
from app.services.auth.dependencies import get_current_user


class _FakeQuery:
    """Just enough of the SQLAlchemy Query chain for list_approvals() —
    filter/order_by are no-ops (the test fixture is already the exact
    result set), count/offset/limit/all operate on the underlying list."""

    def __init__(self, items: list):
        self._items = items

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def count(self) -> int:
        return len(self._items)

    def offset(self, n: int):
        return self

    def limit(self, n: int):
        return self

    def all(self) -> list:
        return self._items


class _FakeSession:
    def __init__(self, approval, project=None):
        self._approval = approval
        self._project = project
        self.committed = False

    def get(self, model, id_):
        if model is ApprovalRequestModel:
            return self._approval if id_ == self._approval.id else None
        if model is ProjectModel:
            return self._project if self._project and id_ == self._project.id else None
        return None

    def query(self, model, *more):
        # *more: routers/approvals.py's _resolve_emails() does a
        # multi-column db.query(UserModel.id, UserModel.email) select — this
        # fixture has no user rows to resolve emails for regardless, so
        # every multi-column call is safe to fall through to the same empty
        # result the single-column "unknown model" case already returns.
        if not more and model is ApprovalRequestModel and self._approval is not None:
            return _FakeQuery([self._approval])
        return _FakeQuery([])

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _build_app(approval, project, role: str):
    app = FastAPI()
    app.include_router(approvals.router)
    app.dependency_overrides[get_db] = lambda: _FakeSession(approval, project)
    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, department=None, is_active=True, email="test-user@example.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def test_project_manager_cannot_list_approvals():
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=uuid.uuid4(), status="pending",
        created_at=datetime.now(timezone.utc),
    )
    client = TestClient(_build_app(approval, None, Role.PROJECT_MANAGER.value))
    response = client.get("/approvals")
    assert response.status_code == 403


def test_admin_can_list_approvals():
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=uuid.uuid4(), status="pending",
        created_at=datetime.now(timezone.utc),
    )
    client = TestClient(_build_app(approval, None, Role.ADMIN.value))
    response = client.get("/approvals")
    assert response.status_code == 200


def test_admin_approving_a_project_submission_activates_it():
    project = ProjectModel(id=uuid.uuid4(), name="x", status="submitted")
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=project.id, status="pending",
        created_at=datetime.now(timezone.utc),
    )
    client = TestClient(_build_app(approval, project, Role.ADMIN.value))
    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved"})
    assert response.status_code == 200
    assert project.status == "active"
    assert approval.status == "approved"


def test_admin_rejecting_a_project_submission():
    project = ProjectModel(id=uuid.uuid4(), name="x", status="submitted")
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=project.id, status="pending",
        created_at=datetime.now(timezone.utc),
    )
    client = TestClient(_build_app(approval, project, Role.ADMIN.value))
    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "rejected", "reason": "needs more detail"})
    assert response.status_code == 200
    assert project.status == "rejected"


class _FakeDocumentSession(_FakeSession):
    def __init__(self, approval, document):
        super().__init__(approval, None)
        self._document = document
        self.deleted = None

    def get(self, model, id_):
        if model is DocumentModel:
            return self._document if id_ == self._document.id else None
        return super().get(model, id_)

    def delete(self, obj):
        self.deleted = obj


def test_admin_approving_a_document_delete_request_deletes_it(monkeypatch):
    monkeypatch.setattr(documents, "delete_document_points", lambda document_id: None)
    monkeypatch.setattr(documents.shutil, "rmtree", lambda path, ignore_errors=True: None)

    document = DocumentModel(id=uuid.uuid4(), filename="x.pdf", storage_dir=None)
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="delete_document", target_type="document", target_id=document.id, status="pending",
        created_at=datetime.now(timezone.utc),
    )
    app = FastAPI()
    app.include_router(approvals.router)
    fake_db = _FakeDocumentSession(approval, document)
    app.dependency_overrides[get_db] = lambda: fake_db
    fake_user = SimpleNamespace(id=uuid.uuid4(), role=Role.ADMIN.value, department=None, is_active=True, email="admin@example.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    client = TestClient(app)

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved"})
    assert response.status_code == 200
    assert fake_db.deleted is document
    assert approval.status == "approved"


def test_admin_rejecting_a_document_delete_request_leaves_it_untouched(monkeypatch):
    document = DocumentModel(id=uuid.uuid4(), filename="x.pdf", storage_dir=None)
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="delete_document", target_type="document", target_id=document.id, status="pending",
        created_at=datetime.now(timezone.utc),
    )
    app = FastAPI()
    app.include_router(approvals.router)
    fake_db = _FakeDocumentSession(approval, document)
    app.dependency_overrides[get_db] = lambda: fake_db
    fake_user = SimpleNamespace(id=uuid.uuid4(), role=Role.ADMIN.value, department=None, is_active=True, email="admin@example.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    client = TestClient(app)

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "rejected"})
    assert response.status_code == 200
    assert fake_db.deleted is None
    assert approval.status == "rejected"


def test_deciding_an_already_decided_request_is_rejected():
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=uuid.uuid4(), status="approved",
    )
    client = TestClient(_build_app(approval, None, Role.ADMIN.value))
    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved"})
    assert response.status_code == 409
