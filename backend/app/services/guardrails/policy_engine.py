"""Centralized, deterministic policy engine — the single place that turns
findings from the guardrail stages (input security, risk analysis, output
security, citation, grounding) into one of ALLOW/BLOCK/REDACT/REGENERATE/
ESCALATE. This module makes NO detection decisions of its own; it only
composes decisions the existing check modules already made
(GuardrailResult.blocked, a GuardrailStep's .action) into one explicit,
named outcome, replacing what was previously three separate implicit
decision points in routers/chat.py (the input-guardrail-block branch, the
output-guardrail-block branch, and the groundedness fail-closed override).

Deliberately takes explicit keyword arguments rather than the whole
GuardrailOrchestrationState — keeps this module free of any dependency on
orchestrator_state.py (which itself imports PolicyDecision from here;
importing the state type back would be circular) and makes each call site's
inputs explicit rather than implicit in a large shared dict.

Scope for this pass (see the approved plan's "Sequencing" section): risk_
findings is accepted and carried into the decision's reasoning/audit trail,
but is NOT an independent trigger for BLOCK beyond what input_findings/
output_findings/grounding_findings already enforce today — folding risk
level in as an independent gate is exactly the kind of behavior change the
"zero functional behavior change" bar for this pass rules out. Likewise
REDACT/REGENERATE/ESCALATE are real, typed outcomes callers can act on, but
no precedence rule in this pass actually returns them yet — REGENERATE (on a
genuine high-contradiction groundedness score, as opposed to today's only
groundedness-block path, a detector *failure*) and REDACT are wired in a
follow-up pass per the plan. ESCALATE is deliberately left to
escalation.py's existing pre-flight lockout gate (chat.py's check_escalation()
call, before this engine ever runs) rather than duplicated here — see
risk_analysis.py's docstring for the same reasoning.
"""

from dataclasses import dataclass
from typing import Literal

from app.services.guardrails.risk_analysis import RiskAssessment
from app.services.guardrails.types import GuardrailResult, GuardrailStep

PolicyAction = Literal["ALLOW", "BLOCK", "REDACT", "REGENERATE", "ESCALATE"]


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str
    # Name of the check that drove a BLOCK, when there is one — carried
    # through so callers can reproduce today's _stored_text_for_blocked_input
    # placeholder-selection logic (routers/chat.py) without this module
    # needing to know anything about message persistence.
    blocking_step_name: str | None = None


def decide(
    *,
    input_findings: GuardrailResult | None = None,
    risk_findings: RiskAssessment | None = None,
    output_findings: GuardrailResult | None = None,
    citation_findings: GuardrailStep | None = None,
    grounding_findings: GuardrailStep | None = None,
) -> PolicyDecision:
    """Called twice per turn from the two orchestration stages
    (orchestrator_graph.py): once after input security + risk analysis
    (only input_findings/risk_findings passed), once after output security +
    citation + grounding (only output_findings/citation_findings/
    grounding_findings passed). Precedence is deterministic and
    order-independent: a hard block from any deterministic check always
    wins, regardless of which finding object it came from — mirrors
    pipeline.py's own _blocked_result()/_DECISION_MAP, just centralized
    instead of duplicated at two call sites in chat.py."""
    if input_findings is not None and input_findings.blocked:
        return PolicyDecision(
            "BLOCK", input_findings.block_reason or "Blocked by input guardrails", input_findings.blocking_step_name,
        )

    if output_findings is not None and output_findings.blocked:
        return PolicyDecision(
            "BLOCK", output_findings.block_reason or "Blocked by output guardrails", output_findings.blocking_step_name,
        )

    # check_groundedness() currently only ever returns action == "block" via
    # its own fail_closed detector-failure path — see groundedness_check.py's
    # docstring and guardrails.yaml's groundedness_check.fail_closed setting
    # (false by default). A genuinely high contradiction score is a "pass"
    # action with a flagged detail string; wiring THAT case to REGENERATE is
    # explicitly deferred (see module docstring), so this branch reproduces
    # exactly the one case chat.py already enforces today, no more.
    if grounding_findings is not None and grounding_findings.action == "block":
        return PolicyDecision("BLOCK", "groundedness_check_unavailable", grounding_findings.name)

    reason = "No policy-relevant findings" if risk_findings is None else f"Risk level {risk_findings.level}: {risk_findings.risk_type}"
    return PolicyDecision("ALLOW", reason)
