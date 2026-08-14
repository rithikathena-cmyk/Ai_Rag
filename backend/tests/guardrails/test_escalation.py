"""services/guardrails/escalation.py — repeated-block lockout. Mirrors
tests/llm_rbac/test_rate_limiter.py's convention for controlling time
(monkeypatch escalation.time.monotonic) rather than sleeping in tests.
"""

import uuid

import pytest

from app.core.errors import AppError
from app.services.guardrails import escalation


def _cfg(**overrides):
    base = {"enabled": True, "block_threshold": 5, "window_seconds": 600, "lockout_seconds": 300}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_state():
    escalation._BLOCK_TIMESTAMPS.clear()
    escalation._LOCKOUT_UNTIL.clear()
    yield
    escalation._BLOCK_TIMESTAMPS.clear()
    escalation._LOCKOUT_UNTIL.clear()


def test_disabled_is_a_no_op(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg(enabled=False)})
    user_id = uuid.uuid4()

    for _ in range(20):
        escalation.record_block(user_id)
    escalation.check_escalation(user_id)  # must never raise


def test_below_threshold_does_not_lock_out(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg()})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    user_id = uuid.uuid4()

    for _ in range(4):  # threshold is 5
        escalation.record_block(user_id)

    escalation.check_escalation(user_id)  # must not raise


def test_threshold_blocks_triggers_lockout(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg()})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    user_id = uuid.uuid4()

    for _ in range(5):
        escalation.record_block(user_id)

    with pytest.raises(AppError) as exc_info:
        escalation.check_escalation(user_id)
    assert exc_info.value.status_code == 429
    assert exc_info.value.code == "guardrail_escalation_lockout"


def test_lockout_expires_after_lockout_seconds(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg(lockout_seconds=60)})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    user_id = uuid.uuid4()

    for _ in range(5):
        escalation.record_block(user_id)
    with pytest.raises(AppError):
        escalation.check_escalation(user_id)

    now[0] += 61  # past the lockout window
    escalation.check_escalation(user_id)  # must not raise anymore


def test_blocks_outside_the_rolling_window_do_not_count(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg(window_seconds=60)})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    user_id = uuid.uuid4()

    for _ in range(4):
        escalation.record_block(user_id)

    now[0] += 61  # every prior block ages out of the window
    escalation.record_block(user_id)  # only 1 block within the current window

    escalation.check_escalation(user_id)  # must not raise — never reached the threshold within any window


def test_different_users_tracked_independently(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg()})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    locked_out_user, other_user = uuid.uuid4(), uuid.uuid4()

    for _ in range(5):
        escalation.record_block(locked_out_user)

    with pytest.raises(AppError):
        escalation.check_escalation(locked_out_user)
    escalation.check_escalation(other_user)  # must not raise — a different user's blocks are unrelated


def test_lockout_message_includes_remaining_seconds(monkeypatch):
    monkeypatch.setattr(escalation, "load_yaml_config", lambda name: {"escalation": _cfg(lockout_seconds=120)})
    now = [1000.0]
    monkeypatch.setattr(escalation.time, "monotonic", lambda: now[0])
    user_id = uuid.uuid4()

    for _ in range(5):
        escalation.record_block(user_id)

    with pytest.raises(AppError) as exc_info:
        escalation.check_escalation(user_id)
    assert "seconds" in str(exc_info.value.detail)
    assert any(str(n) in str(exc_info.value.detail) for n in (119, 120, 121))
