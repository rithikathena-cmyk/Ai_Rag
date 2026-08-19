import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.gateway.schemas import ModelTier
from app.services.llm_rbac import engine, policy_loader, rate_limiter


@pytest.fixture(autouse=True)
def _clear_caches():
    # policy_loader._raw()/role_config() are lru_cache'd (matching
    # gateway/prompt_manager.py's convention) — clear between tests so one
    # test's monkeypatched config never leaks into the next.
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()
    yield
    policy_loader._raw.cache_clear()
    policy_loader.role_config.cache_clear()


def _fake_user(
    role: str, department: str | None = None,
    daily_token_limit_override: int | None = None, monthly_token_limit_override: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, department=department, is_active=True,
        daily_token_limit_override=daily_token_limit_override, monthly_token_limit_override=monthly_token_limit_override,
    )


def _noop_budget(*a, **k) -> None:
    return None


@pytest.fixture(autouse=True)
def _clear_rate_limiter_state():
    rate_limiter._BUCKETS.clear()
    yield
    rate_limiter._BUCKETS.clear()


@pytest.fixture(autouse=True)
def _stub_budget(monkeypatch):
    # Goes through Postgres — out of scope for a pure policy-logic test, and
    # this repo's existing test suite (e.g. tests/test_rbac.py,
    # tests/guardrails/test_retrieval_permissions.py) already establishes the
    # pattern of stubbing the I/O boundary rather than standing up real
    # infrastructure for a unit test.
    monkeypatch.setattr(engine.quotas, "check_budget", _noop_budget)


# --------------------------------------------------------- real shipped config

def test_employee_role_is_haiku_only():
    # Role-based model-access policy update: Employee moved from Sonnet-only
    # to Haiku-only (the cheapest/fastest role-driven tier).
    cfg = policy_loader.role_config("user")
    assert cfg.tiers_allowed == frozenset({"haiku"})
    assert cfg.default_tier == "haiku"


# ---------------------------------------------------- role-based model access

# The exact matrix from the role-based model-access policy: which
# (role, tier) pairs are ALLOW vs DENY. `tiers_allowed` is asserted directly
# (policy_loader's yaml parsing, incl. CEO/Admin's "*" wildcard resolving to
# every role-driven tier); `authorize_llm_request(..., requested_tier=...)`
# is asserted separately below (services/llm_rbac/engine.py::_resolve_tier —
# the actual backend enforcement point, equivalent to the spec's
# can_use_model()/403 pattern) since that's the boundary a caller can't
# bypass by editing a request body.
MODEL_ACCESS_MATRIX = {
    "user": {"haiku": True, "sonnet": False, "opus": False},
    "hr": {"haiku": True, "sonnet": True, "opus": False},
    "project_manager": {"haiku": False, "sonnet": True, "opus": True},
    "ceo": {"haiku": True, "sonnet": True, "opus": True},
    "admin": {"haiku": True, "sonnet": True, "opus": True},
}


@pytest.mark.parametrize(
    "role,tier,expected",
    [(role, tier, expected) for role, tiers in MODEL_ACCESS_MATRIX.items() for tier, expected in tiers.items()],
)
def test_model_access_matrix_tiers_allowed(role, tier, expected):
    cfg = policy_loader.role_config(role)
    assert (tier in cfg.tiers_allowed) is expected, f"{role} x {tier}"


@pytest.mark.parametrize(
    "role,tier,expected",
    [(role, tier, expected) for role, tiers in MODEL_ACCESS_MATRIX.items() for tier, expected in tiers.items()],
)
def test_model_access_matrix_backend_enforcement(role, tier, expected):
    # This is the actual, unbypassable gate: authorize_llm_request(...,
    # requested_tier=...) is exactly what routers/chat.py passes a client-
    # supplied model_tier through — an Employee POSTing {"model_tier":
    # "opus"} hits this same code path and gets a 403, never a 200.
    user = _fake_user(role, policy_loader.role_config(role).department_default)
    if expected:
        decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", requested_tier=tier)
        assert decision.model_tier == ModelTier(tier)
    else:
        with pytest.raises(AppError) as exc_info:
            engine.authorize_llm_request(db=None, user=user, endpoint="chat", requested_tier=tier)
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "llm_rbac_tier_denied"


def test_ceo_and_admin_have_access_to_every_role_driven_tier_via_wildcard():
    # "*" in yaml resolves through policy_loader._ROLE_DRIVEN_TIERS, not a
    # hardcoded per-role list — so both roles automatically include any
    # *existing* role-driven tier without their own yaml entry changing.
    from app.services.llm_rbac.policy_loader import _ROLE_DRIVEN_TIERS

    assert policy_loader.role_config("ceo").tiers_allowed == _ROLE_DRIVEN_TIERS
    assert policy_loader.role_config("admin").tiers_allowed == _ROLE_DRIVEN_TIERS


