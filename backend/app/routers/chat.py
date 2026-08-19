import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.permissions import Permission
from app.core.request_context import get_current_request_id
from app.db.postgres import get_db
from app.models.pii_occurrence import PiiOccurrenceModel
from app.models.user import UserModel
from app.gateway.claude_gateway import GenerationError
from app.gateway.usage_tracker import record_denied
from app.services.agents import policies as agent_policies
from app.services.agents.planner import run_agent, run_retrieval_fallback
from app.services.agents.router import AgentName, Intent, RoutingDecision, route
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.dependencies import get_current_user
from app.services.employee_pii.service import create_pii_approval_request
from app.services.guardrails.citation_rail import confidence_score
from app.services.guardrails.escalation import check_escalation
from app.services.guardrails.orchestrator_graph import run_input_stage, run_output_stage
from app.services.guardrails.pii import redact_pii
from app.services.guardrails.pii_intent import detect_employee_pii_intent
from app.services.guardrails.types import GuardrailStep
from app.services.llm_rbac import policy_loader
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

# Checks whose block means the user's raw message itself contains something
# that must never be persisted verbatim (services/guardrails/pipeline.py's
# "pii_detected_input"/"secret_detected" decision reasons) — mapped to the
# specific placeholder for what that check actually found. pii_redact's own
# redacted text only covers the PII types pii.py's regex recognizes;
# presidio_check/gliner_check are detect-only and never produce a redacted
# variant at all (see their module docstrings); secret_detected_check
# (services/guardrails/secrets.py) matches credential shapes pii.py's
# recognizers don't cover at all, so redact_pii() would pass a raw AWS
# key/JWT/private-key block straight through untouched. Persisting the raw
# text for any of these would undo the same "block reason never echoes the
# matched value" guarantee docs/GUARDRAILS_ARCHITECTURE.md §11 already
# applies to the reply text, so each gets a fixed placeholder instead of any
# version of the real text.
_WITHHELD_PLACEHOLDERS = {
    "pii_redact": "[message withheld — contained personal information]",
    "presidio_check": "[message withheld — contained personal information]",
    "gliner_check": "[message withheld — contained personal information]",
    "secret_detected_check": "[message withheld — contained a credential or secret]",
}


def _stored_text_for_blocked_input(raw_message: str, blocked_by: str | None) -> str:
    """What to write to messages.content for a turn the input pipeline
    blocked. A PII- or secret-detecting check firing gets the fixed
    placeholder above (no partial-redaction guesswork for types pii.py's
    regex can't cover). Any OTHER check (scope/injection/destructive/
    toxicity/...) still gets the real text — useful audit trail, no secrecy
    concern — but run through redact_pii() regardless, because a message can
    legitimately contain PII even when a different, unrelated check is the
    one that actually fired first (e.g. scope_semantic_check blocking a
    PII-bearing message for being off-topic, before pii_redact/
    presidio_check/gliner_check ever run — verified live: "My SSN is ...,
    can you look up my file?" was blocked as out-of-scope, not as PII, under
    this pipeline's real check ordering). redact_pii() is a no-op on text
    with nothing it recognizes, so this never changes what gets stored for
    the ordinary case."""
    if blocked_by in _WITHHELD_PLACEHOLDERS:
        return _WITHHELD_PLACEHOLDERS[blocked_by]
    redacted, _step = redact_pii(raw_message)
    return redacted


