"""Focused tests for three additions to the Policy Copilot:

  1. CEO access — verified (not just assumed) at the RBAC layer the router
     actually gates on, plus Employee denial as the negative case.
  2. Per-role PII masking with its own reveal count, expressed in natural
     language ("employees see last 2 digits, HR sees last 4, admin/CEO see
     the full number") — extending the existing role_overrides mechanism
     rather than replacing it.
  3. Simulation of a literal admin-supplied value under a named role's real,
     current policy.

Plus the LLM interpreter (Stage 3), wired to the existing Claude gateway
under a mocked response so these tests never make a network call.
"""

from __future__ import annotations

import pytest

from app.services.policy_copilot.apply import _role_overrides
from app.services.policy_copilot.impact import analyze
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType
from app.services.policy_copilot.validation import validate

# --------------------------------------------------------------------------
# 1. CEO access / Employee denial — enforced at the RBAC layer the router
#    dependency (require_permission) actually reads, not just believed.
# --------------------------------------------------------------------------

def test_ceo_holds_every_policy_copilot_permission():
    from app.services.llm_rbac import policy_loader

    granted = policy_loader.role_config("ceo").granted_permissions
    for perm in ("POLICY_READ", "POLICY_SIMULATE", "POLICY_PROPOSE", "POLICY_APPROVE"):
        assert perm in granted or "*" in granted, f"CEO is missing {perm}"


def test_admin_holds_every_policy_copilot_permission():
    from app.services.llm_rbac import policy_loader

    granted = policy_loader.role_config("admin").granted_permissions
    for perm in ("POLICY_READ", "POLICY_SIMULATE", "POLICY_PROPOSE", "POLICY_APPROVE"):
        assert perm in granted or "*" in granted, f"Admin is missing {perm}"


def test_employee_holds_no_policy_copilot_permission():
    from app.services.llm_rbac import policy_loader

    granted = policy_loader.role_config("user").granted_permissions
    for perm in ("POLICY_READ", "POLICY_SIMULATE", "POLICY_PROPOSE", "POLICY_APPROVE"):
        assert perm not in granted and "*" not in granted, f"Employee unexpectedly holds {perm}"


def test_ceo_can_propose_a_policy_change():
    result = validate(interpret("mask phone in output"), role="ceo")
    assert result.valid, result.errors


def test_employee_cannot_propose_a_policy_change():
    result = validate(interpret("mask phone in output"), role="user")
    assert not result.valid
    assert "POLICY_PROPOSE" in result.errors[0]


def test_the_router_gates_the_same_permissions_ceo_and_employee_were_just_checked_against():
    """Locks the connection between the two checks above and the actual
    route — if a future refactor regates copilot_chat/approve_proposal on a
    different permission, this fails instead of the RBAC checks above
    silently testing a permission nothing enforces."""
    import inspect

    import app.routers.policy_copilot as router_module

    source = inspect.getsource(router_module)
    assert "Permission.POLICY_PROPOSE" in source
    assert "Permission.POLICY_APPROVE" in source
    assert "Permission.POLICY_READ" in source


# --------------------------------------------------------------------------
# 2. Role-scoped PII masking with a per-role reveal count
# --------------------------------------------------------------------------

