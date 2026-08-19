"""Per-role PII policy through the Copilot, end to end.

The sentence under test is the one an administrator actually types:

    "Mask phone numbers in output only show last four digits to visible for
     employee and hr can see all the number"

Three separate things have to survive that sentence: the reveal count, the
role exception, and the fact that neither may be inferred loosely — a role
merely appearing in a message must never widen that role's access.
"""

from __future__ import annotations

import pytest

from app.services.guardrail_policy.pii_policy import PIIPolicyResolution
from app.services.policy_copilot.apply import _role_overrides
from app.services.policy_copilot.impact import analyze, overall_risk
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType
from app.services.policy_copilot.validation import validate

THE_SENTENCE = (
    "Mask phone numbers in output only show last four digits to visible for employee "
    "and hr can see all the number"
)


# --------------------------------------------------------------------------
# interpretation
# --------------------------------------------------------------------------

def test_the_sentence_becomes_one_masked_change_with_one_exception():
    intent = interpret(THE_SENTENCE)

    assert intent.intent is IntentType.UPDATE_POLICY
    assert [(c.entity, c.location, c.action, c.reveal_last) for c in intent.changes] == [
        ("PHONE", "OUTPUT", "MASK", 4)
    ]
    assert [(e.role, e.location, e.action) for e in intent.role_exceptions] == [
        ("hr", "OUTPUT", "ALLOW")
    ]


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("mask phone in output show last 4 digits", 4),
        ("mask phone in output, reveal last two characters", 2),
        ("mask phone in output keeping the last 6 digits visible", 6),
        ("mask phone in output", None),
    ],
)
def test_reveal_count_is_read_from_the_phrasings_admins_use(phrase, expected):
    assert interpret(phrase).changes[0].reveal_last == expected


def test_a_two_direction_request_puts_the_reveal_only_on_the_masked_direction():
    intent = interpret(
        "mask phone in input and redact it in output, show last 4 digits, hr can see all"
    )

    assert [(c.location, c.action, c.reveal_last) for c in intent.changes] == [
        ("INPUT", "MASK", 4), ("OUTPUT", "REDACT", None),
    ]
    # The exception covers both directions the request touches.
    assert {(e.location, e.action) for e in intent.role_exceptions} == {
        ("INPUT", "ALLOW"), ("OUTPUT", "ALLOW"),
    }


def test_a_reveal_beyond_the_allowed_range_is_ignored_rather_than_applied():
    """A count outside 1-8 is dropped back to the entity's built-in mask
    shape. Silently clamping it to 8 would hand out more digits than the
    admin's own sentence asked for, in the one direction that matters."""
    assert interpret("mask phone in output show last 40 digits").changes[0].reveal_last is None


def test_reveal_is_only_read_for_a_mask():
    """REDACT replaces the value outright — a reveal count on it would be a
    number stored in the row that does nothing, until someone later switches
    the action to MASK and silently resurrects it."""
    assert interpret("redact phone in output, last four digits").changes[0].reveal_last is None


# --------------------------------------------------------------------------
# the exception must not be inferred loosely
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase",
    [
        # A role named as the SUBJECT of the restriction, not as an exemption.
        "mask phone in output for hr",
        "hr should not see phone numbers",
        "block phone in input, hr included",
        # A question about a role, not a grant.
        "what can hr see?",
    ],
)
def test_naming_a_role_does_not_by_itself_grant_it_an_exception(phrase, monkeypatch):
    # Every phrase here is one Stage 2 (the deterministic parser) does not
    # recognise as a role exception — confirmed directly against
    # _find_role_exceptions below. Since the Policy Copilot's Stage 3 LLM
    # fallback was wired to the real gateway, an unrecognised phrase like
    # "hr should not see phone numbers" would otherwise fall through to a
    # REAL network call here; blocking it keeps this test about Stage 2's
    # own behaviour (which is what it is actually testing) rather than
    # whatever a live model happens to return for an ambiguous phrase.
    from app.gateway.claude_gateway import GenerationError, claude_gateway
    from app.gateway.schemas import GenerationErrorReason

    def _blocked(*_a, **_kw):
        raise GenerationError("blocked in test", reason=GenerationErrorReason.NO_API_KEY)

    monkeypatch.setattr(claude_gateway, "generate", _blocked)

    from app.services.policy_copilot.interpreter import _find_role_exceptions

    assert _find_role_exceptions(phrase) == (), f"{phrase!r} widened at the deterministic layer"

    intent = interpret(phrase)
    assert intent.role_exceptions == (), f"{phrase!r} widened {intent.role_exceptions}"


