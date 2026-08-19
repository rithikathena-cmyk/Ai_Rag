"""Guardrail Policy Center — PII enforcement wiring (services/guardrail_policy/
pii_policy.py + services/guardrails/pii.py's direction-aware redact_pii() +
pipeline.py's new output-side BLOCK branch + service.py's broadened
approval-gating). Complements test_guardrail_policies.py (RBAC/versioning/
rollback/cache/regex-word/playground, unaffected by this pass) and
test_chat_input_pii_block_persistence.py/test_pipeline_pii_block.py (which
already re-verify redact_pii()'s direction=None default is unchanged).
"""

import uuid

import pytest

from app.core.errors import AppError
from app.db.postgres import new_session
from app.models.approval_request import ApprovalRequestModel
from app.models.guardrail_policy import GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.services.guardrail_policy import service, store
from app.services.guardrail_policy.pii_policy import _PERSONAL_DATA, PIIPolicyResolution, resolve_pii_policy
from app.services.guardrail_policy.validation import validate_configuration
from app.services.auth.password import hash_password
from app.services.guardrails import pii
from app.services.guardrails.pipeline import run_output_guardrails


# ---- Action vocabulary ---------------------------------------------------


def test_flag_and_mask_are_accepted_pii_actions():
    validated = validate_configuration(
        "PII", {"entity": "PHONE", "input_action": "FLAG", "output_action": "MASK"},
    )
    assert validated["input_action"] == "FLAG"
    assert validated["output_action"] == "MASK"


def test_old_warn_action_name_is_rejected():
    with pytest.raises(AppError):
        validate_configuration("PII", {"entity": "PHONE", "input_action": "WARN", "output_action": "REDACT"})


def test_pii_config_requires_both_directions():
    with pytest.raises(AppError):
        validate_configuration("PII", {"entity": "PHONE", "input_action": "FLAG"})


# ---- resolve_pii_policy() fallback behavior ------------------------------


def test_unconfigured_entity_falls_back_to_the_safe_default_table(monkeypatch):
    # Isolated from the shared DB on purpose. This asserts the built-in
    # DEFAULT, which only holds when no row exists — and this suite writes real
    # rows into the shared development database (see SF-10), so without the
    # stub the assertion depends on whichever tests ran before it.
    monkeypatch.setattr(store, "get_all_policies", lambda category: [])

    # Uniform policy: SSN is personal data, so it gets the same
    # MASK-in/REDACT-out pair as every other personal identifier rather than
    # its own stricter REDACT/BLOCK row.
    resolution = resolve_pii_policy("ssn")  # lowercase — must normalize
    assert resolution.input_action == "MASK"
    assert resolution.output_action == "REDACT"
    assert resolution.enabled is True

    # Credentials are the one deliberate exception and stay hard-blocked.
    credential = resolve_pii_policy("API_KEY")
    assert credential.input_action == "BLOCK"
    assert credential.output_action == "BLOCK"


def test_entity_with_no_explicit_default_still_gets_real_protection():
    # Never a silent ALLOW for something pii.py's recognizers can produce
    # but this table doesn't explicitly list (e.g. IP_ADDRESS/IBAN/
    # EMPLOYEE_ID/MEDICAL_RECORD_NUMBER/DATE_OF_BIRTH).
    # Anything unlisted is personal data by definition, so it gets the same
    # pair as every named personal identifier — MASK is strictly stronger
    # than the FLAG this used to fall back to, which left the value in place.
    resolution = resolve_pii_policy("IP_ADDRESS")
    assert resolution.input_action == "MASK"
    assert resolution.output_action == "REDACT"


