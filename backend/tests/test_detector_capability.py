"""Detector Capability Registry (services/guardrail_policy/detector_capability.py)
and the create/update-time enforcement gate it backs
(services/guardrail_policy/validation.py::_enforce_detector_capability).

Real Postgres session for the end-to-end create_policy() tests (same
convention as test_guardrail_policies.py/test_guardrail_policy_pii_
enforcement.py) — optimistic locking, JSONB configuration storage, and the
policy-store cache are exactly the DB semantics under test.
"""

import uuid

import pytest

from app.core.errors import AppError
from app.db.postgres import new_session
from app.models.approval_request import ApprovalRequestModel
from app.models.guardrail_policy import GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.services.auth.password import hash_password
from app.services.guardrail_policy import service, store
from app.services.guardrail_policy.detector_capability import (
    CONFIGURABLE_ENTITIES, DEFAULT_DETECTOR_PATTERNS, DetectorState, capability_for,
)
from app.services.guardrail_policy.validation import validate_configuration


# --------------------------------------------------------------------------
# Static membership — evidence-based, narrow by design (module docstring)
# --------------------------------------------------------------------------

def test_exactly_the_four_named_entities_are_configurable():
    assert CONFIGURABLE_ENTITIES == {"BANK_ACCOUNT", "IFSC", "CUSTOMER_ID", "VEHICLE_PLATE"}


def test_employee_id_is_never_configurable():
    """Deliberate product decision (entities.py's own note) — not an
    unimplemented gap."""
    assert "EMPLOYEE_ID" not in CONFIGURABLE_ENTITIES


def test_only_ifsc_has_a_default_pattern():
    """BANK_ACCOUNT/CUSTOMER_ID have no universal standard shape — see
    pii_patterns.py's own BANK_ACCOUNT_RE comment."""
    assert set(DEFAULT_DETECTOR_PATTERNS) == {"IFSC"}


# --------------------------------------------------------------------------
# validate_configuration()'s enforcement gate — DB-free, pure
# --------------------------------------------------------------------------

def test_an_entity_with_a_built_in_detector_needs_no_detector_pattern():
    validated = validate_configuration("PII", {"entity": "SSN", "input_action": "MASK", "output_action": "REDACT"})
    assert validated["detector_pattern"] is None


def test_a_detector_pattern_on_a_built_in_entity_is_refused():
    with pytest.raises(AppError) as exc:
        validate_configuration(
            "PII",
            {"entity": "SSN", "input_action": "MASK", "output_action": "REDACT", "detector_pattern": r"\d+"},
        )
    assert exc.value.code == "detector_pattern_not_applicable"


def test_employee_id_is_refused_even_with_a_pattern_supplied():
    """Not configurable at all — supplying a pattern doesn't change that."""
    with pytest.raises(AppError):
        validate_configuration(
            "PII",
            {
                "entity": "EMPLOYEE_ID", "input_action": "MASK", "output_action": "REDACT",
                "detector_pattern": r"[A-Z]{3}-\d{5}",
            },
        )


def test_bank_account_with_no_pattern_and_no_default_is_refused():
    with pytest.raises(AppError) as exc:
        validate_configuration("PII", {"entity": "BANK_ACCOUNT", "input_action": "MASK", "output_action": "REDACT"})
    assert exc.value.code == "detector_pattern_required"


def test_ifsc_with_no_pattern_falls_back_to_the_published_default():
    validated = validate_configuration("PII", {"entity": "IFSC", "input_action": "MASK", "output_action": "REDACT"})
    assert validated["detector_pattern"] == DEFAULT_DETECTOR_PATTERNS["IFSC"]


def test_customer_id_with_an_explicit_pattern_is_accepted():
    validated = validate_configuration(
        "PII",
        {
            "entity": "CUSTOMER_ID", "input_action": "MASK", "output_action": "REDACT",
            "detector_pattern": r"CUST-\d{6}",
        },
    )
    assert validated["detector_pattern"] == r"CUST-\d{6}"


def test_customer_id_pattern_still_goes_through_the_redos_gate():
    with pytest.raises(AppError):
        validate_configuration(
            "PII",
            {
                "entity": "CUSTOMER_ID", "input_action": "MASK", "output_action": "REDACT",
                "detector_pattern": r"(a+)+$",
            },
        )


# --------------------------------------------------------------------------
# capability_for() — the DB-aware classification
# --------------------------------------------------------------------------

def test_a_built_in_entity_with_no_active_row_is_disabled(monkeypatch):
    monkeypatch.setattr(store, "get_active_policies", lambda category: [])
    result = capability_for("SSN")
    assert result.state is DetectorState.DISABLED
    assert result.detector_source == "built-in"


def test_a_built_in_entity_with_an_active_row_is_enabled(monkeypatch):
    row = type("Row", (), {"configuration": {"entity": "SSN"}, "enabled": True})()
    monkeypatch.setattr(store, "get_active_policies", lambda category: [row])
    result = capability_for("SSN")
    assert result.state is DetectorState.ENABLED


def test_employee_id_is_unsupported_with_no_offered_pattern(monkeypatch):
    monkeypatch.setattr(store, "get_all_policies", lambda category: [])
    result = capability_for("EMPLOYEE_ID")
    assert result.state is DetectorState.UNSUPPORTED
    assert result.detector_source == "none"
    assert result.pattern is None


