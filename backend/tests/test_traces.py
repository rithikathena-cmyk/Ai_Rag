"""routers/traces.py — the Traces sidebar page's list endpoint. Every
authenticated role can call it, but what comes back is scoped by that
role's own visibility: VIEW_AUDIT_LOGS roles (CEO/Admin) keep the original
org-wide view; every other role is hard-scoped to conversations they own,
regardless of what role/user_id/department filters they pass. Real Postgres
session (matches test_activity_audit_logging.py's convention for this kind
of durable-state test), since this endpoint reads real
messages/conversations/users rows, not something worth mocking.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import new_session
from app.models.conversation import ConversationModel
from app.models.message import MessageModel
from app.models.user import UserModel
from app.routers import traces
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password

BLOCKED_TRACE = [
    {"agent": "Access", "tool": "authorization", "input": None, "summary": "pass: Employee role permitted to use this assistant (manufacturing)"},
    {"agent": "Guardrails", "tool": "scope_semantic_check", "input": None, "summary": "block: This question is outside the supported enterprise knowledge scope"},
]


def _client_as(role: str, user_id: uuid.UUID | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(traces.router)
    fake_user = SimpleNamespace(id=user_id or uuid.uuid4(), role=role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return TestClient(app)


def _make_user_with_conversation(role: str = "user", department: str = "manufacturing"):
    email = f"traces-test-{uuid.uuid4().hex[:8]}@example.com"
    db = new_session()
    user = UserModel(
        email=email, display_name="Traces Test User", password_hash=hash_password("Throwaway-Pass-1!"),
        is_active=True, role=role, department=department,
    )
    db.add(user)
    db.commit()
    owner_id = user.id

    conv = ConversationModel(user_id=owner_id, title="Traces test conversation")
    db.add(conv)
    db.commit()
    conversation_id = conv.id

    user_msg = MessageModel(conversation_id=conversation_id, role="user", content="What is the temperature of Chennai?")
    db.add(user_msg)
    db.commit()
    assistant_msg = MessageModel(
        conversation_id=conversation_id, role="assistant",
        content="That request is outside the enterprise knowledge scope this assistant supports.",
        trace=BLOCKED_TRACE,
    )
    db.add(assistant_msg)
    db.commit()
    message_id = assistant_msg.id
    db.close()
    return owner_id, conversation_id, message_id


def _cleanup(owner_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    db = new_session()
    db.query(MessageModel).filter(MessageModel.conversation_id == conversation_id).delete()
    db.query(ConversationModel).filter(ConversationModel.id == conversation_id).delete()
    db.query(UserModel).filter(UserModel.id == owner_id).delete()
    db.commit()
    db.close()


@pytest.fixture
def seeded_conversation():
    owner_id, conversation_id, message_id = _make_user_with_conversation()
    try:
        yield owner_id, conversation_id, message_id
    finally:
        _cleanup(owner_id, conversation_id)


# ---- Access — every role can call this endpoint now ----------------------


def test_privileged_role_can_list_traces():
    client = _client_as(role="admin")

    response = client.get("/traces")

    assert response.status_code == 200


def test_non_privileged_role_can_also_list_traces():
    """The key behavior change: a plain Employee role used to get a 403 here
    (router-level VIEW_AUDIT_LOGS gate) — now it gets 200, scoped to their
    own history (see the self-scoping tests below), not a permission error."""
    client = _client_as(role="user")

    response = client.get("/traces")

    assert response.status_code == 200


# ---- Self-scoping for non-privileged roles --------------------------------


def test_non_privileged_role_sees_their_own_trace():
    owner_id, _conversation_id, message_id = _make_user_with_conversation(role="user")
    try:
        client = _client_as(role="user", user_id=owner_id)

        response = client.get("/traces")

        assert response.status_code == 200
        items = response.json()["items"]
        assert any(i["message_id"] == str(message_id) for i in items)
    finally:
        _cleanup(owner_id, _conversation_id)


def test_non_privileged_role_never_sees_another_users_trace():
    owner_id, conversation_id, message_id = _make_user_with_conversation(role="user")
    try:
        other_caller_id = uuid.uuid4()  # a different, unrelated caller
        client = _client_as(role="user", user_id=other_caller_id)

        response = client.get("/traces")

        assert response.status_code == 200
        items = response.json()["items"]
        assert not any(i["message_id"] == str(message_id) for i in items)
    finally:
        _cleanup(owner_id, conversation_id)


def test_non_privileged_role_cannot_use_user_id_filter_to_see_someone_elses_trace():
    """The actual security property: even if a non-privileged caller
    explicitly asks for someone else's user_id via the query string, the
    endpoint ignores that filter in favor of the hard self-scope — this
    isn't just "the UI doesn't expose it," the server enforces it."""
    owner_id, conversation_id, message_id = _make_user_with_conversation(role="user")
    try:
        other_caller_id = uuid.uuid4()
        client = _client_as(role="user", user_id=other_caller_id)

        response = client.get("/traces", params={"user_id": str(owner_id)})

        assert response.status_code == 200
        items = response.json()["items"]
        assert not any(i["message_id"] == str(message_id) for i in items)
    finally:
        _cleanup(owner_id, conversation_id)