def test_an_exception_matching_the_base_action_is_dropped_as_redundant():
    """Otherwise the row carries a role_overrides entry that says nothing, and
    has to be reasoned about every time the base action changes."""
    intent = interpret("allow employees to see all phone numbers in output")

    assert intent.changes[0].action == "ALLOW"
    assert intent.role_exceptions == ()


def test_an_exception_needs_an_explicit_full_visibility_phrase():
    assert interpret("mask phone in output but hr can see all the number").role_exceptions
    assert not interpret("mask phone in output but hr can see it later").role_exceptions


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def test_the_exception_forces_approval_and_says_who_is_exempt():
    result = validate(interpret(THE_SENTENCE), role="admin")

    assert result.valid
    assert result.requires_approval, "an exception is a relaxation and must be approved"
    assert any("HR" in w and "MASK" in w for w in result.warnings)


def test_an_exception_on_a_direction_nothing_changes_is_refused():
    """Otherwise the exception would be written against the safe default,
    quietly exempting a role from a rule the proposal never showed."""
    intent = interpret(THE_SENTENCE)
    stray = intent.model_copy(update={
        "role_exceptions": tuple(
            e.model_copy(update={"location": "INPUT"}) for e in intent.role_exceptions
        ),
    })
    result = validate(stray, role="admin")

    assert not result.valid
    assert "INPUT" in result.errors[0]


def test_an_employee_cannot_propose_an_exception_for_itself():
    """Authority is re-derived from the real role, never from the message."""
    result = validate(interpret(THE_SENTENCE), role="user")

    assert not result.valid
    assert "POLICY_PROPOSE" in result.errors[0] or "policy:propose" in result.errors[0].lower()


# --------------------------------------------------------------------------
# impact
# --------------------------------------------------------------------------

def test_impact_shows_every_role_not_only_the_exempted_one():
    reports = analyze(interpret(THE_SENTENCE))
    assert len(reports) == 1
    effects = {e.role: e for e in reports[0].role_effects}

    assert set(effects) == {"user", "hr", "project_manager", "ceo", "admin"}
    assert effects["hr"].is_exception and effects["hr"].action == "ALLOW"
    assert all(
        not e.is_exception and e.action == "MASK"
        for role, e in effects.items() if role != "hr"
    )


def test_the_simulated_sample_is_the_string_the_pipeline_would_produce():
    """Rendered by the real token builder, so the digit count an approver
    reads is the digit count they will get."""
    report = analyze(interpret(THE_SENTENCE))[0]

    assert report.proposed_sample == "###0142"
    assert report.reveal_last == 4
    assert next(e for e in report.role_effects if e.role == "hr").sample == "555-0142"


def test_an_exception_on_an_ml_detected_entity_says_it_will_not_fully_apply():
    """GLiNER and Presidio redact their spans before the policy-aware pass
    runs, with no role — so an exception only reaches what the deterministic
    recognizers claim. An approver must not be left thinking otherwise."""
    report = analyze(interpret("mask address in output, hr can see the full address"))[0]

    assert any(
        "deterministic recognizers claim" in n and "every role" in n
        for n in report.notes
    ), report.notes


def test_a_deterministic_entity_gets_no_such_caveat():
    report = analyze(interpret(THE_SENTENCE))[0]
    assert not any("deterministic recognizers claim" in n for n in report.notes)


def test_risk_reflects_the_exception_not_only_the_base_action():
    """The base action here STRENGTHENS nothing and the exception is a full
    ALLOW; rating the proposal on the base action alone would show LOW next
    to a row handing a role every digit."""
    assert overall_risk(analyze(interpret(THE_SENTENCE))) == "HIGH"


