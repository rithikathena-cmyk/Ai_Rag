"""Natural-language interpretation fixes for the Policy Copilot.

Fixes for five common admin phrasings that were previously mishandled or
impossible. Each represents a distinct root cause and interacts with the
interpreter's three-stage pipeline (REFUSAL → DETERMINISTIC → LLM).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import AppError
from app.db.postgres import new_session
from app.gateway.claude_gateway import GenerationError, claude_gateway as gateway_singleton
from app.gateway.schemas import GenerateResult, TokenUsage
from app.models.guardrail_policy import GuardrailPolicyModel
from app.models.user import UserModel
from app.services.auth.password import hash_password
from app.services.guardrail_policy import service, store
from app.services.guardrail_policy.detector_capability import DetectorState, capability_for
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType
from app.services.policy_copilot.service import handle


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _fake_llm_result(json_text: str):
    return GenerateResult(
        text=json_text, stop_reason="end_turn", usage=TokenUsage(), request_id="test",
        model="test-model", latency_ms=1.0,
    )


def _fake_user(role: str) -> UserModel:
    """Minimal user stub for tests that don't need a real DB session."""
    user = UserModel(
        id=uuid.uuid4(), email="test@example.com", display_name="Test",
        password_hash="fake", is_active=True, role=role, department="test",
    )
    return user


def _block_llm(monkeypatch, message="the LLM should not have been called"):
    def _boom(*a, **kw):
        raise AssertionError(message)
    monkeypatch.setattr(gateway_singleton, "generate", _boom)


# --------------------------------------------------------------------------
# Fix 1: "Mask email addresses for employees." — role audience without
# explicit direction (Bug #1)
# --------------------------------------------------------------------------