def _persist_pii_occurrences(
    db: Session, *, message_id: uuid.UUID, conversation_id: uuid.UUID, direction: str, result,
) -> None:
    """Writes GuardrailResult.pii_occurrences (populated only when
    settings.guardrail_pii_raw_capture_enabled is on — see pipeline.py) to
    the isolated pii_occurrences table, tied to the message that carries the
    already-sanitized text. A no-op — zero queries — whenever the flag is
    off or nothing was captured, which is the default. Never touches
    messages.content/messages.trace; this is the ONLY write path into that
    table (see PiiOccurrenceModel's own docstring)."""
    if result is None or not result.pii_occurrences:
        return
    for occ in result.pii_occurrences:
        db.add(PiiOccurrenceModel(
            message_id=message_id, conversation_id=conversation_id, direction=direction,
            entity_type=occ.entity_type, detector=occ.detector, country=occ.country,
            raw_value=occ.raw_value, sanitized_value=occ.sanitized_value, policy_version=occ.policy_version,
        ))
    db.commit()


def _guardrail_trace(steps: list[GuardrailStep]) -> list["ChatTraceStep"]:
    return [ChatTraceStep(agent="Guardrails", tool=s.name, input=None, summary=f"{s.action}: {s.detail}") for s in steps]


def _access_trace_step(role: str, department: str | None) -> "ChatTraceStep":
    # Authentication already happened (get_current_user, JWT) before this
    # handler is ever entered; this records the LLM-RBAC authorization
    # decision that already ran above (authorize_llm_request()) as a real
    # trace step, rather than the frontend hardcoding a fake "Access
    # verified: pass" it can't actually back with data — see the Security &
    # Activity panel plan. Wording is deliberately account-level ("this role
    # may use the assistant at all"), not message-level — it runs before any
    # guardrail even looks at the message text, so it must never read as
    # "this specific question was approved" (that's scope_semantic_check's
    # job, a separate later step) — found live: a user reasonably read
    # "Role hr authorized (hr)" on an off-topic, scope-blocked question as
    # the panel endorsing that question's topic. display_name (e.g.
    # "Employee"), not the raw llm_rbac.yaml role key ("user") — the key
    # collides with the ordinary English word "user" and read as confusing
    # in this exact sentence.
    display_name = policy_loader.role_config(role).display_name
    return ChatTraceStep(
        agent="Access", tool="authorization", input=None,
        summary=f"pass: {display_name} role permitted to use this assistant ({department or 'no department'})",
    )


