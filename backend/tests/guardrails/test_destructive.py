"""services/guardrails/destructive.py — destructive-intent detection with a
discussion-vs-intent distinction."""

import pytest

from app.core.config import settings
from app.services.guardrails.destructive import check_destructive_intent


@pytest.fixture(autouse=True)
def _enabled():
    original = settings.guardrail_block_destructive_intent
    settings.guardrail_block_destructive_intent = True
    yield
    settings.guardrail_block_destructive_intent = original


def test_disabled_check_passes_through():
    settings.guardrail_block_destructive_intent = False
    assert check_destructive_intent("delete all records").action == "pass"


_BLOCK = [
    "Delete all production records.",
    "DROP DATABASE production;",
    "TRUNCATE TABLE employees;",
    "Shutdown the production server.",
    "Disable authentication.",
    "delete all records",
    "drop table users",
    "wipe database",
    "destroy all files",
    "remove all users",
    "delete backups",
    # Explicit two-stage requirements: execution-verb-framed operations
    # must still block even though the operation itself (DROP TABLE) is
    # also used in an ALLOW example below — same operation, different
    # framing, different verdict.
    "DROP TABLE employees",
    "Run DROP TABLE users",
    "run DROP TABLE employees",
    "Execute DELETE FROM employees",
    "execute DROP TABLE employees",
    "Remove all database records.",
    "Truncate the production database.",
    "delete all employee records",
    # Regression: _TARGET previously covered only DB-shaped nouns
    # (file/document/record/table/...), missing this app's own document
    # vocabulary (report/manual/SOP/policy/spec/procedure/schedule) — a
    # user could ask to delete a report by name and slip past Stage 1
    # entirely. Also regression-tests the {0,4} -> {0,6} word-gap widening:
    # "the Line 7 stoppage incident" is 5 filler words before the target
    # noun, which the original {0,4} cap rejected outright.
    "Delete the Line 7 stoppage incident report",
    "Remove the recruitment SOP",
    "Erase the FX-2200 specification",
    "Delete the leave management policy",
    "Wipe the equipment maintenance manual",
    "Purge the preventive maintenance schedule",
    "Destroy the engineering change procedure",
]


@pytest.mark.parametrize("text", _BLOCK)
def test_block_cases(text):
    assert check_destructive_intent(text).action == "block"


_ALLOW = [
    "Explain how DROP TABLE works.",
    "What is SQL DELETE?",
    "How can database backups protect against accidental deletion?",
    "What is database truncation?",
    "What's the leave policy?",
    "Can you summarize this report?",
    # Explicit two-stage requirements from the spec this hardening pass was
    # built against.
    "What is DROP TABLE?",
    "Explain DROP TABLE",
    "What training exists for DROP TABLE?",
    "How does DROP TABLE work?",
    "Tell me about SQL DELETE",
    "What does TRUNCATE mean?",
    "training for DROP TABLE",
    "explain how to DROP TABLE",
    "what does DELETE FROM do?",
    # Same new-target nouns as the BLOCK regression cases above, but framed
    # as ordinary retrieval/discussion — confirms the widened _TARGET list
    # and word-gap don't turn every mention of a report/manual/SOP/policy
    # into a false positive.
    "Can you summarize the Line 7 stoppage incident report?",
    "What's in the recruitment SOP?",
    "Explain the FX-2200 specification",
    "What does the leave management policy say about sick days?",
    "Search for the equipment maintenance manual",
]


@pytest.mark.parametrize("text", _ALLOW)
def test_allow_cases(text):
    assert check_destructive_intent(text).action == "pass"


def test_discussion_marker_does_not_exempt_an_unrelated_later_sentence():
    """A "how" earlier in the message, in a different sentence, must not
    exempt a genuinely dangerous instruction that follows it."""
    step = check_destructive_intent("I wonder how this works. Now delete all the records.")
    assert step.action == "block"


def test_execution_indicator_after_educational_marker_overrides_back_to_block():
    """"explain your training, then run X" must still block — an
    execution-framed instruction closer to the operation than the
    educational marker overrides the exemption, matching Stage 2's actual
    decision rule (closest-marker-wins, not "any educational word anywhere
    in the sentence")."""
    step = check_destructive_intent("Explain your training and then run DROP TABLE employees now.")
    assert step.action == "block"


def test_known_adversarial_limitation_learn_to_delete():
    """Documented, not silently accepted: "learn" is one of the spec's
    required educational markers, and it directly precedes the operation
    verb here with no execution indicator in between — so this currently
    ALLOWS despite reading like a real request in imperative disguise. Not
    reachable via the required BLOCK/ALLOW test lists (no combination of
    them produces this shape), and tightening it (e.g. suppressing "learn"
    when immediately followed by "to <verb>") would risk rejecting genuine
    "I want to learn to use DROP TABLE safely" questions instead — flagged
    in the evaluation report's remaining-risks section rather than
    quietly patched around without a measured tradeoff."""
    step = check_destructive_intent("I want to learn to delete all the records.")
    assert step.action == "pass"  # current, documented behavior — see docstring