def test_fix1_role_audience_stage2_only(monkeypatch):
    """Stage 2 should recognize "for employees" as a role-audience cue and
    treat it as OUTPUT (what comes back to that role)."""
    _block_llm(monkeypatch, "role audience should resolve in Stage 2")
    intent = interpret("Mask email addresses for employees.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "EMAIL"
    assert [(c.location, c.action) for c in intent.changes] == [("OUTPUT", "MASK")]
    assert [(e.role, e.location, e.action) for e in intent.role_exceptions] == [("user", "OUTPUT", "MASK")]


def test_fix1_multiple_roles_in_audience(monkeypatch):
    """Multiple roles in audience phrase."""
    _block_llm(monkeypatch)
    intent = interpret("Block SSN for HR and admins.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "SSN"
    assert [(e.role, e.location, e.action) for e in intent.role_exceptions] == [
        ("hr", "OUTPUT", "BLOCK"),
        ("admin", "OUTPUT", "BLOCK"),
    ]


def test_fix1_audience_with_to_preposition(monkeypatch):
    """'to' variant of audience phrase."""
    _block_llm(monkeypatch)
    intent = interpret("Flag phone numbers to employees.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "PHONE"
    assert [(e.role, e.action) for e in intent.role_exceptions] == [("user", "FLAG")]


# --------------------------------------------------------------------------
# Fix 2: "Employees can see only the last 2 characters..." — "only" after
# verb (Bug #2)
# --------------------------------------------------------------------------

def test_fix2_only_after_verb_stage2_only(monkeypatch):
    """Stage 2 regex now accepts 'only' AFTER the see/view/access verb, not
    just before."""
    _block_llm(monkeypatch)
    intent = interpret("Employees can see only the last 2 characters of phone numbers.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "PHONE"
    assert [(e.role, e.location, e.action, e.reveal_last) for e in intent.role_exceptions] == [
        ("user", "OUTPUT", "MASK", 2)
    ]


def test_fix2_only_before_verb_still_works(monkeypatch):
    """Regression: existing 'only' BEFORE verb phrasing must still work."""
    _block_llm(monkeypatch)
    intent = interpret("Employees can only see the last 3 digits of credit cards.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "CREDIT_CARD"
    assert [(e.role, e.action, e.reveal_last) for e in intent.role_exceptions] == [
        ("user", "MASK", 3)
    ]


# --------------------------------------------------------------------------
# Fix 3: "HR can see full emails but employees should see masked emails."
# — "masked" as a visibility tail (Bug #3)
# --------------------------------------------------------------------------

def test_fix3_masked_keyword_stage2_only(monkeypatch):
    """Stage 2 regex now recognizes 'masked' as a visibility tail (MASK
    action). The regex matches two separate clauses in one sentence and
    merges their role exceptions."""
    _block_llm(monkeypatch)
    intent = interpret("HR can see full emails but employees should see masked emails.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "EMAIL"
    # Both HR (ALLOW, full visibility) and employees (MASK, standard shape) exceptions
    assert sorted([(e.role, e.action, e.reveal_last) for e in intent.role_exceptions]) == [
        ("hr", "ALLOW", None),
        ("user", "MASK", None),
    ]


def test_fix3_masked_without_digit_count_has_no_reveal(monkeypatch):
    """When 'masked' is the visibility tail with no digit count specified,
    reveal_last is None (entity's standard mask shape applies)."""
    _block_llm(monkeypatch)
    intent = interpret("Redact email in output, but project managers can see masked email.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "EMAIL"
    assert [(e.role, e.action, e.reveal_last) for e in intent.role_exceptions] == [
        ("project_manager", "MASK", None)
    ]


# --------------------------------------------------------------------------
# Fix 4: "Add US vehicle number plate format..." — VEHICLE_PLATE entity
# registration (Bug #4)
# --------------------------------------------------------------------------

def test_fix4_vehicle_plate_stage2_recognizes_entity(monkeypatch):
    """Stage 2 recognizes VEHICLE_PLATE, but routes to LLM since no pattern."""
    def _mock_generate(request):
        # LLM will be called to extract/generate pattern
        return _fake_llm_result(
            '{"intent": "UPDATE_POLICY", "entity": "VEHICLE_PLATE", "base_action": "FLAG", '
            '"base_location": "INPUT", "detector_pattern": null, "role_policies": [], '
            '"confidence": 0.88, "reasoning": "Flag vehicle plates in input"}'
        )

    monkeypatch.setattr(gateway_singleton, "generate", _mock_generate)
    intent = interpret("Flag vehicle plates in input.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "VEHICLE_PLATE"
    assert [(c.location, c.action) for c in intent.changes] == [("INPUT", "FLAG")]


def test_fix4_number_plate_spoken_form(monkeypatch):
    """Alternative phrasing 'number plate' routes to LLM for pattern extraction."""
    def _mock_generate(request):
        return _fake_llm_result(
            '{"intent": "UPDATE_POLICY", "entity": "VEHICLE_PLATE", "base_action": "MASK", '
            '"base_location": "OUTPUT", "detector_pattern": null, "role_policies": [], '
            '"confidence": 0.90, "reasoning": "Mask number plates in output"}'
        )

    monkeypatch.setattr(gateway_singleton, "generate", _mock_generate)
    intent = interpret("Mask number plates in output.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "VEHICLE_PLATE"


def test_fix4_license_plate_spoken_form(monkeypatch):
    """'license plate' variant with role audience."""
    def _mock_generate(request):
        return _fake_llm_result(
            '{"intent": "UPDATE_POLICY", "entity": "VEHICLE_PLATE", "base_action": "BLOCK", '
            '"base_location": "OUTPUT", "detector_pattern": null, '
            '"role_policies": [{"role": "EMPLOYEE", "location": "OUTPUT", "action": "BLOCK"}], '
            '"confidence": 0.89, "reasoning": "Block license plates for employees"}'
        )

    monkeypatch.setattr(gateway_singleton, "generate", _mock_generate)
    intent = interpret("Block license plates for employees.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "VEHICLE_PLATE"
    assert [(e.role, e.action) for e in intent.role_exceptions] == [("user", "BLOCK")]


# --------------------------------------------------------------------------
# Fix 5: "Block SSN in output." — regression (Bug #5)
# --------------------------------------------------------------------------

def test_fix5_ssn_stage2_only_no_llm_call(monkeypatch):
    """Regression: a simple, Stage-2-resolvable request must NOT call the
    LLM. Prove it by raising on any LLM call."""
    _block_llm(monkeypatch, "LLM should not be called for Stage 2-resolvable requests")
    intent = interpret("Block SSN in output.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "SSN"
    assert [(c.location, c.action) for c in intent.changes] == [("OUTPUT", "BLOCK")]


# --------------------------------------------------------------------------
# Stage 3 fallback — LLM is genuinely reached and works (Requirement #9)
# --------------------------------------------------------------------------

def test_stage3_fallback_llm_called_and_extracts_intent(monkeypatch):
    """A phrasing Stage 2 cannot resolve should route to Stage 3 (LLM).
    Demonstrate that the LLM is called and its response is parsed."""
    def _mock_generate(request):
        return _fake_llm_result(
            '{"intent": "UPDATE_POLICY", "entity": "EMAIL", "base_action": "MASK", '
            '"base_location": "OUTPUT", "role_policies": [], "confidence": 0.85, '
            '"reasoning": "Admin wants to mask email in output"}'
        )

    monkeypatch.setattr(gateway_singleton, "generate", _mock_generate)
    intent = interpret("Conceal all email addresses when they are returned.")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "EMAIL"
    assert intent.method == "llm"
    assert intent.confidence == 0.85


def test_stage3_fallback_low_confidence_rejected(monkeypatch):
    """Stage 3 below the confidence floor returns None, which the caller
    converts to CLARIFICATION_NEEDED."""
    def _mock_generate(request):
        return _fake_llm_result(
            '{"intent": "UPDATE_POLICY", "entity": "EMAIL", "base_action": null, '
            '"base_location": null, "role_policies": [], "confidence": 0.30, '
            '"reasoning": "Very uncertain about this"}'
        )

    monkeypatch.setattr(gateway_singleton, "generate", _mock_generate)
    intent = interpret("Maybe mask emails? I think?")

    assert intent.intent is IntentType.CLARIFICATION_NEEDED


# --------------------------------------------------------------------------
# End-to-end VEHICLE_PLATE detector creation via apply_proposal
# --------------------------------------------------------------------------

@pytest.fixture
def make_user():
    """Real Postgres user fixture."""
    created_ids: list[uuid.UUID] = []

    def _make(role: str) -> uuid.UUID:
        email = f"vehicle-plate-test-{uuid.uuid4().hex[:8]}@example.com"
        db = new_session()
        user = UserModel(
            email=email, display_name="Vehicle Plate Test",
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
    from app.models.approval_request import ApprovalRequestModel
    from app.models.guardrail_policy import GuardrailPolicyVersionModel

    db = new_session()
    db.query(ApprovalRequestModel).filter(ApprovalRequestModel.target_id == policy_id).delete()
    db.query(GuardrailPolicyVersionModel).filter(GuardrailPolicyVersionModel.policy_id == policy_id).delete()
    db.query(GuardrailPolicyModel).filter(GuardrailPolicyModel.id == policy_id).delete()
    db.commit()
    db.close()


def test_fix4_vehicle_plate_e2e_detector_creation(make_user, monkeypatch):
    """End-to-end: create a VEHICLE_PLATE detector with an explicit pattern.
    Confirm it becomes ENABLED in the detector capability registry."""
    _block_llm(monkeypatch)
    monkeypatch.setattr(store, "get_active_policies", lambda category: [])

    user_id = make_user("admin")
    db = new_session()
    policy_id = None
    try:
        user = db.get(UserModel, user_id)
        result = service.create_policy(
            db, policy_key=f"test.vehicle.{uuid.uuid4().hex[:8]}", name="Vehicle Plate", description=None,
            category="PII", action="MASK",
            configuration={
                "entity": "VEHICLE_PLATE", "input_action": "ALLOW", "output_action": "MASK",
                "detector_pattern": r"\d{3}-[A-Z]{3}",
            },
            priority=100, mode="ENFORCE", created_by=user,
        )
        policy = result.policy
        assert policy is not None
        policy_id = policy.id
        assert policy.configuration["entity"] == "VEHICLE_PLATE"
        assert policy.configuration["detector_pattern"] == r"\d{3}-[A-Z]{3}"

        capability = capability_for("VEHICLE_PLATE", db)
        assert capability.state is DetectorState.ENABLED
    finally:
        _cleanup(policy_id)
        db.close()
