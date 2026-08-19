"""PII_VIEW_RAW: capture opt-in, RBAC enforcement, and the privileged reveal
endpoint. Real Postgres (this suite's established convention), real RBAC
dependency, real audit logger — nothing here re-implements what's under test.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.postgres import get_db, new_session
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.pii_occurrence import PiiOccurrenceModel
from app.models.user import UserModel
from app.routers import pii_access
from app.services.auth.dependencies import get_current_user

# --------------------------------------------------------------------------
# capture mechanism — pure, no app needed
# --------------------------------------------------------------------------

def test_redact_pii_does_not_capture_by_default():
    """capture defaults to None — passing nothing is a strict no-op on THAT
    dimension. direction=None still unconditionally redacts regardless (its
    own long-standing behavior, unrelated to capture) — this asserts only
    that capture itself changed nothing about what gets redacted."""
    from app.services.guardrails.pii import redact_pii

    without_capture, _ = redact_pii("Call me at 555-0142.")
    with_capture, _ = redact_pii("Call me at 555-0142.", capture=[])
    assert without_capture == with_capture


def test_redact_pii_captures_when_a_list_is_passed():
    from app.services.guardrails.pii import redact_pii

    captured = []
    text_out, step = redact_pii("Call me at 555-0142.", direction="output", capture=captured)

    assert step.action == "redact"
    assert len(captured) == 1
    occ = captured[0]
    assert occ.entity_type == "PHONE"
    assert occ.raw_value == "555-0142"
    assert occ.sanitized_value in text_out  # the token actually substituted
    assert occ.detector == "regex"


def test_redact_pii_never_captures_allowed_or_flagged_matches(monkeypatch):
    from app.services.guardrail_policy.pii_policy import PIIPolicyResolution
    from app.services.guardrails import pii

    monkeypatch.setattr(
        pii, "resolve_pii_policy",
        lambda entity, role=None: PIIPolicyResolution(input_action="ALLOW", output_action="ALLOW", enabled=True),
    )
    captured = []
    text_out, step = pii.redact_pii("Call me at 555-0142.", direction="output", capture=captured)

    assert text_out == "Call me at 555-0142."  # ALLOW leaves it untouched
    assert captured == []  # nothing redacted, nothing to capture


def test_gliner_capture_records_the_real_span(monkeypatch):
    from app.services.guardrails import gliner_check

    monkeypatch.setattr(gliner_check, "_config", lambda: {"enabled": True, "labels": ["home address"]})
    monkeypatch.setattr(
        gliner_check, "_get_model",
        lambda name: type("M", (), {"predict_entities": lambda self, text, labels, threshold:
            [{"label": "home address", "text": "42 Baker Street", "start": 11, "end": 26, "score": 0.9}]})(),
    )
    monkeypatch.setattr(gliner_check, "is_vetoed", lambda label, text: False)

    captured = []
    redacted, step = gliner_check.check_with_gliner("I live at 42 Baker Street, London.", capture=captured)

    assert step.action == "redact"
    assert len(captured) == 1
    assert captured[0].raw_value == "42 Baker Street"
    assert captured[0].detector == "gliner"
    assert captured[0].entity_type == "HOME_ADDRESS"
    assert "42 Baker Street" not in redacted


# --------------------------------------------------------------------------
# permission wiring
# --------------------------------------------------------------------------

def test_pii_view_raw_is_admin_only_by_default():
    from app.services.llm_rbac import policy_loader

    for role in ("user", "hr", "project_manager", "ceo"):
        assert "PII_VIEW_RAW" not in policy_loader.role_config(role).granted_permissions, role
    admin = policy_loader.role_config("admin").granted_permissions
    assert "PII_VIEW_RAW" in admin or "*" in admin


# --------------------------------------------------------------------------
# the endpoints, against a real throwaway conversation/message/occurrence
# --------------------------------------------------------------------------

@pytest.fixture
def seeded_occurrence():
    """A real conversation + message + pii_occurrence row, owned by a real
    throwaway user — deleted afterward regardless of outcome. Mirrors
    test_live_chat_flow.py's own throwaway_user convention."""
    db = new_session()
    owner = UserModel(
        email=f"pii-view-{uuid.uuid4().hex[:8]}@example.com", display_name="PII View Test Owner",
        password_hash="x", is_active=True, role="user", department="manufacturing",
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)

    convo = ConversationModel(user_id=owner.id)
    db.add(convo)
    db.commit()
    db.refresh(convo)

    msg = MessageModel(conversation_id=convo.id, role="assistant", content="Call ###0142.", trace=[])
    db.add(msg)
    db.commit()
    db.refresh(msg)

    occ = PiiOccurrenceModel(
        message_id=msg.id, conversation_id=convo.id, direction="output", entity_type="PHONE",
        detector="regex", raw_value="555-0142", sanitized_value="###0142", policy_version=None,
    )
    db.add(occ)
    db.commit()
    db.refresh(occ)

    try:
        yield convo.id, msg.id, occ.id, owner.id
    finally:
        db.execute(text("DELETE FROM pii_occurrences WHERE id = :id"), {"id": occ.id})
        db.execute(text("DELETE FROM messages WHERE id = :id"), {"id": msg.id})
        db.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": convo.id})
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner.id})
        db.commit()
        db.close()


