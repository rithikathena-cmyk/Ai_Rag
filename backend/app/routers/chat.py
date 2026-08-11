import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.user import UserModel
from app.gateway.claude_gateway import GenerationError
from app.gateway.usage_tracker import record_denied
from app.services.agents.planner import run_agent, run_retrieval_fallback
from app.services.auth.dependencies import get_current_user
from app.services.guardrails.citation_rail import NAME as CITATION_CHECK_NAME, check_citations, confidence_score
from app.services.guardrails.pipeline import run_input_guardrails, run_output_guardrails
from app.services.guardrails.types import GuardrailStep
from app.services.llm_rbac.engine import authorize_llm_request
from app.services.llm_rbac.report_policy import authorize_report
from app.services.memory.preferences import get_preferences
from app.services.memory.store import (
    add_message,
    authorize_conversation_access,
    build_context,
    create_conversation,
    get_conversation,
    maybe_summarize,
)

router = APIRouter()


def _guardrail_trace(steps: list[GuardrailStep]) -> list["ChatTraceStep"]:
    return [ChatTraceStep(agent="Guardrails", tool=s.name, input=None, summary=f"{s.action}: {s.detail}") for s in steps]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    top_k: int | None = Field(default=None, gt=0, le=50)
    # Optional capability name matching llm_rbac.yaml's permission catalog
    # (e.g. "workforce_planning", "engineering_planning") — lets a client
    # (e.g. a role-specific "quick action" button) opt into the fine-grained
    # allow/deny check and Opus-tier escalation for that specific request.
    # Omitting it still gets full role/department/tool/quota governance; see
    # services/llm_rbac/engine.py::authorize_llm_request()'s docstring.
    action: str | None = None
    # Optional report-type name (services/llm_rbac/report_policy.py's
    # catalog — e.g. "project_status", "manual_summary", "attendance") — a
    # distinct, narrower check from `action` above: "may this role generate
    # *this* report type, and if so what's it scoped to." Runs alongside
    # authorize_llm_request(), not instead of it.
    report_type: str | None = None
    # Optional client-chosen model-tier override (e.g. "sonnet"/"opus") — the
    # chat UI's "try a different model" retry button sends this after a
    # degraded (non-LLM fallback) response. Still fully bounded by the
    # caller's role via llm_rbac.yaml's tiers_allowed (see
    # services/llm_rbac/engine.py::_resolve_tier) — a client can never pick a
    # tier their role doesn't permit. Omitting it keeps the normal
    # role/action-based auto-resolution.
    model_tier: str | None = None


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
    confidence: Literal["high", "medium", "low", "n/a"]
    model_tier: str
    # True when the configured Claude model was unavailable and the reply is
    # services/agents/planner.py::run_retrieval_fallback()'s raw-search
    # degraded response rather than a real LLM-synthesized answer — the
    # signal the chat UI uses to offer a "try a different model" retry.
    degraded: bool
    # Why `degraded` is True — one of gateway/schemas.py's
    # GenerationErrorReason values (e.g. "no_api_key", "model_disabled",
    # "auth_failed", "provider_unavailable", "provider_error", "capacity",
    # "internal"), or None when not degraded. Lets the chat UI show an
    # accurate, specific message instead of a single generic
    # "no AI model configured" sentence regardless of the real cause — see
    # docstring on services/agents/planner.py::run_retrieval_fallback().
    # Deliberately just this enum value, never the underlying exception text
    # (which stays server-side, in the logger calls made where the
    # GenerationError was actually raised/classified).
    degraded_reason: str | None


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    # LLM RBAC gate — the single entrypoint every user-role-driven Claude
    # request goes through before anything else runs (permission, rate
    # limit, quota). A denial here is itself an auditable event.
    try:
        decision = authorize_llm_request(
            db, current_user, endpoint="chat", action=request.action, requested_tier=request.model_tier,
        )
    except AppError as exc:
        record_denied(
            agent_name="planner_agent", user_id=current_user.id, role=current_user.role,
            department=current_user.department, denial_reason=str(exc.detail),
            requested_capability=request.action,
        )
        raise

    report_row_filter = None
    if request.report_type:
        # Report-type authorization — layered on top of the LLM RBAC gate
        # above, not a replacement for it. See
        # services/llm_rbac/report_policy.py's module docstring.
        report_decision = authorize_report(current_user, request.report_type)
        if report_decision.status == "denied":
            record_denied(
                agent_name="planner_agent", user_id=current_user.id, role=current_user.role,
                department=current_user.department,
                denial_reason=report_decision.reason or "Report type not allowed",
                requested_capability=request.report_type,
            )
            raise AppError(403, "report_type_denied", report_decision.reason or "Report type not allowed for this role")
        if not report_decision.data_available:
            # Allowed by RBAC, but there's no real data source behind this
            # report type in this schema (no machine/attendance/production
            # table) — an honest failure, not a fabricated report. See
            # report_policy.py's NO_DATA_REPORT_TYPES.
            record_denied(
                agent_name="planner_agent", user_id=current_user.id, role=current_user.role,
                department=current_user.department,
                denial_reason=f"No data source configured for report type '{request.report_type}'",
                requested_capability=request.report_type,
            )
            raise AppError(
                501, "no_data_source",
                f"No data source is configured for report type '{request.report_type}' yet",
            )
        report_row_filter = report_decision.row_filter

    if request.conversation_id is not None:
        conversation = get_conversation(db, request.conversation_id)
        if conversation is None:
            raise AppError(404, "conversation_not_found", f"Conversation {request.conversation_id} not found")
        # Without this, a stale conversation_id belonging to a different user
        # (e.g. left behind in frontend session state across a sign-out/sign-in
        # on the same browser tab) would silently continue that other user's
        # conversation — this caller's message appended to it, and its prior
        # history fed back as context for this reply. Same rule routers/
        # conversations.py's read/delete endpoints already enforce.
        authorize_conversation_access(conversation, current_user)
    else:
        conversation = create_conversation(db, user_id=current_user.id)

    summary, history = build_context(db, conversation.id)
    preferences = get_preferences(db, current_user.id)

    input_guardrails = run_input_guardrails(request.message) if settings.guardrails_enabled else None

    if input_guardrails and input_guardrails.blocked:
        add_message(db, conversation.id, role="user", content=request.message)
        add_message(db, conversation.id, role="assistant", content=input_guardrails.block_reason)
        return ChatResponse(
            conversation_id=conversation.id,
            reply=input_guardrails.block_reason,
            sources=[],
            report=None,
            trace=_guardrail_trace(input_guardrails.steps),
            confidence="n/a",
            model_tier=decision.model_tier.value,
            degraded=False,
            degraded_reason=None,
        )

    message = input_guardrails.text if input_guardrails else request.message
    add_message(db, conversation.id, role="user", content=message)

    degraded = False
    try:
        result = run_agent(
            message,
            history=history,
            conversation_summary=summary,
            preferences=preferences,
            top_k=request.top_k,
            user_id=current_user.id,
            role=decision.role,
            department=decision.department,
            knowledge_departments=decision.knowledge_departments,
            sql_allowed_tables=decision.sql_allowed_tables,
            allowed_tools=decision.allowed_tools,
            model_tier=decision.model_tier,
            action=request.action,
            report_row_filter=report_row_filter,
        )
    except GenerationError as exc:
        degraded = True
        result = run_retrieval_fallback(
            message, db, top_k=request.top_k, user_id=current_user.id,
            role=decision.role, knowledge_departments=decision.knowledge_departments,
            reason=exc.reason,
        )

    output_guardrails = run_output_guardrails(result.reply) if settings.guardrails_enabled else None
    reply = output_guardrails.text if output_guardrails else result.reply
    blocked = bool(output_guardrails and output_guardrails.blocked)
    if blocked:
        reply = output_guardrails.block_reason

    # A blocked reply isn't a real grounded answer, so there's nothing
    # meaningful to check citations against or score confidence for.
    citation_step = (
        GuardrailStep(CITATION_CHECK_NAME, "pass", "Output blocked upstream; citation check skipped")
        if blocked
        else check_citations(reply, result.sources)
    )
    confidence: Literal["high", "medium", "low", "n/a"] = "n/a" if blocked else confidence_score(result.sources)

    add_message(db, conversation.id, role="assistant", content=reply, sources=result.sources, report=result.report)
    try:
        maybe_summarize(db, conversation.id, user_id=current_user.id, role=decision.role, department=decision.department)
    except GenerationError:
        pass  # summarization is best-effort; never fail an otherwise-good response over it

    trace = (
        _guardrail_trace(input_guardrails.steps if input_guardrails else [])
        + [ChatTraceStep(**t) for t in result.trace]
        + _guardrail_trace((output_guardrails.steps if output_guardrails else []) + [citation_step])
    )

    return ChatResponse(
        conversation_id=conversation.id,
        reply=reply,
        sources=[ChatSource(**s) for s in result.sources],
        report=ChatReport(**result.report) if result.report else None,
        trace=trace,
        confidence=confidence,
        model_tier=decision.model_tier.value,
        degraded=degraded,
        degraded_reason=result.degraded_reason,
    )
