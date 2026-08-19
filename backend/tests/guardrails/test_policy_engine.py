"""policy_engine.decide() — deterministic precedence over already-computed
findings. This module makes no detection decisions; these tests exercise
precedence order, not any individual check's own behavior (those stay
covered by their own test files)."""

from app.services.guardrails.policy_engine import decide
from app.services.guardrails.risk_analysis import RiskAssessment
from app.services.guardrails.types import GuardrailResult, GuardrailStep


def test_no_findings_allows():
    decision = decide()
    assert decision.action == "ALLOW"


def test_clean_input_allows():
    input_findings = GuardrailResult(text="hi", blocked=False, steps=[GuardrailStep("length_check", "pass", "ok")])
    risk = RiskAssessment("LOW", "normal_rag_query")
    decision = decide(input_findings=input_findings, risk_findings=risk)
    assert decision.action == "ALLOW"


def test_blocked_input_findings_blocks_with_reason_and_blocking_step():
    input_findings = GuardrailResult(
        text="hi", blocked=True, block_reason="I'm not able to help with that request.",
        steps=[GuardrailStep("secret_detected_check", "block", "AWS key detected")],
        blocking_step_name="secret_detected_check",
    )
    decision = decide(input_findings=input_findings)
    assert decision.action == "BLOCK"
    assert decision.reason == "I'm not able to help with that request."
    assert decision.blocking_step_name == "secret_detected_check"


def test_blocked_output_findings_blocks():
    output_findings = GuardrailResult(
        text="reply", blocked=True, block_reason="Blocked on output",
        steps=[GuardrailStep("presidio_check", "block", "PII in reply")],
        blocking_step_name="presidio_check",
    )
    decision = decide(output_findings=output_findings)
    assert decision.action == "BLOCK"
    assert decision.reason == "Blocked on output"
    assert decision.blocking_step_name == "presidio_check"


def test_groundedness_block_action_blocks_when_output_not_already_blocked():
    output_findings = GuardrailResult(text="reply", blocked=False, steps=[])
    grounding_findings = GuardrailStep("groundedness_check", "block", "check unavailable, failed closed: RuntimeError")
    decision = decide(output_findings=output_findings, grounding_findings=grounding_findings)
    assert decision.action == "BLOCK"
    assert decision.blocking_step_name == "groundedness_check"


def test_output_block_takes_precedence_over_groundedness_block():
    """Both fire — the output-guardrail block's own reason wins, matching
    chat.py's original `if not blocked and groundedness_step.action ==
    "block"` guard (the override only ever applied when nothing else had
    already blocked)."""
    output_findings = GuardrailResult(
        text="reply", blocked=True, block_reason="Blocked on output",
        steps=[GuardrailStep("presidio_check", "block", "PII in reply")],
        blocking_step_name="presidio_check",
    )
    grounding_findings = GuardrailStep("groundedness_check", "block", "check unavailable, failed closed: RuntimeError")
    decision = decide(output_findings=output_findings, grounding_findings=grounding_findings)
    assert decision.action == "BLOCK"
    assert decision.reason == "Blocked on output"
    assert decision.blocking_step_name == "presidio_check"


def test_groundedness_flagged_but_not_blocked_action_allows():
    """The normal contradiction-detected case: action is still 'pass' (see
    groundedness_check.py's docstring — flag-only by design in this pass),
    so it must not block."""
    output_findings = GuardrailResult(text="reply", blocked=False, steps=[])
    grounding_findings = GuardrailStep("groundedness_check", "pass", "Reply may contradict its sources (score=0.91)")
    decision = decide(output_findings=output_findings, grounding_findings=grounding_findings)
    assert decision.action == "ALLOW"
