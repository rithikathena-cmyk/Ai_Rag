"""services/guardrails/pii.py — obfuscated/reformatted PII shapes that
bypass the ordinary EMAIL_RE / PHONE_CANDIDATE_RE candidate patterns.

Live-verified gap this covers: asking the chat model to "spell out the
phone number digit by digit," "write each digit as a word," "put a space
between every character," or write an email address "with spaces around
the @ sign, like: name at domain dot com" each reproduced the real,
unredacted value in the reply — the model complied with a completely
benign-sounding formatting request, and the resulting text simply didn't
match any existing PII pattern. This is the deterministic fix: dedicated
candidate patterns for these specific obfuscated shapes, reusing the same
downstream validation (canonicalize_phone/is_valid_phone/phone_confidence)
as the ordinary PHONE recognizer wherever applicable.
"""

import pytest

from app.core.config import settings
from app.services.guardrails.pii import redact_pii


@pytest.fixture(autouse=True)
def _placeholder_mode():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


# ------------------------------------------------------- char-spaced phone ---

_CHAR_SPACED_POSITIVE = [
    "2 0 6 5 5 5 0 1 3 8",
    "( 2 0 6 )   5 5 5 - 0 1 3 8",
    "2-0-6-5-5-5-0-1-3-8",
]


@pytest.mark.parametrize("value", _CHAR_SPACED_POSITIVE)
def test_char_spaced_phone_is_redacted(value):
    text, step = redact_pii(f"the number is {value}, call anytime")
    assert "[REDACTED_PHONE]" in text
    assert step.action == "redact"
    # The digits themselves must not survive anywhere in the output,
    # including inside the area-code-in-parens prefix.
    assert "206" not in text.replace("[REDACTED_PHONE]", "")
    assert "0138" not in text.replace("[REDACTED_PHONE]", "")


def test_char_spaced_phone_full_span_redacted_including_area_code():
    # Regression case for the exact gap found live: only the second half of
    # a parenthesized-area-code number was matched, leaving "( 2 0 6 )"
    # exposed in plain text.
    text, step = redact_pii("With a space between every character: ( 2 0 6 )   5 5 5 - 0 1 3 8")
    assert "( 2 0 6 )" not in text
    assert step.action == "redact"
    assert text.count("[REDACTED_PHONE]") == 1


# ------------------------------------------------------ spelled-out phone ---

_SPELLED_OUT_POSITIVE = [
    "two zero six five five five zero one three eight",
    "Two, zero, six, five, five, five, zero, one, three, eight",
    "two oh six five five five oh one three eight",
    "two - zero - six - five - five - five - zero - one - three - eight",
]


@pytest.mark.parametrize("value", _SPELLED_OUT_POSITIVE)
def test_spelled_out_phone_is_redacted(value):
    text, step = redact_pii(f"the phone number, spelled out digit by digit, is: {value}")
    assert "[REDACTED_PHONE]" in text
    assert step.action == "redact"
    assert value not in text


def test_spelled_out_phone_hyphen_separated_is_redacted():
    # Regression case for the exact gap found live via the real chat UI: the
    # model separated the spelled-out digit words with hyphens
    # ("two - zero - six - ...") rather than spaces/commas, which the
    # original separator class ([\s,]+) didn't tolerate — the whole number
    # rendered unredacted in the live UI.
    value = "two - zero - six - five - five - five - zero - one - three - eight"
    text, step = redact_pii(f"Phone (digit by digit): {value}")
    assert "[REDACTED_PHONE]" in text
    assert step.action == "redact"
    assert value not in text


# ------------------------------------------------------ spelled-out email ---

_SPELLED_OUT_EMAIL_POSITIVE = [
    "diego dot marsh dot test at harborline-test dot internal",
    "jane dot doe at example dot com",
]


@pytest.mark.parametrize("value", _SPELLED_OUT_EMAIL_POSITIVE)
def test_spelled_out_email_is_redacted(value):
    text, step = redact_pii(f"his contact email is: {value}")
    assert "[REDACTED_EMAIL]" in text
    assert step.action == "redact"
    assert value not in text


def test_spelled_out_email_with_spelled_out_hyphen_in_domain_is_redacted():
    # Regression case for the exact gap found live via the real chat UI: the
    # model spelled out a hyphenated domain label as a separate word
    # ("harborline hyphen test") instead of typing the "-" character inline
    # ("harborline-test"), which the original rigid "word dot word... at
    # word dot word" pattern (one contiguous token per segment) didn't
    # tolerate — the whole address rendered unredacted in the live UI.
    value = "diego dot marsh dot test at harborline hyphen test dot internal"
    text, step = redact_pii(f"Email: {value}")
    assert "[REDACTED_EMAIL]" in text
    assert step.action == "redact"
    assert value not in text
    assert "harborline" not in text


def test_spelled_out_email_letter_by_letter_with_bare_hyphen_separators_is_redacted():
    # Regression case for the exact gap found live via the real chat UI: the
    # model spelled the address out letter by letter, joining every
    # character with a literal '-' used as plain punctuation ("d - i - e -
    # g - o - dot - m - a - r - s - h ..."), not the word "hyphen" — the
    # original connector set (dot/at/hyphen/dash/underscore as literal
    # words only) didn't accept a bare '-' as a separator, so the whole
    # address rendered unredacted in the live UI.
    value = (
        "d - i - e - g - o - dot - m - a - r - s - h - dot - t - e - s - t - at - "
        "h - a - r - b - o - r - l - i - n - e - hyphen - t - e - s - t - dot - "
        "i - n - t - e - r - n - a - l"
    )
    text, step = redact_pii(f"Email address, letter by letter: {value}")
    assert "[REDACTED_EMAIL]" in text
    assert step.action == "redact"
    assert value not in text


# --------------------------------------------------------------- benign ---

_BENIGN = [
    "the meeting is at noon, see you there",
    "the version is 2 dot 0",
    "please check page 3 - 4 for details",
    "we shipped 1 2 3 units this week",  # 3 isolated digits, below the 7-digit floor
    "call me at the office please",
    "the file extension is dot txt",
    "look at the documentation dot pdf file for details",  # one "at", one "dot" chain, wrong shape
    "the ticket number is INC dash 1234 dash prod",  # dash chain, no "at"/"dot" at all
    "employee dash id dash number dash lookup",  # 4+ word dash chain, still no "at"/"dot"
    "well - known - fact - pattern",  # bare-hyphen chain, no "at"/"dot" at all
    "the plan is broken down into pre - launch - review - stages",  # same, longer
]


@pytest.mark.parametrize("text_in", _BENIGN)
def test_benign_text_is_not_falsely_flagged(text_in):
    text, step = redact_pii(text_in)
    assert text == text_in
    assert step.action == "pass"


def test_ordinary_grouped_phone_format_still_works():
    # Sanity check: the ordinary PHONE_CANDIDATE_RE path (multi-digit runs,
    # e.g. "555-1234") is untouched by these additions.
    text, step = redact_pii("please call us at 555-1234 for support")
    assert "[REDACTED_PHONE]" in text
    assert step.action == "redact"
