from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "length_check"


def check_length(text: str) -> GuardrailStep:
    if not text.strip():
        return GuardrailStep(NAME, "block", "Message is empty")

    max_chars = settings.guardrail_max_input_chars
    if len(text) > max_chars:
        return GuardrailStep(NAME, "block", f"Message exceeds max length of {max_chars} characters")

    return GuardrailStep(NAME, "pass", "Within length limits")