def test_a_disabled_row_falls_back_to_the_safe_default(monkeypatch):
    """SF-01. REWRITTEN — this test previously asserted the opposite.

    It used to require that a disabled row stay authoritative, on the
    reasoning that an admin turning protection off should not be silently
    overridden. Live evaluation showed that reading is unsafe: a leftover
    disabled row keyed to CREDIT_CARD caused real card numbers to pass
    through the pipeline unredacted in both directions, because
    _resolve_match() maps a disabled resolution to status "allow".

    The security semantics changed deliberately: disabling a RULE no longer
    removes protection for the ENTITY. Removing protection is still fully
    supported — it now requires an enabled row with an explicit ALLOW, which
    is visible, risk-classified and approval-gated. See
    test_an_explicit_allow_on_an_enabled_row_is_still_honored below."""
    fake_row = type(
        "FakeRow", (),
        {"configuration": {"entity": "SSN", "input_action": "ALLOW", "output_action": "ALLOW"}, "enabled": False, "mode": "ENFORCE"},
    )()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [fake_row])

    resolution = resolve_pii_policy("SSN")

    assert resolution.input_action != "ALLOW"
    assert resolution.output_action != "ALLOW"
    assert (resolution.input_action, resolution.output_action) == _PERSONAL_DATA
    assert resolution.enabled is True          # the safe default IS an active policy
    assert resolution.source == "default"
    assert resolution.disabled_row_present is True


def test_an_explicit_allow_on_an_enabled_row_is_still_honored(monkeypatch):
    """The other half of SF-01: intentional ALLOW must remain possible, or
    the fix would simply have removed a capability instead of making it
    explicit."""
    fake_row = type(
        "FakeRow", (),
        {"configuration": {"entity": "EMAIL", "input_action": "ALLOW", "output_action": "ALLOW"}, "enabled": True, "mode": "ENFORCE"},
    )()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [fake_row])

    resolution = resolve_pii_policy("EMAIL")

    assert resolution.input_action == "ALLOW"
    assert resolution.output_action == "ALLOW"
    assert resolution.source == "custom"
    assert resolution.disabled_row_present is False


def test_a_disabled_rows_detection_sources_and_dry_run_are_also_ignored(monkeypatch):
    """Every field of a disabled row is discarded, not just its actions — a
    row that also narrowed detection_sources or sat in DRY_RUN would
    otherwise keep applying from a rule the operator believes is off."""
    fake_row = type(
        "FakeRow", (),
        {"configuration": {"entity": "SSN", "input_action": "ALLOW", "detection_sources": []}, "enabled": False, "mode": "DRY_RUN"},
    )()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [fake_row])

    resolution = resolve_pii_policy("SSN")

    assert resolution.dry_run is False
    assert "regex" in resolution.detection_sources


@pytest.mark.parametrize("action", ["REDACT", "MASK", "BLOCK", "FLAG"])
def test_an_enabled_custom_action_is_authoritative(monkeypatch, action):
    fake_row = type(
        "FakeRow", (),
        {"configuration": {"entity": "PHONE", "input_action": action, "output_action": action}, "enabled": True, "mode": "ENFORCE"},
    )()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [fake_row])

    resolution = resolve_pii_policy("PHONE")

    assert resolution.input_action == action
    assert resolution.source == "custom"


@pytest.mark.parametrize("entity", ["CREDIT_CARD", "SSN", "AADHAAR", "PHONE", "EMAIL", "PAN"])
def test_no_custom_policy_yields_the_safe_default(monkeypatch, entity):
    monkeypatch.setattr(store, "get_all_policies", lambda category: [])

    resolution = resolve_pii_policy(entity)

    assert (resolution.input_action, resolution.output_action) == _PERSONAL_DATA
    assert resolution.source == "default"
    assert resolution.disabled_row_present is False


def test_a_dry_run_row_is_reported_as_such(monkeypatch):
    fake_row = type(
        "FakeRow", (),
        {"configuration": {"entity": "SSN", "input_action": "BLOCK", "output_action": "BLOCK"}, "enabled": True, "mode": "DRY_RUN"},
    )()
    monkeypatch.setattr(store, "get_all_policies", lambda category: [fake_row])

    resolution = resolve_pii_policy("SSN")

    assert resolution.dry_run is True


