"""services/projects/service.py — CRUD + lifecycle orchestration. Fake-session
pattern (matches tests/test_audit_logging.py): no live Postgres, just enough
of a Session stand-in to observe what gets added/committed. add_members()/
reallocate_members() (the two functions that actually query existing rows)
are exercised indirectly via tests/test_projects_rbac.py's structural checks
rather than duplicated here — everything else in service.py never queries,
only adds/commits, which this fake supports directly.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.models.approval_request import ApprovalRequestModel
from app.models.project import ProjectModel
from app.services.projects import service


class _FakeSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    def add_all(self, objs) -> None:
        self.added.extend(objs)

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj) -> None:
        pass


def test_create_project_starts_as_draft():
    db = _FakeSession()
    manager_id, creator_id = uuid.uuid4(), uuid.uuid4()
    project = service.create_project(
        db, name="New Line", description="desc", department="engineering",
        manager_id=manager_id, created_by=creator_id,
    )
    assert project.status == "draft"
    assert project.manager_id == manager_id
    assert project.created_by == creator_id
    assert db.committed


def test_update_project_rejects_non_draft_without_allow_any_status():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="active")
    with pytest.raises(AppError) as exc_info:
        service.update_project(db, project, name="y")
    assert exc_info.value.status_code == 409


def test_update_project_allows_non_draft_for_admin():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="active")
    updated = service.update_project(db, project, name="y", allow_any_status=True)
    assert updated.name == "y"


def test_update_project_on_rejected_resubmits_to_draft():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="rejected")
    updated = service.update_project(db, project, name="y")
    assert updated.status == "draft"
    assert updated.name == "y"


def test_submit_for_approval_creates_a_pending_request_and_moves_to_submitted():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="draft")
    user = SimpleNamespace(id=uuid.uuid4(), role="project_manager")
    approval = service.submit_for_approval(db, project, user)
    assert project.status == "submitted"
    assert approval.status == "pending"
    assert approval.target_type == "project"
    assert approval.target_id == project.id
    assert approval.requested_by == user.id
    assert any(isinstance(o, ApprovalRequestModel) for o in db.added)


def test_apply_decision_approved_auto_activates():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="submitted")
    service.apply_decision(db, project, "approved")
    assert project.status == "active"


def test_apply_decision_rejected_moves_to_rejected():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="submitted")
    service.apply_decision(db, project, "rejected")
    assert project.status == "rejected"


def test_ceo_direct_transitions():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="active", priority="medium")

    service.change_priority(db, project, "critical")
    assert project.priority == "critical"

    new_manager = uuid.uuid4()
    service.change_manager(db, project, new_manager)
    assert project.manager_id == new_manager

    service.pause(db, project)
    assert project.status == "paused"

    service.resume(db, project)
    assert project.status == "active"

    service.complete(db, project)
    assert project.status == "completed"

    service.close(db, project)
    assert project.status == "closed"
    assert project.closed_at is not None


def test_close_rejects_a_non_completed_project():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="active")
    with pytest.raises(AppError) as exc_info:
        service.close(db, project)
    assert exc_info.value.status_code == 409


def test_cancel_sets_closed_at():
    db = _FakeSession()
    project = ProjectModel(id=uuid.uuid4(), name="x", status="active")
    service.cancel(db, project)
    assert project.status == "cancelled"
    assert project.closed_at is not None
