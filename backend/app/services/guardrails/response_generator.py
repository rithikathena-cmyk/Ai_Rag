"""Pure, deterministic function: GuardrailDecision in, natural-language
string out. No access to the raw user message, no model call, no ability to
change a decision — it only ever SELECTS wording for an already-final
decision (see pipeline.py, the only place a GuardrailDecision is
constructed). This is what makes "the response layer can't override
security" a structural guarantee rather than a policy one: there's no LLM
anywhere in this function, so there's nothing here capable of deciding
anything — only of phrasing what was already decided.

Deliberately deterministic templates, not an LLM call, per this package's
existing preference for local/free mechanisms over per-message API cost
(the same reasoning semantic_check.py and presidio_check.py document for
their own choice of local models over an LLM judge) — this is the response-
wording equivalent of that same tradeoff, at zero marginal cost or new
attack surface. A future version could add an LLM rephrasing pass on top,
but it would need to receive only this decision (never the raw user
message) and its output would need to be assigned only to the reply text,
never inspected for a "should this proceed" signal.
"""

from app.services.guardrails.decisions import GuardrailDecision

# Reason -> user-facing sentence. Every existing hardcoded message this
# package used before this module existed is preserved verbatim here (see
# pipeline.py's history) — only the three "scope_unclear_*" entries and the
# generic "insufficient_context"/"document_reference"/"pii_reference" wording
# are new, replacing what used to be a flat "That's outside what I can help
# with here." for every low-similarity scope outcome regardless of cause.
_TEMPLATES: dict[str, str] = {
    "prompt_injection": "I'm not able to help with that request.",
    "destructive_intent": (
        "I can't perform destructive actions like that, and this assistant has no ability to modify or delete anything."
    ),
    "semantic_risk": "I'm not able to help with that request.",
    "deberta_injection": "I'm not able to help with that request.",
    "toxicity_input": (
        "I can't continue with messages that include abusive, threatening, or hateful language — feel free to rephrase."
    ),
    "pii_detected_input": (
        "I can't process messages that include personal information such as emails, phone numbers, or ID numbers "
        "— please rephrase without them."
    ),
    "system_prompt_leak": "I'm not able to share that.",
    "toxicity_output": "I can't share that response because it may contain harmful or abusive content.",
    "pii_detected_output": "I can't share that response because it may contain sensitive personal information.",
    "scope_keyword": (
        "That request falls outside the areas this assistant currently supports. "
        "I can help with questions related to the enterprise knowledge base."
    ),
    "semantic_scope": (
        "That request falls outside the areas this assistant currently supports. "
        "I can help with questions related to the enterprise knowledge base."
    ),
    "pii_reference": (
        "I see you've shared contact information — what would you like me to do with it? "
        "For example, I can look up related records if you ask a specific question."
    ),
    "document_reference": "I found something matching that reference — what would you like to know about it?",
    "insufficient_context": "I'm not quite sure what you'd like me to do with that. Could you tell me what you'd like to know?",
}

_DEFAULT = "I'm not able to help with that request."


def generate_user_response(decision: GuardrailDecision, detail: str | None = None) -> str:
    # length is the one reason that needs the check's own detail string
    # (e.g. "exceeds max length of 4000 characters") rather than a fixed
    # sentence — every other reason is fully decision-driven, no message
    # content involved, which is what keeps this function's "can't see the
    # raw message" guarantee meaningful for every other case.
    if decision.reason == "length" and detail:
        return f"Your message couldn't be processed: {detail}."
    return _TEMPLATES.get(decision.reason, _DEFAULT)
