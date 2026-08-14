"""services/guardrails/length.py — char-count, repeated-character-run, and
line-count guards."""

import pytest

from app.core.config import settings
from app.services.guardrails.length import check_length


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_max_input_chars, settings.guardrail_max_input_lines, settings.guardrail_max_repeated_chars)
    yield
    settings.guardrail_max_input_chars, settings.guardrail_max_input_lines, settings.guardrail_max_repeated_chars = original


def test_empty_message_blocks():
    assert check_length("").action == "block"
    assert check_length("   ").action == "block"


def test_normal_message_passes():
    assert check_length("What is the leave policy?").action == "pass"


def test_over_max_chars_blocks():
    settings.guardrail_max_input_chars = 10
    assert check_length("this is way more than ten characters").action == "block"


def test_unicode_counted_by_code_point_not_bytes():
    """A multi-byte Unicode character must count as ONE character, not
    however many UTF-8 bytes it encodes to — Python's len() on a str
    already does this correctly (code points, not bytes)."""
    settings.guardrail_max_input_chars = 5
    # 5 emoji = 5 code points, should pass at a 5-char limit even though
    # each one is a multi-byte UTF-8 sequence.
    assert check_length("😀😀😀😀😀").action == "pass"


def test_repeated_character_run_blocks():
    settings.guardrail_max_input_chars = 4000
    settings.guardrail_max_repeated_chars = 30
    assert check_length("a" * 50).action == "block"
    assert check_length("!" * 50).action == "block"


def test_repeated_character_run_under_threshold_passes():
    settings.guardrail_max_repeated_chars = 30
    assert check_length("aaaa").action == "pass"


def test_deterministic_same_input_same_result():
    result1 = check_length("what is the leave policy").action
    result2 = check_length("what is the leave policy").action
    assert result1 == result2


def test_max_line_count_blocks():
    settings.guardrail_max_input_lines = 5
    assert check_length("\n".join(f"line {i}" for i in range(10))).action == "block"


def test_under_max_line_count_passes():
    settings.guardrail_max_input_lines = 5
    assert check_length("line one\nline two").action == "pass"