# --------------------------------------------------------------------------
# "what can HR see?"
# --------------------------------------------------------------------------

def test_asking_what_a_role_can_see_reports_its_pii_exceptions(monkeypatch):
    """PII handling is a separate axis from permissions: a role can hold no
    special permission and still see more of an entity than everyone else.
    Answering without that leaves out what the question usually means."""
    from app.services.guardrail_policy import pii_policy
    from app.services.policy_copilot import answers

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = {
            "entity": "PHONE", "input_action": "MASK", "output_action": "MASK",
            "role_overrides": {"hr": {"output_action": "ALLOW"}},
        }

    monkeypatch.setattr(
        pii_policy, "_find_row", lambda entity: _Row() if entity == "PHONE" else None
    )

    hr = answers.explain_access(role="hr")
    assert "PII exceptions" in hr
    assert "PHONE: output action ALLOW" in hr

    employee = answers.explain_access(role="user")
    assert "no entity has an exception for this role" in employee


# --------------------------------------------------------------------------
# the write path's own gate
# --------------------------------------------------------------------------

def test_an_override_weakening_a_critical_entity_is_caught_at_the_write_path():
    """The Copilot's own validation is not the only gate. A per-role exception
    changes neither input_action nor output_action, so without an explicit
    check it would be the one way to relax a critical entity without an
    approval step."""
    from app.services.guardrail_policy.service import _is_critical_pii_creation_weakening

    config = {
        "entity": "SSN",
        "input_action": "REDACT",
        "output_action": "REDACT",
        "role_overrides": {"hr": {"output_action": "ALLOW"}},
    }
    assert _is_critical_pii_creation_weakening("PII", config)

    # Same shape, no exception: nothing to gate.
    assert not _is_critical_pii_creation_weakening("PII", {**config, "role_overrides": {}})


def test_an_override_no_weaker_than_the_base_is_not_treated_as_a_weakening():
    from app.services.guardrail_policy.service import _overrides_weaken

    base = {"input_action": "MASK", "output_action": "MASK"}
    assert not _overrides_weaken({"role_overrides": {"hr": {"output_action": "BLOCK"}}}, base)
    assert _overrides_weaken({"role_overrides": {"hr": {"output_action": "ALLOW"}}}, base)


def test_the_config_schema_rejects_an_unknown_role_or_slot():
    """The row is the last place an exception can be introduced; a typo'd role
    would sit there looking active while matching nobody."""
    from app.core.errors import AppError
    from app.services.guardrail_policy.validation import validate_configuration

    base = {"entity": "PHONE", "input_action": "MASK", "output_action": "MASK"}

    ok = validate_configuration("PII", {**base, "reveal_last": 4,
                                        "role_overrides": {"HR": {"output_action": "ALLOW"}}})
    assert ok["reveal_last"] == 4
    assert ok["role_overrides"] == {"hr": {"output_action": "ALLOW"}}

    for bad in (
        {"role_overrides": {"hr_manager": {"output_action": "ALLOW"}}},
        {"role_overrides": {"hr": {"output_action": "SHOW"}}},
        {"role_overrides": {"hr": {"granted_permissions": ["*"]}}},
        {"role_overrides": {"hr": {}}},
        {"reveal_last": 0},
        {"reveal_last": 40},
    ):
        with pytest.raises(AppError):
            validate_configuration("PII", {**base, **bad})


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def test_exceptions_reshape_into_the_role_overrides_the_resolver_reads():
    overrides = _role_overrides([
        {"role": "HR", "location": "OUTPUT", "action": "ALLOW"},
        {"role": "hr", "location": "INPUT", "action": "ALLOW"},
        {"role": "ceo", "location": "OUTPUT", "action": "ALLOW"},
    ])

    assert overrides == {
        "hr": {"output_action": "ALLOW", "input_action": "ALLOW"},
        "ceo": {"output_action": "ALLOW"},
    }