def test_non_privileged_role_cannot_use_role_filter_to_see_other_roles_traces():
    owner_id, conversation_id, message_id = _make_user_with_conversation(role="user")
    try:
        other_caller_id = uuid.uuid4()
        client = _client_as(role="user", user_id=other_caller_id)

        response = client.get("/traces", params={"role": "user"})

        assert response.status_code == 200
        items = response.json()["items"]
        assert not any(i["message_id"] == str(message_id) for i in items)
    finally:
        _cleanup(owner_id, conversation_id)


def test_non_privileged_role_gets_raw_unsanitized_detail_for_their_own_trace():
    """Own-history access still includes the real, unsanitized check detail
    (classifier score included) — chosen deliberately: it's the caller's
    own data, not someone else's, so the same protection the chat panel
    applies to OTHER people's requests doesn't apply here."""
    owner_id, conversation_id, message_id = _make_user_with_conversation(role="user")
    try:
        client = _client_as(role="user", user_id=owner_id)

        response = client.get("/traces")

        item = next(i for i in response.json()["items"] if i["message_id"] == str(message_id))
        scope_step = next(s for s in item["trace"] if s["tool"] == "scope_semantic_check")
        assert scope_step["summary"] == "block: This question is outside the supported enterprise knowledge scope"
    finally:
        _cleanup(owner_id, conversation_id)


# ---- Privileged (VIEW_AUDIT_LOGS) roles keep org-wide visibility ---------


def test_privileged_role_sees_other_users_trace(seeded_conversation):
    owner_id, _conversation_id, message_id = seeded_conversation
    client = _client_as(role="admin")

    response = client.get("/traces", params={"user_id": str(owner_id)})

    assert response.status_code == 200
    assert any(i["message_id"] == str(message_id) for i in response.json()["items"])


def test_privileged_role_filter_by_role_still_works(seeded_conversation):
    owner_id, _conversation_id, message_id = seeded_conversation
    client = _client_as(role="admin")

    matching = client.get("/traces", params={"user_id": str(owner_id), "role": "user"})
    non_matching = client.get("/traces", params={"user_id": str(owner_id), "role": "hr"})

    assert any(i["message_id"] == str(message_id) for i in matching.json()["items"])
    assert not any(i["message_id"] == str(message_id) for i in non_matching.json()["items"])


def test_blocked_filter_narrows_results(seeded_conversation):
    owner_id, _conversation_id, message_id = seeded_conversation
    client = _client_as(role="admin")

    blocked_only = client.get("/traces", params={"user_id": str(owner_id), "blocked": "true"})
    allowed_only = client.get("/traces", params={"user_id": str(owner_id), "blocked": "false"})

    assert any(i["message_id"] == str(message_id) for i in blocked_only.json()["items"])
    assert not any(i["message_id"] == str(message_id) for i in allowed_only.json()["items"])


def test_lists_the_seeded_trace_with_the_preceding_question(seeded_conversation):
    owner_id, _conversation_id, message_id = seeded_conversation
    client = _client_as(role="admin")

    response = client.get("/traces", params={"user_id": str(owner_id)})

    item = next(i for i in response.json()["items"] if i["message_id"] == str(message_id))
    assert item["question"] == "What is the temperature of Chennai?"
    assert item["role"] == "user"
    assert item["department"] == "manufacturing"
    assert item["trace"] == BLOCKED_TRACE


# ---- Pagination ------------------------------------------------------


def test_limit_is_hard_capped_server_side(monkeypatch):
    monkeypatch.setattr(traces, "_MAX_LIMIT", 5)
    client = _client_as(role="admin")

    response = client.get("/traces", params={"limit": 9999})

    assert response.status_code == 200
    assert len(response.json()["items"]) <= 5
