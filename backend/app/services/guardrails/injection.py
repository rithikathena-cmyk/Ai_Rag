import re
import unicodedata

from app.core.config import settings
from app.services.guardrails.types import GuardrailStep

NAME = "prompt_injection_check"

# Collapses "i g n o r e previous instructions"-style letter-spacing: 2+
# consecutive single-character "words" followed by one more single
# character get their spaces removed. Requires \b\w\b (exactly one word
# character between boundaries) on every token in the run, so ordinary
# prose with an occasional standalone "I" or "a" is untouched — it only
# fires on a RUN of 3+ back-to-back single letters, which normal writing
# doesn't produce.
_LETTER_SPACING_RE = re.compile(r"(?:\b\w\b[ ]){2,}\b\w\b")
_REPEATED_PUNCTUATION_RE = re.compile(r"([^\w\s])\1{2,}")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_matching(text: str) -> str:
    """Deterministic preprocessing so obfuscated variants collapse onto the
    same patterns the plain phrase already matches, rather than needing a
    parallel obfuscated-pattern list per phrase. NFKC folds visually-
    identical Unicode variants (fullwidth/compatibility forms) to their
    plain ASCII equivalent; lowercasing makes the patterns below
    case-insensitive without needing re.IGNORECASE on each one individually
    (the letter-spacing collapse operates on already-lowercased text)."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = _LETTER_SPACING_RE.sub(lambda m: m.group(0).replace(" ", ""), normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _REPEATED_PUNCTUATION_RE.sub(r"\1", normalized)
    return normalized.strip()


# Phrase-level, not keyword-level: every pattern requires an action verb
# together with its object ("ignore ... instructions", "reveal ...
# prompt") specifically so a sentence that merely mentions "instruction",
# "system", "prompt", or "security" in an ordinary, non-instructional
# context ("what is a system prompt?") never matches — none of these fire
# on the bare noun alone. Patterns match against _normalize_for_matching()'s
# output (already lowercased), so no re.IGNORECASE needed here.
_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"ignore (all |any )?(previous|prior|above)\s+instructions",
        r"disregard (all |any )?(previous|prior|above)\s+instructions",
        r"ignore\s+(security|safety)\s+(restrictions|guidelines|rules)",
        r"reveal (your |the |hidden )?(system )?(prompt|instructions)",
        r"(show|print|repeat) (me |us )?(your |the )?(hidden |system )?(prompt|instructions)",
        r"you are now (in )?(an? )?(developer|debug|dan|jailbreak|unrestricted)( mode)?",
        r"\bdeveloper mode\b",
        r"\bjailbreak\b",
        r"\bdan mode\b",
        r"forget (all |everything )?(your )?(previous|prior)?\s*(instructions|training)",
        r"override\s+(the\s+)?system\s+instructions",
        r"new instructions\s*:",
        r"act as (if you (were|are)|though you (were|are)|(an?\s+)?developer)",
        r"pretend (you are|to be) (?!a helpful)",
        r"bypass\s+(safety|security|guardrails|restrictions)",
        r"disable\s+(the\s+)?guardrails",
    )
)


def check_prompt_injection(text: str) -> GuardrailStep:
    if not settings.guardrail_block_prompt_injection:
        return GuardrailStep(NAME, "pass", "Check disabled")

    normalized = _normalize_for_matching(text)
    for pattern in _PATTERNS:
        match = pattern.search(normalized)
        if match:
            return GuardrailStep(NAME, "block", f"Matched injection pattern: {match.group(0)!r}")

    return GuardrailStep(NAME, "pass", "No injection patterns matched")
