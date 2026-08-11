import logging
import uuid

from app.core.yaml_config import load_yaml_config
from app.db.postgres import new_session
from app.gateway.schemas import TokenUsage
from app.models.gateway_usage_log import GatewayUsageLogModel
from app.services.llm_rbac import quotas as llm_rbac_quotas
from app.services.monitoring.metrics import record_token_usage

logger = logging.getLogger(__name__)


def _price_per_million(model: str) -> tuple[float, float]:
    pricing = load_yaml_config("models.yaml").get("pricing", {})
    entry = pricing.get(model, {})
    return entry.get("input_per_million_usd", 0.0), entry.get("output_per_million_usd", 0.0)


def estimate_cost_usd(model: str, usage: TokenUsage) -> float:
    input_price, output_price = _price_per_million(model)
    return (usage.input_tokens / 1_000_000) * input_price + (usage.output_tokens / 1_000_000) * output_price


def record_usage(
    *,
    request_id: str,
    agent_name: str,
    model: str,
    tier: str,
    usage: TokenUsage,
    latency_ms: float,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    department: str | None = None,
    prompt_version: str | None = None,
    tool_calls: list[str] | None = None,
    documents_retrieved: list[str] | None = None,
    requested_capability: str | None = None,
    output_format: str | None = None,
    resource_scope: dict | None = None,
) -> None:
    """Persists one gateway call to Postgres (the LLM-RBAC audit log —
    docs/AUDIT_LOGGING.md — when user_id/role are supplied) and mirrors it
    into the existing in-memory metrics dashboard. Best-effort: a tracking
    failure must never break the caller's actual LLM response (same pattern
    as `_log_upload` in routers/documents.py).

    `user_id`/`role`/`department` are only ever supplied by callers driven by
    an end-user request (routers/chat.py, routers/search.py via
    services/agents/planner.py) — the internal system callers
    (generation_judge.py, memory/store.py) leave them None, since those
    aren't governed by LLM RBAC (see services/llm_rbac/engine.py's
    docstring). When user_id is supplied, this also advances that user's
    daily/monthly quota counters (services/llm_rbac/quotas.py) in the same
    step, so a quota check on the *next* request sees this one's usage."""
    record_token_usage(agent_name, model, usage.input_tokens, usage.output_tokens)

    cost = estimate_cost_usd(model, usage)
    try:
        db = new_session()
        try:
            db.add(
                GatewayUsageLogModel(
                    request_id=request_id,
                    agent_name=agent_name,
                    model=model,
                    tier=tier,
                    tokens_input=usage.input_tokens,
                    tokens_output=usage.output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    user_id=user_id,
                    role=role,
                    department=department,
                    prompt_version=prompt_version,
                    tool_calls=tool_calls,
                    documents_retrieved=documents_retrieved,
                    requested_capability=requested_capability,
                    output_format=output_format,
                    resource_scope=resource_scope,
                    decision="allowed",
                )
            )
            if user_id is not None:
                llm_rbac_quotas.increment_usage(
                    db, user_id, tokens=usage.input_tokens + usage.output_tokens, cost_usd=cost
                )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Gateway usage tracking failed for request_id=%s agent=%s", request_id, agent_name)


def record_search(
    *,
    request_id: str,
    user_id: uuid.UUID,
    role: str,
    department: str | None,
    latency_ms: float,
    requested_capability: str | None = None,
    documents_retrieved: list[str] | None = None,
) -> None:
    """Audit-log + quota counterpart to record_usage() for a successful
    POST /search call — reuses GatewayUsageLogModel the same way
    record_denied() already does (model="n/a", tier="n/a", zero tokens/cost)
    rather than a new table, since this is already the one durable "who did
    what, was it allowed" trail. Also advances daily_requests via
    increment_usage() — today that only happens for actual Claude Gateway
    generations, so search-only traffic was checked against daily_requests
    (services/llm_rbac/engine.py) but never counted toward it.

    Deliberately does NOT store the raw query text: it's user-typed free
    text that could contain names/HR/health terms, and no existing column on
    this model captures query text for any endpoint — adding one is a
    separate schema + retention/PII decision, not made here.

    Best-effort, matching record_usage()/record_denied()'s existing pattern:
    a tracking failure must never break the caller's actual search result."""
    try:
        db = new_session()
        try:
            db.add(
                GatewayUsageLogModel(
                    request_id=request_id,
                    agent_name="search_endpoint",
                    model="n/a",
                    tier="n/a",
                    tokens_input=0,
                    tokens_output=0,
                    latency_ms=latency_ms,
                    cost_usd=0.0,
                    user_id=user_id,
                    role=role,
                    department=department,
                    requested_capability=requested_capability,
                    documents_retrieved=documents_retrieved,
                    decision="allowed",
                )
            )
            llm_rbac_quotas.increment_usage(db, user_id, tokens=0, cost_usd=0.0)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Search audit logging failed for request_id=%s user_id=%s", request_id, user_id)


def record_denied(
    *,
    agent_name: str,
    user_id: uuid.UUID,
    role: str,
    department: str | None,
    denial_reason: str,
    requested_capability: str | None = None,
) -> None:
    """The audit-log counterpart to record_usage() for a request the LLM RBAC
    policy engine rejected before Claude was ever called (permission, rate
    limit, or budget denial) — the spec requires every decision logged, not
    just allowed ones. Zero tokens/cost, since nothing was generated."""
    try:
        db = new_session()
        try:
            db.add(
                GatewayUsageLogModel(
                    request_id=str(uuid.uuid4()),
                    agent_name=agent_name,
                    model="n/a",
                    tier="n/a",
                    tokens_input=0,
                    tokens_output=0,
                    latency_ms=0.0,
                    cost_usd=0.0,
                    user_id=user_id,
                    role=role,
                    department=department,
                    requested_capability=requested_capability,
                    decision="denied",
                    denial_reason=denial_reason[:256],
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Gateway denied-request audit logging failed for user_id=%s agent=%s", user_id, agent_name)