def test_hr_and_project_manager_model_access_is_not_identical():
    # The one non-negotiable requirement from the policy: these two roles
    # must never end up with the same allowed-tier set.
    hr_tiers = policy_loader.role_config("hr").tiers_allowed
    pm_tiers = policy_loader.role_config("project_manager").tiers_allowed
    assert hr_tiers != pm_tiers


# ------------------------------------------------------- coarse rbac_permissions

def test_employee_granted_permissions_is_chat_and_readonly_analytics():
    # VIEW_ANALYTICS is deliberately org-wide (every role holds it) so the
    # read-only Metrics dashboards — latency/tokens, retrieval, gateway cost,
    # guardrail counts — are visible to everyone. Employee still gets NO
    # document, user, role, audit, or admin permission; and the raw guardrail
    # `detail` strings behind those counts stay gated on VIEW_AUDIT_LOGS,
    # which Employee does not hold (see
    # test_employee_cannot_see_raw_guardrail_detail in test_permission_matrix).
    cfg = policy_loader.role_config("user")
    assert cfg.granted_permissions == frozenset({"CHAT", "VIEW_CONVERSATIONS", "VIEW_ANALYTICS"})


def test_hr_and_project_manager_get_document_and_analytics_but_not_admin_permissions():
    _BASE = frozenset({
        "CHAT", "VIEW_CONVERSATIONS", "VIEW_DOCUMENTS", "UPLOAD_DOCUMENTS", "DELETE_DOCUMENTS",
        "MANAGE_DOCUMENTS", "VIEW_ANALYTICS", "VIEW_USERS",
    })
    # HR additionally gets MANAGE_EMPLOYEE_PII (docs/GUARDRAILS_ARCHITECTURE.md
    # §14 — HR decides employee-PII approval requests scoped to their own
    # department); Project Manager does not.
    expected = {"hr": _BASE | {"MANAGE_EMPLOYEE_PII"}, "project_manager": _BASE}
    for role, expected_permissions in expected.items():
        cfg = policy_loader.role_config(role)
        assert cfg.granted_permissions == expected_permissions
        # None of the admin-only permissions leak in for either role.
        assert not cfg.granted_permissions & {"MANAGE_USERS", "VIEW_ROLES", "MANAGE_ROLES", "VIEW_AUDIT_LOGS", "SYSTEM_SETTINGS"}


def test_ceo_role_exists_distinct_from_admin():
    ceo = policy_loader.role_config("ceo")
    admin = policy_loader.role_config("admin")

    assert ceo.role == "ceo"
    assert ceo.display_name == "CEO"
    # CEO gets broad enterprise data access, same as admin...
    assert ceo.knowledge_departments == admin.knowledge_departments
    # ...but a real capability list, not admin's "*" wildcard.
    assert ceo.permissions_allow != frozenset({"*"})
    assert "system_settings" not in ceo.permissions_allow
    assert "administration" not in ceo.permissions_allow


def test_ceo_granted_permissions_excludes_manage_users_manage_roles_and_settings():
    cfg = policy_loader.role_config("ceo")
    assert cfg.granted_permissions == frozenset({
        "CHAT", "VIEW_CONVERSATIONS", "VIEW_DOCUMENTS", "UPLOAD_DOCUMENTS", "DELETE_DOCUMENTS",
        "MANAGE_DOCUMENTS", "VIEW_ANALYTICS", "VIEW_USERS", "VIEW_ROLES", "VIEW_AUDIT_LOGS",
        # CEO decides employee-PII approval requests unscoped — see
        # docs/GUARDRAILS_ARCHITECTURE.md §14.
        "MANAGE_EMPLOYEE_PII",
        # CEO is an explicit co-approver (alongside Admin) for the Guardrail
        # Policy Center specifically — a dedicated permission, not
        # SYSTEM_SETTINGS, which CEO is still excluded from below. See
        # core/permissions.py's comment on MANAGE_GUARDRAIL_POLICIES.
        "MANAGE_GUARDRAIL_POLICIES",
        # Policy Copilot, granted alongside MANAGE_GUARDRAIL_POLICIES so
        # CEO's effective authority is unchanged. Split into four so a
        # deployment can require separate proposers and approvers without a
        # code change — see core/permissions.py.
        "POLICY_READ", "POLICY_SIMULATE", "POLICY_PROPOSE", "POLICY_APPROVE",
    })
    assert "MANAGE_USERS" not in cfg.granted_permissions
    assert "MANAGE_ROLES" not in cfg.granted_permissions
    assert "SYSTEM_SETTINGS" not in cfg.granted_permissions


