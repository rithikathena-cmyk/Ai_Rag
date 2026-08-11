"""Loads and caches backend/config/llm_rbac.yaml into typed RoleConfig
objects. This is the single place llm_rbac.yaml's shape is interpreted —
every other module in services/llm_rbac/ and its callers work with
RoleConfig, never the raw dict.
"""

from dataclasses import dataclass
from functools import lru_cache

from app.core.roles import Role
from app.core.yaml_config import load_yaml_config

# Safety net for a missing/corrupt llm_rbac.yaml in a deployed image — NOT a
# copy of the shipped file's full permission catalog (that would just create
# two sources of truth that can silently drift). The load-bearing enforcement
# surfaces (tools/tiers/knowledge departments/quotas) get a conservative
# default here; the fine-grained named-action check (permissions.allow/deny)
# no-ops to "allow" when this fallback is in use, since there's no catalog to
# check an action name against. Mirrors gateway/model_router.py's own
# _DEFAULTS-as-safety-net precedent.
_FALLBACK_TOOLS: dict[str, frozenset[str]] = {
    Role.USER.value: frozenset({"search_documents"}),
    Role.HR.value: frozenset({"search_documents", "query_analytics", "generate_report"}),
    Role.PROJECT_MANAGER.value: frozenset({"search_documents", "query_analytics", "generate_report"}),
    Role.ADMIN.value: frozenset({"search_documents", "query_analytics", "generate_report"}),
}
_FALLBACK_TIER: dict[str, str] = {
    Role.USER.value: "sonnet",
    Role.HR.value: "sonnet",
    Role.PROJECT_MANAGER.value: "sonnet",
    Role.ADMIN.value: "sonnet",
}

# Every role-driven model tier (app/gateway/schemas.py::ModelTier) that a
# role's tiers_allowed can name — deliberately excludes FAST/REASONING,
# which are a separate vocabulary for internal system callers (see
# ModelTier's own docstring), never something an end-user role resolves to.
# A role's yaml entry can write `tiers_allowed: ["*"]` (same wildcard
# convention as permissions.allow's "*") instead of spelling out every tier,
# resolved to this full set below — CEO/Admin use this so they
# automatically pick up any *existing* tier without a yaml edit. Adding a
# genuinely new tier (a 4th ModelTier member + models.yaml entry) is still
# necessarily a code change — "*" can't grant a tier that doesn't exist yet.
_ROLE_DRIVEN_TIERS: frozenset[str] = frozenset({"haiku", "sonnet", "opus"})
_FALLBACK_DEPARTMENTS: dict[str, tuple[str, ...]] = {
    Role.USER.value: ("manufacturing",),
    Role.HR.value: ("hr",),
    Role.PROJECT_MANAGER.value: ("engineering",),
    Role.ADMIN.value: ("manufacturing", "hr", "engineering", "executive"),
}
_FALLBACK_QUOTAS: dict[str, dict] = {
    Role.USER.value: {
        "requests_per_minute": 20, "daily_requests": 200, "daily_tokens": 100000,
        "monthly_tokens": 2000000, "monthly_cost_usd": 50, "max_concurrent_requests": 2,
    },
    Role.HR.value: {
        "requests_per_minute": 30, "daily_requests": 300, "daily_tokens": 300000,
        "monthly_tokens": 6000000, "monthly_cost_usd": 200, "max_concurrent_requests": 3,
    },
    Role.PROJECT_MANAGER.value: {
        "requests_per_minute": 30, "daily_requests": 300, "daily_tokens": 300000,
        "monthly_tokens": 6000000, "monthly_cost_usd": 200, "max_concurrent_requests": 3,
    },
    Role.ADMIN.value: {
        "requests_per_minute": None, "daily_requests": None, "daily_tokens": None,
        "monthly_tokens": None, "monthly_cost_usd": None, "max_concurrent_requests": 10,
    },
}


@dataclass(frozen=True)
class RoleConfig:
    role: str
    display_name: str
    department_default: str | None
    tiers_allowed: frozenset[str]
    default_tier: str
    escalate_to_opus_for: frozenset[str]
    dynamic: bool
    knowledge_departments: tuple[str, ...]
    tools: frozenset[str]
    sql_allowed_tables: frozenset[str] | None  # None = no narrowing (sql_guard's own default allowlist)
    permissions_allow: frozenset[str]
    permissions_deny: frozenset[str]
    quotas: dict
    approval_required_actions: frozenset[str]
    # Coarse, REST-resource permission catalog (app/core/permissions.py) for
    # nav visibility + require_permission() endpoint gating — distinct from
    # permissions_allow/deny above, which gates fine-grained named actions
    # inside /chat and /search. "*" means every permission is granted
    # (admin's real yaml entry uses this); an unknown/fallback role gets an
    # empty set (deny-all), NOT "*" — see _fallback_role_config's comment.
    granted_permissions: frozenset[str]
    # Report-type catalog (services/llm_rbac/report_policy.py) — a distinct
    # concept from permissions_allow: this names report *artifacts* a role
    # may request, not general capabilities. "*" wildcard supported, same as
    # permissions_allow's admin entry.
    reports_allowed: frozenset[str]


