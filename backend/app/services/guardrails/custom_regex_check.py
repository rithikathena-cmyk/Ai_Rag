"""Runs admin-authored REGEX-category GuardrailPolicyModel rows (Guardrail
Policy Center) against input text. A brand-new, additive check — never a
modification of injection.py/destructive.py/pii_patterns.py's existing
hard-coded patterns, so this pass's admin-authored-regex capability can
never change how any EXISTING pattern is matched.

Scope this pass: only the BLOCK action actually blocks; WARN/ALLOW/ESCALATE
never block (a rule using those is inert at runtime this pass — accepted and
storable, but has no enforcement effect yet). REDACT is accepted at the
validation layer too but has no runtime effect here either: GuardrailStep's
3-value action vocabulary (pass/redact/block) plus pipeline.py's uniform
check loop (which only ever special-cases "block" for every check in that
loop, same as this one) gives a rule-level REDACT nowhere to actually rewrite
the message the way pii_redact/gliner_check's own dedicated post-loop
handling does. Wiring that up is real, separate work — deferred, not
silently faked as working.
"""

from app.services.guardrail_policy import store
from app.services.guardrail_policy.regex_safety import run_with_timeout, safe_compile
from app.services.guardrails.types import GuardrailStep

NAME = "custom_regex_check"


def check_custom_regex(text: str) -> GuardrailStep:
    for policy in store.get_active_policies("REGEX"):
        pattern = policy.configuration.get("pattern", "")
        entity = policy.configuration.get("entity", "RULE")
        try:
            compiled = safe_compile(pattern)
            match = run_with_timeout(compiled, text)
        except Exception:
            # Should already be unreachable (validation.py rejects an unsafe/
            # invalid pattern before it can ever be saved) — a defensive
            # skip here, not a match, so one bad row can't take down every
            # other active rule's evaluation in the same request.
            continue
        if match is not None and policy.action == "BLOCK":
            return GuardrailStep(NAME, "block", f"Matched custom regex rule {policy.name!r} (entity={entity})")
    return GuardrailStep(NAME, "pass", "No custom regex rule matched")