def test_admin_granted_every_permission():
    from app.core.permissions import PERMISSION_VALUES

    cfg = policy_loader.role_config("admin")
    assert cfg.granted_permissions == frozenset(PERMISSION_VALUES)


def test_unknown_role_granted_permissions_defaults_to_empty_not_wildcard():
    # Contrast with the fine-grained permissions_allow, which intentionally
    # falls back to "*" for an unrecognized role key still inside a present
    # config (there's no fallback dict entry for a role that was never
    # given rbac_permissions) — the new coarse system defaults to deny.
    cfg = policy_loader.role_config("some_role_not_in_the_yaml")
    assert cfg.granted_permissions == frozenset()
    assert cfg.tools == frozenset({"search_documents"})


def test_hr_and_pm_escalate_only_for_named_actions():
    hr = policy_loader.role_config("hr")
    assert hr.escalate_to_opus_for == frozenset({"workforce_planning", "leave_analytics"})
    pm = policy_loader.role_config("project_manager")
    assert pm.escalate_to_opus_for == frozenset({"engineering_planning", "risk_assessment"})


def test_admin_escalation_is_the_union_of_every_other_role():
    admin = policy_loader.role_config("admin")
    assert admin.escalate_to_opus_for == frozenset(
        {"workforce_planning", "leave_analytics", "engineering_planning", "risk_assessment"}
    )


def test_authorize_employee_denied_for_hr_only_action():
    user = _fake_user("user", "manufacturing")
    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="workforce_planning")
    assert exc_info.value.status_code == 403


def test_authorize_employee_allowed_for_its_own_action():
    user = _fake_user("user", "manufacturing")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="manufacturing_qa")
    assert decision.allowed is True
    assert decision.model_tier == ModelTier.HAIKU
    assert decision.allowed_tools == frozenset({"search_documents"})


def test_authorize_hr_never_escalates_to_opus_it_no_longer_has():
    # Role-based model-access policy update: HR lost Opus access entirely
    # (Haiku + Sonnet only) — workforce_planning is still in HR's
    # escalate_to_opus_for list (declared-but-inert, see llm_rbac.yaml's
    # comment), but _resolve_tier() must never actually hand HR an opus
    # tier it's no longer allowed to use.
    user = _fake_user("hr", "hr")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="workforce_planning")
    assert decision.model_tier == ModelTier.SONNET


def test_authorize_hr_stays_sonnet_for_routine_action():
    user = _fake_user("hr", "hr")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="hr_document_search")
    assert decision.model_tier == ModelTier.SONNET


def test_authorize_hr_denied_for_pm_only_action():
    user = _fake_user("hr", "hr")
    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="engineering_planning")
    assert exc_info.value.status_code == 403


def test_authorize_admin_wildcard_allows_any_action():
    user = _fake_user("admin", "executive")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="anything_at_all")
    assert decision.allowed is True


def test_authorize_admin_allowed_routine_action_at_sonnet():
    # Not every admin request escalates — a routine action with no
    # escalation trigger stays at the cheaper default tier even for admin.
    user = _fake_user("admin", "executive")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="hr_document_search")
    assert decision.allowed is True
    assert decision.model_tier == ModelTier.SONNET


def test_authorize_project_manager_denied_for_hr_only_action():
    user = _fake_user("project_manager", "engineering")
    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="hr_administration")
    assert exc_info.value.status_code == 403


def test_authorize_project_manager_allowed_to_upload_and_delete_documents():
    user = _fake_user("project_manager", "engineering")
    upload = engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="upload_documents")
    assert upload.allowed is True
    delete = engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="delete_documents")
    assert delete.allowed is True
    assert delete.requires_approval is True  # llm_rbac.yaml's approval_required_actions


def test_authorize_employee_denied_upload_and_delete_documents():
    user = _fake_user("user", "manufacturing")
    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="upload_documents")
    assert exc_info.value.status_code == 403
    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="delete_documents")
    assert exc_info.value.status_code == 403


def test_authorize_hr_allowed_upload_and_delete_documents():
    # Enterprise permission matrix update: HR now has Upload Documents (full)
    # and Delete Documents (Limited — queued for approval, see
    # llm_rbac.yaml's hr.approval_required_actions) — previously both were
    # denied outright.
    user = _fake_user("hr", "hr")
    upload = engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="upload_documents")
    assert upload.allowed is True
    delete = engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="delete_documents")
    assert delete.allowed is True
    assert delete.requires_approval is True


# -------------------------------------------------- project governance actions

def test_authorize_project_manager_allowed_project_lifecycle_actions():
    user = _fake_user("project_manager", "engineering")
    for action in ("project_creation", "project_update", "project_allocation", "project_submit"):
        decision = engine.authorize_llm_request(db=None, user=user, endpoint="projects", action=action)
        assert decision.allowed is True