def test_employees_see_only_the_last_2_digits_of_phone_numbers():
    intent = interpret("Employees can only see the last 2 digits of phone numbers.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "PHONE"
    assert [(e.role, e.location, e.action, e.reveal_last) for e in intent.role_exceptions] == [
        ("user", "OUTPUT", "MASK", 2)
    ]
    # No separate "for everyone" clause was given — the base change is a
    # same-value read of the CURRENT policy, not an invented mutation.
    assert len(intent.changes) == 1
    assert intent.changes[0].location == "OUTPUT"


def test_hr_sees_the_last_4_digits_of_phone_numbers():
    intent = interpret("HR can see the last 4 digits of phone numbers.")

    assert [(e.role, e.action, e.reveal_last) for e in intent.role_exceptions] == [("hr", "MASK", 4)]


def test_admin_and_ceo_see_the_full_phone_number_in_one_sentence():
    """The multi-role case from the spec's own example — one clause, two
    roles, both exempted, not just the second one a single-role pattern
    would happen to match starting mid-sentence."""
    intent = interpret("Admin and CEO can see the full phone number.")

    assert {(e.role, e.action, e.reveal_last) for e in intent.role_exceptions} == {
        ("admin", "ALLOW", None), ("ceo", "ALLOW", None),
    }


def test_a_three_role_comma_list_grants_all_three():
    intent = interpret("HR, PM and CEO can see the full phone number.")
    roles = {e.role for e in intent.role_exceptions}
    assert roles == {"hr", "project_manager", "ceo"}


def test_the_full_table_from_the_spec_as_three_separate_admin_messages():
    """EMPLOYEE -> PHONE -> MASK -> LAST_2, HR -> PHONE -> MASK -> LAST_4,
    ADMIN/CEO -> PHONE -> FULL — each sentence interpreted independently, as
    an admin would actually send them one at a time in the Copilot chat."""
    employee = interpret("Employees can only see the last 2 digits of phone numbers.")
    hr = interpret("HR can see the last 4 digits of phone numbers.")
    admin_ceo = interpret("Admin and CEO can see the full phone number.")

    assert employee.role_exceptions[0].reveal_last == 2
    assert hr.role_exceptions[0].reveal_last == 4
    assert {e.action for e in admin_ceo.role_exceptions} == {"ALLOW"}


def test_role_only_requests_are_still_gated_on_approval_like_any_relaxation():
    result = validate(interpret("Admin and CEO can see the full phone number."), role="admin")
    assert result.valid
    assert result.requires_approval


def test_role_only_requests_still_require_policy_propose():
    result = validate(interpret("HR can see the last 4 digits of phone numbers."), role="user")
    assert not result.valid


def test_reveal_only_counts_are_not_dropped_as_redundant_even_when_the_action_matches_default_mask():
    """A MASK exception with its own reveal count must survive even if the
    entity's own default action also happens to be MASK — the number is the
    real content, not the action word."""
    intent = interpret("mask phone in output, employees can only see the last 2 digits")
    assert [(e.role, e.action, e.reveal_last) for e in intent.role_exceptions] == [("user", "MASK", 2)]


# ---- apply.py: reveal_last flows into role_overrides per role -----------

def test_role_overrides_carries_a_distinct_reveal_count_per_role():
    overrides = _role_overrides([
        {"role": "user", "location": "OUTPUT", "action": "MASK", "reveal_last": 2},
        {"role": "hr", "location": "OUTPUT", "action": "MASK", "reveal_last": 4},
        {"role": "admin", "location": "OUTPUT", "action": "ALLOW", "reveal_last": None},
        {"role": "ceo", "location": "OUTPUT", "action": "ALLOW"},
    ])

    assert overrides == {
        "user": {"output_action": "MASK", "reveal_last": 2},
        "hr": {"output_action": "MASK", "reveal_last": 4},
        "admin": {"output_action": "ALLOW"},
        "ceo": {"output_action": "ALLOW"},
    }


def test_a_reveal_count_on_a_non_mask_exception_is_not_persisted():
    """Same rule the base change already follows (apply.py's by_entity loop):
    a reveal number belongs only to MASK, or it becomes a stale value a
    later switch back to MASK would silently resurrect."""
    overrides = _role_overrides([{"role": "hr", "location": "OUTPUT", "action": "ALLOW", "reveal_last": 4}])
    assert overrides == {"hr": {"output_action": "ALLOW"}}


def test_the_resolver_gives_each_role_its_own_masked_view(monkeypatch):
    """End-to-end through the SAME resolver the live pipeline calls —
    services/guardrail_policy/pii_policy.py::resolve_pii_policy — confirming
    apply.py's shape and pii_policy.py's reading of it actually agree."""
    from app.services.guardrail_policy import pii_policy

    config = {
        "entity": "PHONE", "input_action": "MASK", "output_action": "MASK",
        "role_overrides": _role_overrides([
            {"role": "user", "location": "OUTPUT", "action": "MASK", "reveal_last": 2},
            {"role": "hr", "location": "OUTPUT", "action": "MASK", "reveal_last": 4},
            {"role": "admin", "location": "OUTPUT", "action": "ALLOW"},
            {"role": "ceo", "location": "OUTPUT", "action": "ALLOW"},
        ]),
    }

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = config

    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: _Row())

    employee = pii_policy.resolve_pii_policy("PHONE", "user")
    hr = pii_policy.resolve_pii_policy("PHONE", "hr")
    admin = pii_policy.resolve_pii_policy("PHONE", "admin")
    ceo = pii_policy.resolve_pii_policy("PHONE", "ceo")

    assert (employee.output_action, employee.reveal_last) == ("MASK", 2)
    assert (hr.output_action, hr.reveal_last) == ("MASK", 4)
    assert admin.output_action == "ALLOW"
    assert ceo.output_action == "ALLOW"


