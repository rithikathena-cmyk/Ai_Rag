import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.conversation import ConversationModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission
from app.services.memory.store import (
    BROAD_CONVERSATION_VISIBILITY_ROLES,
    authorize_conversation_access,
    get_conversation,
    list_messages,
)

router = APIRouter(dependencies=[Depends(require_permission(Permission.VIEW_CONVERSATIONS))])

# Every role gets VIEW_CONVERSATIONS (see llm_rbac.yaml) — the router-level
# dependency above just establishes "logged in with that base permission";
# the actual scoping (own conversations only, unless CEO/Admin) happens per
# route below, since VIEW_CONVERSATIONS alone doesn't imply "see everyone's."


class ConversationSummary(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    title: str | None
    message_count: int
    created_at: datetime
    updated_at: datetime | None


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sources: list[dict] | None
    report: dict | None
    created_at: datetime


class ConversationDetailResponse(ConversationSummary):
    summary: str | None
    messages: list[MessageResponse]


def _to_summary(row: ConversationModel) -> ConversationSummary:
    return ConversationSummary(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        message_count=row.message_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations(
    user_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    # Previously had no auth at all — an unfiltered request (no user_id)
    # listed every user's conversations. A non-privileged caller is now
    # forced to their own id regardless of what they pass; only CEO/Admin
    # may query an arbitrary user_id or leave it unfiltered.
    if current_user.role not in BROAD_CONVERSATION_VISIBILITY_ROLES:
        user_id = current_user.id
    query = db.query(ConversationModel)
    if user_id is not None:
        query = query.filter(ConversationModel.user_id == user_id)
    query = query.order_by(ConversationModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return ConversationListResponse(items=[_to_summary(r) for r in rows], total=total)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation_detail(
    conversation_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    # Previously unauthenticated — anyone could read any conversation's full
    # message history (including any generated report payloads) by ID.
    row = get_conversation(db, conversation_id)
    if row is None:
        raise AppError(404, "conversation_not_found", f"Conversation {conversation_id} not found")
    authorize_conversation_access(row, current_user)
    messages = list_messages(db, conversation_id)
    return ConversationDetailResponse(
        **_to_summary(row).model_dump(),
        summary=row.summary,
        messages=[
            MessageResponse(
                id=m.id, role=m.role, content=m.content, sources=m.sources, report=m.report, created_at=m.created_at
            )
            for m in messages
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    # Previously unauthenticated — anyone could delete any user's conversation.
    row = get_conversation(db, conversation_id)
    if row is None:
        raise AppError(404, "conversation_not_found", f"Conversation {conversation_id} not found")
    authorize_conversation_access(row, current_user)
    db.delete(row)
    db.commit()
