"""services/guardrails/pii_intent.py — employee-PII approval-workflow intent
detection. Table-driven, mirrors test_destructive.py's style."""

from app.services.guardrails.pii_intent import detect_employee_pii_intent


def test_no_employee_id_never_matches_even_with_pii_and_a_verb():
    """No target to create an approval against without a concrete
    employee-ID-shaped token — see the function's own docstring."""
    assert detect_employee_pii_intent("update my email to jane@example.com") is None


def test_bare_employee_mention_with_no_id_no_pii_no_verb_does_not_match():
    assert detect_employee_pii_intent("what is our employee handbook policy on remote work?") is None


def test_employee_id_alone_with_no_action_or_pii_does_not_match():
    """An EMP-ID mentioned in an unrelated sentence, with neither a
    recognized action verb nor an actual PII value, isn't enough signal —
    this must fall through to ordinary chat, not divert into the approval
    flow."""
    assert detect_employee_pii_intent("what team is EMP001 on?") is None


def test_ordinary_unrelated_message_does_not_match():
    assert detect_employee_pii_intent("What is our leave policy?") is None


def test_empty_and_whitespace_input_does_not_match():
    assert detect_employee_pii_intent("") is None
    assert detect_employee_pii_intent("   ") is None


_ACTION_CASES = [
    ("update EMP001's phone number to 555-0100", "modify", "EMP001"),
    ("please change EMP002's email", "modify", "EMP002"),
    ("correct EMP003's address", "modify", "EMP003"),
    ("add new employee EMP010 to the system", "add", "EMP010"),
    ("register EMP011 as a new employee", "add", "EMP011"),
    ("store EMP002 email jane@example.com", "store", "EMP002"),
    ("save EMP004's government id for future retrieval", "store", "EMP004"),
    ("what is EMP001 contact info", "read", "EMP001"),
    ("show me EMP005's email", "read", "EMP005"),
    ("who is EMP006", "read", "EMP006"),
    ("retrieve EMP007's stored phone number", "retrieve", "EMP007"),
    ("look up EMP008's address", "retrieve", "EMP008"),
]


def test_action_classification():
    for text, expected_action, expected_id in _ACTION_CASES:
        intent = detect_employee_pii_intent(text)
        assert intent is not None, f"expected a match for {text!r}"
        assert intent.action == expected_action, f"{text!r}: expected action={expected_action!r}, got {intent.action!r}"
        assert intent.employee_id == expected_id


def test_pii_value_alone_with_employee_id_matches_as_other():
    """No recognized action verb, but a real PII value plus an employee ID
    is still real signal — classified "other" rather than silently ignored."""
    intent = detect_employee_pii_intent("EMP001 jane.doe@example.com")
    assert intent is not None
    assert intent.action == "other"
    assert intent.pii_types == ("EMAIL",)


def test_masked_text_never_contains_the_raw_pii_value():
    intent = detect_employee_pii_intent(
        "My SSN is 123-45-6789 and my email is jane.doe@example.com, can you update EMP001's file?"
    )
    assert intent is not None
    assert intent.action == "modify"
    assert "123-45-6789" not in intent.masked_text
    assert "jane.doe@example.com" not in intent.masked_text
    assert "[REDACTED_SSN]" in intent.masked_text
    # EMAIL is partially masked (guardrail_pii_mode="mask" by default — see
    # pii.py's _mask_email): first 2 local-part chars, rest of the local
    # part as '#', generic ".com" ending in place of the real domain.
    assert "ja######.com" in intent.masked_text
    assert set(intent.pii_types) == {"SSN", "EMAIL"}


def test_employee_id_normalized_to_uppercase():
    intent = detect_employee_pii_intent("update emp001's phone number")
    assert intent is not None
    assert intent.employee_id == "EMP001"


def test_masked_text_redacts_a_bare_local_format_phone_number():
    """The approval workflow's masked_text is redact_pii()'s output (see
    module docstring) — the NANP local-format phone fix (pii_validators.py)
    must apply here too, not just the general chat pipeline. Previously
    "555-0100" (no area code) was undetected and passed straight into
    masked_text unredacted."""
    intent = detect_employee_pii_intent("update EMP001's phone number to 555-0100")
    assert intent is not None
    assert "555-0100" not in intent.masked_text
    # PHONE is partially masked (guardrail_pii_mode="mask" by default — see
    # pii.py's _mask_phone): first 2 + last 1 digit visible, rest as '#'.
    assert "55####0" in intent.masked_text
    assert "PHONE" in intent.pii_types


def test_action_precedence_modify_wins_over_read_verbs_in_same_message():
    """"update" (modify) and "what" (read) both technically appear —
    modify is checked first (see _ACTION_PATTERNS' own ordering comment) so
    a message that's clearly a write wins over an incidental read-shaped
    word elsewhere in the sentence."""
    intent = detect_employee_pii_intent("update EMP001's phone number, not sure what it was before")
    assert intent is not None
    assert intent.action == "modify"
