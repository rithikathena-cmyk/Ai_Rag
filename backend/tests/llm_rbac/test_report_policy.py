"""services/llm_rbac/report_policy.py::authorize_report() — the spec's §19
report-type ALLOW/DENY matrix, plus the honest no-data-source behavior for
report types with no backing table in this schema (see NO_DATA_REPORT_TYPES)."""

import uuid
from types import SimpleNamespace

import pytest

from app.services.llm_rbac import policy_loader, report_policy


@pytest.fixture(autouse=True)
def _clear_caches():
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()
    yield
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()


def _user(role: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role)


# --------------------------------------------------------------------- Employee

def test_employee_machine_status_allowed_but_no_data_source():
    decision = report_policy.authorize_report(_user("user"), "machine_status")
    assert decision.status == "allowed"
    assert decision.data_available is False


def test_employee_production_summary_allowed_but_no_data_source():
    decision = report_policy.authorize_report(_user("user"), "production_summary")
    assert decision.status == "allowed"
    assert decision.data_available is False


def test_employee_denied_hr_report():
    decision = report_policy.authorize_report(_user("user"), "attendance")
    assert decision.status == "denied"


def test_employee_denied_enterprise_report():
    decision = report_policy.authorize_report(_user("user"), "enterprise")
    assert decision.status == "denied"


def test_employee_manual_summary_is_real_and_department_scoped():
    decision = report_policy.authorize_report(_user("user"), "manual_summary")
    assert decision.status == "allowed"
    assert decision.data_available is True
    assert decision.row_filter == {"knowledge_departments": ("manufacturing",)}


# --------------------------------------------------------------------------- HR

def test_hr_attendance_allowed_but_no_data_source():
    decision = report_policy.authorize_report(_user("hr"), "attendance")
    assert decision.status == "allowed"
    assert decision.data_available is False


def test_hr_employee_performance_allowed_but_no_data_source():
    decision = report_policy.authorize_report(_user("hr"), "employee_performance")
    assert decision.status == "allowed"
    assert decision.data_available is False


def test_hr_denied_machine_control():
    # Not a report type at all for any role — must deny, not silently no-op.
    decision = report_policy.authorize_report(_user("hr"), "machine_control")
    assert decision.status == "denied"


def test_hr_denied_project_status():
    decision = report_policy.authorize_report(_user("hr"), "project_status")
    assert decision.status == "denied"


# ------------------------------------------------------------------ Project Manager

def test_pm_project_status_allowed_and_scoped_to_own():
    user = _user("project_manager")
    decision = report_policy.authorize_report(user, "project_status")
    assert decision.status == "allowed"
    assert decision.data_available is True
    assert decision.row_filter == {"scope": "own", "user_id": user.id}


def test_pm_engineering_report_allowed_and_scoped_to_own():
    user = _user("project_manager")
    decision = report_policy.authorize_report(user, "engineering_report")
    assert decision.status == "allowed"
    assert decision.row_filter == {"scope": "own", "user_id": user.id}


def test_pm_denied_hr_salary_style_report():
    decision = report_policy.authorize_report(_user("project_manager"), "employee_performance")
    assert decision.status == "denied"


def test_pm_denied_project_portfolio():
    # project_portfolio is CEO/Admin-only — PM's catalog doesn't include it.
    decision = report_policy.authorize_report(_user("project_manager"), "project_portfolio")
    assert decision.status == "denied"


# --------------------------------------------------------------------- CEO/Admin

def test_admin_project_portfolio_allowed_and_unrestricted():
    decision = report_policy.authorize_report(_user("admin"), "project_portfolio")
    assert decision.status == "allowed"
    assert decision.data_available is True
    assert decision.row_filter == {"scope": "all"}


def test_admin_executive_report_allowed_and_unrestricted():
    decision = report_policy.authorize_report(_user("admin"), "executive_report")
    assert decision.status == "allowed"
    assert decision.data_available is True
    assert decision.row_filter == {"scope": "all"}


def test_admin_allowed_every_role_specific_report_type():
    for report_type in ("machine_status", "attendance", "project_status", "manual_summary"):
        decision = report_policy.authorize_report(_user("admin"), report_type)
        assert decision.status == "allowed"


def test_admin_production_still_reports_no_data_source():
    # Admin's wildcard grants ALLOW, but doesn't fabricate a data source that
    # doesn't exist — data_available stays honest regardless of role.
    decision = report_policy.authorize_report(_user("admin"), "production")
    assert decision.status == "allowed"
    assert decision.data_available is False


# ------------------------------------------------------------------------ kill switch

def test_disabled_kill_switch_allows_and_marks_data_availability_correctly(monkeypatch):
    monkeypatch.setattr(policy_loader, "_raw", lambda: {"enabled": False})
    decision = report_policy.authorize_report(_user("user"), "machine_status")
    assert decision.status == "allowed"
    assert decision.data_available is False  # still honest about missing data even when RBAC is off
