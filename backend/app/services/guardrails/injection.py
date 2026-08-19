import base64
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
# Zero-width space (U+200B) / non-joiner (U+200C) / joiner (U+200D) /
# word-joiner (U+2060) / BOM (U+FEFF) — invisible when rendered, but split
# "ignore" into "ign<ZWSP>ore" and defeat every pattern below without NFKC
# touching them (NFKC only folds *compatibility* forms, not these — they
# have no visible "plain" equivalent to fold to, they're just removed).
# Built from chr() codepoints rather than embedding the literal invisible
# characters in this source file, which would be both unreviewable (nothing
# visibly there to diff) and fragile (silently corrupted by any tool that
# normalizes whitespace).
_ZERO_WIDTH_CHARS = "".join(chr(cp) for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH_CHARS}]")
# Cyrillic/Greek lowercase letters that render visually identical (or close
# enough) to a Latin letter — NFKC does NOT fold these: they're distinct
# *canonical* letters in their own scripts, not compatibility variants of
# Latin ones, so "ignоre" with a Cyrillic 'о' (U+043E) passes straight
# through NFKC untouched and defeats every pattern below despite reading as
# identical to "ignore" to a human. Deliberately the same "fast, practical,
# not exhaustive" tier as the base64 decoder below — the handful of letters
# most likely to actually appear in a spoofed ASCII-looking phrase (a full
# Unicode confusables.txt table runs to thousands of entries covering
# scripts no realistic attack here would use); applied after lowercasing
# below, so only lowercase forms are needed — Cyrillic/Greek uppercase
# letters case-fold to their own script's lowercase pair via str.lower(),
# same as Latin does.
_HOMOGLYPH_MAP = str.maketrans({
    # Cyrillic -> Latin
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ѕ": "s", "ј": "j", "ԁ": "d",
    # Greek -> Latin
    "ο": "o", "α": "a", "ρ": "p", "υ": "u", "ν": "v", "κ": "k", "ι": "i",
})
# A base64-shaped run long enough to plausibly encode a short instruction
# (not just an incidentally base64-alphabet-shaped word) — 24+ chars, valid
# base64 alphabet, no whitespace inside the run.
_BASE64_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")


def _normalize_for_matching(text: str) -> str:
    """Deterministic preprocessing so obfuscated variants collapse onto the
    same patterns the plain phrase already matches, rather than needing a
    parallel obfuscated-pattern list per phrase. NFKC folds visually-
    identical Unicode variants (fullwidth/compatibility forms) to their
    plain ASCII equivalent; lowercasing makes the patterns below
    case-insensitive without needing re.IGNORECASE on each one individually
    (the letter-spacing collapse operates on already-lowercased text)."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = normalized.lower()
    normalized = normalized.translate(_HOMOGLYPH_MAP)
    normalized = _LETTER_SPACING_RE.sub(lambda m: m.group(0).replace(" ", ""), normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    normalized = _REPEATED_PUNCTUATION_RE.sub(r"\1", normalized)
    return normalized.strip()


def _decode_base64_candidates(text: str) -> list[str]:
    """Best-effort decode of every base64-shaped run in `text` that decodes
    to printable-ish text — a cheap, deterministic way to catch "decode this
    and follow it" style obfuscation without a real base64 payload ever
    reaching the model unexamined. Not exhaustive (doesn't recurse into
    nested encodings, doesn't try non-standard alphabets) — a fast first
    layer, same tier as the rest of this module, not a substitute for the
    semantic/ML layers above it for anything cleverer."""
    decoded: list[str] = []
    for match in _BASE64_CANDIDATE_RE.finditer(text):
        candidate = match.group(0)
        try:
            raw = base64.b64decode(candidate, validate=True)
            text_out = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        # Mostly-printable check — a real short base64-encoded instruction
        # decodes to plain text; incidental base64-alphabet noise (a hash,
        # an ID, random-looking data) usually doesn't decode to something
        # this printable-dense.
        if text_out and sum(c.isprintable() for c in text_out) / len(text_out) > 0.9:
            decoded.append(text_out)
    return decoded


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
        # RAG-poisoning-shaped, not user-typed-injection-shaped: a retrieved
        # document can't say "ignore *previous* instructions" (it has no
        # conversational "previous" to refer to) — the natural phrasing for
        # a poisoned chunk trying to hijack the turn is "ignore the user('s)
        # ...", found missing exactly this way live (see
        # tests/test_planner_retrieved_content_scanning.py and this
        # module's own test_ignore_the_user_phrasing_blocks — a crafted
        # chunk containing this exact phrasing reached the model unflagged
        # before this pattern existed, relying entirely on the prompt-level
        # defense in planner_agent_v9.yaml to neutralize it).
        r"ignore (the |this )?user'?s?\s+(\w+\s+)?(question|request|instructions|input|message|prompt)",
        # `(\w+\s+)?` tolerates exactly one adjective between "reveal ...
        # your/the/hidden" and the target noun ("reveal your COMPLETE
        # system prompt", "reveal your ENTIRE system prompt") — the
        # original pattern required the noun immediately after the
        # modifier and missed this live (see this module's
        # test_reveal_with_intervening_adjective_still_blocks). Deliberately
        # capped at one word, not open-ended, to avoid matching a
        # genuinely unrelated multi-word phrase that just happens to end in
        # "instructions" (e.g. a real HR sentence with several words between
        # "your" and "instructions").
        r"reveal (your |the |hidden )?(\w+\s+)?(system )?(prompt|instructions)",
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


def _find_match(normalized: str) -> re.Match | None:
    for pattern in _PATTERNS:
        match = pattern.search(normalized)
        if match:
            return match
    return None


def check_prompt_injection(text: str) -> GuardrailStep:
    if not settings.guardrail_block_prompt_injection:
        return GuardrailStep(NAME, "pass", "Check disabled")

    normalized = _normalize_for_matching(text)
    match = _find_match(normalized)
    if match:
        return GuardrailStep(NAME, "block", f"Matched injection pattern: {match.group(0)!r}")

    # Base64-decode-and-rescan — same patterns, applied to what a base64
    # payload embedded in the message actually says once decoded, so
    # "aWdub3JlIHByZXZpb3VzIGluc3RydWN0aW9ucw==" doesn't sail through just
    # because the literal text never contains the word "ignore".
    for decoded_text in _decode_base64_candidates(text):
        decoded_match = _find_match(_normalize_for_matching(decoded_text))
        if decoded_match:
            return GuardrailStep(
                NAME, "block", f"Matched injection pattern in a base64-decoded segment: {decoded_match.group(0)!r}"
            )

    return GuardrailStep(NAME, "pass", "No injection patterns matched")
