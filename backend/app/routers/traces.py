import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.llm_rbac import policy_loader

# Every authenticated role can call this endpoint — but what it returns is
# scoped by that role's own visibility, not gated at the router level like
# routers/audit.py. Two tiers, resolved per-request in list_traces() below:
#   - VIEW_AUDIT_LOGS roles (CEO/Admin today) keep the original org-wide
#     view, filterable by any role/user_id/department, exactly as before.
#   - every other role is hard-scoped to conversations THEY own
#     (ConversationModel.user_id == current_user.id) regardless of what
#     role/user_id/department filters they pass — this endpoint returns the
#     RAW, unsanitized trace (classifier confidence scores included, the
#     exact detail frontend/src/lib/guardrails.ts's SANITIZED_MESSAGE map
#     hides from the per-message chat panel), so "can only see your own
#     history" is a real privacy boundary here, not just a UX default. A
#     non-privileged role can never see another user's requests through
#     this endpoint, no matter what query params it sends.
router = APIRouter(prefix="/traces", tags=["traces"])

_MAX_LIMIT = 200
# Bound for the blocked/allowed filter's pre-fetch scan below — see that
# branch's own comment for why this isn't a straight SQL WHERE.
_BLOCKED_FILTER_SCAN_LIMIT = 2000


class TraceListItem(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID | None
    user_email: str | None
    user_display_name: str | None
    role: str | None
    department: str | None
    # The user's own message that this trace/reply is answering — looked up
    # as the nearest preceding role="user" message in the same conversation,
    # since messages.trace is only ever set on the assistant's own row.
    question: str | None
    created_at: datetime
    trace: list[dict]


class TraceListResponse(BaseModel):
    items: list[TraceListItem]
    total: int


def _is_blocked(trace: list[dict]) -> bool:
    # Same rule as frontend/src/lib/guardrails.ts's isBlockedResponse() —
    # kept in exactly these two places (Python here, TS there), both
    # reading the identical "Guardrails agent, block: prefix" signal off the
    # same trace shape, rather than inventing a third derived field.
    return any(
        step.get("agent") == "Guardrails" and str(step.get("summary", "")).split(":", 1)[0] == "block"
        for step in trace
    )


@router.get("", response_model=TraceListResponse)
def list_traces(
    role: str | None = None,
    department: str | None = None,
    blocked: bool | None = None,
    user_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """Parameterized filtering only, same convention as routers/audit.py's
    list_events() — every filter below is a SQLAlchemy column comparison,
    `limit` is hard-capped server-side regardless of what's requested."""
    limit = min(limit, _MAX_LIMIT)

    granted = policy_loader.role_config(current_user.role).granted_permissions
    has_broad_visibility = Permission.VIEW_AUDIT_LOGS.value in granted or "*" in granted

    query = (
        db.query(MessageModel, ConversationModel, UserModel)
        .join(ConversationModel, MessageModel.conversation_id == ConversationModel.id)
        .outerjoin(UserModel, ConversationModel.user_id == UserModel.id)
        .filter(MessageModel.role == "assistant", MessageModel.trace.isnot(None))
    )
    if has_broad_visibility:
        if role is not None:
            query = query.filter(UserModel.role == role)
        if department is not None:
            query = query.filter(UserModel.department == department)
        if user_id is not None:
            query = query.filter(ConversationModel.user_id == user_id)
    else:
        # Non-privileged caller — role/department/user_id filters above are
        # silently ignored (not honored as a caller-supplied override) in
        # favor of this hard scope, so there's no query-param path to see
        # anyone else's requests.
        query = query.filter(ConversationModel.user_id == current_user.id)
    if date_from is not None:
        query = query.filter(MessageModel.created_at >= date_from)
    if date_to is not None:
        query = query.filter(MessageModel.created_at <= date_to)
    query = query.order_by(MessageModel.created_at.desc())

    if blocked is None:
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
    else:
        # blocked/allowed isn't a stored column — it's derived from trace
        # content (_is_blocked() above), so it can't be a plain SQL WHERE
        # without a JSONB-array EXISTS subquery. Given this is an internal
        # admin review tool, not a high-traffic endpoint, a bounded
        # pre-fetch + Python filter is the pragmatic tradeoff: scans the
        # _BLOCKED_FILTER_SCAN_LIMIT most recent rows matching every OTHER
        # filter, then filters/paginates in Python. A deployment with more
        # matching history than that scan window won't all be reachable via
        # this filter — an explicit, bounded limit, not a silent cap.
        candidates = query.limit(_BLOCKED_FILTER_SCAN_LIMIT).all()
        filtered = [row for row in candidates if _is_blocked(row[0].trace) == blocked]
        total = len(filtered)
        rows = filtered[offset : offset + limit]

    conversation_ids = {row[0].conversation_id for row in rows}
    user_messages = (
        db.query(MessageModel)
        .filter(MessageModel.conversation_id.in_(conversation_ids), MessageModel.role == "user")
        .all()
        if conversation_ids
        else []
    )
    by_conversation: dict[uuid.UUID, list[MessageModel]] = {}
    for m in user_messages:
        by_conversation.setdefault(m.conversation_id, []).append(m)
    for msgs in by_conversation.values():
        msgs.sort(key=lambda m: m.created_at)

    def _question_for(assistant_msg: MessageModel) -> str | None:
        preceding = [m for m in by_conversation.get(assistant_msg.conversation_id, []) if m.created_at < assistant_msg.created_at]
        return preceding[-1].content if preceding else None

    items = [
        TraceListItem(
            message_id=msg.id,
            conversation_id=msg.conversation_id,
            user_id=conv.user_id,
            user_email=user.email if user else None,
            user_display_name=user.display_name if user else None,
            role=user.role if user else None,
            department=user.department if user else None,
            question=_question_for(msg),
            created_at=msg.created_at,
            trace=msg.trace or [],
        )
        for msg, conv, user in rows
    ]
    return TraceListResponse(items=items, total=total)
