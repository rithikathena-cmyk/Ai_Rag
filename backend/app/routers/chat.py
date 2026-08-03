import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.user import UserModel
from app.services.agents.planner import run_agent
from app.services.generation.client import GenerationError
from app.services.memory.preferences import get_preferences
from app.services.memory.store import add_message, build_context, create_conversation, get_conversation, maybe_summarize

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class ChatSource(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    document_filename: str | None
    chunk_index: int
    text: str


class ChatReport(BaseModel):
    id: str
    title: str
    format: str
    row_count: int
    download_url: str


class ChatTraceStep(BaseModel):
    agent: str
    tool: str
    input: str | None
    summary: str


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    reply: str
    sources: list[ChatSource]
    report: ChatReport | None
    trace: list[ChatTraceStep]


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    if request.user_id is not None and db.get(UserModel, request.user_id) is None:
        raise AppError(404, "user_not_found", f"User {request.user_id} not found")

    if request.conversation_id is not None:
        conversation = get_conversation(db, request.conversation_id)
        if conversation is None:
            raise AppError(404, "conversation_not_found", f"Conversation {request.conversation_id} not found")
    else:
        conversation = create_conversation(db, user_id=request.user_id)

    summary, history = build_context(db, conversation.id)
    preferences = get_preferences(db, request.user_id) if request.user_id else None

    add_message(db, conversation.id, role="user", content=request.message)

    try:
        result = run_agent(
            request.message,
            history=history,
            conversation_summary=summary,
            preferences=preferences,
        )
    except GenerationError as exc:
        raise AppError(502, "generation_failed", f"Failed to generate a response: {exc}")

    add_message(db, conversation.id, role="assistant", content=result.reply, sources=result.sources, report=result.report)
    try:
        maybe_summarize(db, conversation.id)
    except GenerationError:
        pass  # summarization is best-effort; never fail an otherwise-good response over it

    return ChatResponse(
        conversation_id=conversation.id,
        reply=result.reply,
        sources=[ChatSource(**s) for s in result.sources],
        report=ChatReport(**result.report) if result.report else None,
        trace=[ChatTraceStep(**t) for t in result.trace],
    )
