"""risk_analysis.classify_risk() — pure aggregation over already-computed
GuardrailStep outcomes, no detector of its own. See that module's docstring
for why it's keyed on step.name/.action rather than parsed score text."""

from app.services.guardrails.risk_analysis import classify_risk
from app.services.guardrails.types import GuardrailResult, GuardrailStep


def _result(*steps: GuardrailStep) -> GuardrailResult:
    return GuardrailResult(text="irrelevant", blocked=False, steps=list(steps))


def test_no_findings_is_low_normal_query():
    assessment = classify_risk(None)
    assert assessment.level == "LOW"
    assert assessment.risk_type == "normal_rag_query"


def test_all_pass_steps_is_low_normal_query():
    result = _result(
        GuardrailStep("length_check", "pass", "ok"),
        GuardrailStep("scope_check", "pass", "ok"),
    )
    assessment = classify_risk(result)
    assert assessment.level == "LOW"
    assert assessment.risk_type == "normal_rag_query"


def test_secret_detected_block_is_critical():
    result = _result(GuardrailStep("secret_detected_check", "block", "AWS key detected"))
    assessment = classify_risk(result)
    assert assessment.level == "CRITICAL"
    assert assessment.risk_type == "data_exfiltration"
    assert assessment.contributing_checks == ["secret_detected_check"]


def test_destructive_intent_block_is_critical():
    result = _result(GuardrailStep("destructive_intent_check", "block", "DROP TABLE"))
    assessment = classify_risk(result)
    assert assessment.level == "CRITICAL"
    assert assessment.risk_type == "destructive_action"


def test_prompt_injection_block_is_high():
    result = _result(GuardrailStep("prompt_injection_check", "block", "ignore instructions"))
    assessment = classify_risk(result)
    assert assessment.level == "HIGH"
    assert assessment.risk_type == "prompt_injection"


def test_deberta_injection_block_is_high():
    result = _result(GuardrailStep("deberta_injection_check", "block", "classified unsafe"))
    assessment = classify_risk(result)
    assert assessment.level == "HIGH"
    assert assessment.risk_type == "prompt_injection"


def test_deferred_scope_block_is_medium_unauthorized_access():
    result = _result(GuardrailStep("scope_semantic_check", "block", "outside the areas this assistant supports"))
    assessment = classify_risk(result)
    assert assessment.level == "MEDIUM"
    assert assessment.risk_type == "unauthorized_access_attempt"


def test_pii_redact_only_is_medium_sensitive_data():
    """pii_redact successfully masking PII (action == 'redact', never
    'block') is a real but softer signal than a PII check that actually
    blocks — matches pipeline.py: pii_redact's own action is never 'block'."""
    result = _result(GuardrailStep("pii_redact", "redact", "Redacted: SSN×1"))
    assessment = classify_risk(result)
    assert assessment.level == "MEDIUM"
    assert assessment.risk_type == "sensitive_data_request"


def test_presidio_block_is_high_sensitive_data():
    result = _result(GuardrailStep("presidio_check", "block", "high-confidence PII entities detected"))
    assessment = classify_risk(result)
    assert assessment.level == "HIGH"
    assert assessment.risk_type == "sensitive_data_request"


def test_highest_severity_wins_regardless_of_step_order():
    """Live-verified shape (this session's SSN example): scope_semantic_check
    blocks (MEDIUM) and pii_redact also fires (MEDIUM) — but if a CRITICAL
    check is ALSO present later in the steps list, it must still win, not
    the earlier-appearing MEDIUM ones."""
    result = _result(
        GuardrailStep("scope_semantic_check", "block", "outside the areas this assistant supports"),
        GuardrailStep("pii_redact", "redact", "Redacted: SSN×1"),
        GuardrailStep("secret_detected_check", "block", "AWS key detected"),
    )
    assessment = classify_risk(result)
    assert assessment.level == "CRITICAL"
    assert assessment.risk_type == "data_exfiltration"
    assert "secret_detected_check" in assessment.contributing_checks