def _select_agent(
    message: str, *, decision, summary: str | None, request_id: str | None, current_user: UserModel,
) -> tuple[RoutingDecision, "ChatTraceStep"]:
    """Runs the supervisor/router (services/agents/router.py) and re-validates
    its choice against the caller's real RBAC-resolved knowledge_departments
    (services/agents/policies.py::agent_allowed_for_role) before returning.
    route() itself already only prompts with, and only accepts, a reachable
    agent — this second check is defense in depth against the same real RBAC
    data rather than trusting a single code path. A BUSINESS routing
    decision only, never a security one: see router.py's module docstring."""
    reachable_agents = [
        agent for agent in AgentName if agent_policies.agent_allowed_for_role(agent, decision.knowledge_departments)
    ]
    routing_decision = route(
        message,
        reachable_agents=reachable_agents,
        conversation_summary=summary,
        request_id=request_id,
        user_id=current_user.id,
        role=decision.role,
        department=decision.department,
    )
    if not agent_policies.agent_allowed_for_role(routing_decision.agent, decision.knowledge_departments):
        audit_logger.log(
            AuditEventType.AGENT_AUTHORIZATION_DENIED, outcome=AuditOutcome.DENIED, request_id=request_id,
            actor_id=current_user.id, actor_role=decision.role, resource_type="CHAT", action="SELECT_AGENT",
            metadata={"denied_agent": routing_decision.agent.value},
        )
        routing_decision = RoutingDecision(
            agent=AgentName.GENERAL_RAG, intent=Intent.GENERAL_CHAT, confidence=0.0,
            reason="post-hoc RBAC check denied the routed agent",
            required_capabilities=["rag"], is_fallback=True,
        )
    audit_logger.log(
        AuditEventType.AGENT_ROUTING_DECISION, outcome=AuditOutcome.SUCCESS, request_id=request_id,
        actor_id=current_user.id, actor_role=decision.role, resource_type="CHAT", action="SELECT_AGENT",
        metadata={
            "agent": routing_decision.agent.value, "intent": routing_decision.intent.value,
            "confidence": routing_decision.confidence, "is_fallback": routing_decision.is_fallback,
        },
    )
    # No pass:/redact:/block: prefix — an informational routing decision, not
    # a Guardrails verdict, parsed by lib/guardrails.ts's buildActivityTimeline
    # as a plain PASSED step whose full detail text is shown, not collapsed
    # to a generic "Passed" the way a "pass:"-prefixed summary would be.
    step = ChatTraceStep(
        agent="Supervisor", tool="select_agent", input=None,
        summary=(
            f"intent={routing_decision.intent.value} agent={routing_decision.agent.value} "
            f"confidence={routing_decision.confidence:.2f}"
        ),
    )
    return routing_decision, step


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
    # Document-level metadata (not chunk content) for the chat UI's source
    # panel — see services/agents/retrieval_agent.py::search_documents()
    # for why this is safe to surface: it's attached to a chunk that already
    # passed every RBAC/department filter before reaching this response.
    document_department: str | None = None
    document_type: str | None = None
    security_classification: str | None = None
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
    # Total wall-clock time for this /chat call — RBAC gate, guardrails,
    # retrieval, every Claude Gateway round-trip, everything — not just the
    # model's own latency (that's already broken out per-call in
    # gateway_usage_logs/GET /admin/gateway-usage). This is what a user
    # actually experienced waiting for a reply, shown in the chat UI.
    response_time_ms: float


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)):
    _start = time.perf_counter()

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
    access_step = _access_trace_step(decision.role, decision.department)

    # NEW: Guardrails Engine - Multi-rail protection
    from app.services.guardrails.engine import GuardrailsEngine, Surface
    guardrails_engine = GuardrailsEngine()

    # Evaluate input through guardrails
    input_eval = guardrails_engine.evaluate_input(
        request.message,
        current_user,
        Surface.USER_PROMPT
    )

    if input_eval.should_block:
        raise AppError(
            400,
            "guardrail_block_input",
            f"Your request was blocked by guardrails: {input_eval.block_reason}"
        )

    # Use redacted text if guardrails modified it
    message_to_process = input_eval.text_after_redaction or request.message

    # Guardrail-block escalation gate — a user who has accumulated enough
    # recent guardrail blocks (services/guardrails/escalation.py) is turned
    # away here, before conversation lookup/creation or any guardrail check
    # runs on this new message. Distinct from the RBAC gate above (that's
    # about what this role is permitted to do; this is about a pattern of
    # blocked messages regardless of role) and from rate_limiter.py (that's
    # request volume; this is blocked-message frequency).
    check_escalation(current_user.id)

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

    # Employee-PII approval gate — checked against the RAW message, before
    # the general input guardrail pipeline below. A match here takes over
    # the whole turn and returns immediately; run_agent() is never called on
    # this path, which is what makes "raw PII never reaches the LLM for this
    # capability" a structural guarantee rather than a prompt-level trust
    # assumption (see services/guardrails/pii_intent.py's module docstring).
    # A message that doesn't match falls through to run_input_guardrails()
    # completely unchanged, including today's existing hard PII block — see
    # docs/GUARDRAILS_ARCHITECTURE.md §14.
    pii_intent = detect_employee_pii_intent(request.message)
    if pii_intent is not None:
        granted = policy_loader.role_config(current_user.role).granted_permissions
        if Permission.MANAGE_EMPLOYEE_PII.value in granted or "*" in granted:
            approval = create_pii_approval_request(db, current_user, pii_intent, request.message)
            add_message(db, conversation.id, role="user", content=pii_intent.masked_text)
            reply = f"This request requires approval — request {approval.id} is pending review."
            trace = [access_step] + _guardrail_trace([
                GuardrailStep(
                    "employee_pii_intent", "block",
                    f"Detected {pii_intent.action} request for employee {pii_intent.employee_id}",
                ),
                GuardrailStep(
                    "employee_pii_mask", "redact",
                    f"Masked PII types: {', '.join(pii_intent.pii_types) or 'none'}",
                ),
                GuardrailStep(
                    "employee_pii_approval_requested", "block",
                    f"Approval request {approval.id} created, pending review",
                ),
            ])
            add_message(
                db, conversation.id, role="assistant", content=reply, trace=[t.model_dump() for t in trace],
            )
            return ChatResponse(
                conversation_id=conversation.id,
                reply=reply,
                sources=[],
                report=None,
                trace=trace,
                confidence="n/a",
                model_tier=decision.model_tier.value,
                degraded=False,
                degraded_reason=None,
                response_time_ms=(time.perf_counter() - _start) * 1000,
            )
        # Role isn't granted MANAGE_EMPLOYEE_PII — fall through to the
        # existing input guardrail pipeline unchanged, so an unauthorized
        # role sees today's ordinary PII handling, not a new error shape
        # that would reveal this capability exists.

    # Input-security orchestration graph (services/guardrails/
    # orchestrator_graph.py): input_security -> risk_analysis -> policy_check,
    # all three wrapping existing, unmodified functions — see that module's
    # docstring for why AUTHENTICATION/AUTHORIZATION aren't graph nodes here.
    input_stage = run_input_stage({
        "request_id": get_current_request_id(),
        "user_id": current_user.id,
        "role": decision.role,
        "department": decision.department,
        "user_message": request.message,
    })
    input_guardrails = input_stage.get("input_findings")

    if input_stage["policy_decision"].action == "BLOCK":
        # blocking_step_name, not steps[-1].name: with the scope_semantic_check
        # shadowing fix (pipeline.py), a deferred scope block can be the final
        # reason even though a later check (e.g. pii_redact) still ran and
        # appended its own "pass" step afterward — steps[-1] would then name
        # the wrong check and wrongly trigger the PII placeholder below for a
        # message that was never about PII at all.
        blocked_by = input_stage.get("blocking_step_name")
        stored_user_message = _stored_text_for_blocked_input(request.message, blocked_by)
        blocked_trace = [access_step] + _guardrail_trace(input_guardrails.steps if input_guardrails else [])
        blocked_user_msg = add_message(db, conversation.id, role="user", content=stored_user_message)
        _persist_pii_occurrences(
            db, message_id=blocked_user_msg.id, conversation_id=conversation.id,
            direction="input", result=input_guardrails,
        )
        add_message(
            db, conversation.id, role="assistant", content=input_stage["reply"],
            trace=[t.model_dump() for t in blocked_trace],
        )
        return ChatResponse(
            conversation_id=conversation.id,
            reply=input_stage["reply"],
            sources=[],
            report=None,
            trace=blocked_trace,
            confidence="n/a",
            model_tier=decision.model_tier.value,
            degraded=False,
            degraded_reason=None,
            response_time_ms=(time.perf_counter() - _start) * 1000,
        )

    message = input_stage["normalized_message"]

    user_msg = add_message(db, conversation.id, role="user", content=message)
    _persist_pii_occurrences(
        db, message_id=user_msg.id, conversation_id=conversation.id, direction="input", result=input_guardrails,
    )

    # Multi-agent supervisor/router — runs strictly after input guardrails
    # have already passed (the guardrail pipeline itself is unchanged). See
    # _select_agent()'s docstring and services/agents/router.py/policies.py.
    routing_decision, select_agent_step = _select_agent(
        message, decision=decision, summary=summary,
        request_id=get_current_request_id(), current_user=current_user,
    )

    if (
        not routing_decision.is_fallback
        and routing_decision.intent != Intent.GENERAL_CHAT
        and routing_decision.confidence < settings.agent_router_confidence_threshold
    ):
        # Low-confidence, non-fallback classification — ask the user to
        # clarify rather than guessing a specialist agent. A real fallback
        # decision (is_fallback=True) skips this path entirely and routes
        # straight to general_rag instead: it already means "we don't trust
        # this," so asking the user to clarify about the router's own
        # failure would be a confusing UX (see RoutingDecision.is_fallback's
        # docstring). Same early-return shape as the employee-PII-intent and
        # input-guardrail-block branches above — persist a real message,
        # return a ChatResponse, never call run_agent().
        reply = "I'm not sure I understood what you're looking for — could you rephrase or add a bit more detail?"
        trace = [access_step] + _guardrail_trace(input_guardrails.steps if input_guardrails else []) + [select_agent_step]
        add_message(db, conversation.id, role="assistant", content=reply, trace=[t.model_dump() for t in trace])
        return ChatResponse(
            conversation_id=conversation.id,
            reply=reply,
            sources=[],
            report=None,
            trace=trace,
            confidence="n/a",
            model_tier=decision.model_tier.value,
            degraded=False,
            degraded_reason=None,
            response_time_ms=(time.perf_counter() - _start) * 1000,
        )

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
            allowed_tools=agent_policies.resolve_agent_tools(routing_decision.agent, decision.allowed_tools),
            model_tier=decision.model_tier,
            action=request.action,
            report_row_filter=report_row_filter,
            agent_name=routing_decision.agent,
        )
    except GenerationError as exc:
        degraded = True
        result = run_retrieval_fallback(
            message, db, top_k=request.top_k, user_id=current_user.id,
            role=decision.role, knowledge_departments=decision.knowledge_departments,
            reason=exc.reason,
        )

    # Output-security orchestration graph (services/guardrails/
    # orchestrator_graph.py): output_security -> citation -> grounding ->
    # final_policy. Replicates, node-for-node, the same sequence and the same
    # "a blocked reply isn't a real grounded answer, so citation/groundedness
    # are skipped against it" behavior this function used to implement
    # inline — see that module's docstring for the groundedness fail-closed
    # override in particular.
    output_stage = run_output_stage({
        "request_id": get_current_request_id(),
        "user_id": current_user.id,
        "role": decision.role,
        "department": decision.department,
        "llm_response": result.reply,
        "retrieved_documents": result.sources,
    })
    output_guardrails = output_stage.get("output_findings")
    citation_step = output_stage["citation_findings"]
    groundedness_step = output_stage["grounding_findings"]
    reply = output_stage["reply"]
    blocked = output_stage["policy_decision"].action == "BLOCK"
    confidence: Literal["high", "medium", "low", "n/a"] = "n/a" if blocked else confidence_score(result.sources)

    # Trace order matches the actual flow: access authorization -> input
    # guardrails -> agent routing -> planner/retrieval/LLM -> output
    # guardrails -> citation -> groundedness.
    trace = (
        [access_step]
        + _guardrail_trace(input_guardrails.steps if input_guardrails else [])
        + [select_agent_step]
        + [ChatTraceStep(**t) for t in result.trace]
        + _guardrail_trace((output_guardrails.steps if output_guardrails else []) + [citation_step, groundedness_step])
    )

    assistant_msg = add_message(
        db, conversation.id, role="assistant", content=reply, sources=result.sources, report=result.report,
        trace=[t.model_dump() for t in trace],
    )
    _persist_pii_occurrences(
        db, message_id=assistant_msg.id, conversation_id=conversation.id, direction="output", result=output_guardrails,
    )
    try:
        maybe_summarize(db, conversation.id, user_id=current_user.id, role=decision.role, department=decision.department)
    except GenerationError:
        pass  # summarization is best-effort; never fail an otherwise-good response over it

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
        response_time_ms=(time.perf_counter() - _start) * 1000,
    )
