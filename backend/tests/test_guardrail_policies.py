"""Guardrail Policy Center — routers/guardrail_policies.py +
services/guardrail_policy/*. Real Postgres session (matches
test_conversations_update.py/test_audit_logging.py's convention) since
optimistic locking, versioning, and JSONB configuration storage are exactly
the DB semantics under test, not something a fake session could faithfully
stand in for.

created_by/updated_by/requested_by/decided_by are all real FKs to users.id
(same gotcha test_conversations_update.py's own docstring calls out), so any
test that actually persists a policy needs a REAL UserModel row via
make_user() below — a bare fake is only safe for a pure-403/pure-validation
test that never reaches a DB insert.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import new_session
from app.models.approval_request import ApprovalRequestModel
from app.models.guardrail_policy import GuardrailPolicyModel, GuardrailPolicyVersionModel
from app.models.user import UserModel
from app.routers import approvals, guardrail_policies
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password
from app.services.guardrail_policy import store


@pytest.fixture
def make_user():
    created_ids: list[uuid.UUID] = []

    def _make(role: str) -> uuid.UUID:
        email = f"guardrail-policy-test-{uuid.uuid4().hex[:8]}@example.com"
        db = new_session()
        user = UserModel(
            email=email, display_name="Guardrail Policy Test User", password_hash=hash_password("Throwaway-Pass-1!"),
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


def _fake_user(user_id: uuid.UUID, role: str):
    return type("FakeUser", (), {"id": user_id, "role": role, "is_active": True, "email": f"{user_id}@example.com"})()


def _client_as(user_id: uuid.UUID, role: str) -> TestClient:
    app = FastAPI()
    app.include_router(guardrail_policies.router)
    app.include_router(approvals.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user(user_id, role)
    return TestClient(app)


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


def _key() -> str:
    return f"test.policy.{uuid.uuid4().hex[:8]}"


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "policy_key": _key(), "name": "Test policy", "category": "REGEX", "action": "BLOCK",
        "configuration": {"pattern": "CONFIDENTIAL-[0-9]+", "entity": "PROJECT_CODE"},
    }
    body.update(overrides)
    response = client.post("/guardrail-policies", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---- RBAC ------------------------------------------------------------


def test_admin_can_create_policy(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        policy = _create(client)
        policy_id = uuid.UUID(policy["id"])
        assert policy["version"] == 1
        assert policy["enabled"] is True
    finally:
        _cleanup(policy_id)


def test_ceo_can_create_policy(make_user):
    client = _client_as(make_user("ceo"), "ceo")
    policy_id = None
    try:
        policy = _create(client)
        policy_id = uuid.UUID(policy["id"])
        assert policy["version"] == 1
    finally:
        _cleanup(policy_id)


def test_employee_cannot_create_policy():
    client = _client_as(uuid.uuid4(), "user")

    response = client.post(
        "/guardrail-policies",
        json={
            "policy_key": _key(), "name": "x", "category": "REGEX", "action": "BLOCK",
            "configuration": {"pattern": "abc", "entity": "X"},
        },
    )

    assert response.status_code == 403


def test_employee_cannot_list_policies():
    client = _client_as(uuid.uuid4(), "user")

    response = client.get("/guardrail-policies")

    assert response.status_code == 403


# ---- Validation (never reaches the DB, no real user needed) ------------


def test_invalid_semantic_threshold_above_one_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={"policy_key": _key(), "name": "x", "category": "SEMANTIC", "action": "BLOCK", "configuration": {"threshold": 1.5}},
    )

    assert response.status_code == 422


def test_invalid_semantic_threshold_negative_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={"policy_key": _key(), "name": "x", "category": "SEMANTIC", "action": "BLOCK", "configuration": {"threshold": -0.1}},
    )

    assert response.status_code == 422


def test_invalid_semantic_threshold_nan_rejected():
    # httpx's own JSON encoder refuses to serialize a bare NaN float at all
    # (raises client-side before a request is even sent) — so this exercises
    # the validation layer directly instead of round-tripping through HTTP,
    # which is also the more precise place to assert this particular rule.
    from app.core.errors import AppError
    from app.services.guardrail_policy.validation import validate_configuration

    with pytest.raises(AppError):
        validate_configuration("SEMANTIC", {"threshold": float("nan")})


def test_malformed_regex_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={
            "policy_key": _key(), "name": "x", "category": "REGEX", "action": "BLOCK",
            "configuration": {"pattern": "(unclosed", "entity": "X"},
        },
    )

    assert response.status_code == 422


def test_catastrophic_backtracking_regex_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={
            "policy_key": _key(), "name": "x", "category": "REGEX", "action": "BLOCK",
            "configuration": {"pattern": "(a+)+$", "entity": "X"},
        },
    )

    assert response.status_code == 422


def test_oversized_regex_pattern_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={
            "policy_key": _key(), "name": "x", "category": "REGEX", "action": "BLOCK",
            "configuration": {"pattern": "a" * 500, "entity": "X"},
        },
    )

    assert response.status_code == 422


def test_unknown_category_rejected():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies",
        json={"policy_key": _key(), "name": "x", "category": "NOT_A_CATEGORY", "action": "BLOCK", "configuration": {}},
    )

    assert response.status_code == 422


# ---- Optimistic locking / versioning ------------------------------------


def test_update_with_stale_version_is_rejected(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        policy = _create(client)
        policy_id = uuid.UUID(policy["id"])

        first = client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "priority": 50})
        assert first.status_code == 200
        assert first.json()["status"] == "applied"

        stale = client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "priority": 75})

        assert stale.status_code == 409
    finally:
        _cleanup(policy_id)


def test_update_creates_a_version_row_and_bumps_version(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        policy = _create(client)
        policy_id = uuid.UUID(policy["id"])

        response = client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "priority": 42})
        assert response.status_code == 200
        assert response.json()["policy"]["version"] == 2

        versions = client.get(f"/guardrail-policies/{policy_id}/versions")
        assert versions.status_code == 200
        version_numbers = [v["version"] for v in versions.json()]
        assert version_numbers == [2, 1]  # newest first, nothing overwritten
    finally:
        _cleanup(policy_id)


def test_rollback_creates_a_new_version_copying_the_target_forward(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        policy = _create(client, configuration={"pattern": "ORIGINAL-[0-9]+", "entity": "ORIGINAL"})
        policy_id = uuid.UUID(policy["id"])
        client.patch(
            f"/guardrail-policies/{policy_id}",
            json={"expected_version": 1, "configuration": {"pattern": "CHANGED-[0-9]+", "entity": "CHANGED"}},
        )

        response = client.post(
            f"/guardrail-policies/{policy_id}/rollback", json={"expected_version": 2, "target_version": 1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 3  # rollback is a NEW version, v1 itself is untouched
        assert body["configuration"]["entity"] == "ORIGINAL"

        versions = client.get(f"/guardrail-policies/{policy_id}/versions").json()
        assert len(versions) == 3
    finally:
        _cleanup(policy_id)


# ---- Approval gating for critical PII disables --------------------------


def test_disabling_a_critical_pii_entity_queues_an_approval_instead_of_applying(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        policy = _create(
            client, category="PII", action="BLOCK",
            configuration={"entity": "PASSWORD", "severity": "CRITICAL", "input_action": "BLOCK", "output_action": "BLOCK"},
        )
        policy_id = uuid.UUID(policy["id"])
        assert policy["enabled"] is True

        response = client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "enabled": False})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["approval_id"] is not None

        still_enabled = client.get(f"/guardrail-policies/{policy_id}")
        assert still_enabled.json()["enabled"] is True  # not applied yet
        assert still_enabled.json()["version"] == 1
    finally:
        _cleanup(policy_id)


def test_approving_a_critical_pii_disable_applies_it(make_user):
    admin_client = _client_as(make_user("admin"), "admin")
    ceo_client = _client_as(make_user("ceo"), "ceo")
    policy_id = None
    try:
        policy = _create(
            admin_client, category="PII", action="BLOCK",
            configuration={"entity": "API_KEY", "severity": "CRITICAL", "input_action": "BLOCK", "output_action": "BLOCK"},
        )
        policy_id = uuid.UUID(policy["id"])
        queued = admin_client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "enabled": False})
        approval_id = queued.json()["approval_id"]

        decision = ceo_client.post(f"/approvals/{approval_id}/decide", json={"decision": "approved"})

        assert decision.status_code == 200
        applied = admin_client.get(f"/guardrail-policies/{policy_id}")
        assert applied.json()["enabled"] is False
        assert applied.json()["version"] == 2
    finally:
        _cleanup(policy_id)


def test_rejecting_a_critical_pii_disable_leaves_it_unchanged(make_user):
    admin_client = _client_as(make_user("admin"), "admin")
    ceo_client = _client_as(make_user("ceo"), "ceo")
    policy_id = None
    try:
        policy = _create(
            admin_client, category="PII", action="BLOCK",
            configuration={"entity": "SECRET", "severity": "CRITICAL", "input_action": "BLOCK", "output_action": "BLOCK"},
        )
        policy_id = uuid.UUID(policy["id"])
        queued = admin_client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "enabled": False})
        approval_id = queued.json()["approval_id"]

        decision = ceo_client.post(f"/approvals/{approval_id}/decide", json={"decision": "rejected"})

        assert decision.status_code == 200
        unchanged = admin_client.get(f"/guardrail-policies/{policy_id}")
        assert unchanged.json()["enabled"] is True
        assert unchanged.json()["version"] == 1
    finally:
        _cleanup(policy_id)


# ---- Runtime enforcement / cache invalidation ---------------------------


def test_new_regex_rule_is_enforced_without_a_restart(make_user):
    from app.services.guardrails.custom_regex_check import check_custom_regex

    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        marker = f"MARKER-{uuid.uuid4().hex[:8]}"
        policy = _create(client, configuration={"pattern": marker, "entity": "TEST_MARKER"})
        policy_id = uuid.UUID(policy["id"])

        step = check_custom_regex(f"here is the {marker} value")

        assert step.action == "block"
        assert policy["name"] in step.detail
    finally:
        _cleanup(policy_id)


def test_disabled_regex_rule_does_not_block(make_user):
    from app.services.guardrails.custom_regex_check import check_custom_regex

    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        marker = f"MARKER-{uuid.uuid4().hex[:8]}"
        policy = _create(client, configuration={"pattern": marker, "entity": "TEST_MARKER"})
        policy_id = uuid.UUID(policy["id"])
        client.patch(f"/guardrail-policies/{policy_id}", json={"expected_version": 1, "enabled": False})

        step = check_custom_regex(f"here is the {marker} value")

        assert step.action == "pass"
    finally:
        _cleanup(policy_id)


def test_word_rule_word_mode_does_not_match_a_longer_word(make_user):
    from app.services.guardrails.custom_word_check import check_custom_word

    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        word = f"blockedword{uuid.uuid4().hex[:6]}"
        policy = _create(
            client, category="WORD_FILTER", configuration={"word": word, "match_mode": "WORD"},
        )
        policy_id = uuid.UUID(policy["id"])

        exact = check_custom_word(f"this message contains {word} directly")
        longer = check_custom_word(f"this message contains {word}suffix instead")

        assert exact.action == "block"
        assert longer.action == "pass"  # \b word-boundary matching — no "admin" blocking "administrator"
    finally:
        _cleanup(policy_id)


# ---- Test playground (never persists, no real user needed) --------------


def test_playground_detects_a_regex_match():
    client = _client_as(uuid.uuid4(), "admin")

    response = client.post(
        "/guardrail-policies/test",
        json={
            "category": "REGEX", "configuration": {"pattern": "SSN-[0-9]{4}", "entity": "SSN"}, "action": "BLOCK",
            "sample_text": "reference number SSN-1234 on file",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detected"] is True
    assert body["action"] == "BLOCK"
    assert body["risk_level"] == "HIGH"


def test_playground_does_not_persist_anything():
    client = _client_as(uuid.uuid4(), "admin")
    before = client.get("/guardrail-policies").json()["total"]

    client.post(
        "/guardrail-policies/test",
        json={
            "category": "WORD_FILTER", "configuration": {"word": "test", "match_mode": "WORD"}, "action": "BLOCK",
            "sample_text": "this is a test message",
        },
    )

    after = client.get("/guardrail-policies").json()["total"]
    assert after == before


# ---- Uniqueness -----------------------------------------------------


def test_policy_key_must_be_unique(make_user):
    client = _client_as(make_user("admin"), "admin")
    policy_id = None
    try:
        key = _key()
        policy = _create(client, policy_key=key)
        policy_id = uuid.UUID(policy["id"])

        response = client.post(
            "/guardrail-policies",
            json={
                "policy_key": key, "name": "dup", "category": "REGEX", "action": "BLOCK",
                "configuration": {"pattern": "abc", "entity": "X"},
            },
        )

        assert response.status_code == 409
    finally:
        _cleanup(policy_id)
