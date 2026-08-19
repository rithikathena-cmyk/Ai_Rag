"""RBAC matrix: role x permission x corpus x model tier.

Reads the REAL policy loader (`services/llm_rbac/policy_loader.py`), which is
the same resolution `/chat` and every router use. No permission logic is
re-implemented here — a change to llm_rbac.yaml must show up as a change in
these assertions, which is the point.
"""

import pytest

from app.services.llm_rbac import policy_loader

ROLES = ("user", "hr", "project_manager", "ceo", "admin")

#: Roles that must never hold these, regardless of any other grant.
ADMIN_ONLY = ("MANAGE_USERS", "MANAGE_ROLES", "SYSTEM_SETTINGS")


def _perms(role: str) -> set[str]:
    return set(policy_loader.role_config(role).granted_permissions)


def _has(role: str, permission: str) -> bool:
    p = _perms(role)
    return permission in p or "*" in p


# ------------------------------------------------------------- corpus scoping

@pytest.mark.parametrize("role,expected", [
    ("user", {"manufacturing"}),
    ("hr", {"hr"}),
    ("project_manager", {"engineering"}),
])
def test_non_privileged_roles_are_confined_to_their_own_corpus(role, expected):
    """Corpus scoping is what makes one shared knowledge base safe for every
    role — it is enforced before retrieval, not by asking the model nicely."""
    assert set(policy_loader.role_config(role).knowledge_departments) == expected


def test_employee_cannot_reach_hr_or_financial_capabilities():
    denied = set(policy_loader.role_config("user").permissions_deny)
    for capability in ("hr_information", "financial_information", "executive_reports"):
        assert capability in denied, f"Employee is not denied {capability}"


@pytest.mark.parametrize("role,capability", [
    ("user", "hr_information"),
    ("user", "financial_information"),
    ("user", "upload_documents"),
    ("project_manager", "hr_information"),
    ("project_manager", "payroll"),
])
def test_capability_is_effectively_denied(role, capability):
    """Asserts the OUTCOME, not the mechanism. `engine.py` is default-deny —
    an action absent from `permissions_allow` is refused (engine.py:108), so a
    capability is safe if it is either explicitly denied or simply not
    granted. Asserting explicit deny-listing would fail on the many
    capabilities that are correctly protected by the default."""
    cfg = policy_loader.role_config(role)
    allowed = set(cfg.permissions_allow)
    assert "*" not in allowed, f"{role} holds a wildcard grant"
    assert capability in set(cfg.permissions_deny) or capability not in allowed


def test_the_engine_is_default_deny_not_default_allow():
    """The property the test above depends on, asserted BEHAVIOURALLY against
    the real `_check_permission`. If this ever inverts, every unlisted
    capability silently becomes a grant across every role."""
    from app.core.errors import AppError
    from app.services.llm_rbac.engine import _check_permission

    cfg = policy_loader.role_config("user")
    unlisted = "an_action_nobody_ever_granted"
    assert unlisted not in set(cfg.permissions_allow)
    assert unlisted not in set(cfg.permissions_deny)

    with pytest.raises(AppError) as exc:
        _check_permission(cfg, "user", unlisted)
    assert exc.value.status_code == 403

    # And an explicitly denied action is refused for its own, distinct reason.
    with pytest.raises(AppError):
        _check_permission(cfg, "user", "hr_information")


# ------------------------------------------------------------ permission grid

@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_chat(role):
    assert _has(role, "CHAT")


@pytest.mark.parametrize("role", ROLES)
def test_view_analytics_is_org_wide(role):
    """Deliberate: the read-only Metrics dashboards are visible to everyone."""
    assert _has(role, "VIEW_ANALYTICS")


@pytest.mark.parametrize("role", ("user", "hr", "project_manager"))
@pytest.mark.parametrize("permission", ADMIN_ONLY)
def test_non_admin_roles_hold_no_administrative_permission(role, permission):
    assert not _has(role, permission), f"{role} unexpectedly holds {permission}"


def test_ceo_is_not_a_superset_of_admin():
    """A deliberate split: CEO sees everything the business produces but holds
    no user/role/system administration."""
    for permission in ADMIN_ONLY:
        assert not _has("ceo", permission), f"CEO unexpectedly holds {permission}"


@pytest.mark.parametrize("role,expected", [
    ("user", False), ("hr", False), ("project_manager", False), ("ceo", True), ("admin", True),
])
def test_raw_audit_visibility_is_limited_to_ceo_and_admin(role, expected):
    """VIEW_AUDIT_LOGS is the line that gates org-wide trace visibility AND the
    raw guardrail detail (classifier scores, configured deny-keywords)."""
    assert _has(role, "VIEW_AUDIT_LOGS") is expected


@pytest.mark.parametrize("role,expected", [
    ("user", False), ("hr", False), ("project_manager", False), ("ceo", True), ("admin", True),
])
def test_only_ceo_and_admin_manage_guardrail_policy(role, expected):
    assert _has(role, "MANAGE_GUARDRAIL_POLICIES") is expected


@pytest.mark.parametrize("role,expected", [
    ("user", False), ("hr", False), ("project_manager", False), ("ceo", False), ("admin", True),
])
def test_only_admin_holds_system_settings(role, expected):
    assert _has(role, "SYSTEM_SETTINGS") is expected


@pytest.mark.parametrize("role,expected", [
    ("user", False), ("hr", True), ("project_manager", False), ("ceo", True), ("admin", True),
])
def test_employee_pii_approval_is_limited(role, expected):
    assert _has(role, "MANAGE_EMPLOYEE_PII") is expected


# -------------------------------------------------------------- model tiering

def test_model_tiers_differ_by_role():
    """Two roles must never end up with an identical tier set — that would
    mean the tier policy is not actually differentiating anything."""
    hr = tuple(policy_loader.role_config("hr").tiers_allowed)
    pm = tuple(policy_loader.role_config("project_manager").tiers_allowed)
    assert hr != pm


def test_employee_cannot_reach_the_top_tier():
    assert "opus" not in policy_loader.role_config("user").tiers_allowed