# ---- redact_pii() direction-aware enforcement ----------------------------


def _mock_resolution(monkeypatch, **overrides):
    defaults = {"input_action": "REDACT", "output_action": "BLOCK", "enabled": True}
    defaults.update(overrides)
    # `role` is accepted and ignored: these tests exercise the base
    # resolution, and per-role overrides have their own tests.
    monkeypatch.setattr(
        pii, "resolve_pii_policy", lambda entity, role=None: PIIPolicyResolution(**defaults)
    )


@pytest.mark.parametrize("direction_field,action,expected_status", [
    ("input_action", "ALLOW", "pass"),
    ("input_action", "FLAG", "pass"),
    ("input_action", "MASK", "redact"),
    ("input_action", "REDACT", "redact"),
    ("input_action", "BLOCK", "block"),
    ("input_action", "ESCALATE", "block"),
])
def test_every_input_action_resolves_to_the_right_guardrail_status(monkeypatch, direction_field, action, expected_status):
    _mock_resolution(monkeypatch, **{direction_field: action})

    _text, step = pii.redact_pii("My SSN is 123-45-6789.", direction="input")

    assert step.action == expected_status
    assert "123-45-6789" not in step.detail


@pytest.mark.parametrize("direction_field,action,expected_status", [
    ("output_action", "ALLOW", "pass"),
    ("output_action", "FLAG", "pass"),
    ("output_action", "MASK", "redact"),
    ("output_action", "REDACT", "redact"),
    ("output_action", "BLOCK", "block"),
    ("output_action", "ESCALATE", "block"),
])
def test_every_output_action_resolves_to_the_right_guardrail_status(monkeypatch, direction_field, action, expected_status):
    _mock_resolution(monkeypatch, **{direction_field: action})

    _text, step = pii.redact_pii("Their SSN is 123-45-6789.", direction="output")

    assert step.action == expected_status
    assert "123-45-6789" not in step.detail


def test_allow_leaves_the_raw_text_untouched(monkeypatch):
    _mock_resolution(monkeypatch, input_action="ALLOW")

    text, step = pii.redact_pii("My SSN is 123-45-6789.", direction="input")

    assert text == "My SSN is 123-45-6789."
    assert step.action == "pass"


def test_flag_leaves_text_untouched_but_notes_it_in_the_detail(monkeypatch):
    _mock_resolution(monkeypatch, input_action="FLAG")

    text, step = pii.redact_pii("My SSN is 123-45-6789.", direction="input")

    assert text == "My SSN is 123-45-6789."
    assert "Flagged for review" in step.detail
    assert "SSN" in step.detail


def test_direction_none_default_never_calls_the_policy_store(monkeypatch):
    def _boom(entity):
        raise AssertionError("resolve_pii_policy must not be called when direction=None")

    monkeypatch.setattr(pii, "resolve_pii_policy", _boom)

    text, step = pii.redact_pii("My SSN is 123-45-6789.")

    assert step.action == "redact"
    assert "[REDACTED_SSN]" in text


# ---- Output-side BLOCK reaches the real pipeline -------------------------


def test_output_block_stops_the_whole_reply(monkeypatch):
    _mock_resolution(monkeypatch, output_action="BLOCK")

    result = run_output_guardrails("Their SSN on file is 123-45-6789.")

    assert result.blocked is True
    assert "123-45-6789" not in result.block_reason
    assert result.blocking_step_name == "pii_redact"


def test_output_mask_redacts_and_continues(monkeypatch):
    _mock_resolution(monkeypatch, output_action="MASK")

    result = run_output_guardrails("Contact them at jane@example.com for details.")

    assert result.blocked is False
    assert "jane@example.com" not in result.text


# ---- Approval-gating, broadened beyond "disable only" --------------------


