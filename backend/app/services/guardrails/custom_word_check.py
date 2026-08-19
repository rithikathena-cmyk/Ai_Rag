"""Runs admin-authored WORD_FILTER-category GuardrailPolicyModel rows
(Guardrail Policy Center) against input text — blocked words, sensitive
words, custom organizational terms, all admin-defined at runtime rather than
hard-coded. Same scope note as custom_regex_check.py: only BLOCK actually
blocks this pass; REDACT/WARN/ALLOW/ESCALATE are accepted and storable but
inert at runtime.

WORD match mode uses \\b word-boundary matching specifically so a rule for
"admin" does not also match "administrator" — the exact false-positive the
spec calls out — unless the rule author explicitly chooses PHRASE (plain
substring) or REGEX (full pattern, still routed through the same ReDoS gate
regex rules use — see validation.py).
"""

import re

from app.services.guardrail_policy import store
from app.services.guardrail_policy.regex_safety import run_with_timeout, safe_compile
from app.services.guardrails.types import GuardrailStep

NAME = "custom_word_check"


def build_matcher(word: str, match_mode: str, case_sensitive: bool) -> re.Pattern:
    flags = 0 if case_sensitive else re.IGNORECASE
    if match_mode == "EXACT":
        return re.compile(r"^\s*" + re.escape(word) + r"\s*$", flags)
    if match_mode == "PHRASE":
        return re.compile(re.escape(word), flags)
    if match_mode == "REGEX":
        return safe_compile(word)
    return re.compile(r"\b" + re.escape(word) + r"\b", flags)  # WORD (default)


def check_custom_word(text: str) -> GuardrailStep:
    for policy in store.get_active_policies("WORD_FILTER"):
        word = policy.configuration.get("word", "")
        match_mode = policy.configuration.get("match_mode", "WORD")
        case_sensitive = bool(policy.configuration.get("case_sensitive", False))
        try:
            matcher = build_matcher(word, match_mode, case_sensitive)
            match = run_with_timeout(matcher, text)
        except Exception:
            continue
        if match is not None and policy.action == "BLOCK":
            return GuardrailStep(NAME, "block", f"Matched custom word rule {policy.name!r} (mode={match_mode})")
    return GuardrailStep(NAME, "pass", "No custom word rule matched")
