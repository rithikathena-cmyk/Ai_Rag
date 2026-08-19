"""Policy resolution and mutation safety.

The governing rule: **no mutation may ever produce an implicit ALLOW.**
Turning a policy off, deleting it, or mis-configuring it must fall back to the
safe default — never to "this entity is now unprotected". Anyone toggling a
row in the Policy Center is switching off *a custom rule*, and would not
reasonably expect to be switching off *protection for that entity*.
"""

from dataclasses import dataclass

import pytest

from app.services.guardrail_policy import pii_policy
from app.services.guardrail_policy.pii_policy import (
    _CREDENTIAL, _PERSONAL_DATA, resolve_pii_policy,
)


@dataclass
class _Row:
    """Stands in for a GuardrailPolicyModel row without touching the DB."""
    enabled: bool = True
    mode: str = "ENFORCE"
    configuration: dict | None = None


def _with_row(monkeypatch, row):
    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: row)


@pytest.fixture
def unconfigured(monkeypatch):
    """No policy row exists for any entity.

    These three tests are about what happens with NO custom rule, but
    `resolve_pii_policy()` reads the live policy store — so a real row in the
    shared dev database (an admin masking phone numbers, say) makes the
    premise false and fails a test about something else entirely. Stating
    "unconfigured" by construction is what the test names already claim.
    See SF-10 in docs/SECURITY_FINDINGS.md.
    """
    monkeypatch.setattr(pii_policy, "_find_row", lambda entity: None)


# --------------------------------------------------------------------- defaults

def test_unconfigured_personal_data_gets_the_uniform_default(unconfigured):
    for entity in ("SSN", "CREDIT_CARD", "EMAIL", "PHONE", "AADHAAR", "PAN", "PASSPORT", "BANK_ACCOUNT"):
        r = resolve_pii_policy(entity)
        assert (r.input_action, r.output_action) == _PERSONAL_DATA, entity
        assert r.enabled is True, entity


def test_unconfigured_credentials_stay_blocked(unconfigured):
    for entity in ("API_KEY", "PASSWORD", "ACCESS_TOKEN", "SECRET"):
        r = resolve_pii_policy(entity)
        assert (r.input_action, r.output_action) == _CREDENTIAL, entity


def test_completely_unknown_entity_is_never_allowed(unconfigured):
    r = resolve_pii_policy("SOME_ENTITY_NOBODY_MODELLED")
    assert r.input_action != "ALLOW" and r.output_action != "ALLOW"
    assert (r.input_action, r.output_action) == _PERSONAL_DATA


def test_entity_lookup_is_case_and_whitespace_insensitive():
    assert resolve_pii_policy("  ssn ").input_action == resolve_pii_policy("SSN").input_action


# ------------------------------------------------------------- mutation safety

def test_disabled_row_must_fall_back_to_the_safe_default(monkeypatch):
    """CRITICAL. A disabled row currently resolves to enabled=False, and
    pii.py::_resolve_match() turns that into status 'allow' — i.e. the entity
    loses ALL protection rather than reverting to the default.

    Live impact found during evaluation: a leftover disabled test row keyed to
    CREDIT_CARD left real card numbers passing through the pipeline entirely
    unredacted, in both directions."""
    _with_row(monkeypatch, _Row(enabled=False, configuration={
        "entity": "CREDIT_CARD", "input_action": "FLAG", "output_action": "BLOCK",
    }))
    r = resolve_pii_policy("CREDIT_CARD")
    assert r.enabled is True or (r.input_action, r.output_action) == _PERSONAL_DATA, (
        "a disabled policy row must revert CREDIT_CARD to the safe default, not disable protection"
    )


def test_weakening_a_credential_requires_approval_at_the_write_path():
    """An admin must not be able to SILENTLY allow a credential type.

    The control lives at the write path, not the read path — a resolver's job
    is to report configuration faithfully, and a test asserting that it
    refuses valid configuration would be testing the wrong layer. What must
    hold is that moving a critical entity away from BLOCK is detected as a
    weakening, which routes the change into the approval workflow instead of
    applying it immediately (`service.py::update_policy`)."""
    from app.services.guardrail_policy.service import _is_critical_pii_weakening

    policy = _Row(configuration={
        "entity": "API_KEY", "input_action": "BLOCK", "output_action": "BLOCK",
    })
    policy.category = "PII"

    for weakened in ("ALLOW", "FLAG", "MASK", "REDACT"):
        updates = {"configuration": {
            "entity": "API_KEY", "input_action": weakened, "output_action": "BLOCK",
        }}
        assert _is_critical_pii_weakening(policy, updates), (
            f"moving API_KEY input from BLOCK to {weakened} was not flagged for approval"
        )

    # Disabling a critical policy is equally a weakening.
    assert _is_critical_pii_weakening(policy, {"enabled": False})

    # ...and an unchanged or strengthened policy is not.
    assert not _is_critical_pii_weakening(policy, {"configuration": {
        "entity": "API_KEY", "input_action": "BLOCK", "output_action": "BLOCK",
    }})


def test_the_resolver_reports_explicit_allow_faithfully(monkeypatch):
    """The read path must not quietly rewrite what an authorised, approved
    change configured — silently ignoring a configured ALLOW would make the
    Policy Center lie about what is in force."""
    _with_row(monkeypatch, _Row(configuration={
        "entity": "API_KEY", "input_action": "ALLOW", "output_action": "ALLOW",
    }))
    r = resolve_pii_policy("API_KEY")
    assert r.input_action == "ALLOW"
    assert r.source == "custom"


