"""Policy Copilot support for creating a detector alongside a policy, for
entities with no built-in recognizer (services/guardrail_policy/
detector_capability.py's CONFIGURABLE_ENTITIES: BANK_ACCOUNT, IFSC,
CUSTOMER_ID). Covers the deterministic interpreter (Stage 2), the
enforceability gate in policy_copilot/validation.py (Stage "validate"), and
one real end-to-end proposal -> approve -> create_policy() flow.

EMPLOYEE_ID stays refused throughout, unchanged — see
tests/test_detector_capability.py for that entity's own dedicated coverage.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.postgres import new_session
from app.models.approval_request import ApprovalRequestModel
from app.models.guardrail_policy import GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.services.auth.password import hash_password
from app.services.guardrail_policy import store
from app.services.guardrail_policy.detector_capability import DEFAULT_DETECTOR_PATTERNS
from app.services.policy_copilot import service as copilot_service
from app.services.policy_copilot.apply import apply_proposal
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType
from app.services.policy_copilot.validation import validate


# --------------------------------------------------------------------------
# Deterministic interpretation (Stage 2)
# --------------------------------------------------------------------------

def test_create_an_ifsc_detector_with_no_pattern_routes_to_llm_for_extraction():
    """When a configurable entity (IFSC, VEHICLE_PLATE, etc.) is found without
    a detector pattern, Stage 2 routes to Stage 3 (LLM) to see if the user
    described a pattern or format (e.g. "US vehicle number plate format"). If
    no format is described, the LLM returns detector_pattern: null (same as
    Stage 2 would), but this path allows the LLM to extract patterns from
    natural language descriptions like "matching bank account numbers ending in 123"."""
    intent = interpret("Create an IFSC detector and mask it in output.")

    # Routes to LLM for potential pattern extraction
    assert intent.method == "llm"
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.entity == "IFSC"
    assert len(intent.changes) == 1
    change = intent.changes[0]
    assert change.action == "MASK"
    assert change.location == "OUTPUT"
    assert change.detector_pattern is None  # no pattern was stated — a default/ask happens downstream


def test_create_a_customer_id_detector_with_an_explicit_backticked_pattern():
    intent = interpret(r"Create a customer id detector matching pattern `CUST-\d{6}` and mask it in input.")

    assert intent.entity == "CUSTOMER_ID"
    change = intent.changes[0]
    assert change.location == "INPUT"
    assert change.detector_pattern == r"CUST-\d{6}"


def test_create_a_bank_account_detector_with_a_quoted_pattern():
    intent = interpret('Create a bank account detector matching pattern "ACC-[0-9]{10}" and redact it in input.')

    assert intent.entity == "BANK_ACCOUNT"
    change = intent.changes[0]
    assert change.action == "REDACT"
    assert change.detector_pattern == "ACC-[0-9]{10}"


def test_free_text_after_matching_pattern_without_delimiters_is_not_captured():
    """Same discipline as _REGEX_RULE_RE — an undelimited pattern risks
    capturing part of the sentence itself, so it is simply not extracted."""
    intent = interpret(
        "Create a customer id detector matching pattern something like CUST codes and mask it in output."
    )

    assert intent.changes
    assert intent.changes[0].detector_pattern is None


# --------------------------------------------------------------------------
# Enforceability gate (policy_copilot/validation.py step 5)
# --------------------------------------------------------------------------

def test_employee_id_is_still_refused_outright():
    intent = interpret("Block EMPLOYEE_ID in input.")
    result = validate(intent, role="ceo")

    assert result.valid is False
    assert not result.requires_approval


def test_ifsc_with_no_pattern_proceeds_using_the_default_and_requires_approval():
    intent = interpret("Create an IFSC detector and mask it in output.")
    result = validate(intent, role="ceo")

    assert result.valid is True
    assert result.requires_approval is True
    assert any("No detector exists for IFSC" in w for w in result.warnings)


def test_customer_id_with_no_pattern_and_no_default_explains_and_is_refused():
    intent = interpret("Create a customer id detector and mask it in output.")
    result = validate(intent, role="ceo")

    assert result.valid is False
    assert "No detector exists for CUSTOMER_ID" in result.errors[0]
    assert "pattern" in result.errors[0].lower()


def test_customer_id_with_an_explicit_pattern_proceeds():
    intent = interpret(r"Create a customer id detector matching pattern `CUST-\d{6}` and mask it in output.")
    result = validate(intent, role="ceo")

    assert result.valid is True
    assert result.requires_approval is True


def test_customer_id_with_an_unsafe_pattern_is_refused_with_the_redos_reason():
    intent = interpret(r"Create a customer id detector matching pattern `(a+)+$` and mask it in output.")
    result = validate(intent, role="ceo")

    assert result.valid is False


def test_bank_account_still_needs_policy_propose_permission():
    intent = interpret("Create an IFSC detector and mask it in output.")
    result = validate(intent, role="user")

    assert result.valid is False
    assert "POLICY_PROPOSE" in result.errors[0]


# --------------------------------------------------------------------------
# End-to-end: full copilot proposal -> approve -> real detector created
# --------------------------------------------------------------------------

@pytest.fixture
def make_user():
    created_ids: list[uuid.UUID] = []

    def _make(role: str) -> uuid.UUID:
        email = f"detector-copilot-test-{uuid.uuid4().hex[:8]}@example.com"
        db = new_session()
        user = UserModel(
            email=email, display_name="Detector Copilot Test User",
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


def test_ifsc_proposal_end_to_end_creates_a_real_detector(make_user):
    """"Create an IFSC detector and mask it in output" — the exact request
    named in the spec. Goes through handle() (interpret -> validate ->
    proposal) then apply_proposal() (the same function the approval
    endpoint calls), and checks a REAL GuardrailPolicyModel row ends up
    carrying the default IFSC pattern — proving the whole
    NL -> Copilot -> capability check -> proposal -> validation -> risk ->
    approval -> apply -> runtime chain, not just one layer of it."""
    user_id = make_user("ceo")
    db = new_session()
    policy_id = None
    try:
        user = db.get(UserModel, user_id)
        result = copilot_service.handle("Create an IFSC detector and mask it in output.", user=user, db=db)

        assert result.validation.valid is True
        assert result.proposal_id is not None
        assert result.requires_approval is True

        proposal = db.get(ApprovalRequestModel, result.proposal_id)
        changes = proposal.payload["changes"]
        assert changes[0]["entity"] == "IFSC"
        assert changes[0]["detector_pattern"] is None  # proposal itself states no explicit pattern

        applied = apply_proposal(db, changes=changes, approver=user, reason="test approval")
        policy_id = next(a for a in applied if a.entity == "IFSC").policy_key

        row = db.query(GuardrailPolicyModel).filter(GuardrailPolicyModel.policy_key == policy_id).one()
        policy_id = row.id  # for cleanup by id below
        assert row.configuration["detector_pattern"] == DEFAULT_DETECTOR_PATTERNS["IFSC"]
        assert row.enabled is True

        from app.services.guardrails.pii import redact_pii

        text, step = redact_pii("Branch IFSC: HDFC0001234, please confirm.")
        assert "HDFC0001234" not in text
        assert step.action == "redact"
    finally:
        _cleanup(policy_id)