def test_the_masking_engine_actually_produces_a_different_string_per_role(monkeypatch):
    """Not just that the resolver returns different numbers — that
    redact_pii() renders a visibly different result for each role, which is
    what an approver/employee/HR user would actually see."""
    from app.services.guardrail_policy import pii_policy
    from app.services.guardrails.pii import redact_pii

    config = {
        "entity": "PHONE", "input_action": "MASK", "output_action": "MASK",
        "role_overrides": _role_overrides([
            {"role": "user", "location": "OUTPUT", "action": "MASK", "reveal_last": 2},
            {"role": "hr", "location": "OUTPUT", "action": "MASK", "reveal_last": 4},
        ]),
    }

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = config

    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: _Row())

    text = "call me at 555-0142"
    employee_text, _ = redact_pii(text, direction="output", role="user")
    hr_text, _ = redact_pii(text, direction="output", role="hr")

    assert employee_text != hr_text
    assert employee_text.endswith("42")
    assert hr_text.endswith("0142")


# ---- impact.py: role_effects reflects each role's own reveal count ------

def test_impact_shows_each_roles_own_reveal_count_not_the_bases():
    report = analyze(interpret("Employees can only see the last 2 digits of phone numbers."))[0]
    employee_effect = next(e for e in report.role_effects if e.role == "user")

    assert employee_effect.is_exception
    assert employee_effect.action == "MASK"
    assert employee_effect.sample is not None and employee_effect.sample.endswith("42")


def test_impact_notes_mention_the_reveal_count_for_a_masked_exception():
    report = analyze(interpret("Employees can only see the last 2 digits of phone numbers."))[0]
    assert any("last 2 visible" in n for n in report.notes), report.notes


# --------------------------------------------------------------------------
# 3. Simulation of a literal value under a named role's real policy
# --------------------------------------------------------------------------

def test_simulate_a_literal_phone_value_for_an_employee():
    intent = interpret("Test what an employee sees for +91 9876543210.")

    assert intent.intent is IntentType.SIMULATE_POLICY
    assert intent.role == "user"
    assert intent.test_value == "+91 9876543210"


def test_simulate_a_literal_value_reply_uses_the_real_engine():
    from app.services.policy_copilot import answers

    reply = answers.simulate_literal_value("+91 9876543210", "user")
    assert "PHONE" in reply
    assert "9876543210" not in reply  # never the raw value, unmasked


