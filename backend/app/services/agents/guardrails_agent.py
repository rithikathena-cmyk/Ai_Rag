from app.services.guardrails.pipeline import run_input_guardrails, run_output_guardrails
from app.services.guardrails.types import GuardrailResult


def check_input(text: str, role: str | None = None) -> GuardrailResult:
	"""Run input guardrails on user message.

	Args:
		text: User message to validate
		role: Optional user role for RBAC-scoped checks

	Returns:
		GuardrailResult with blocked status, reason, and detailed steps
	"""
	return run_input_guardrails(text, role=role)


def check_output(text: str, role: str | None = None) -> GuardrailResult:
	"""Run output guardrails on model response.

	Args:
		text: Model response to validate
		role: Optional user role for PII redaction decisions

	Returns:
		GuardrailResult with blocked status, reason, and detailed steps
	"""
	return run_output_guardrails(text, role=role)


def validate_request(text: str, role: str | None = None) -> dict:
	"""Validate request and return structured decision.

	Args:
		text: Text to validate
		role: Optional user role

	Returns:
		Dict with:
			- allowed: bool (whether request passed all checks)
			- text: str (cleaned/redacted text if passed)
			- reason: str | None (human-readable block reason if blocked)
			- steps: list (detailed guardrail steps executed)
			- blocking_step_name: str | None (which check blocked it)
	"""
	result = check_input(text, role=role)
	return {
		"allowed": not result.blocked,
		"text": result.text,
		"reason": result.block_reason,
		"steps": result.steps,
		"blocking_step_name": result.blocking_step_name,
	}


def validate_response(text: str, role: str | None = None) -> dict:
	"""Validate response and return structured decision.

	Args:
		text: Text to validate
		role: Optional user role

	Returns:
		Dict with:
			- allowed: bool (whether response passed all checks)
			- text: str (cleaned/redacted text if passed)
			- reason: str | None (human-readable block reason if blocked)
			- steps: list (detailed guardrail steps executed)
			- blocking_step_name: str | None (which check blocked it)
	"""
	result = check_output(text, role=role)
	return {
		"allowed": not result.blocked,
		"text": result.text,
		"reason": result.block_reason,
		"steps": result.steps,
		"blocking_step_name": result.blocking_step_name,
	}
