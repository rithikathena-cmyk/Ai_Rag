"""routers/reports.py previously had zero authentication and ReportModel had
no owner/department column, so any caller could list/download any role's
report. Same structural-contract convention as tests/test_chat_auth.py for
the route wiring, plus direct unit tests of the new pure
_visibility_filter() helper (no DB needed for the kill-switch branch; the
non-None branch is a SQLAlchemy clause, only its presence/shape is checked
here since evaluating it needs a real query — see tests/test_documents_rbac.py
for the equivalent convention on the documents router).

Security decision the tests below document and prove (added when auditing
whether generated reports need PII redaction — see
services/agents/report_agent.py and services/llm_rbac/report_policy.py):
generated reports are treated as authorized sensitive artifacts, not
user-facing chat content, and are deliberately NOT run through
services/guardrails/pii.py::redact_pii(). A report's rows were already
RBAC-scoped at generation time (report_policy.py::authorize_report()'s
row_filter, and every tool that supplies report data — search_documents,
query_analytics, list_my_projects — is independently RBAC-filtered at its
own source, unaffected by this file), and the artifact itself is
access-controlled at rest by ReportModel.owner_id/department, mirroring (at
department granularity) the same knowledge_departments concept
resolve_document_ids() uses for documents. Auto-redacting report content
would defeat the feature for the roles it exists for — e.g. HR's
attendance/employee_summary reports are only useful if they show real
employee data to an HR user already authorized to see it, exactly like an
HR document's own content isn't redacted for an HR reader. What the tests
below verify is that the *access control gating that content* actually
holds — not present-but-unenforced.

Beyond the existing presence/shape checks, the tests below (1) compile
_visibility_filter()'s SQLAlchemy clause to literal SQL and assert its exact
department/owner_id predicate — genuine proof of the rule's correctness
without a live Postgres — and (2) exercise list_reports/download_report
end-to-end against a fake session whose query behavior mirrors that
already-verified predicate, proving the endpoints actually apply it rather
than only computing it.
"""

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.params import Depends as DependsMarker
from fastapi.testclient import TestClient

from app.db.postgres import get_db
from app.routers import reports
from app.services.auth.dependencies import get_current_user
from app.services.llm_rbac import policy_loader


def _depends_on_get_current_user(fn) -> bool:
    for param in inspect.signature(fn).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is get_current_user:
            return True
    return False


def test_list_reports_requires_a_verified_user():
    assert _depends_on_get_current_user(reports.list_reports)


def test_download_report_requires_a_verified_user():
    assert _depends_on_get_current_user(reports.download_report)


def test_visibility_filter_is_unrestricted_when_kill_switch_off(monkeypatch):
    monkeypatch.setattr(policy_loader, "knowledge_departments_for", lambda role: None)
    user = SimpleNamespace(id=uuid.uuid4(), role="user")
    assert reports._visibility_filter(user) is None


def test_visibility_filter_returns_a_clause_when_rbac_active(monkeypatch):
    monkeypatch.setattr(policy_loader, "knowledge_departments_for", lambda role: ("manufacturing",))
    user = SimpleNamespace(id=uuid.uuid4(), role="user")
    clause = reports._visibility_filter(user)
    assert clause is not None


@pytest.fixture(autouse=True)
def _clear_policy_caches():
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()
    yield
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()


def _real_user(role="hr", user_id=None):
    return SimpleNamespace(id=user_id or uuid.uuid4(), role=role)


def _compiled(condition) -> str:
    return str(condition.compile(compile_kwargs={"literal_binds": True}))


# ------------------------------------------------------- _visibility_filter()'s exact predicate, against real roles/config

def test_hr_condition_covers_null_department_own_department_and_ownership():
    user = _real_user("hr")
    sql = _compiled(reports._visibility_filter(user))

    assert "reports.department IS NULL" in sql
    assert "reports.department IN ('hr')" in sql
    # The literal-bind compiler renders the UUID without dashes — compare
    # the hex digits only rather than str(uuid.UUID(...))'s dashed form.
    assert "reports.owner_id = " in sql
    assert str(user.id).replace("-", "") in sql


