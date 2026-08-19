import re

from app.core.config import settings
from app.services.guardrail_policy import store as policy_store
from app.services.guardrails.types import GuardrailStep

NAME = "length_check"

_POLICY_KEY = "message_limit.default"

_repeated_char_re_cache: dict[int, re.Pattern] = {}


def _max_input_chars() -> int:
    """DB override (Guardrail Policy Center's Message Limits screen) with a
    fallback to settings.guardrail_max_input_chars, same precedence pattern
    as semantic_check.py/deberta_injection_check.py's _config(). A disabled
    override row (enabled=False) is honored as "use the settings default,"
    not as "no length limit at all" — this check has no independent
    enabled/disabled concept of its own (it always ran; only the *bound* is
    admin-editable here), so a disabled override just means the
    admin-configured bound doesn't apply, not that the check stops running.
    max_output_chars is stored/validated alongside max_input_chars but has
    no runtime enforcement point yet — no existing check in this codebase
    measures the LLM's own reply length today; that's flagged as a real
    limitation, not silently applied."""
    override = policy_store.get_policy(_POLICY_KEY)
    if override is not None and override.enabled and override.mode == "ENFORCE":
        value = override.configuration.get("max_input_chars")
        if isinstance(value, int):
            return value
    return settings.guardrail_max_input_chars


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
    max_chars = _max_input_chars()
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
