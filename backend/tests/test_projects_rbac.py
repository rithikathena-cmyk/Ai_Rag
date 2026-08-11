"""routers/projects.py — greenfield project governance. Structural checks
follow tests/test_documents_rbac.py's convention (get_current_user wiring,
source-level confirmation of which actions/dependencies gate each route);
one TestClient-based check confirms require_role(Role.ADMIN) actually blocks
a Project Manager from a CEO/Admin-only direct action, since that's real
FastAPI dependency-injection behavior worth exercising end-to-end, not just
asserting statically (matches tests/test_rbac.py's own convention).
"""

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.params import Depends as DependsMarker
from fastapi.testclient import TestClient

from app.core.roles import Role
from app.db.postgres import get_db
from app.models.project import ProjectModel
from app.routers import projects
from app.services.auth.dependencies import get_current_user

_GET_CURRENT_USER_ROUTES = [
    projects.create_project,
    projects.list_projects,
    projects.get_project,
    projects.update_project,
    projects.add_members,
    projects.submit_project,
]

_ADMIN_ONLY_ROUTES = [
    projects.reallocate_project,
    projects.change_priority,
    projects.change_manager,
    projects.pause_project,
    projects.resume_project,
    projects.complete_project,
    projects.close_project,
    projects.cancel_project,
]


def _depends_on(fn, dependency) -> bool:
    for param in inspect.signature(fn).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is dependency:
            return True
    return False


def test_pm_and_admin_reachable_routes_require_a_verified_user():
    for route in _GET_CURRENT_USER_ROUTES:
        assert _depends_on(route, get_current_user), f"{route.__name__} is missing get_current_user"


def test_ceo_admin_only_routes_use_require_role_admin_not_bare_auth():
    # require_role() returns a fresh closure per call, so identity comparison
    # against get_current_user (a module-level singleton function) is what
    # distinguishes "any authenticated user" from "admin only" — none of
    # these routes should depend directly on get_current_user.
    for route in _ADMIN_ONLY_ROUTES:
        assert not _depends_on(route, get_current_user), (
            f"{route.__name__} should be gated by require_role(Role.ADMIN), not bare get_current_user"
        )


def test_create_and_update_and_submit_are_rbac_gated():
    # Each route calls the local _authorize() helper (which itself wraps
    # authorize_llm_request(), asserted separately below) with its own
    # action name — checking for the action string is enough to confirm
    # each route is individually gated, not just the module in general.
    assert "authorize_llm_request" in inspect.getsource(projects._authorize)
    for route, action in (
        (projects.create_project, "project_creation"),
        (projects.update_project, "project_update"),
        (projects.add_members, "project_allocation"),
        (projects.submit_project, "project_submit"),
    ):
        source = inspect.getsource(route)
        assert f'"{action}"' in source
        assert "_authorize(" in source


def test_pm_can_only_act_on_own_projects_not_any_project():
    for route in (projects.update_project, projects.add_members, projects.submit_project):
        source = inspect.getsource(route)
        assert "not_project_manager" in source


def test_submit_creates_a_real_approval_request():
    source = inspect.getsource(projects.submit_project)
    assert "submit_for_approval" in source


# --------------------------------------------------------- require_role(ADMIN) end to end

class _FakeSession:
    def __init__(self, project):
        self._project = project

    def get(self, model, project_id):
        return self._project if project_id == self._project.id else None

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _project(status: str) -> ProjectModel:
    # ProjectModel's default="medium"/server_default="draft" etc. only apply
    # on an actual INSERT — constructing the object directly (no session)
    # leaves them None, so tests that serialize it through ProjectResponse
    # need to set every non-nullable field explicitly.
    return ProjectModel(id=uuid.uuid4(), name="x", status=status, priority="medium", created_at=datetime.now(timezone.utc))


def _build_app(project: ProjectModel, role: str):
    app = FastAPI()
    app.include_router(projects.router)
    app.dependency_overrides[get_db] = lambda: _FakeSession(project)
    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, department=None, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return app


def test_project_manager_cannot_pause_a_project():
    project = _project("active")
    client = TestClient(_build_app(project, Role.PROJECT_MANAGER.value))
    response = client.post(f"/projects/{project.id}/pause")
    assert response.status_code == 403


def test_admin_can_pause_a_project():
    project = _project("active")
    client = TestClient(_build_app(project, Role.ADMIN.value))
    response = client.post(f"/projects/{project.id}/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "paused"
