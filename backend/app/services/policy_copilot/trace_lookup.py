"""Read-only queries over real guardrail traces, for the Copilot's
EXPLAIN_GUARDRAIL_FAILURE ("why was my request blocked?") and
GUARDRAIL_ACTIVITY ("show me today's guardrail failures") intents.

Deliberately re-derives — rather than imports — the same query shape and
two-tier RBAC scoping `routers/traces.py` already implements (VIEW_AUDIT_LOGS
holders see every request; everyone else sees only their own): importing a
router module from a service module would be a layering violation, and the
two call sites read the identical `MessageModel.trace` JSON shape
(`{"agent": ..., "tool": ..., "summary": "<action>: <detail>"}`, written by
routers/chat.py::_guardrail_trace()) for the same reason — a Copilot answer
must never show more, or less, than the dedicated Traces page already would
for the same caller.

Every answer here is assembled from real persisted trace data. None of it is
generated — same rule as answers.py's other functions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.permissions import Permission
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.user import UserModel
from app.services.guardrails.decision_explainer import explain_decision
from app.services.llm_rbac import policy_loader

#: Same bound routers/traces.py uses for its own blocked/allowed Python-side
#: filter — an internal admin review tool, not a high-traffic endpoint.
_SCAN_LIMIT = 500


def _has_broad_visibility(user: UserModel) -> bool:
    granted = policy_loader.role_config(user.role).granted_permissions
    return Permission.VIEW_AUDIT_LOGS.value in granted or "*" in granted


def _is_blocked(trace: list[dict]) -> bool:
    return any(
        step.get("agent") == "Guardrails" and str(step.get("summary", "")).split(":", 1)[0] == "block"
        for step in trace
    )


def _blocking_step(trace: list[dict]) -> dict | None:
    return next(
        (
            step for step in trace
            if step.get("agent") == "Guardrails" and str(step.get("summary", "")).split(":", 1)[0] == "block"
        ),
        None,
    )


def _scoped_query(db: Session, user: UserModel):
    query = (
        db.query(MessageModel, ConversationModel)
        .join(ConversationModel, MessageModel.conversation_id == ConversationModel.id)
        .filter(MessageModel.role == "assistant", MessageModel.trace.isnot(None))
    )
    if not _has_broad_visibility(user):
        query = query.filter(ConversationModel.user_id == user.id)
    return query.order_by(MessageModel.created_at.desc())


def explain_most_recent_failure(db: Session, user: UserModel) -> str:
    """"Why was my request blocked?" — the most recent blocked reply visible
    to this caller (org-wide for VIEW_AUDIT_LOGS holders, own conversations
    only otherwise — same split as the Traces page)."""
    rows = _scoped_query(db, user).limit(_SCAN_LIMIT).all()
    scope = "org-wide" if _has_broad_visibility(user) else "your own conversations"
    for msg, _conv in rows:
        step = _blocking_step(msg.trace or [])
        if step is None:
            continue
        when = msg.created_at.isoformat() if msg.created_at else "unknown time"
        reason = str(step.get("summary", "")).split(":", 1)
        detail = reason[1].strip() if len(reason) > 1 else step.get("summary", "unknown")
        check = step.get("tool") or "unknown check"
        lines = [
            f"Most recent blocked request ({scope}, {when}):",
            "",
            f"  Blocked by : {check}",
            f"  Reason     : {detail}",
        ]
        # Best-effort, post-hoc only — the decision above is already final;
        # this can only add a plain-language gloss on it, never change it.
        # See decision_explainer.py's module docstring for the full posture.
        explanation = explain_decision(blocking_check=check, action="BLOCK", detail=detail)
        if explanation:
            lines += ["", f"  {explanation}"]
        lines += ["", "Ask \"show me today's guardrail failures\" for a broader view."]
        return "\n".join(lines)
    return f"No blocked requests found in {scope} (checked the most recent {_SCAN_LIMIT} traced replies)."


def activity_summary(db: Session, user: UserModel, hours: int | None = None) -> str:
    """"Show me today's guardrail failures" — an aggregate over real trace
    data for the requested window (default 24h), scoped the same way as
    explain_most_recent_failure() above."""
    window = hours or 24
    since = datetime.now(timezone.utc) - timedelta(hours=window)
    rows = _scoped_query(db, user).filter(MessageModel.created_at >= since).limit(_SCAN_LIMIT).all()
    scope = "org-wide" if _has_broad_visibility(user) else "your own conversations"

    if not rows:
        return f"No traced activity in the last {window}h ({scope})."

    blocked_steps = [_blocking_step(msg.trace or []) for msg, _conv in rows]
    blocked_steps = [s for s in blocked_steps if s is not None]

    lines = [
        f"{len(blocked_steps)} of {len(rows)} traced replies were blocked in the last {window}h ({scope}).",
    ]
    if blocked_steps:
        by_check: dict[str, int] = {}
        for step in blocked_steps:
            check = str(step.get("tool") or "unknown")
            by_check[check] = by_check.get(check, 0) + 1
        lines += ["", "By check:"]
        for check, count in sorted(by_check.items(), key=lambda kv: -kv[1])[:10]:
            lines.append(f"  {count:>3}x  {check}")
    return "\n".join(lines)