def _make_app(role: str, user_id):
    app = FastAPI()
    app.include_router(pii_access.router)

    fake_user = type("U", (), {"id": user_id, "role": role, "department": "manufacturing", "is_active": True})()
    app.dependency_overrides[get_current_user] = lambda: fake_user

    def _db():
        db = new_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    return app


@pytest.mark.parametrize("role", ["user", "hr", "project_manager", "ceo"])
def test_non_admin_roles_are_denied_the_reveal_endpoint(seeded_occurrence, role):
    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    client = TestClient(_make_app(role, owner_id))

    resp = client.get(f"/admin/traces/{msg_id}/pii/{entity_id}")

    assert resp.status_code == 403


def test_admin_can_reveal_and_it_is_audited(seeded_occurrence):
    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    client = TestClient(_make_app("admin", owner_id))

    resp = client.get(f"/admin/traces/{msg_id}/pii/{entity_id}", params={"reason": "security investigation"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["raw_value"] == "555-0142"
    assert body["entity_type"] == "PHONE"

    db = new_session()
    try:
        row = db.execute(
            text("SELECT event_type, metadata FROM audit_events WHERE resource_id = :id ORDER BY created_at DESC LIMIT 1"),
            {"id": str(entity_id)},
        ).mappings().first()
        assert row is not None
        assert row["event_type"] == "PII_VIEWED"
        assert "555-0142" not in str(row["metadata"])  # raw value never in the audit row
    finally:
        db.close()


def test_list_occurrences_never_includes_the_raw_value(seeded_occurrence):
    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    client = TestClient(_make_app("admin", owner_id))

    resp = client.get(f"/admin/traces/{msg_id}/pii")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert "raw_value" not in body["items"][0]
    assert body["items"][0]["sanitized_value"] == "###0142"


def test_unauthenticated_request_is_rejected(seeded_occurrence):
    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    app = FastAPI()
    app.include_router(pii_access.router)  # no dependency_overrides at all

    client = TestClient(app)
    resp = client.get(f"/admin/traces/{msg_id}/pii/{entity_id}")

    assert resp.status_code in (401, 403)  # get_current_user requires a real bearer token


def test_wrong_message_id_for_a_real_entity_is_404(seeded_occurrence):
    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    client = TestClient(_make_app("admin", owner_id))

    resp = client.get(f"/admin/traces/{uuid.uuid4()}/pii/{entity_id}")

    assert resp.status_code == 404


def test_authorize_conversation_access_gates_the_reveal_endpoint(monkeypatch, seeded_occurrence):
    """PII_VIEW_RAW is Admin-only by default, and Admin is also a broad-
    visibility role (BROAD_CONVERSATION_VISIBILITY_ROLES) — no role in the
    default config can hold the permission but lack ownership visibility, so
    this stubs authorize_conversation_access() directly to prove the reveal
    route actually calls it (raises -> 404), rather than only exercising a
    combination the default config makes unreachable."""
    from app.core.errors import AppError
    from app.routers import pii_access as pii_access_module

    convo_id, msg_id, entity_id, owner_id = seeded_occurrence
    monkeypatch.setattr(
        pii_access_module, "authorize_conversation_access",
        lambda conversation, user: (_ for _ in ()).throw(AppError(404, "conversation_not_found", "x")),
    )
    client = TestClient(_make_app("admin", owner_id))

    resp = client.get(f"/admin/traces/{msg_id}/pii/{entity_id}")

    assert resp.status_code == 404