def test_authorize_employee_denied_all_project_actions():
    user = _fake_user("user", "manufacturing")
    for action in ("project_creation", "project_update", "project_allocation", "project_submit"):
        with pytest.raises(AppError) as exc_info:
            engine.authorize_llm_request(db=None, user=user, endpoint="projects", action=action)
        assert exc_info.value.status_code == 403


def test_authorize_hr_denied_all_project_actions():
    user = _fake_user("hr", "hr")
    for action in ("project_creation", "project_update", "project_allocation", "project_submit"):
        with pytest.raises(AppError) as exc_info:
            engine.authorize_llm_request(db=None, user=user, endpoint="projects", action=action)
        assert exc_info.value.status_code == 403


def test_authorize_admin_wildcard_allows_project_actions():
    user = _fake_user("admin", "executive")
    for action in ("project_creation", "project_update", "project_allocation", "project_submit"):
        decision = engine.authorize_llm_request(db=None, user=user, endpoint="projects", action=action)
        assert decision.allowed is True


def test_authorize_project_manager_delete_documents_requires_approval():
    # Confirms the fix to the previously-latent inconsistency: delete_documents
    # is now actually in PM's permissions.allow, so the check passes and
    # reaches requires_approval, instead of 403ing before ever getting there.
    user = _fake_user("project_manager", "engineering")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="documents", action="delete_documents")
    assert decision.allowed is True
    assert decision.requires_approval is True


def test_authorize_omitted_action_skips_permission_check_but_keeps_governance():
    user = _fake_user("user", "manufacturing")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action=None)
    assert decision.allowed is True
    assert decision.model_tier == ModelTier.HAIKU
    assert decision.knowledge_departments == ("manufacturing",)


def test_department_defaults_from_role_when_user_has_none():
    user = _fake_user("hr", department=None)
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat")
    assert decision.department == "hr"


def test_department_prefers_explicit_user_department():
    user = _fake_user("hr", department="executive")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat")
    assert decision.department == "executive"


# ------------------------------------------------------------------- rate limit

def test_authorize_raises_429_after_exceeding_requests_per_minute():
    # "user" role's requests_per_minute is 20 in the real shipped config.
    user = _fake_user("user", "manufacturing")
    for _ in range(20):
        decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action=None)
        assert decision.allowed is True

    with pytest.raises(AppError) as exc_info:
        engine.authorize_llm_request(db=None, user=user, endpoint="chat", action=None)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "rate_limited"


def test_authorize_rate_limit_is_per_user_not_global():
    user_a = _fake_user("user", "manufacturing")
    user_b = _fake_user("user", "manufacturing")
    for _ in range(20):
        engine.authorize_llm_request(db=None, user=user_a, endpoint="chat", action=None)

    # user_a's bucket is now exhausted, user_b's is untouched.
    decision = engine.authorize_llm_request(db=None, user=user_b, endpoint="chat", action=None)
    assert decision.allowed is True


def test_authorize_admin_has_no_rate_limit():
    # admin's requests_per_minute is null (unlimited) in the real shipped config.
    user = _fake_user("admin", "executive")
    for _ in range(50):
        decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="anything_at_all")
        assert decision.allowed is True


# ------------------------------------------------------------------ kill switch

def test_disabled_kill_switch_allows_everything(monkeypatch):
    monkeypatch.setattr(policy_loader, "_raw", lambda: {"enabled": False})
    user = _fake_user("user", "manufacturing")
    decision = engine.authorize_llm_request(db=None, user=user, endpoint="chat", action="anything")
    assert decision.allowed is True
    assert decision.knowledge_departments is None  # no category restriction while disabled


# --------------------------------------------------------- missing-config fallback

def test_missing_config_falls_back_to_conservative_defaults(monkeypatch):
    monkeypatch.setattr(policy_loader, "load_yaml_config", lambda _name: {})
    policy_loader._raw.cache_clear()
    cfg = policy_loader.role_config("user")
    assert cfg.tools == frozenset({"search_documents"})
    assert cfg.default_tier == "sonnet"
    # Fine-grained named-action checks no-op to allow when there's no
    # catalog to check against — see policy_loader.py's module docstring.
    assert cfg.permissions_allow == frozenset({"*"})


# ------------------------------------------------------- knowledge_departments_for

def test_knowledge_departments_for_matches_role_config():
    assert policy_loader.knowledge_departments_for("hr") == ("hr",)
    assert policy_loader.knowledge_departments_for("admin") == (
        "manufacturing", "hr", "engineering", "executive",
    )


def test_knowledge_departments_for_is_none_when_kill_switch_off(monkeypatch):
    monkeypatch.setattr(policy_loader, "_raw", lambda: {"enabled": False})
    assert policy_loader.knowledge_departments_for("user") is None
