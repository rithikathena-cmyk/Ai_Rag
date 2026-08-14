from app.core.config import settings
from app.services.guardrails.deberta_injection_check import check_with_deberta
from app.services.guardrails.decisions import GuardrailDecision
from app.services.guardrails.destructive import check_destructive_intent
from app.services.guardrails.gliner_check import check_with_gliner
from app.services.guardrails.injection import check_prompt_injection
from app.services.guardrails.length import check_length
from app.services.guardrails.output import check_system_prompt_leak
from app.services.guardrails.pii import redact_pii
from app.services.guardrails.presidio_check import check_with_presidio
from app.services.guardrails.response_generator import generate_user_response
from app.services.guardrails.scope import check_scope
from app.services.guardrails.scope_semantic_check import check_scope_semantic
from app.services.guardrails.semantic_check import check_semantic_risk
from app.services.guardrails.toxicity_check import check_toxicity
from app.services.guardrails.types import GuardrailResult, GuardrailStep
from app.services.monitoring.metrics import record_guardrail_event

# step.name -> GuardrailDecision. Wording itself lives in
# response_generator.py (generate_user_response()) — this map only says
# WHICH decision/reason a given check's block corresponds to; it never
# contains a user-facing sentence directly. Three "scope_unclear_*" names
# don't come from any check module's own NAME constant — scope_semantic_
# check.py deliberately reports one of these three instead of its usual
# "scope_semantic_check" name when a low-similarity message also lacks
# request structure (see that module for why), so this map is what turns
# "no clear request, contains what looks like an email" into a
# clarification instead of the same flat "outside scope" refusal a genuine
# off-topic question gets.
_DECISION_MAP: dict[str, GuardrailDecision] = {
    "length_check": GuardrailDecision("BLOCKED", "length"),
    "prompt_injection_check": GuardrailDecision("BLOCKED", "prompt_injection"),
    "destructive_intent_check": GuardrailDecision("BLOCKED", "destructive_intent"),
    "semantic_risk_check": GuardrailDecision("BLOCKED", "semantic_risk"),
    "deberta_injection_check": GuardrailDecision("BLOCKED", "deberta_injection"),
    "scope_check": GuardrailDecision("OUT_OF_SCOPE", "scope_keyword"),
    "scope_semantic_check": GuardrailDecision("OUT_OF_SCOPE", "semantic_scope"),
    "scope_unclear_pii": GuardrailDecision("UNCLEAR", "pii_reference"),
    "scope_unclear_document": GuardrailDecision("UNCLEAR", "document_reference"),
    "scope_unclear_context": GuardrailDecision("UNCLEAR", "insufficient_context"),
    "toxicity_check": GuardrailDecision("BLOCKED", "toxicity_input"),
    "presidio_check": GuardrailDecision("BLOCKED", "pii_detected_input"),
    "gliner_check": GuardrailDecision("BLOCKED", "pii_detected_input"),
    "pii_redact": GuardrailDecision("BLOCKED", "pii_detected_input"),
}
_DEFAULT_DECISION = GuardrailDecision("BLOCKED", "unknown")


def _record(direction: str, step: GuardrailStep) -> None:
    record_guardrail_event(direction, step.name, step.action, step.detail)


def run_input_guardrails(text: str) -> GuardrailResult:
    steps: list[GuardrailStep] = []
    current = text

    for check in (
        # Cheap, deterministic regex/keyword checks first — every one of
        # these can short-circuit the loop before any model inference runs.
        check_length, check_prompt_injection, check_destructive_intent, check_scope,
        # Model-based checks last — each is a real embedding/classifier/NER
        # inference, only worth paying for once the free checks above pass.
        # Grouped by concern: injection/jailbreak (semantic_risk, deberta),
        # then topic scope (scope_semantic), then abuse (toxicity), then PII
        # (presidio, gliner).
        check_semantic_risk, check_with_deberta, check_scope_semantic, check_toxicity,
        check_with_presidio, check_with_gliner,
    ):
        step = check(current)
        steps.append(step)
        _record("input", step)
        if step.action == "block":
            decision = _DECISION_MAP.get(step.name, _DEFAULT_DECISION)
            reason = generate_user_response(decision, detail=step.detail)
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
        reason = generate_user_response(_DECISION_MAP["pii_redact"])
        return GuardrailResult(text=current, blocked=True, block_reason=reason, steps=steps)

    return GuardrailResult(text=current, blocked=False, steps=steps)


def run_output_guardrails(text: str) -> GuardrailResult:
    steps: list[GuardrailStep] = []

    leak_step = check_system_prompt_leak(text)
    steps.append(leak_step)
    _record("output", leak_step)
    if leak_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "system_prompt_leak"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps)

    # Toxicity/abuse check on the reply itself — same function, same config
    # (guardrails.yaml's toxicity_check:) as the input-side call above;
    # mirrors presidio_check/gliner_check's dual-sided wiring. Distinct
    # reason ("toxicity_output" vs input's "toxicity_input") since the two
    # directions need different wording — one's about the user's message,
    # the other's about the assistant's own reply.
    toxicity_step = check_toxicity(text)
    steps.append(toxicity_step)
    _record("output", toxicity_step)
    if toxicity_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "toxicity_output"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps)

    # Second-pass semantic PII check on the reply itself — same function,
    # same config (guardrails.yaml's presidio_check:), same allowlist as the
    # input-side call in run_input_guardrails(); see presidio_check.py's
    # module docstring for why a generated reply can carry the same
    # structurally-precise identifier types (passport/IBAN/bank account/...)
    # this check targets on input, which pii.py's regex layer below has no
    # recognizer for either way.
    presidio_step = check_with_presidio(text)
    steps.append(presidio_step)
    _record("output", presidio_step)
    if presidio_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "pii_detected_output"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps)

    # Second semantic PII pass on the reply — same complementary relationship
    # to presidio_step as on the input side (see gliner_check.py's module
    # docstring): a curated natural-language label set catching PII shapes
    # neither Presidio's allowlist nor pii.py's regex below recognize.
    gliner_step = check_with_gliner(text)
    steps.append(gliner_step)
    _record("output", gliner_step)
    if gliner_step.action == "block":
        reason = generate_user_response(GuardrailDecision("BLOCKED", "pii_detected_output"))
        return GuardrailResult(text=text, blocked=True, block_reason=reason, steps=steps)

    redacted, pii_step = redact_pii(text)
    steps.append(pii_step)
    _record("output", pii_step)

    return GuardrailResult(text=redacted, blocked=False, steps=steps)
