import re

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "length_check"

_repeated_char_re_cache: dict[int, re.Pattern] = {}


def _repeated_char_re(threshold: int) -> re.Pattern:
    # Cached per threshold value (settings can change at runtime, e.g. in
    # tests) rather than compiled fresh on every call.
    pattern = _repeated_char_re_cache.get(threshold)
    if pattern is None:
        pattern = re.compile(r"(.)\1{" + str(threshold - 1) + r",}")
        _repeated_char_re_cache[threshold] = pattern
    return pattern


def check_length(text: str) -> GuardrailStep:
    if not text.strip():
        return GuardrailStep(NAME, "block", "Message is empty")

    # len() on a Python str already counts Unicode code points, not bytes
    # or UTF-8 code units — this is the correct behavior for a Unicode-
    # supporting app (a multi-byte character shouldn't count as "more"
    # input than a single-byte one), and needs no extra logic to get right.
    max_chars = settings.guardrail_max_input_chars
    if len(text) > max_chars:
        return GuardrailStep(NAME, "block", f"Message exceeds max length of {max_chars} characters")

    threshold = settings.guardrail_max_repeated_chars
    match = _repeated_char_re(threshold).search(text)
    if match:
        return GuardrailStep(
            NAME, "block", f"Message contains {len(match.group(0))} repeated {match.group(1)!r} characters in a row"
        )

    max_lines = settings.guardrail_max_input_lines
    line_count = text.count("\n") + 1
    if line_count > max_lines:
        return GuardrailStep(NAME, "block", f"Message exceeds max line count of {max_lines}")

    return GuardrailStep(NAME, "pass", "Within length limits")
