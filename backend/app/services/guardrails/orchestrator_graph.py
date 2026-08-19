"""LangGraph orchestration for the guardrail pipeline — makes explicit, as
named nodes with a typed shared state, the same sequence routers/chat.py has
always run as plain sequential function calls. Every node's body calls an
EXISTING, unmodified function (run_input_guardrails, run_output_guardrails,
check_citations, check_groundedness, classify_risk, policy_engine.decide);
this module contributes orchestration and audit structure, not new
detection logic. This pass is a pure restructuring — see the approved plan's
"Sequencing" section: behavior must stay identical to today's chat.py.

Two separately-compiled graphs, not one continuous graph spanning the whole
request:

  run_input_stage()  : input_security -> risk_analysis -> policy_check
  run_output_stage() : output_security -> citation -> grounding -> final_policy

The main LLM call (run_agent()/run_retrieval_fallback() in planner.py) and
conversation persistence (add_message()) sit BETWEEN these two stages, in
chat.py, exactly where they already do today. They are not folded into a
single graph because chat.py must persist the (possibly redacted) user
message to the database between "did input security allow this" and "call
the LLM" — an existing, load-bearing ordering (see chat.py's own comment on
_stored_text_for_blocked_input) that this pass must not change. Modeling
that as a LangGraph interrupt/checkpoint would add real complexity and risk
for zero behavior change, so it stays two plain function calls from chat.py
instead — which is also a faithful reading of the spec's own diagram: it
draws two separate POLICY_CHECK/FINAL_POLICY gates with the main LLM call
between them, not one gate.

AUTHENTICATION and AUTHORIZATION are not nodes in run_input_stage() because
they already happen, deterministically, before this module is ever called:
get_current_user() (JWT) and authorize_llm_request() (services/llm_rbac/
engine.py) both run earlier in chat.py's chat() handler. Duplicating them as
graph nodes here would mean either re-deriving an authorization decision
(risking disagreement with the real one) or trivially rubber-stamping an
already-made decision — neither is useful; the existing deterministic gate
stays the single authority, exactly as the spec's own constraints require
("the deterministic backend must verify user+role+department+resource+
classification+action").

SECURE_RETRIEVAL / DOCUMENT_SECURITY / CONTEXT_FIREWALL are similarly not
separate nodes: today, all three already happen INSIDE run_agent() (RBAC-
filtered retrieval in retrieval_agent.py, _flag_suspicious_chunks() for
document security, the raw/public source-view split for the context
firewall — see docs/GUARDRAILS_ARCHITECTURE.md). Splitting them into
independently-callable outer functions would mean restructuring planner.py's
internals, which the approved plan defers to a follow-up pass. chat.py calls
run_agent() as an ordinary function between the two graphs, unchanged.

request_understanding is the one LLM-backed node in either graph (see
request_classifier.py's own module docstring for the full posture): it runs
LAST in run_input_stage(), strictly after policy_check, rather than before
or alongside it — nothing in this graph waits on it or reads its result to
decide anything, so the real, mandatory checks and the policy decision they
drive never depend on it being fast, available, or even present. A model
failure there (no API key, timeout, refusal) leaves request_classification
as None and changes nothing else about this graph's behavior.

An earlier revision ran this node in parallel with input_security (both off
the same START edge, fanning into policy_check) to keep its latency off the
critical path. That was reverted: LangGraph dispatches concurrent branches
onto worker threads, and running policy_check's audit_logger.log() call off
the main thread surfaced a genuine SQLAlchemy session/threading issue — a
raw IntegrityError escaping that function's own broad except-Exception
catch-all (see test_input_stage_blocks_and_records_the_block, which
reproduced deterministically, even in complete isolation, under the parallel
design). A trace-only annotation nothing downstream reads is not worth that
risk in a security-critical graph, so this stays a single sequential chain
like every other node here, accepting the small added latency instead.
"""

