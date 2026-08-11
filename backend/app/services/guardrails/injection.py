import re

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "prompt_injection_check"

_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above)\s+instructions",
        r"disregard (all |any )?(previous|prior|above)",
        r"reveal (your |the )?(system )?prompt",
        r"(show|print|repeat) (me |us )?your (system )?prompt",
        r"you are now (in )?(developer|debug|dan|jailbreak) mode",
        r"\bdeveloper mode\b",
        r"\bjailbreak\b",
        r"\bdan mode\b",
        r"forget (all |everything )?(your )?(previous|prior)?\s*(instructions|training)",
        r"new instructions\s*:",
        r"act as (if you (were|are)|though you (were|are))",
        r"pretend (you are|to be) (?!a helpful)",
    )
)


def check_prompt_injection(text: str) -> GuardrailStep:
    if not settings.guardrail_block_prompt_injection:
        return GuardrailStep(NAME, "pass", "Check disabled")

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailStep(NAME, "block", f"Matched injection pattern: {match.group(0)!r}")

    return GuardrailStep(NAME, "pass", "No injection patterns matched")