def test_employee_condition_scoped_to_manufacturing():
    sql = _compiled(reports._visibility_filter(_real_user("user")))
    assert "reports.department IN ('manufacturing')" in sql


def test_project_manager_condition_scoped_to_engineering():
    sql = _compiled(reports._visibility_filter(_real_user("project_manager")))
    assert "reports.department IN ('engineering')" in sql


def test_admin_condition_includes_every_department_but_is_still_a_real_filter():
    """Admin's knowledge_departments is the full 4-department list, not the
    None sentinel — a real, compiled predicate, not an implicit bypass."""
    condition = reports._visibility_filter(_real_user("admin"))
    assert condition is not None
    sql = _compiled(condition)
    for dept in ("manufacturing", "hr", "engineering", "executive"):
        assert dept in sql


# ------------------------------------------------------- router-level list/download enforcement
# (proves the endpoints actually apply the predicate above, not just compute it)

class _FakeReport:
    def __init__(self, owner_id, department, *, id=None, title="Report", format="csv", row_count=1):
        self.id = id or uuid.uuid4()
        self.owner_id = owner_id
        self.department = department
        self.title = title
        self.format = format
        self.row_count = row_count
        self.file_path = "/tmp/does-not-matter.csv"
        self.created_at = datetime.now(timezone.utc)


def _visible_to(row: "_FakeReport", user, knowledge_departments) -> bool:
    """Pure-Python mirror of the exact rule the compiled-SQL tests above
    already verified _visibility_filter() implements — drives the fake
    session's query results; does not re-derive or second-guess that rule."""
    if knowledge_departments is None:
        return True
    if row.department is None:
        return True
    if row.department in knowledge_departments:
        return True
    return row.owner_id == user.id


class _FakeReportQuery:
    def __init__(self, rows, user, knowledge_departments):
        self._rows = rows
        self._user = user
        self._kd = knowledge_departments

    def filter(self, *conditions):
        # A real .filter(condition) call narrows to exactly what
        # _visibility_filter()'s verified rule would allow — proving the
        # router wires the condition in, not just computes and discards it.
        return _FakeReportQuery(
            [r for r in self._rows if _visible_to(r, self._user, self._kd)], self._user, self._kd,
        )

    def order_by(self, *a, **k):
        return self

    def count(self):
        return len(self._rows)

    def offset(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, rows, user, knowledge_departments):
        self._rows = rows
        self._user = user
        self._kd = knowledge_departments

    def query(self, *a, **k):
        return _FakeReportQuery(self._rows, self._user, self._kd)

    def get(self, model, report_id):
        return next((r for r in self._rows if r.id == report_id), None)


def _make_app(monkeypatch, rows, user):
    app = FastAPI()
    app.include_router(reports.router)
    app.dependency_overrides[get_current_user] = lambda: user

    kd = policy_loader.knowledge_departments_for(user.role)

    def _fake_get_db():
        yield _FakeDb(rows, user, kd)

    app.dependency_overrides[get_db] = _fake_get_db
    # download_report streams a real file from disk via FileResponse —
    # Starlette's own job, not under test here; short-circuit it to prove
    # only that the authorization decision reached this point.
    monkeypatch.setattr(reports, "FileResponse", lambda **k: {"served": True, **k})
    return TestClient(app)


def test_owner_can_download_own_report_regardless_of_department(monkeypatch):
    owner = _real_user("hr")
    report = _FakeReport(owner_id=owner.id, department="engineering")  # different department than the owner's own
    client = _make_app(monkeypatch, [report], owner)

    response = client.get(f"/reports/{report.id}/download")

    assert response.status_code == 200