def test_ifsc_with_no_configured_row_is_unsupported_but_offers_the_default(monkeypatch):
    monkeypatch.setattr(store, "get_all_policies", lambda category: [])
    result = capability_for("IFSC")
    assert result.state is DetectorState.UNSUPPORTED
    assert result.detector_source == "configurable"
    assert result.pattern == DEFAULT_DETECTOR_PATTERNS["IFSC"]


def test_customer_id_with_a_configured_enabled_row_is_enabled(monkeypatch):
    row = type("Row", (), {
        "category": "PII", "configuration": {"entity": "CUSTOMER_ID", "detector_pattern": r"CUST-\d{6}"},
        "enabled": True,
    })()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [row])
    result = capability_for("CUSTOMER_ID")
    assert result.state is DetectorState.ENABLED
    assert result.detector_source == "configured"
    assert result.pattern == r"CUST-\d{6}"


def test_customer_id_with_a_configured_disabled_row_is_disabled_not_unsupported(monkeypatch):
    """Unlike a built-in entity's DISABLED (safe default still applies), a
    disabled CONFIGURED detector genuinely means no recognizer runs — but
    the state is still DISABLED, not UNSUPPORTED, since a real pattern does
    exist, just switched off."""
    row = type("Row", (), {
        "category": "PII", "configuration": {"entity": "CUSTOMER_ID", "detector_pattern": r"CUST-\d{6}"},
        "enabled": False,
    })()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [row])
    result = capability_for("CUSTOMER_ID")
    assert result.state is DetectorState.DISABLED
    assert result.detector_source == "configured"


# --------------------------------------------------------------------------
# End-to-end via create_policy() — real DB
# --------------------------------------------------------------------------

@pytest.fixture
def make_user():
    created_ids: list[uuid.UUID] = []

    def _make(role: str) -> uuid.UUID:
        email = f"detector-capability-test-{uuid.uuid4().hex[:8]}@example.com"
        db = new_session()
        user = UserModel(
            email=email, display_name="Detector Capability Test User",
            password_hash=hash_password("Throwaway-Pass-1!"), is_active=True, role=role, department="executive",
        )
        db.add(user)
        db.commit()
        created_ids.append(user.id)
        db.close()
        return user.id

    yield _make

    if created_ids:
        db = new_session()
        db.query(UserModel).filter(UserModel.id.in_(created_ids)).delete(synchronize_session=False)
        db.commit()
        db.close()


def _cleanup(policy_id: uuid.UUID | None) -> None:
    store.invalidate()
    if policy_id is None:
        return
    db = new_session()
    db.query(ApprovalRequestModel).filter(ApprovalRequestModel.target_id == policy_id).delete()
    db.query(GuardrailPolicyVersionModel).filter(GuardrailPolicyVersionModel.policy_id == policy_id).delete()
    db.query(GuardrailPolicyModel).filter(GuardrailPolicyModel.id == policy_id).delete()
    db.commit()
    db.close()


def test_create_policy_refuses_bank_account_with_no_pattern(make_user):
    user_id = make_user("admin")
    db = new_session()
    try:
        user = db.get(UserModel, user_id)
        with pytest.raises(AppError):
            service.create_policy(
                db, policy_key=f"test.pii.{uuid.uuid4().hex[:8]}", name="Bank account", description=None,
                category="PII", action="MASK",
                configuration={"entity": "BANK_ACCOUNT", "input_action": "MASK", "output_action": "REDACT"},
                priority=100, mode="ENFORCE", created_by=user,
            )
    finally:
        _cleanup(None)


def test_create_policy_for_ifsc_fills_in_the_default_pattern_and_becomes_enabled(make_user):
    user_id = make_user("admin")
    db = new_session()
    policy_id = None
    try:
        user = db.get(UserModel, user_id)
        result = service.create_policy(
            db, policy_key=f"test.pii.{uuid.uuid4().hex[:8]}", name="IFSC", description=None,
            category="PII", action="MASK",
            configuration={"entity": "IFSC", "input_action": "MASK", "output_action": "REDACT"},
            priority=100, mode="ENFORCE", created_by=user,
        )
        policy = result.policy
        assert policy is not None
        policy_id = policy.id
        assert policy.configuration["detector_pattern"] == DEFAULT_DETECTOR_PATTERNS["IFSC"]

        capability = capability_for("IFSC", db)
        assert capability.state is DetectorState.ENABLED
    finally:
        _cleanup(policy_id)


def test_create_policy_for_customer_id_with_explicit_pattern_succeeds(make_user):
    user_id = make_user("admin")
    db = new_session()
    policy_id = None
    try:
        user = db.get(UserModel, user_id)
        result = service.create_policy(
            db, policy_key=f"test.pii.{uuid.uuid4().hex[:8]}", name="Customer ID", description=None,
            category="PII", action="MASK",
            configuration={
                "entity": "CUSTOMER_ID", "input_action": "MASK", "output_action": "REDACT",
                "detector_pattern": r"CUST-\d{6}",
            },
            priority=100, mode="ENFORCE", created_by=user,
        )
        policy = result.policy
        assert policy is not None
        policy_id = policy.id
        assert policy.configuration["detector_pattern"] == r"CUST-\d{6}"
    finally:
        _cleanup(policy_id)