from app.core.config import settings
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditOutcome, event_type_for_reason, reason_code_for_check
from app.services.guardrails.citation_rail import NAME as CITATION_CHECK_NAME, check_citations
from app.services.guardrails.decisions import GuardrailDecision
from app.services.guardrails.escalation import record_block
from app.services.guardrails.groundedness_check import NAME as GROUNDEDNESS_CHECK_NAME, check_groundedness
from app.services.guardrails.orchestrator_state import GuardrailOrchestrationState
from app.services.guardrails.pipeline import run_input_guardrails, run_output_guardrails
from app.services.guardrails.policy_engine import decide as policy_decide
from app.services.guardrails.request_classifier import classify_request
from app.services.guardrails.response_generator import generate_user_response
from app.services.guardrails.risk_analysis import classify_risk
from app.services.guardrails.types import GuardrailStep
from langgraph.graph import END, START, StateGraph


def _audit_policy_denied(state: GuardrailOrchestrationState, blocking_step_name: str | None) -> None:
    """Shared by both policy-check nodes below — a BLOCK decision from
    either stage is audited the same way, categorized by which check
    actually drove it (event_types.py's reason/event-type maps), never by
    logging the check's own detail string (score/matched pattern). Best-
    effort: audit_logger.log() itself never raises (see that module's
    docstring), so a logging failure here can't affect the block decision
    it's describing."""
    reason_code = reason_code_for_check(blocking_step_name)
    audit_logger.log(
        event_type_for_reason(reason_code),
        outcome=AuditOutcome.BLOCKED,
        request_id=state.get("request_id", "-"),
        actor_id=state.get("user_id"),
        actor_role=state.get("role"),
        resource_type="CHAT",
        action="GUARDRAIL_CHECK",
        reason_code=reason_code.value,
        metadata={"check_name": blocking_step_name} if blocking_step_name else None,
    )


def _request_understanding_node(state: GuardrailOrchestrationState) -> dict:
    """Trace annotation only — see request_classifier.py's and this module's
    own docstrings for why nothing downstream reads this to decide anything.
    Runs even when settings.guardrails_enabled is False: it never enforces,
    so the same "guardrails off" switch that disables the real checks below
    has no reason to also disable a label used only for display."""
    return {"request_classification": classify_request(state["user_message"])}


def _input_security_node(state: GuardrailOrchestrationState) -> dict:
    if not settings.guardrails_enabled:
        return {"input_findings": None, "normalized_message": state["user_message"]}
    result = run_input_guardrails(state["user_message"], state.get("role"))
    return {"input_findings": result, "normalized_message": result.text}


def _risk_analysis_node(state: GuardrailOrchestrationState) -> dict:
    return {"risk_findings": classify_risk(state.get("input_findings"))}


def _policy_check_node(state: GuardrailOrchestrationState) -> dict:
    decision = policy_decide(input_findings=state.get("input_findings"), risk_findings=state.get("risk_findings"))
    updates: dict = {"input_policy": decision, "policy_decision": decision}
    if decision.action == "BLOCK":
        record_block(state["user_id"])
        _audit_policy_denied(state, decision.blocking_step_name)
        updates["block_reason"] = decision.reason
        updates["blocking_step_name"] = decision.blocking_step_name
        updates["reply"] = decision.reason
    return updates


def _output_security_node(state: GuardrailOrchestrationState) -> dict:
    if not settings.guardrails_enabled:
        return {"output_findings": None, "reply": state["llm_response"]}
    result = run_output_guardrails(state["llm_response"], state.get("role"))
    updates: dict = {"output_findings": result, "reply": result.text}
    if result.blocked:
        record_block(state["user_id"])
        updates["reply"] = result.block_reason
    return updates


def _citation_node(state: GuardrailOrchestrationState) -> dict:
    output_findings = state.get("output_findings")
    if output_findings and output_findings.blocked:
        step = GuardrailStep(CITATION_CHECK_NAME, "pass", "Output blocked upstream; citation check skipped")
    else:
        step = check_citations(state["reply"], state.get("retrieved_documents", []))
    return {"citation_findings": step}


def _grounding_node(state: GuardrailOrchestrationState) -> dict:
    output_findings = state.get("output_findings")
    if output_findings and output_findings.blocked:
        step = GuardrailStep(GROUNDEDNESS_CHECK_NAME, "pass", "Output blocked upstream; groundedness check skipped")
    else:
        step = check_groundedness(state["reply"], state.get("retrieved_documents", []))
    return {"grounding_findings": step}