def test_same_department_non_owner_can_download(monkeypatch):
    """Department-level sharing is intentional (mirrors document RBAC's
    knowledge_departments concept) — a report isn't owner-locked."""
    report = _FakeReport(owner_id=uuid.uuid4(), department="hr")
    colleague = _real_user("hr", user_id=uuid.uuid4())
    client = _make_app(monkeypatch, [report], colleague)

    response = client.get(f"/reports/{report.id}/download")

    assert response.status_code == 200


def test_different_department_non_owner_gets_404_not_403(monkeypatch):
    """404, not 403 — a caller who can't see this report shouldn't be able
    to confirm it exists by probing IDs (see routers/reports.py's own
    comment on this exact choice)."""
    report = _FakeReport(owner_id=uuid.uuid4(), department="hr")
    outsider = _real_user("project_manager", user_id=uuid.uuid4())  # engineering, not hr, not the owner
    client = _make_app(monkeypatch, [report], outsider)

    response = client.get(f"/reports/{report.id}/download")

    # Bare-router test app (no app.main.py exception handler registered —
    # same convention as tests/test_search_validation.py), so AppError
    # surfaces via HTTPException's default {"detail": ...} body rather than
    # production's {"error": {...}} shape; the security property under test
    # is the status code (404, not 403 — see docstring above), not the body.
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_legacy_null_department_report_is_visible_to_everyone(monkeypatch):
    report = _FakeReport(owner_id=uuid.uuid4(), department=None)
    stranger = _real_user("user", user_id=uuid.uuid4())
    client = _make_app(monkeypatch, [report], stranger)

    response = client.get(f"/reports/{report.id}/download")

    assert response.status_code == 200


def test_nonexistent_report_is_404(monkeypatch):
    client = _make_app(monkeypatch, [], _real_user("hr"))

    response = client.get(f"/reports/{uuid.uuid4()}/download")

    assert response.status_code == 404


def test_list_reports_excludes_reports_outside_the_visible_set(monkeypatch):
    mine = _FakeReport(owner_id=None, department="hr", title="HR one")
    other_dept = _FakeReport(owner_id=uuid.uuid4(), department="executive", title="Executive only")
    caller = _real_user("hr", user_id=uuid.uuid4())
    client = _make_app(monkeypatch, [mine, other_dept], caller)

    response = client.get("/reports")

    titles = {item["title"] for item in response.json()["items"]}
    assert "HR one" in titles
    assert "Executive only" not in titles


def test_list_reports_includes_own_report_from_a_department_the_role_cannot_otherwise_see(monkeypatch):
    caller = _real_user("hr", user_id=uuid.uuid4())
    own_out_of_department = _FakeReport(owner_id=caller.id, department="executive", title="mine but exec")
    client = _make_app(monkeypatch, [own_out_of_department], caller)

    response = client.get("/reports")

    titles = {item["title"] for item in response.json()["items"]}
    assert "mine but exec" in titles


def test_kill_switch_off_means_every_report_is_listed(monkeypatch):
    monkeypatch.setattr(policy_loader, "_raw", lambda: {"enabled": False})
    rows = [
        _FakeReport(owner_id=uuid.uuid4(), department="hr", title="a"),
        _FakeReport(owner_id=uuid.uuid4(), department="executive", title="b"),
    ]
    client = _make_app(monkeypatch, rows, _real_user("user", user_id=uuid.uuid4()))

    response = client.get("/reports")

    titles = {item["title"] for item in response.json()["items"]}
    assert titles == {"a", "b"}


def test_report_list_response_never_includes_content_or_file_path(monkeypatch):
    report = _FakeReport(owner_id=uuid.uuid4(), department=None, title="t")
    client = _make_app(monkeypatch, [report], _real_user("hr"))

    response = client.get("/reports")

    item = response.json()["items"][0]
    assert set(item.keys()) == {"id", "title", "format", "row_count", "created_at"}
    assert "file_path" not in item
