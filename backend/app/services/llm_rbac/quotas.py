"""Daily/monthly token & cost budget checks — the enforcement mechanism
services/llm_rbac/engine.py uses for per-role budgets over day/month windows.

RoleUsageCounterModel is a small pre-aggregated rollup (see its docstring):
gateway_usage_logs stays the detailed audit trail, this table exists purely
so check_budget() is one indexed row lookup instead of re-aggregating the
full log table on every request.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.role_usage_counter import RoleUsageCounterModel

logger = logging.getLogger(__name__)


def _period_start(period_type: str, now: datetime) -> datetime:
    if period_type == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _get_or_create(db: Session, user_id: uuid.UUID, period_type: str, period_start: datetime) -> RoleUsageCounterModel:
    row = (
        db.query(RoleUsageCounterModel)
        .filter(
            RoleUsageCounterModel.user_id == user_id,
            RoleUsageCounterModel.period_type == period_type,
            RoleUsageCounterModel.period_start == period_start,
        )
        .one_or_none()
    )
    if row is None:
        row = RoleUsageCounterModel(user_id=user_id, period_type=period_type, period_start=period_start)
        db.add(row)
        db.flush()
    return row


def _get_or_create_resilient(db: Session, user_id: uuid.UUID, period_type: str, period_start: datetime) -> RoleUsageCounterModel:
    # authorize_llm_request (search + chat's shared entrypoint) previously
    # let a Postgres hiccup here surface as a raw, unclassified 500 — the one
    # DB access on the authorization path Phase 2 hardening hadn't reached
    # yet, found live under concurrent /search load (row-lock contention on
    # role_usage_counters' one row per user/period). Same retry + graceful
    # AppError shape as services/retrieval/search.py's _run_postgres.
    #
    # Import deferred (not module-level): app.db.resilience imports
    # app.gateway.retry_handler, which triggers app/gateway/__init__.py,
    # which imports claude_gateway -> usage_tracker -> this module — a
    # module-level import here would be circular. By call time every module
    # in that chain has already finished loading.
    from app.db.resilience import POSTGRES_EXCEPTIONS, postgres_call_with_retry

    try:
        return postgres_call_with_retry(
            lambda: _get_or_create(db, user_id, period_type, period_start), agent_name="llm_rbac.check_budget", db=db
        )
    except POSTGRES_EXCEPTIONS as exc:
        logger.exception("llm_rbac: quota check failed for user_id=%s period_type=%s", user_id, period_type)
        raise AppError(503, "rbac_check_unavailable", "Unable to verify your request quota right now. Please try again shortly.") from exc


def effective_quotas(
    role_quotas: dict, *, daily_token_limit_override: int | None = None, monthly_token_limit_override: int | None = None
) -> dict:
    """Merges a per-user token-limit override (UserModel.daily_token_limit_override /
    monthly_token_limit_override, set by Admin/CEO via PUT /users/{id}/token-limit,
    see routers/users.py) on top of the role's default quotas from llm_rbac.yaml.
    The override wins when set; every other quota field (requests, cost, rate limit)
    stays role-level only — this endpoint only ever caps tokens."""
    merged = dict(role_quotas)
    if daily_token_limit_override is not None:
        merged["daily_tokens"] = daily_token_limit_override
    if monthly_token_limit_override is not None:
        merged["monthly_tokens"] = monthly_token_limit_override
    return merged


def check_budget(db: Session, user_id: uuid.UUID, role_quotas: dict) -> None:
    """Raises AppError(429) if the caller's day/month request, token, or cost
    budget is already exhausted. Read-only — increment_usage() is what
    advances the counters, called only after a gateway call actually
    completes, so a request that gets denied here never itself counts
    against the budget it just failed."""
    now = datetime.now(timezone.utc)

    daily_requests = role_quotas.get("daily_requests")
    daily_tokens = role_quotas.get("daily_tokens")
    if daily_requests is not None or daily_tokens is not None:
        day_row = _get_or_create_resilient(db, user_id, "day", _period_start("day", now))
        if daily_requests is not None and day_row.request_count >= daily_requests:
            raise AppError(429, "llm_rbac_quota_exceeded", "Daily request budget exhausted for this role")
        if daily_tokens is not None and day_row.tokens_used >= daily_tokens:
            raise AppError(429, "llm_rbac_quota_exceeded", "Daily token budget exhausted for this role")

    monthly_tokens = role_quotas.get("monthly_tokens")
    monthly_cost = role_quotas.get("monthly_cost_usd")
    if monthly_tokens is not None or monthly_cost is not None:
        month_row = _get_or_create_resilient(db, user_id, "month", _period_start("month", now))
        if monthly_tokens is not None and month_row.tokens_used >= monthly_tokens:
            raise AppError(429, "llm_rbac_quota_exceeded", "Monthly token budget exhausted for this role")
        if monthly_cost is not None and month_row.cost_usd_used >= monthly_cost:
            raise AppError(429, "llm_rbac_quota_exceeded", "Monthly cost budget exhausted for this role")


def get_usage(db: Session, user_id: uuid.UUID) -> dict:
    """Read-only day/month usage snapshot — for display (e.g. a user-facing
    quota widget), never for an allow/deny decision. Unlike check_budget()'s
    _get_or_create, this never inserts a row: a user with no activity yet
    just reads back as all zeros."""
    now = datetime.now(timezone.utc)

    def _read(period_type: str) -> RoleUsageCounterModel | None:
        return (
            db.query(RoleUsageCounterModel)
            .filter(
                RoleUsageCounterModel.user_id == user_id,
                RoleUsageCounterModel.period_type == period_type,
                RoleUsageCounterModel.period_start == _period_start(period_type, now),
            )
            .one_or_none()
        )

    day_row = _read("day")
    month_row = _read("month")
    return {
        "daily_requests_used": day_row.request_count if day_row else 0,
        "daily_tokens_used": day_row.tokens_used if day_row else 0,
        "monthly_tokens_used": month_row.tokens_used if month_row else 0,
        "monthly_cost_usd_used": month_row.cost_usd_used if month_row else 0.0,
    }


def reset_usage(db: Session, user_id: uuid.UUID) -> None:
    """Zeroes a user's CURRENT day/month usage counters — the ones
    check_budget()/get_usage() actually read. An explicit admin action (see
    POST /users/{id}/usage/reset in routers/users.py), deliberately never an
    automatic side effect of PUT /users/{id}/token-limit: tying a reset to
    every limit edit would let a user who's already exhausted their quota
    get a clean slate from any admin touch of their limit at all, silently
    turning the quota from an abuse control into something bypassable.
    Doesn't touch past periods — those are historical record, not a live
    counter, so nothing to "reset" there. Caller commits."""
    now = datetime.now(timezone.utc)
    for period_type in ("day", "month"):
        row = (
            db.query(RoleUsageCounterModel)
            .filter(
                RoleUsageCounterModel.user_id == user_id,
                RoleUsageCounterModel.period_type == period_type,
                RoleUsageCounterModel.period_start == _period_start(period_type, now),
            )
            .one_or_none()
        )
        if row is not None:
            row.tokens_used = 0
            row.cost_usd_used = 0.0
            row.request_count = 0


def increment_usage(db: Session, user_id: uuid.UUID, *, tokens: int, cost_usd: float) -> None:
    """Called from gateway/usage_tracker.py::record_usage() right after a
    successful gateway call, in the same transaction as the audit-log row."""
    now = datetime.now(timezone.utc)
    for period_type in ("day", "month"):
        row = _get_or_create(db, user_id, period_type, _period_start(period_type, now))
        row.tokens_used += tokens
        row.cost_usd_used += cost_usd
        row.request_count += 1
