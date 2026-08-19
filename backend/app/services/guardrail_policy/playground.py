"""Guardrail Policy Center test playground (spec §16) — evaluates a
CATEGORY + proposed CONFIGURATION (not necessarily saved yet) against a
sample text, using the same real detection logic every runtime check uses,
never a second/simplified implementation. Returns a safe summary only:
category, whether something was detected, the configured action, and a
generic risk level — never raw internals (model scores, thresholds, matched
spans) per the spec's explicit "do not expose... raw secrets, raw PII"
constraint. Every call is itself audited (POLICY_TESTED) by the router, not
this module — this module is pure evaluation, no side effects of its own.
"""

from app.services.guardrail_policy.regex_safety import run_with_timeout, test_pattern_safety
from app.services.guardrails.custom_word_check import build_matcher
from app.services.guardrails.gliner_check import check_with_gliner
from app.services.guardrails.pii import find_pii_labels


def _risk_for_action(action: str) -> str:
    return {
        "BLOCK": "HIGH", "ESCALATE": "CRITICAL", "REDACT": "MEDIUM", "MASK": "MEDIUM", "FLAG": "MEDIUM", "ALLOW": "LOW",
    }.get(action, "LOW")


def evaluate(
    category: str, configuration: dict, action: str, sample_text: str, direction: str | None = None,
) -> dict:
    detected = False
    detail = "No match"
    resolved_action = action

    if category == "REGEX":
        compiled = test_pattern_safety(configuration.get("pattern", ""))
        detected = run_with_timeout(compiled, sample_text) is not None
        detail = "Pattern matched the sample text" if detected else "Pattern did not match"

    elif category == "WORD_FILTER":
        matcher = build_matcher(
            configuration.get("word", ""), configuration.get("match_mode", "WORD"),
            bool(configuration.get("case_sensitive", False)),
        )
        detected = run_with_timeout(matcher, sample_text) is not None
        detail = "Word/phrase matched the sample text" if detected else "No match"

    elif category == "PII":
        # Tests the PROPOSED configuration's own input_action/output_action
        # directly (never the live DB-backed store — a policy under test
        # here may not be saved yet at all, per this module's own
        # docstring), for the direction the caller selected (spec §15's
        # explicit "INPUT TEST"/"OUTPUT TEST" tabs). Detection itself still
        # reuses the exact same real detectors (find_pii_labels()'s regex
        # recognizers + GLiNER) every runtime check uses — only which
        # action a match resolves to is playground-local/proposed.
        entity = str(configuration.get("entity", "")).strip().upper()
        labels = set(find_pii_labels(sample_text))
        _gliner_text, gliner_step = check_with_gliner(sample_text)
        if gliner_step.action == "redact":
            labels |= {label.strip() for label in gliner_step.detail.removeprefix("Redacted: ").split(",")}
        detected = entity in labels
        direction_key = "output_action" if direction == "output" else "input_action"
        resolved_action = configuration.get(direction_key, action)
        detail = f"Detected PII types: {', '.join(sorted(labels)) or 'none'}"

    elif category == "SEMANTIC":
        from app.services.guardrails.semantic_check import _matcher

        _example, score = _matcher.best_match(sample_text)
        threshold = float(configuration.get("threshold", 0.8))
        detected = score >= threshold
        detail = "Above the configured semantic-risk threshold" if detected else "Below the configured threshold"

    elif category == "PROMPT_INJECTION":
        from app.services.guardrails.deberta_injection_check import _get_pipeline

        threshold = float(configuration.get("threshold", 0.9))
        try:
            result = _get_pipeline("protectai/deberta-v3-base-prompt-injection-v2")(sample_text)[0]
            detected = result.get("label") == "INJECTION" and float(result.get("score", 0.0)) >= threshold
            detail = "Classified as prompt injection" if detected else "Classified as safe"
        except Exception:
            detected = False
            detail = "Classifier unavailable — could not evaluate"

    elif category == "MESSAGE_LIMIT":
        max_chars = int(configuration.get("max_input_chars", 4000))
        detected = len(sample_text) > max_chars
        detail = f"Sample is {'over' if detected else 'within'} the configured character limit"

    return {
        "category": category,
        "detected": detected,
        "action": resolved_action if detected else "ALLOW",
        "risk_level": _risk_for_action(resolved_action) if detected else "LOW",
        "detail": detail,
    }
