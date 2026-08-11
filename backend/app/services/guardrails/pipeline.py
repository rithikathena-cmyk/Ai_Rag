from app.core.config import settings
from app.services.guardrails.destructive import check_destructive_intent
from app.services.guardrails.injection import check_prompt_injection
from app.services.guardrails.length import check_length
from app.services.guardrails.llm_check import check_with_llm
from app.services.guardrails.output import check_system_prompt_leak
from app.services.guardrails.pii import redact_pii
from app.services.guardrails.scope import check_scope
from app.services.guardrails.semantic_check import check_semantic_risk
from app.services.guardrails.types import GuardrailResult, GuardrailStep
from app.services.monitoring.metrics import record_guardrail_event

_BLOCK_MESSAGES = {
    "length_check": "Your message couldn't be processed: {detail}.",
    "prompt_injection_check": "I'm not able to help with that request.",
    "destructive_intent_check": "I can't perform destructive actions like that, and this assistant has no ability to modify or delete anything.",
    "semantic_risk_check": "I'm not able to help with that request.",
    "scope_check": "That's outside what I can help with here.",
    "llm_advanced_check": "I'm not able to help with that request.",
    "pii_redact": "I can't process messages that include personal information such as emails, phone numbers, or ID numbers — please rephrase without them.",
}


def _record(direction: str, step: GuardrailStep) -> None:
    record_guardrail_event(direction, step.name, step.action, step.detail)


def run_input_guardrails(text: str) -> GuardrailResult:
    steps: list[GuardrailStep] = []
    current = text

    for check in (
        check_length, check_prompt_injection, check_destructive_intent, check_semantic_risk, check_scope,
        check_with_llm,
    ):
        step = check(current)
        steps.append(step)
        _record("input", step)
        if step.action == "block":
            reason = _BLOCK_MESSAGES.get(step.name, "I'm not able to help with that request.").format(detail=step.detail)
            return GuardrailResult(text=current, blocked=True, block_reason=reason, steps=steps)

    current, pii_step = redact_pii(current)
    steps.append(pii_step)
    _record("input", pii_step)
    # Unlike output-side PII (below), input PII containing a request is
    # blocked outright by default (guardrail_pii_block_input) rather than
    # forwarded redacted — the user themselves is the source here, so there
    # is no "the model already generated it, redaction is what's left"
    # rationale the output path has. Set guardrail_pii_block_input=False to
    # restore the original redact-and-continue behavior.
    if pii_step.action == "redact" and settings.guardrail_pii_block_input:
        reason = _BLOCK_MESSAGES["pii_redact"]
        return GuardrailResult(text=current, blocked=True, block_reason=reason, steps=steps)

    return GuardrailResult(text=current, blocked=False, steps=steps)


def run_output_guardrails(text: str) -> GuardrailResult:
    steps: list[GuardrailStep] = []

    leak_step = check_system_prompt_leak(text)
    steps.append(leak_step)
    _record("output", leak_step)
    if leak_step.action == "block":
        reason = "I'm not able to share that."
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps)

    redacted, pii_step = redact_pii(text)
    steps.append(pii_step)
    _record("output", pii_step)

    return GuardrailResult(text=redacted, blocked=False, steps=steps)
