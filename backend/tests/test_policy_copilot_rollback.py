"""Copilot rollback — ROLLBACK_POLICY was interpreted and validated but
never actually applied: apply_proposal()'s changes loop is a no-op for a
rollback (target_version lives outside `changes`, which is empty for this
intent), and the approve route's own `if not changes: raise 422` guard
meant a rollback proposal could never even reach apply. Fixed by giving
rollback its own apply path that delegates to guardrail_policy/service.py's
EXISTING rollback_policy() — the same function the Policy Center's manual
rollback button already uses.
"""

import pytest

from app.core.errors import AppError
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.validation import validate


def test_rollback_is_interpreted_and_validated():
    intent = interpret("rollback phone policy to version 3")

    assert intent.intent.value == "ROLLBACK_POLICY"
    assert intent.entity == "PHONE"
    assert intent.target_version == 3

    result = validate(intent, role="admin")
    assert result.valid, result.errors


def test_rollback_without_a_version_is_never_silently_guessed(monkeypatch):
    """The interpreter's rollback pattern requires an explicit version
    number (pre-existing behavior, unrelated to this fix) — without one, the
    request isn't recognized as ROLLBACK_POLICY at all and falls through to
    Stage 3 (the LLM fallback), which has no field for a guessed
    target_version either, so this stays CLARIFICATION_NEEDED, which
    validate() also refuses. Either way, nothing here ever picks a
    target_version on its own. The gateway is blocked below purely so this
    stays a deterministic, offline test — not because the outcome depends
    on it; a real reply would produce the identical result."""
    from app.gateway.claude_gateway import GenerationError, claude_gateway
    from app.gateway.schemas import GenerationErrorReason

    monkeypatch.setattr(
        claude_gateway, "generate",
        lambda *a, **kw: (_ for _ in ()).throw(GenerationError("blocked", reason=GenerationErrorReason.NO_API_KEY)),
    )

    intent = interpret("rollback the phone policy")

    result = validate(intent, role="admin")

    assert not result.valid
    assert intent.target_version is None


def test_proposal_payload_carries_the_entity_for_rollback(monkeypatch):
    """This is the field that was missing entirely before this fix — without
    it, apply_rollback() has no way to know WHICH policy row to roll back,
    since ROLLBACK_POLICY's `changes` list is always empty."""
    from types import SimpleNamespace

    from app.models.approval_request import ApprovalRequestModel
    from app.services.policy_copilot import service as copilot_service

    captured = {}

    class _FakeDB:
        def add(self, obj):
            captured["proposal"] = obj

        def commit(self):
            pass

        def refresh(self, obj):
            obj.id = "fake-id"

    user = SimpleNamespace(id="admin-id", role="admin")
    monkeypatch.setattr(
        copilot_service, "validate",
        lambda intent, role: SimpleNamespace(valid=True, errors=[], warnings=[], requires_approval=True),
    )

    result = copilot_service.handle("rollback phone policy to version 3", user=user, db=_FakeDB())

    assert captured["proposal"].payload["entity"] == "PHONE"
    assert captured["proposal"].payload["target_version"] == 3
    assert result.reply  # a real reply was produced, not an exception


def test_apply_rollback_delegates_to_the_existing_rollback_service(monkeypatch):
    """No new versioning/history logic — this only resolves entity -> row
    and calls the same rollback_policy() the Policy Center's own manual
    rollback button already uses."""
    from types import SimpleNamespace

    from app.services.policy_copilot import apply as apply_module

    fake_row = SimpleNamespace(id="row-id", version=5)
    monkeypatch.setattr(apply_module, "_find_row", lambda db, entity: fake_row)

    calls = []

    def _fake_rollback(db, policy_id, *, expected_version, target_version, changed_by):
        calls.append((policy_id, expected_version, target_version))
        return SimpleNamespace(configuration={"input_action": "MASK"}, policy_key="pii.phone", version=6)

    monkeypatch.setattr(apply_module.policy_service, "rollback_policy", _fake_rollback)
    monkeypatch.setattr(apply_module.store, "invalidate", lambda: None)

    approver = SimpleNamespace(id="admin-id", role="admin")
    result = apply_module.apply_rollback(
        db=None, entity="PHONE", target_version=3, approver=approver, reason="test",
    )

    assert calls == [("row-id", 5, 3)]
    assert result.operation == "rolled_back"
    assert result.version == 6


def test_the_approve_route_recognizes_the_real_stored_intent_value():
    """Caught live, not by a unit test: the router compared payload['intent']
    against the lowercase string 'rollback_policy', but service.py stores
    intent.intent.value verbatim — IntentType.ROLLBACK_POLICY.value is the
    UPPERCASE 'ROLLBACK_POLICY' (see schemas.py's enum definition). The
    mismatch meant every rollback proposal fell through to the 'no changes'
    422, silently, even after apply_rollback() itself was correct in
    isolation. This asserts the router's literal against the real enum
    value, not a guessed string, so a future rename can't reopen the gap."""
    from app.services.policy_copilot.schemas import IntentType

    import app.routers.policy_copilot as router_module
    import inspect

    source = inspect.getsource(router_module.approve_proposal)
    assert f'"{IntentType.ROLLBACK_POLICY.value}"' in source


def test_apply_rollback_404s_when_no_policy_row_exists(monkeypatch):
    from app.services.policy_copilot import apply as apply_module

    monkeypatch.setattr(apply_module, "_find_row", lambda db, entity: None)

    with pytest.raises(AppError) as exc_info:
        apply_module.apply_rollback(db=None, entity="PHONE", target_version=1, approver=None, reason=None)

    assert exc_info.value.status_code == 404
