"""Typed shared state threaded through the guardrail orchestration graphs
(orchestrator_graph.py). This is an assembly point, not a new source of
truth: every field is either copied verbatim from an existing result type
(GuardrailResult/GuardrailStep from pipeline.py, AgentRunResult from
planner.py, RiskAssessment from risk_analysis.py, PolicyDecision from
policy_engine.py) or a small piece of request context chat.py already has
(user_id, role, department, message). No field here performs detection —
detection stays exactly where it already lives, in the existing check
modules.

Two request-scoped invocations, not one continuous graph, cross this state:
run_input_stage() (input_security -> risk_analysis -> policy_check) and
run_output_stage() (output_security -> citation -> grounding ->
final_policy) — see orchestrator_graph.py's module docstring for why the
main LLM call and conversation persistence sit between them in chat.py
rather than inside the graph itself. Fields below cover both stages; a given
stage only populates the subset relevant to it.

document_findings/safe_context are declared for spec-completeness
(services/guardrails/planner.py's _flag_suspicious_chunks() and the raw/
public source-view split already implement "document security"/"context
firewall" internally to run_agent() — see docs/GUARDRAILS_ARCHITECTURE.md)
but are not populated by this pass's graphs; surfacing them as first-class
orchestrator findings (rather than internal to planner.py) is deferred to a
follow-up pass per the approved plan's sequencing, so they stay None/empty
here rather than duplicating logic that already runs elsewhere."""

import uuid
from typing import Any, TypedDict

from app.services.guardrails.decisions import GuardrailDecision
from app.services.guardrails.policy_engine import PolicyDecision
from app.services.guardrails.request_classifier import RequestClassification
from app.services.guardrails.risk_analysis import RiskAssessment
from app.services.guardrails.types import GuardrailResult, GuardrailStep


class GuardrailOrchestrationState(TypedDict, total=False):
    # Request/actor context (populated by chat.py before either stage runs).
    request_id: str
    user_id: uuid.UUID
    role: str
    department: str | None

    # Input-security stage.
    user_message: str
    normalized_message: str  # post-pii_redact text run_agent() actually sees
    # Trace annotation only — see request_classifier.py's module docstring.
    # Never read by policy_check_node/policy_engine.decide(); a missing or
    # wrong classification (LLM unavailable, low confidence) changes nothing
    # about which checks run or what the final decision is.
    request_classification: RequestClassification | None
    input_findings: GuardrailResult | None
    risk_findings: RiskAssessment | None
    authorization_findings: dict[str, Any]  # carried through from the pre-flight authorize_llm_request() decision, never re-derived here
    input_policy: PolicyDecision | None

    # Main-LLM output, populated by chat.py between the two stages (see
    # module docstring) from AgentRunResult — not computed by either graph.
    retrieved_documents: list[dict]
    document_findings: list[GuardrailStep]  # not populated this pass; see module docstring
    safe_context: None  # not populated this pass; see module docstring
    model: str
    llm_response: str

    # Output-security stage.
    output_findings: GuardrailResult | None
    citation_findings: GuardrailStep | None
    grounding_findings: GuardrailStep | None
    output_policy: PolicyDecision | None

    # Final outcome (set by whichever stage/branch produces it).
    policy_decision: PolicyDecision | None
    block_reason: str | None
    blocking_step_name: str | None
    reply: str
    redaction_required: bool
    retry_count: int

    # response_generator.py input for a blocked outcome — never a bare
    # string built ad hoc outside that module (see decisions.py's docstring:
    # "Only ever constructed from pipeline.py's fixed reason tables").
    block_decision: GuardrailDecision | None
