"""The LLM RBAC policy engine — the single entrypoint every user-role-driven
caller of the Claude Gateway goes through (routers/chat.py, routers/search.py)
before the planner/gateway ever runs. See docs/LLM_RBAC_ARCHITECTURE.md for
the full request flow.

Internal system callers that aren't driven by an end-user role
(generation_judge.py's eval scoring, memory/store.py's conversation
summarization) are deliberately outside this policy loop — see
docs/CLAUDE_GATEWAY_MODEL_ROUTING.md for why.
"""

import logging

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.gateway.schemas import ModelTier
from app.models.user import UserModel
from app.services.llm_rbac import policy_loader, quotas, rate_limiter
from app.services.llm_rbac.schemas import PolicyDecision

logger = logging.getLogger(__name__)

_ALL_TOOLS = frozenset({"search_documents", "query_analytics", "generate_report", "list_my_projects"})


def authorize_llm_request(
    db: Session, user: UserModel, *, endpoint: str, action: str | None = None, requested_tier: str | None = None
) -> PolicyDecision:
    """Runs every governance check the spec asks for, in order, before a
    single token of the request reaches Claude: named-permission allow/deny,
    daily/monthly token & cost budget. Raises AppError (403 for a permission
    denial, 429 for a quota denial) on the first check that fails — callers
    should catch AppError, write a denied
    audit row (see gateway/usage_tracker.py), and let it propagate to
    FastAPI's existing error handler; nothing about that error path changes.

    `action` is an optional, caller-supplied capability name matching
    llm_rbac.yaml's permission catalog (e.g. "workforce_planning",
    "hr_report_generation") — used both for the fine-grained allow/deny check
    and to decide whether this specific request escalates to the Opus tier.
    Omitting it (the common case — a normal chat/search turn) still gets the
    role's full tool/knowledge/quota governance; it just skips the named-
    action check and uses the role's default tier. See
    docs/CLAUDE_GATEWAY_MODEL_ROUTING.md for why tier escalation is
    action-based rather than a free-text complexity classifier.

    `requested_tier` is an optional client-chosen override (e.g. the chat
    UI's "try a different model" retry button after a degraded response) —
    still fully gated by the role's tiers_allowed (see _resolve_tier), so a
    caller can never manually pick a tier their role doesn't permit. Omitting
    it keeps the existing action-based auto-resolution unchanged.
    """
    role_cfg = policy_loader.role_config(user.role)
    department = user.department or role_cfg.department_default

    if not policy_loader.is_enabled():
        # Kill switch — pre-RBAC behavior: everything allowed, cheapest tier,
        # every tool, no department narrowing.
        return PolicyDecision(
            allowed=True,
            role=user.role,
            department=department,
            model_tier=ModelTier.FAST,
            allowed_tools=_ALL_TOOLS,
            sql_allowed_tables=None,
            knowledge_departments=None,
            max_concurrent_requests=None,
        )

    _check_permission(role_cfg, user.role, action)

    # Cheap in-memory check before the Postgres-backed budget check below —
    # shared with every authorize_llm_request caller (search + chat) so both
    # get rate-limited from the same per-role requests_per_minute config,
    # with no duplicated quota-interpretation logic outside this module.
    rate_limiter.check_rate_limit(user.id, role_cfg.quotas.get("requests_per_minute"))

    quotas.check_budget(db, user.id, role_cfg.quotas)

    return PolicyDecision(
        allowed=True,
        role=user.role,
        department=department,
        model_tier=_resolve_tier(role_cfg, action, requested_tier),
        allowed_tools=role_cfg.tools,
        sql_allowed_tables=role_cfg.sql_allowed_tables,
        knowledge_departments=role_cfg.knowledge_departments,
        max_concurrent_requests=role_cfg.quotas.get("max_concurrent_requests"),
        requires_approval=bool(action and action in role_cfg.approval_required_actions),
    )


def _check_permission(role_cfg: policy_loader.RoleConfig, role: str, action: str | None) -> None:
    if action is None or "*" in role_cfg.permissions_allow:
        return
    if action in role_cfg.permissions_deny:
        raise AppError(403, "llm_rbac_denied", f"'{action}' is explicitly denied for role '{role}'")
    if action not in role_cfg.permissions_allow:
        raise AppError(403, "llm_rbac_denied", f"'{action}' is not permitted for role '{role}'")


def _resolve_tier(role_cfg: policy_loader.RoleConfig, action: str | None, requested_tier: str | None = None) -> ModelTier:
    """Resolved from role + (optional) action — unless the caller explicitly
    requested a tier (the manual "try a different model" path), in which case
    that choice wins outright, still bounded by role_cfg.tiers_allowed: an
    Employee's tiers_allowed contains only "sonnet", so a requested "opus" is
    rejected (403) rather than silently downgraded, same as any other
    permission denial — see the caller-facing check right below."""
    if requested_tier is not None:
        if requested_tier not in {t.value for t in ModelTier}:
            raise AppError(400, "invalid_model_tier", f"{requested_tier!r} is not a recognized model tier")
        if requested_tier not in role_cfg.tiers_allowed:
            raise AppError(
                403, "llm_rbac_tier_denied",
                f"Role {role_cfg.role!r} is not permitted to use model tier {requested_tier!r}",
            )
        return ModelTier(requested_tier)

    tier_value = role_cfg.default_tier
    if action and action in role_cfg.escalate_to_opus_for and "opus" in role_cfg.tiers_allowed:
        tier_value = "opus"
    if tier_value not in role_cfg.tiers_allowed:
        tier_value = role_cfg.default_tier
    try:
        return ModelTier(tier_value)
    except ValueError:
        logger.warning("llm_rbac: role %s has unrecognized tier %r, falling back to sonnet", role_cfg.role, tier_value)
        return ModelTier.SONNET
