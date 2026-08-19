"""Shared severity classification for guardrail findings — the tables that
define which checks are CRITICAL, HIGH, MEDIUM, or LOW severity, and which
checks belong in the PII category with its own severity-flip logic (HIGH on
block, MEDIUM on redact). Used by both risk_analysis.py (to aggregate many
findings into a single risk level) and security_supervisor.py (to pick the
primary blocking finding from several candidates)."""

from typing import Literal

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

_CRITICAL_CHECKS = frozenset({"secret_detected_check", "destructive_intent_check"})
_HIGH_CHECKS = frozenset({"prompt_injection_check", "deberta_injection_check", "semantic_risk_check"})
_MEDIUM_CHECKS = frozenset({"scope_check", "scope_semantic_check", "toxicity_check"})
# PII checks: a block is HIGH (input pipeline's presidio/gliner detecting
# high-confidence PII in a message that ALSO failed to redact it — see
# pipeline.py's decision map), a redact-only outcome is MEDIUM (pii_redact
# successfully handled it, same tier as an out-of-scope request).
_PII_CHECKS = frozenset({"presidio_check", "gliner_check", "pii_redact"})


def level_for_check(check_name: str, action: str) -> RiskLevel:
	"""Map a check name and action to its severity level.

	Args:
		check_name: GuardrailStep.name (e.g., "pii_redact", "prompt_injection_check")
		action: GuardrailAction ("pass", "redact", "block")

	Returns:
		RiskLevel: CRITICAL, HIGH, MEDIUM, or LOW"""
	if check_name in _CRITICAL_CHECKS:
		return "CRITICAL"
	if check_name in _HIGH_CHECKS:
		return "HIGH"
	if check_name in _PII_CHECKS:
		return "HIGH" if action == "block" else "MEDIUM"
	if check_name in _MEDIUM_CHECKS:
		return "MEDIUM"
	return "LOW"


def check_order_index(check_name: str) -> int:
	"""Canonical position of a check in pipeline.py's run_input_guardrails()
	order, used to break ties when multiple checks have equal severity.

	Lower index = earlier in the check sequence.
	"""
	order = [
		"length_check",
		"secret_detected_check",
		"prompt_injection_check",
		"destructive_intent_check",
		"custom_word_check",
		"custom_regex_check",
		"scope_check",
		"semantic_risk_check",
		"deberta_injection_check",
		"scope_semantic_check",
		"scope_semantic_mixed",
		"scope_unclear_pii",
		"scope_unclear_document",
		"scope_unclear_context",
		"toxicity_check",
		"presidio_check",
		"gliner_check",
		"pii_redact",
	]
	try:
		return order.index(check_name)
	except ValueError:
		# Output-only checks or unknown checks go to the end (lower priority for tiebreaking)
		return len(order) + 1000
