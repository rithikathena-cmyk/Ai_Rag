"""The one and only path a raw, pre-redaction PII value can reach an API
response. Two routes:

  GET /admin/traces/{message_id}/pii            — list occurrences for a
      message, SANITIZED (entity type, the same sanitized_value everyone
      already sees, detector, direction, country, timestamp — never
      raw_value). Same visibility rule as GET /traces itself: the caller
      already sees this message's trace in less structured form, so this
      just gives the frontend stable entity_ids to reveal one at a time.

  GET /admin/traces/{message_id}/pii/{entity_id} — reveals ONE occurrence's
      raw_value. Gated on Permission.PII_VIEW_RAW (Admin only by default —
      core/permissions.py), re-authorized against the occurrence's own
      conversation via the SAME authorize_conversation_access() every other
      conversation-scoped read uses, and audited as PII_VIEWED on every
      success before the value is returned.

Both 404 (not 403) when the message/entity doesn't exist or isn't visible to
this caller — same "don't confirm existence to someone who can't see it"
convention traces.py/memory/store.py already use.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.request_context import get_current_request_id
from app.db.postgres import get_db
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.pii_occurrence import PiiOccurrenceModel
from app.models.user import UserModel
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission
from app.services.memory.store import authorize_conversation_access

router = APIRouter(prefix="/admin/traces", tags=["pii-access"])


def _load_message_or_404(db: Session, message_id: uuid.UUID, current_user: UserModel) -> MessageModel:
    message = db.get(MessageModel, message_id)
    if message is None:
        raise AppError(404, "message_not_found", "Message not found")
    conversation = db.get(ConversationModel, message.conversation_id)
    if conversation is None:
        raise AppError(404, "message_not_found", "Message not found")
    authorize_conversation_access(conversation, current_user)  # raises 404, never 403 — see module docstring
    return message


@router.get("/{message_id}/pii")
def list_pii_occurrences(
    message_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """SANITIZED metadata only. Visibility = the same rule as seeing the
    trace at all (own conversation, or VIEW_AUDIT_LOGS) — this does NOT
    require PII_VIEW_RAW, matching that seeing "a PHONE was redacted here"
    is already implicit in the trace this caller can already read; only
    seeing what it originally was requires the stronger permission."""
    _load_message_or_404(db, message_id, current_user)
    rows = (
        db.query(PiiOccurrenceModel)
        .filter(PiiOccurrenceModel.message_id == message_id)
        .order_by(PiiOccurrenceModel.created_at)
        .all()
    )
    return {
        "items": [
            {
                "entity_id": str(r.id),
                "direction": r.direction,
                "entity_type": r.entity_type,
                "detector": r.detector,
                "country": r.country,
                "sanitized_value": r.sanitized_value,
                "policy_version": r.policy_version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.get(
    "/{message_id}/pii/{entity_id}",
    dependencies=[Depends(require_permission(Permission.PII_VIEW_RAW))],
)
def reveal_pii_occurrence(
    message_id: uuid.UUID,
    entity_id: uuid.UUID,
    reason: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    """The privileged reveal. require_permission's dependency already 403s
    any role without PII_VIEW_RAW before this body ever runs (and 401s an
    unauthenticated/expired-session caller before that, via get_current_user
    inside require_permission — see auth/rbac.py). What's left to check here
    is scoped to THIS specific entity: it must belong to a message in a
    conversation this admin may actually see (authorize_conversation_access),
    and it must belong to the message_id in the URL, not just exist somewhere
    — closes the gap where a valid entity_id for someone else's conversation,
    guessed or enumerated, could be requested against an unrelated message_id
    this admin does have access to."""
    _load_message_or_404(db, message_id, current_user)
    occurrence = db.get(PiiOccurrenceModel, entity_id)
    if occurrence is None or occurrence.message_id != message_id:
        raise AppError(404, "pii_entity_not_found", "PII entity not found")

    audit_logger.log(
        AuditEventType.PII_VIEWED,
        outcome=AuditOutcome.SUCCESS,
        request_id=get_current_request_id(),
        actor_id=current_user.id,
        actor_role=current_user.role,
        resource_type="PII_OCCURRENCE",
        resource_id=str(occurrence.id),
        action="REVEAL",
        metadata={
            "message_id": str(message_id),
            "entity_id": str(occurrence.id),
            "pii_type": occurrence.entity_type,
            "policy_version": occurrence.policy_version,
            "reason": reason or "Viewed via Security & Activity trace panel",
        },
    )

    return {
        "entity_id": str(occurrence.id),
        "entity_type": occurrence.entity_type,
        "detector": occurrence.detector,
        "country": occurrence.country,
        "raw_value": occurrence.raw_value,
        "sanitized_value": occurrence.sanitized_value,
        "policy_version": occurrence.policy_version,
    }
