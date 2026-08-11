"""Report-type authorization — a sibling to engine.py, not a replacement for
it. `engine.py::authorize_llm_request()` answers "can this role talk to
Claude, with which tools/tier/quota"; `authorize_report()` answers a
narrower, report-specific question layered on top: "can this role generate
*this* report type, and if so, what data is it scoped to." Both run for a
report-generation chat turn — see routers/chat.py.

Report types with no backing data model in this schema (no machine/shift/
attendance/production table exists anywhere in this repo — see
docs/LLM_GATEWAY_ANALYSIS.md §0) are still real, correctly-enforced catalog
entries: the ALLOW/DENY decision is accurate and tested. What's honest, not
built, is the data itself — see NO_DATA_REPORT_TYPES and
docs/REPORT_AUTHORIZATION.md.
"""

from typing import Literal, NamedTuple

from app.services.llm_rbac import policy_loader

NO_DATA_REPORT_TYPES = frozenset({
    # Employee — no machine/shift/production table.
    "machine_status", "shift_report", "production_summary", "machine_performance",
    # HR — no employee attendance/performance/leave/training/certification table.
    "attendance", "employee_performance", "leave", "training", "certification",
    "workforce", "hr_analytics", "employee_summary",
    # CEO/Admin — no manufacturing-operations table beyond documents/projects.
    "production", "maintenance", "quality", "inventory", "warehouse", "procurement", "hr",
})

# Report types scoped to the caller's own projects (via ProjectModel.manager_id
# / ProjectMemberModel — see services/agents/project_agent.py) rather than
# department/ownership on documents/reports. CEO-only cross-domain names
# (executive_report/enterprise/management_summary/risk) are folded in here
# too — they compose from the same real project/document/report data, always
# unrestricted for the roles that can even request them (admin's wildcard).
_PROJECT_SCOPED_REPORT_TYPES = frozenset({
    "project_status", "project_progress", "project_summary", "project_risk",
    "resource_allocation", "project_performance", "project_document_summary",
    "engineering_report", "project_portfolio",
    "executive_report", "enterprise", "management_summary", "risk",
})

_DOCUMENT_SCOPED_REPORT_TYPES = frozenset({"manual_summary", "sop_summary"})


class ReportDecision(NamedTuple):
    status: Literal["allowed", "denied", "approval_required"]
    reason: str | None
    data_available: bool
    row_filter: dict | None


def _resolve_row_filter(user, role_cfg: "policy_loader.RoleConfig", report_type: str) -> dict | None:
    if report_type in _PROJECT_SCOPED_REPORT_TYPES:
        is_unrestricted = user.role == "admin" or role_cfg.reports_allowed == frozenset({"*"})
        return {"scope": "all"} if is_unrestricted else {"scope": "own", "user_id": user.id}
    if report_type == "assigned_work":
        return {"owner_id": user.id}
    if report_type in _DOCUMENT_SCOPED_REPORT_TYPES:
        return {"knowledge_departments": role_cfg.knowledge_departments}
    return None


def authorize_report(user, report_type: str, resource_scope: dict | None = None) -> ReportDecision:
    """The single entrypoint for "may this role generate this report type."
    Doesn't raise — routers/chat.py decides what to do with a `denied`
    decision (403 + audit row, same pattern as an authorize_llm_request()
    denial). `resource_scope`, if the caller already resolved one (e.g. a
    specific project_id named in the request), is passed straight through as
    the returned row_filter instead of the role-default scope."""
    role_cfg = policy_loader.role_config(user.role)

    if not policy_loader.is_enabled():
        return ReportDecision("allowed", None, report_type not in NO_DATA_REPORT_TYPES, resource_scope)

    allowed = role_cfg.reports_allowed
    if "*" not in allowed and report_type not in allowed:
        return ReportDecision(
            "denied", f"'{report_type}' is not an allowed report type for role '{user.role}'", False, None,
        )

    row_filter = resource_scope if resource_scope is not None else _resolve_row_filter(user, role_cfg, report_type)
    return ReportDecision("allowed", None, report_type not in NO_DATA_REPORT_TYPES, row_filter)
