import uuid

import anthropic
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.services.generation.client import GenerationError, get_client
from app.services.monitoring.metrics import record_token_usage


def create_conversation(db: Session, *, user_id: uuid.UUID | None = None) -> ConversationModel:
    convo = ConversationModel(user_id=user_id)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


def get_conversation(db: Session, conversation_id: uuid.UUID) -> ConversationModel | None:
    return db.get(ConversationModel, conversation_id)


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


def maybe_summarize(db: Session, conversation_id: uuid.UUID) -> None:
    convo = db.get(ConversationModel, conversation_id)
    if convo is None or (convo.message_count or 0) < settings.conversation_summary_trigger_turns:
        return

    messages = list_messages(db, conversation_id)
    fold_upto = max(0, len(messages) - settings.conversation_recent_turns_kept)
    to_fold = messages[convo.summarized_count : fold_upto]
    if not to_fold:
        return

    transcript = "\n".join(f"{m.role}: {m.content}" for m in to_fold)
    prompt = (
        "Update the running summary of this conversation to include the new turns below. "
        "Keep it under 150 words. Preserve names, facts, decisions, and stated preferences. "
        "Write only the summary itself, with no preamble or meta-commentary.\n\n"
        f"Existing summary: {convo.summary or '(none yet)'}\n\n"
        f"New turns:\n{transcript}"
    )
    try:
        response = get_client().messages.create(
            model=settings.claude_model_name,
            max_tokens=settings.memory_summary_max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.memory_summary_effort},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise GenerationError(str(exc)) from exc

    if response.usage:
        record_token_usage(
            "conversation_summary", settings.claude_model_name,
            response.usage.input_tokens, response.usage.output_tokens,
        )

    if response.stop_reason == "refusal":
        return

    summary_text = "".join(b.text for b in response.content if b.type == "text")
    if summary_text:
        convo.summary = summary_text
        convo.summarized_count = fold_upto
        db.commit()