def _final_policy_node(state: GuardrailOrchestrationState) -> dict:
    decision = policy_decide(
        output_findings=state.get("output_findings"),
        citation_findings=state.get("citation_findings"),
        grounding_findings=state.get("grounding_findings"),
    )
    updates: dict = {"output_policy": decision, "policy_decision": decision}
    if decision.action == "BLOCK":
        output_findings = state.get("output_findings")
        if not (output_findings and output_findings.blocked):
            # The one case where `reply` must be REPLACED rather than kept —
            # a groundedness-detector failure, not an output-guardrail block
            # (which already set the right reply text in _output_security_
            # node). Matches chat.py's own comment: "This is the one case
            # that should actually withhold the reply."
            record_block(state["user_id"])
            updates["reply"] = generate_user_response(GuardrailDecision("BLOCKED", "groundedness_check_unavailable"))
            updates["citation_findings"] = GuardrailStep(
                CITATION_CHECK_NAME, "pass", "Output blocked upstream; citation check skipped",
            )
        _audit_policy_denied(state, decision.blocking_step_name)
        updates["block_reason"] = decision.reason
        updates["blocking_step_name"] = decision.blocking_step_name
    return updates


def _build_input_security_graph():
    workflow = StateGraph(GuardrailOrchestrationState)
    workflow.add_node("input_security", _input_security_node)
    workflow.add_node("risk_analysis", _risk_analysis_node)
    workflow.add_node("policy_check", _policy_check_node)
    workflow.add_node("request_understanding", _request_understanding_node)
    # request_understanding runs strictly AFTER policy_check, not in
    # parallel with input_security — an earlier parallel-fan-out design was
    # tried and reverted: LangGraph schedules concurrent branches on worker
    # threads, and running policy_check's own audit_logger.log() call off
    # the main thread surfaced a real SQLAlchemy session/threading issue (a
    # raw IntegrityError escaping that function's own catch-all — see
    # test_input_stage_blocks_and_records_the_block, which failed under the
    # parallel design even in complete isolation). This node's entire
    # purpose is a trace annotation nothing else reads (see its own
    # docstring) — that is not worth adding real concurrency risk to a
    # security-critical graph for. Sequential is slightly slower per request
    # than genuine parallelism would have been; it is also the same
    # single-threaded execution model every other node in this graph
    # already uses safely.
    workflow.add_edge(START, "input_security")
    workflow.add_edge("input_security", "risk_analysis")
    workflow.add_edge("risk_analysis", "policy_check")
    workflow.add_edge("policy_check", "request_understanding")
    workflow.add_edge("request_understanding", END)
    return workflow.compile()


def _build_output_security_graph():
    workflow = StateGraph(GuardrailOrchestrationState)
    workflow.add_node("output_security", _output_security_node)
    workflow.add_node("citation", _citation_node)
    workflow.add_node("grounding", _grounding_node)
    workflow.add_node("final_policy", _final_policy_node)
    workflow.add_edge(START, "output_security")
    workflow.add_edge("output_security", "citation")
    workflow.add_edge("citation", "grounding")
    workflow.add_edge("grounding", "final_policy")
    workflow.add_edge("final_policy", END)
    return workflow.compile()


# Compiled once at import time — same "cache loaded models, avoid
# repeatedly initializing" convention this codebase already applies to its
# ML-model-backed checks (presidio_check.py, gliner_check.py,
# deberta_injection_check.py, groundedness_check.py all lazily build a
# module-level singleton). A LangGraph StateGraph compile is cheap, but
# there's still no reason to repeat it per request.
_INPUT_GRAPH = _build_input_security_graph()
_OUTPUT_GRAPH = _build_output_security_graph()


def run_input_stage(state: GuardrailOrchestrationState) -> GuardrailOrchestrationState:
    return _INPUT_GRAPH.invoke(state)


def run_output_stage(state: GuardrailOrchestrationState) -> GuardrailOrchestrationState:
    return _OUTPUT_GRAPH.invoke(state)