def _fallback_role_config(role: str) -> RoleConfig:
    departments = _FALLBACK_DEPARTMENTS.get(role, ())
    return RoleConfig(
        role=role,
        display_name=role,
        department_default=departments[0] if departments else None,
        tiers_allowed=frozenset({_FALLBACK_TIER.get(role, "sonnet")}),
        default_tier=_FALLBACK_TIER.get(role, "sonnet"),
        escalate_to_opus_for=frozenset(),
        dynamic=False,
        knowledge_departments=departments,
        tools=_FALLBACK_TOOLS.get(role, frozenset({"search_documents"})),
        sql_allowed_tables=None,
        permissions_allow=frozenset({"*"}),
        permissions_deny=frozenset(),
        quotas=_FALLBACK_QUOTAS.get(role, _FALLBACK_QUOTAS[Role.USER.value]),
        approval_required_actions=frozenset(),
        # No fallback report-type catalog to check against, same reasoning as
        # permissions_allow's "*" above — see that field's comment.
        reports_allowed=frozenset({"*"}),
        # Deliberately empty, NOT "*" like permissions_allow above — this
        # fallback also fires for a role string simply absent from a
        # present, valid config (e.g. one of the inert manufacturing
        # Role values, or a typo), not just a missing/corrupt file. Defaulting
        # the new coarse UI/endpoint permission system to deny-all there is
        # the safe choice: it only degrades admin-panel-shaped access, and
        # the core /chat flow is unaffected (governed by permissions_allow's
        # own fallback, unchanged).
        granted_permissions=frozenset(),
    )


@lru_cache(maxsize=None)
def _raw() -> dict:
    return load_yaml_config("llm_rbac.yaml")


def is_enabled() -> bool:
    raw = _raw()
    if not raw:
        return True  # fail-safe: governed by the conservative fallback above, not "off"
    return bool(raw.get("enabled", True))


def departments() -> tuple[str, ...]:
    return tuple(_raw().get("departments") or ())


def all_roles() -> tuple[str, ...]:
    """Every role key actually defined in llm_rbac.yaml — for the read-only
    Roles & Permissions admin view (routers/admin.py::list_roles), not for
    per-request authorization (that's always role_config(role) for the
    caller's own role)."""
    return tuple(_raw().get("roles", {}).keys())


def knowledge_departments_for(role: str) -> tuple[str, ...] | None:
    """Read-side counterpart to services/llm_rbac/engine.py::authorize_llm_request()'s
    `knowledge_departments`, for callers that only need department-visibility
    filtering (document/report browsing via routers/documents.py,
    routers/reports.py) without that function's rate-limit/quota checks —
    those govern Claude Gateway requests specifically, not plain reads.
    `None` means unrestricted, the same kill-switch sentinel
    authorize_llm_request() returns and the same "no restriction" value
    guardrails/retrieval_permissions.py::filter_by_category() already
    expects."""
    if not is_enabled():
        return None
    return role_config(role).knowledge_departments


@lru_cache(maxsize=None)
def role_config(role: str) -> RoleConfig:
    raw = _raw()
    roles_raw = raw.get("roles", {})
    entry = roles_raw.get(role)
    if entry is None:
        return _fallback_role_config(role)

    model = entry.get("model") or {}
    permissions = entry.get("permissions") or {}

    escalate = set(model.get("escalate_to_opus_for") or [])
    if model.get("dynamic"):
        # CEO/Admin: union of every other role's escalation triggers,
        # computed here rather than duplicated in the YAML — see
        # llm_rbac.yaml's comment on roles.admin.model.escalate_to_opus_for.
        for other_role, other_entry in roles_raw.items():
            if other_role == role:
                continue
            escalate |= set(((other_entry or {}).get("model") or {}).get("escalate_to_opus_for") or [])

    sql_tables = entry.get("sql_allowed_tables")
    reports = entry.get("reports") or {}

    raw_tiers = model.get("tiers_allowed") or [_FALLBACK_TIER.get(role, "sonnet")]
    tiers_allowed = _ROLE_DRIVEN_TIERS if "*" in raw_tiers else frozenset(raw_tiers)

    return RoleConfig(
        role=role,
        display_name=entry.get("display_name", role),
        department_default=entry.get("department_default"),
        tiers_allowed=tiers_allowed,
        default_tier=model.get("default_tier", "sonnet"),
        escalate_to_opus_for=frozenset(escalate),
        dynamic=bool(model.get("dynamic", False)),
        knowledge_departments=tuple(entry.get("knowledge_departments") or ()),
        tools=frozenset(entry.get("tools") or ()),
        sql_allowed_tables=frozenset(sql_tables) if sql_tables is not None else None,
        permissions_allow=frozenset(permissions.get("allow") or ()),
        permissions_deny=frozenset(permissions.get("deny") or ()),
        quotas=entry.get("quotas") or {},
        approval_required_actions=frozenset(entry.get("approval_required_actions") or ()),
        reports_allowed=frozenset(reports.get("allowed") or ()),
        # Absent key -> empty set (deny-all for the new coarse system) rather
        # than the fine-grained system's fallback-to-allow — a role entry
        # that hasn't been given rbac_permissions yet shouldn't silently get
        # every UI/endpoint permission.
        granted_permissions=frozenset(entry.get("rbac_permissions") or ()),
    )
