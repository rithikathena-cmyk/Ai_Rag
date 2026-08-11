import re

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "scope_check"


def _keywords(raw: str) -> list[str]:
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def check_scope(text: str) -> GuardrailStep:
    deny = _keywords(settings.guardrail_scope_deny_keywords)
    allow = _keywords(settings.guardrail_scope_allow_keywords)

    if not deny and not allow:
        return GuardrailStep(NAME, "pass", "No scope restrictions configured")

    for kw in deny:
        if re.search(re.escape(kw), text, re.IGNORECASE):
            return GuardrailStep(NAME, "block", f"Matched denied topic: {kw!r}")

    if allow and not any(re.search(re.escape(kw), text, re.IGNORECASE) for kw in allow):
        return GuardrailStep(NAME, "block", "Message doesn't match any allowed topic")

    return GuardrailStep(NAME, "pass", "Within configured scope")
