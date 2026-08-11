import re

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "destructive_intent_check"

_TARGET = r"(file|files|document|documents|data|database|db|table|tables|record|records|backup|backups|everything|all)"

_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b(delete|remove|erase|wipe|purge|destroy|shred|truncate)\b(?:\s+\w+){{0,4}}\s+{_TARGET}\b",
        r"\bdrop\s+table\b",
        r"\brm\s+-rf\b",
        r"\bformat\s+(the\s+)?(disk|drive|database|hard drive)\b",
    )
)


def check_destructive_intent(text: str) -> GuardrailStep:
    if not settings.guardrail_block_destructive_intent:
        return GuardrailStep(NAME, "pass", "Check disabled")

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailStep(NAME, "block", f"Matched destructive-intent pattern: {match.group(0)!r}")

    return GuardrailStep(NAME, "pass", "No destructive intent detected")