@pytest.fixture
def make_user():
    created_ids: list[uuid.UUID] = []

    def _make(role: str) -> uuid.UUID:
        email = f"pii-enforcement-test-{uuid.uuid4().hex[:8]}@example.com"
        db = new_session()
        user = UserModel(
            email=email, display_name="PII Enforcement Test User", password_hash=hash_password("Throwaway-Pass-1!"),
            is_active=True, role=role, department="executive",
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


def test_weakening_a_critical_entity_action_queues_approval_not_just_disabling(make_user):
    user_id = make_user("admin")
    db = new_session()
    try:
        user = db.get(UserModel, user_id)
        policy = service.create_policy(
            db, policy_key=f"test.pii.{uuid.uuid4().hex[:8]}", name="API key", description=None, category="PII",
            action="BLOCK",
            configuration={"entity": "API_KEY", "input_action": "BLOCK", "output_action": "BLOCK"},
            priority=100, mode="ENFORCE", created_by=user,
        ).policy
        policy_id = policy.id
        assert policy.enabled is True  # never disabled — the weakening is an action change, not enabled=False

        result = service.update_policy(
            db, policy_id, expected_version=1,
            updates={"configuration": {"entity": "API_KEY", "input_action": "ALLOW", "output_action": "BLOCK"}},
            updated_by=user,
        )

        assert result.policy is None
        assert result.approval is not None
        assert result.approval.payload["approval_reason_code"] == "critical_pii_weakened"

        db.refresh(policy)
        assert policy.configuration["input_action"] == "BLOCK"  # unapplied — still the original
    finally:
        _cleanup(policy_id)


def test_threshold_crossing_below_half_queues_approval(make_user):
    user_id = make_user("admin")
    db = new_session()
    try:
        user = db.get(UserModel, user_id)
        policy = service.create_policy(
            db, policy_key=f"test.injection.{uuid.uuid4().hex[:8]}", name="Injection threshold", description=None,
            category="PROMPT_INJECTION", action="BLOCK", configuration={"threshold": 0.80}, priority=100,
            mode="ENFORCE", created_by=user,
        ).policy
        policy_id = policy.id

        result = service.update_policy(
            db, policy_id, expected_version=1, updates={"configuration": {"threshold": 0.20}}, updated_by=user,
        )

        assert result.policy is None
        assert result.approval is not None
        assert result.approval.payload["approval_reason_code"] == "threshold_weakened"
    finally:
        _cleanup(policy_id)


def test_threshold_staying_above_half_applies_immediately(make_user):
    user_id = make_user("admin")
    db = new_session()
    try:
        user = db.get(UserModel, user_id)
        policy = service.create_policy(
            db, policy_key=f"test.injection.{uuid.uuid4().hex[:8]}", name="Injection threshold", description=None,
            category="PROMPT_INJECTION", action="BLOCK", configuration={"threshold": 0.80}, priority=100,
            mode="ENFORCE", created_by=user,
        ).policy
        policy_id = policy.id

        result = service.update_policy(
            db, policy_id, expected_version=1, updates={"configuration": {"threshold": 0.65}}, updated_by=user,
        )

        assert result.approval is None
        assert result.policy is not None
        assert result.policy.configuration["threshold"] == 0.65
    finally:
        _cleanup(policy_id)


def test_effective_action_is_derived_as_the_worst_of_input_and_output(make_user):
    user_id = make_user("admin")
    db = new_session()
    try:
        user = db.get(UserModel, user_id)
        # Caller-supplied top-level "action" (ALLOW) must be discarded —
        # the derived value (BLOCK, from output_action) is what's stored.
        policy = service.create_policy(
            db, policy_key=f"test.pii.{uuid.uuid4().hex[:8]}", name="SSN", description=None, category="PII",
            action="ALLOW", configuration={"entity": "SSN", "input_action": "REDACT", "output_action": "BLOCK"},
            priority=100, mode="ENFORCE", created_by=user,
        ).policy
        policy_id = policy.id

        assert policy.action == "BLOCK"
    finally:
        _cleanup(policy_id)
