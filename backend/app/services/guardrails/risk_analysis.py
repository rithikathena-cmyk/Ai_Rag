"""Aggregate risk classification — combines the ALREADY-COMPUTED per-check
outcomes from run_input_guardrails() (pipeline.py) into one overall risk
level and type. This module runs no detector of its own: every signal it
reads is a GuardrailStep.action ('pass'/'redact'/'block') that some other,
existing check already produced. Its only job is aggregation, matching the
spec's "Risk Analysis Agent" — a recommendation for the policy engine, never
an enforcement decision on its own (see policy_engine.py).

Deliberately keyed on GuardrailStep.name + .action (both stable, structured
fields) rather than parsing scores/thresholds out of a step's free-text
.detail string — that text is meant for the human-facing trace and audit
log, not machine parsing, and its wording is free to change independently of
this classification.

escalation.py's block-frequency state is NOT folded in here on purpose:
check_escalation() already runs as its own pre-flight gate in chat.py,
before run_input_guardrails() is even called — a request that reaches this
classifier is structurally guaranteed to be from a user who is not currently
locked out. Re-deriving a frequency signal here would be a second policy
source disagreeing with the first; escalation stays the single authority on
"too many recent blocks," this module stays the authority on "how severe is
THIS request.\""""

from dataclasses import dataclass, field
from typing import Literal

from app.services.guardrails.types import GuardrailResult, GuardrailStep

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Spec §4's risk-type catalog, mapped onto the closest existing detector.
# "tool_manipulation" and "rag_poisoning_attempt" have no corresponding
# check today (see docs/GUARDRAILS_ARCHITECTURE.md's own coverage notes) —
# left out of _CHECK_RISK_TYPE rather than guessed at, since inventing a new
# detector is out of scope for this pass (aggregation of EXISTING signals
# only). A request with no elevated check maps to "normal_rag_query".
_CHECK_RISK_TYPE: dict[str, str] = {
    "secret_detected_check": "data_exfiltration",
    "prompt_injection_check": "prompt_injection",
    "deberta_injection_check": "prompt_injection",
    "semantic_risk_check": "prompt_injection",
    "destructive_intent_check": "destructive_action",
    "scope_check": "unauthorized_access_attempt",
    "scope_semantic_check": "unauthorized_access_attempt",
    "toxicity_check": "sensitive_data_request",
    "presidio_check": "sensitive_data_request",
    "gliner_check": "sensitive_data_request",
    "pii_redact": "sensitive_data_request",
}

# Precedence, highest first — mirrors pipeline.py's own severity judgment
# (secrets/destructive intent are the checks with no "maybe" middle ground;
# scope/PII findings are real but softer signals). A CRITICAL or HIGH check
# firing (action == 'block') always outranks a MEDIUM one firing, regardless
# of check order in the pipeline.
_CRITICAL_CHECKS = frozenset({"secret_detected_check", "destructive_intent_check"})
_HIGH_CHECKS = frozenset({"prompt_injection_check", "deberta_injection_check", "semantic_risk_check"})
_MEDIUM_CHECKS = frozenset({"scope_check", "scope_semantic_check", "toxicity_check"})
# PII checks: a block is HIGH (input pipeline's presidio/gliner detecting
# high-confidence PII in a message that ALSO failed to redact it — see
# pipeline.py's decision map), a redact-only outcome is MEDIUM (pii_redact
# successfully handled it, same tier as an out-of-scope request).
_PII_CHECKS = frozenset({"presidio_check", "gliner_check", "pii_redact"})


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    risk_type: str
    # Names of the check(s) that drove this level — audit trail only, never
    # shown to the frontend (same rule as every other internal check name;
    # see docs/GUARDRAILS_ARCHITECTURE.md §11).
    contributing_checks: list[str] = field(default_factory=list)


def classify_risk(input_findings: GuardrailResult | None) -> RiskAssessment:
    """Pure function: GuardrailResult -> RiskAssessment. None (guardrails
    globally disabled, settings.guardrails_enabled=False) or a clean result
    with nothing but 'pass' steps both yield LOW/normal_rag_query."""
    if input_findings is None:
        return RiskAssessment("LOW", "normal_rag_query")

    blocked_or_redacted = [s for s in input_findings.steps if s.action != "pass"]
    if not blocked_or_redacted:
        return RiskAssessment("LOW", "normal_rag_query")

    level, contributing = _highest_level(blocked_or_redacted)
    risk_type = _risk_type_for(contributing[0]) if contributing else "normal_rag_query"
    return RiskAssessment(level, risk_type, contributing_checks=contributing)


def _highest_level(steps: list[GuardrailStep]) -> tuple[RiskLevel, list[str]]:
    by_level: dict[RiskLevel, list[str]] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    for step in steps:
        by_level[_level_for(step)].append(step.name)
    for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if by_level[level]:
            return level, by_level[level]
    return "LOW", []


def _level_for(step: GuardrailStep) -> RiskLevel:
    if step.name in _CRITICAL_CHECKS:
        return "CRITICAL"
    if step.name in _HIGH_CHECKS:
        return "HIGH"
    if step.name in _PII_CHECKS:
        return "HIGH" if step.action == "block" else "MEDIUM"
    if step.name in _MEDIUM_CHECKS:
        return "MEDIUM"
    return "LOW"


def _risk_type_for(check_name: str) -> str:
    return _CHECK_RISK_TYPE.get(check_name, "normal_rag_query")