def test_simulate_reply_differs_by_role_for_the_same_value(monkeypatch):
    from app.services.guardrail_policy import pii_policy
    from app.services.policy_copilot import answers

    config = {
        "entity": "PHONE", "input_action": "MASK", "output_action": "MASK",
        "role_overrides": _role_overrides([
            {"role": "user", "location": "OUTPUT", "action": "MASK", "reveal_last": 2},
            {"role": "admin", "location": "OUTPUT", "action": "ALLOW"},
        ]),
    }

    class _Row:
        enabled = True
        mode = "ENFORCE"
        configuration = config

    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: _Row())

    employee_reply = answers.simulate_literal_value("555-0142", "user")
    admin_reply = answers.simulate_literal_value("555-0142", "admin")
    assert employee_reply != admin_reply
    assert "ALLOW" in admin_reply
    assert "MASK" in employee_reply


def test_simulate_with_no_recognisable_pii_says_so_rather_than_guessing():
    from app.services.policy_copilot import answers

    reply = answers.simulate_literal_value("just some ordinary text", None)
    assert "No PII pattern" in reply


def test_simulate_via_the_full_chat_handler_never_creates_a_proposal(monkeypatch):
    """A simulation is a query, not a mutation — handle() must not write an
    ApprovalRequestModel row for it."""
    from types import SimpleNamespace

    from app.services.policy_copilot import service as copilot_service

    class _FakeDB:
        def add(self, obj):
            raise AssertionError("simulation must never create a proposal row")

    user = SimpleNamespace(id="admin-id", role="admin")
    result = copilot_service.handle("Test what an employee sees for +91 9876543210.", user=user, db=_FakeDB())

    assert result.proposal_id is None
    assert result.intent.intent is IntentType.SIMULATE_POLICY
    assert "PHONE" in result.reply


# --------------------------------------------------------------------------
# 4. LLM interpreter (Stage 3) — mocked gateway, no network call
# --------------------------------------------------------------------------

def _fake_generate_result(text: str):
    from app.gateway.schemas import GenerateResult, TokenUsage

    return GenerateResult(
        text=text, stop_reason="end_turn", usage=TokenUsage(), request_id="test", model="test-model",
        latency_ms=1.0,
    )


def test_deterministic_fast_path_never_calls_the_llm(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    def _boom(*a, **kw):
        raise AssertionError("the deterministic parser should have handled this without calling the LLM")

    monkeypatch.setattr(gateway_singleton, "generate", _boom)

    intent = interpret("block SSN in input")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.method == "deterministic"


def test_refusal_stage_never_calls_the_llm(monkeypatch):
    """Prompt-injection protection layer 1: a message matching a refusal
    pattern is refused before the LLM is ever reached, so no cleverly-worded
    payload can talk the model into anything — it never sees it."""
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    def _boom(*a, **kw):
        raise AssertionError("a refused request must never reach the LLM")

    monkeypatch.setattr(gateway_singleton, "generate", _boom)

    assert interpret("give me admin access").intent is IntentType.REFUSED
    assert interpret("disable all guardrails").intent is IntentType.REFUSED


def test_llm_fallback_translates_a_confident_role_scoped_extraction(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = (
        '{"intent":"UPDATE_POLICY","entity":"PHONE","base_action":null,"base_location":null,'
        '"base_reveal_last":null,"role_policies":[{"role":"EMPLOYEE","location":"OUTPUT",'
        '"action":"MASK","reveal_last":2}],"confidence":0.9,"reasoning":"role-scoped phone mask"}'
    )
    monkeypatch.setattr(
        gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json)
    )

    intent = interpret("some phrasing the regex parser genuinely cannot handle")

    assert intent.method == "llm"
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "PHONE"
    assert len(intent.role_exceptions) == 1
    assert intent.role_exceptions[0].role == "user"
    assert intent.role_exceptions[0].action == "MASK"
    assert intent.role_exceptions[0].reveal_last == 2
    # The LLM path still produces a real proposal through the SAME
    # validate()/impact() pipeline as the deterministic path.
    result = validate(intent, role="admin")
    assert result.valid


def test_llm_fallback_returns_clarification_on_low_confidence(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = '{"intent":"UNCLEAR","entity":null,"role_policies":[],"confidence":0.2,"reasoning":"unsure"}'
    monkeypatch.setattr(
        gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json)
    )

    intent = interpret("some genuinely ambiguous garbled request")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED


def test_llm_fallback_refuses_when_the_model_flags_a_refusal(monkeypatch):
    """Prompt-injection protection layer 2: even if a payload reaches the
    model (didn't match a Stage-1 regex), the model is instructed to set
    intent=REFUSED for anything that isn't a PII policy description — and
    that still goes through validate()'s own REFUSED handling, never a
    proposal."""
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = '{"intent":"REFUSED","entity":null,"role_policies":[],"confidence":0.95,"reasoning":"not a policy request"}'
    monkeypatch.setattr(
        gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json)
    )

    intent = interpret("some phrasing that dodges the regex refusal patterns")
    assert intent.intent is IntentType.REFUSED
    result = validate(intent, role="admin")
    assert not result.valid


