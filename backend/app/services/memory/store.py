import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.roles import Role
from app.gateway.claude_gateway import GenerationError, claude_gateway
from app.gateway.prompt_manager import load_prompt
from app.gateway.schemas import GenerateRequest, ModelTier
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.user import UserModel

# Only a conversation's own owner, or a CEO/Admin, may read/continue/delete it
# (routers/conversations.py's list/detail/delete endpoints, and routers/
# chat.py's conversation_id-continuation path, all enforce this same rule via
# authorize_conversation_access() below).
BROAD_CONVERSATION_VISIBILITY_ROLES = {Role.CEO.value, Role.ADMIN.value}


def create_conversation(db: Session, *, user_id: uuid.UUID | None = None) -> ConversationModel:
    convo = ConversationModel(user_id=user_id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def get_conversation(db: Session, conversation_id: uuid.UUID) -> ConversationModel | None:
    return db.get(ConversationModel, conversation_id)


def authorize_conversation_access(conversation: ConversationModel, user: UserModel) -> None:
    """Raises 404 (not 403 — avoids confirming a conversation id exists to a
    caller who can't see it) unless `user` owns `conversation` or holds a
    broad-visibility role. Centralized here — not left as a routers/
    conversations.py-only check — specifically so routers/chat.py's
    conversation_id continuation path can't be used to hijack (read from,
    or append messages/context into) another user's conversation just by
    supplying its id in a /chat request body."""
    if user.role in BROAD_CONVERSATION_VISIBILITY_ROLES:
        return
    if conversation.user_id != user.id:
        raise AppError(404, "conversation_not_found", "Conversation not found")


def list_messages(db: Session, conversation_id: uuid.UUID) -> list[MessageModel]:
    return (
        db.query(MessageModel)
        .filter(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.created_at)
        .all()
    )


def add_message(
    db: Session,
    conversation_id: uuid.UUID,
    *,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    report: dict | None = None,
) -> MessageModel:
    msg = MessageModel(conversation_id=conversation_id, role=role, content=content, sources=sources, report=report)
    db.add(msg)

    convo = db.get(ConversationModel, conversation_id)
    convo.message_count = (convo.message_count or 0) + 1
    if role == "user" and convo.title is None:
        convo.title = content[:80]

    db.commit()
    db.refresh(msg)
    return msg


def build_context(db: Session, conversation_id: uuid.UUID) -> tuple[str | None, list[dict]]:
    """Returns (running_summary_or_none, recent_messages_as_claude_history).

    Only the most recent `conversation_recent_turns_kept` messages are
    replayed verbatim; anything older is represented only via `summary`
    (see maybe_summarize). The full history still lives permanently in
    Postgres regardless — this only bounds what gets sent back to Claude.
    """
    convo = db.get(ConversationModel, conversation_id)
    if convo is None:
        return None, []
    recent = list_messages(db, conversation_id)[-settings.conversation_recent_turns_kept :]
    history = [{"role": m.role, "content": m.content} for m in recent]
    return convo.summary, history


def maybe_summarize(
    db: Session, conversation_id: uuid.UUID, *,
    user_id: uuid.UUID | None = None, role: str | None = None, department: str | None = None,
) -> None:
    convo = db.get(ConversationModel, conversation_id)
    if convo is None or (convo.message_count or 0) < settings.conversation_summary_trigger_turns:
        return

    messages = list_messages(db, conversation_id)
    fold_upto = max(0, len(messages) - settings.conversation_recent_turns_kept)
    to_fold = messages[convo.summarized_count : fold_upto]
    if not to_fold:
        return

    transcript = "\n".join(f"{m.role}: {m.content}" for m in to_fold)
    prompt_template = load_prompt("memory_summarizer", "v1")
    prompt = prompt_template.text.format(existing_summary=convo.summary or "(none yet)", new_turns=transcript)

    result = claude_gateway.generate(
        GenerateRequest(
            agent_name="conversation_summary",
            system="",
            messages=[{"role": "user", "content": prompt}],
            tier=ModelTier.FAST,
            max_tokens=settings.memory_summary_max_tokens,
            effort=settings.memory_summary_effort,
            user_id=user_id,
            role=role,
            department=department,
        )
    )  # GenerationError propagates to the caller, same as before this migration

    if result.stop_reason == "refusal":
        return

    if result.text:
        convo.summary = result.text
        convo.summarized_count = fold_upto
        db.commit()