def test_the_overrides_view_lists_only_the_slots_that_actually_differ(monkeypatch):
    """Reporting a role's full resolved triple would list values it inherits
    from the base policy under a heading that says "override"."""
    from app.services.guardrail_policy import pii_policy
    from app.services.policy_copilot import entities_view

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = {
            "entity": "PHONE", "input_action": "MASK", "output_action": "MASK",
            "reveal_last": 4, "role_overrides": {"hr": {"output_action": "ALLOW"}},
        }

    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: _Row())

    assert entities_view.role_overrides("PHONE") == {"hr": {"output_action": "ALLOW"}}


def test_applying_onto_a_disabled_row_does_not_resurrect_its_stale_config(monkeypatch):
    """SF-01 on the write side: the proposal was analysed against the safe
    default, because that is what a disabled row leaves in force. Re-enabling
    the row with its old configuration would apply settings the approver never
    saw."""
    from app.services.policy_copilot import apply as apply_module

    class _StaleRow:
        id = "row-id"
        version = 7
        policy_key = "pii.phone"
        enabled = False
        configuration = {
            "entity": "PHONE",
            "input_action": "ALLOW",          # the stale value, never approved
            "output_action": "ALLOW",
            "redaction_format": "[left over]",
            "detection_sources": ["regex"],
        }

    captured: dict = {}

    class _Result:
        policy = _StaleRow()

    def _fake_update(db, policy_id, *, expected_version, updates, updated_by, reason, pre_approved):
        captured.update(updates["configuration"])
        return _Result()

    monkeypatch.setattr(apply_module, "_find_row", lambda db, entity: _StaleRow())
    monkeypatch.setattr(apply_module.policy_service, "update_policy", _fake_update)
    monkeypatch.setattr(apply_module.store, "invalidate", lambda: None)

    class _Approver:
        id = "approver"
        role = "admin"

    apply_module.apply_proposal(
        db=None,
        changes=[{"entity": "PHONE", "location": "OUTPUT", "action": "MASK", "reveal_last": 4}],
        approver=_Approver(), reason=None,
    )

    assert captured["output_action"] == "MASK"          # the approved change
    assert captured["input_action"] != "ALLOW"          # NOT the stale value
    assert "redaction_format" not in captured           # nor any other leftover
    assert captured["detection_sources"] == ["regex", "presidio", "gliner"]


def test_the_roles_shown_in_impact_are_the_roles_checked_for_overrides():
    """A role present in one list and missing from the other would either be
    enforced without being displayed, or displayed without being enforced."""
    from app.services.policy_copilot.entities_view import _ROLES as view_roles
    from app.services.policy_copilot.impact import _ROLES as impact_roles
    from app.services.policy_copilot.schemas import RoleException

    assert set(view_roles) == {identifier for identifier, _ in impact_roles}
    # And both agree with the schema, which is what actually constrains input.
    schema_roles = set(RoleException.model_fields["role"].annotation.__args__)
    assert set(view_roles) == schema_roles


def test_the_resolver_consumes_exactly_what_apply_writes(monkeypatch):
    """The contract between apply.py and pii_policy.py, asserted in one place
    so a rename on either side fails here rather than at runtime."""
    from app.services.guardrail_policy import pii_policy

    config = {
        "entity": "PHONE",
        "input_action": "MASK",
        "output_action": "MASK",
        "reveal_last": 4,
        "role_overrides": _role_overrides(
            [{"role": "hr", "location": "OUTPUT", "action": "ALLOW"}]
        ),
    }

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = config

    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: _Row())

    assert pii_policy.resolve_pii_policy("PHONE", "hr") == PIIPolicyResolution(
        input_action="MASK", output_action="ALLOW", enabled=True,
        source="custom", reveal_last=4, role_override_applied="hr",
    )
    employee = pii_policy.resolve_pii_policy("PHONE", "user")
    assert (employee.output_action, employee.reveal_last, employee.role_override_applied) == (
        "MASK", 4, None
    )
    # No role at all resolves the BASE policy, never a role's relaxation.
    assert pii_policy.resolve_pii_policy("PHONE").output_action == "MASK"