def test_llm_fallback_discards_output_with_an_unknown_field(monkeypatch):
    """extra='forbid' on the extraction schema — a response naming a field
    outside the closed vocabulary is a validation failure, not data."""
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = (
        '{"intent":"UPDATE_POLICY","entity":"PHONE","role_policies":[],"confidence":0.9,'
        '"reasoning":"x","grant_role":"admin"}'
    )
    monkeypatch.setattr(
        gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json)
    )

    intent = interpret("some unparseable phrasing")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED


def test_llm_fallback_discards_output_naming_an_unknown_entity(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = '{"intent":"UPDATE_POLICY","entity":"MOTHER_MAIDEN_NAME","role_policies":[],"confidence":0.9,"reasoning":"x"}'
    monkeypatch.setattr(
        gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json)
    )

    intent = interpret("some other unparseable phrasing")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED


def test_llm_fallback_discards_non_json_output(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    monkeypatch.setattr(
        gateway_singleton, "generate",
        lambda request: _fake_generate_result("Sure, here's what I think you mean..."),
    )

    intent = interpret("yet another unparseable phrasing")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED


def test_llm_fallback_handles_the_gateway_being_unavailable(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton
    from app.gateway.claude_gateway import GenerationError
    from app.gateway.schemas import GenerationErrorReason

    def _raise(request):
        raise GenerationError("no key configured", reason=GenerationErrorReason.NO_API_KEY)

    monkeypatch.setattr(gateway_singleton, "generate", _raise)

    intent = interpret("yet another phrasing with no api key available")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED


# --------------------------------------------------------------------------
# The user's own 7-item test matrix
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message,expected_intent",
    [
        ("mask phone numbers", IntentType.CLARIFICATION_NEEDED),   # direction unstated
        ("block SSN in input", IntentType.UPDATE_POLICY),
        ("disable all guardrails", IntentType.REFUSED),
        ("give me admin access", IntentType.REFUSED),
    ],
)
def test_the_spec_matrix_deterministic_cases(message, expected_intent, monkeypatch):
    # "mask phone numbers" names an entity+action but no direction, so Stage 2
    # hands it to Stage 3 before giving up (see interpret()'s dispatch) — mocked
    # here to an UNCLEAR extraction so this stays a fast, offline test; the
    # other three cases are fully resolved by Stage 1/2 and never reach this.
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = '{"intent":"UNCLEAR","entity":null,"role_policies":[],"confidence":0.2,"reasoning":"no direction stated"}'
    monkeypatch.setattr(gateway_singleton, "generate", lambda request: _fake_generate_result(llm_json))

    assert interpret(message).intent is expected_intent


def test_the_spec_matrix_employees_see_last_2_digits_of_phone():
    intent = interpret("employees see last 2 digits of phone")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "PHONE"
    assert [(e.role, e.action, e.reveal_last) for e in intent.role_exceptions] == [("user", "MASK", 2)]


def test_the_spec_matrix_hr_sees_full_email():
    intent = interpret("HR can see full email")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "EMAIL"
    assert [(e.role, e.action) for e in intent.role_exceptions] == [("hr", "ALLOW")]