def test_dry_run_never_enforces(monkeypatch):
    """DRY_RUN must detect and record without acting — that is its whole
    purpose as a safe way to trial a stricter rule."""
    _with_row(monkeypatch, _Row(mode="DRY_RUN", configuration={
        "entity": "SSN", "input_action": "BLOCK", "output_action": "BLOCK",
    }))
    assert resolve_pii_policy("SSN").dry_run is True


def test_empty_detection_sources_does_not_silently_disable(monkeypatch):
    """detection_sources=[] falls back to all three rather than resolving to
    'no detector runs', which would be an implicit ALLOW by another route."""
    _with_row(monkeypatch, _Row(configuration={"entity": "SSN", "detection_sources": []}))
    assert set(resolve_pii_policy("SSN").detection_sources) >= {"regex"}


def test_missing_actions_fall_back_to_protection(monkeypatch):
    """A malformed row with no actions at all must not mean ALLOW."""
    _with_row(monkeypatch, _Row(configuration={"entity": "SSN"}))
    r = resolve_pii_policy("SSN")
    assert r.input_action != "ALLOW" and r.output_action != "ALLOW"


@pytest.mark.parametrize("weakened", ["ALLOW", "FLAG"])
def test_weakening_a_critical_entity_is_visible(monkeypatch, weakened):
    """Weakening SSN/CREDIT_CARD must be observable in the resolution so the
    approval workflow can gate it — this asserts the value is faithfully
    reported, not silently normalised away."""
    _with_row(monkeypatch, _Row(configuration={
        "entity": "SSN", "input_action": weakened, "output_action": "REDACT",
    }))
    assert resolve_pii_policy("SSN").input_action == weakened


# ---------------------------------------------- SF-09: the CREATE-side gate

def _cfg(entity, **actions):
    base = {"entity": entity, "input_action": "MASK", "output_action": "REDACT"}
    base.update(actions)
    return base


@pytest.mark.parametrize("entity,actions", [
    ("CREDIT_CARD", {"input_action": "ALLOW"}),
    ("SSN", {"input_action": "FLAG"}),
    ("AADHAAR", {"output_action": "ALLOW"}),
    ("API_KEY", {"input_action": "MASK"}),      # weaker than its BLOCK default
    ("PASSPORT", {"input_action": "ALLOW"}),
])
def test_creating_a_weaker_row_for_a_critical_entity_is_gated(entity, actions):
    """SF-09. The UPDATE gate is a diff and cannot fire when no prior row
    exists — but most entities HAVE no row, so CREATE is the ordinary way to
    permit a critical entity. The absent row is not "no policy", it is the
    safe default, so a create weaker than that default is a weakening."""
    from app.services.guardrail_policy.service import _is_critical_pii_creation_weakening

    assert _is_critical_pii_creation_weakening("PII", _cfg(entity, **actions)) is True


@pytest.mark.parametrize("entity,actions", [
    ("SSN", {"input_action": "BLOCK", "output_action": "BLOCK"}),   # stronger
    ("SSN", {}),                                                    # equal to default
    ("EMAIL", {"input_action": "ALLOW"}),                           # not a critical entity
    ("PHONE", {"input_action": "ALLOW"}),                           # not a critical entity
])
def test_creating_an_equal_or_stronger_row_is_not_gated(entity, actions):
    """The gate must not fire on strengthening or on non-critical entities, or
    every ordinary policy creation would need an approval nobody expects."""
    from app.services.guardrail_policy.service import _is_critical_pii_creation_weakening

    assert _is_critical_pii_creation_weakening("PII", _cfg(entity, **actions)) is False


def test_the_create_gate_ignores_non_pii_categories():
    from app.services.guardrail_policy.service import _is_critical_pii_creation_weakening

    assert _is_critical_pii_creation_weakening("REGEX", _cfg("SSN", input_action="ALLOW")) is False
    assert _is_critical_pii_creation_weakening("WORD_FILTER", _cfg("SSN", input_action="ALLOW")) is False


def test_an_unknown_action_is_never_treated_as_a_weakening():
    """is_weaker() ranks unknown actions as maximally strong specifically so a
    typo or an injected value cannot be read as a weakening and slip past the
    gate — it fails toward requiring review, never past it."""
    from app.services.guardrail_policy.validation import is_weaker

    assert is_weaker("NOT_AN_ACTION", "BLOCK") is False
    assert is_weaker("ALLOW", "NOT_AN_ACTION") is True


def test_strength_ordering_is_shared_not_duplicated():
    """service.py's gating and the Copilot's risk model must read the SAME
    ordering, or they can disagree about whether a change reduces protection."""
    from app.services.guardrail_policy.validation import ACTION_STRENGTH

    assert ACTION_STRENGTH["ALLOW"] < ACTION_STRENGTH["FLAG"] < ACTION_STRENGTH["MASK"]
    assert ACTION_STRENGTH["MASK"] < ACTION_STRENGTH["REDACT"] < ACTION_STRENGTH["BLOCK"]
    assert set(ACTION_STRENGTH) == {"ALLOW", "FLAG", "MASK", "REDACT", "ESCALATE", "BLOCK"}
