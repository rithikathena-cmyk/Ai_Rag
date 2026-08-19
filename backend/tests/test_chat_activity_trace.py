"""User-facing Security & Activity panel — the real trace chat.py now builds
(with the new Access/Model steps) and persists on the assistant message so it
survives a conversation reload. Covers exactly the two things that were
genuinely missing before this pass (see the approved plan): (1) an
Authorization step recording the real authorize_llm_request() decision
instead of a frontend-hardcoded fake "Access verified", and (2) trace
persistence + round-trip through GET /conversations/{id} — previously the
trace only existed in the live POST /chat response and vanished on reload.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db, new_session
from app.gateway.schemas import ModelTier
from app.models.conversation import ConversationModel
from app.models.user import UserModel
from app.routers import chat as chat_router
from app.routers import conversations as conversations_router
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password
from app.services.memory.store import add_message


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()


def _make_chat_app(monkeypatch):
    app = FastAPI()
    app.include_router(chat_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role="user", department="manufacturing", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    def _fake_get_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_get_db

    fake_decision = SimpleNamespace(
        allowed=True, role="user", department="manufacturing", model_tier=ModelTier.HAIKU,
        allowed_tools=frozenset(), sql_allowed_tables=frozenset(), knowledge_departments=("manufacturing",),
        max_concurrent_requests=None, requires_approval=False,
    )
    monkeypatch.setattr(chat_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(chat_router, "get_conversation", lambda db, cid: _FakeConversation())
    monkeypatch.setattr(chat_router, "create_conversation", lambda db, user_id: _FakeConversation())
    monkeypatch.setattr(chat_router, "build_context", lambda db, cid: (None, []))
    monkeypatch.setattr(chat_router, "get_preferences", lambda db, uid: {})
    monkeypatch.setattr(chat_router, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_router, "run_agent", lambda *a, **k: pytest.fail("run_agent must not be called on a blocked turn")
    )

    persisted = []
    monkeypatch.setattr(
        chat_router, "add_message",
        lambda db, conv_id, *, role, content, sources=None, report=None, trace=None: (
            persisted.append({"role": role, "content": content, "trace": trace}),
            SimpleNamespace(id=uuid.uuid4()),
        )[-1],
    )
    # Force a deterministic input-guardrail block (length check — cheapest,
    # no model loading) so this test isolates trace assembly, not guardrail
    # detection behavior itself.
    monkeypatch.setattr(settings, "guardrail_max_input_chars", 5)

    return TestClient(app), persisted


def test_blocked_turn_trace_starts_with_a_real_access_step(monkeypatch):
    client, persisted = _make_chat_app(monkeypatch)

    response = client.post("/chat", json={"message": "this message is over five characters"})

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace[0]["agent"] == "Access"
    assert trace[0]["tool"] == "authorization"
    assert trace[0]["summary"] == "pass: Employee role permitted to use this assistant (manufacturing)"
    # Not fabricated on the frontend — the real step the backend actually
    # recorded is what gets persisted alongside the blocked reply too.
    assistant_msg = next(p for p in persisted if p["role"] == "assistant")
    assert assistant_msg["trace"][0]["agent"] == "Access"


def test_blocked_turn_never_reaches_run_agent_and_trace_stops_there(monkeypatch):
    client, _persisted = _make_chat_app(monkeypatch)

    response = client.post("/chat", json={"message": "this message is over five characters"})

    trace = response.json()["trace"]
    # Access + length_check only — nothing from a "Model"/"Retrieval Agent"
    # step, since run_agent() (and therefore call_model()) never ran.
    assert [step["agent"] for step in trace] == ["Access", "Guardrails"]
    assert "block" in trace[-1]["summary"]


@pytest.fixture
def owned_conversation():
    email = f"activity-trace-test-{uuid.uuid4().hex[:8]}@example.com"
    db = new_session()
    user = UserModel(
        email=email, display_name="Activity Trace Test User", password_hash=hash_password("Throwaway-Pass-1!"),
        is_active=True, role="user", department="manufacturing",
    )
    db.add(user)
    db.commit()
    owner_id = user.id

    row = ConversationModel(user_id=owner_id, title="Trace persistence test")
    db.add(row)
    db.commit()
    conversation_id = row.id
    db.close()
    try:
        yield owner_id, conversation_id
    finally:
        db = new_session()
        db.query(ConversationModel).filter(ConversationModel.id == conversation_id).delete()
        db.query(UserModel).filter(UserModel.id == owner_id).delete()
        db.commit()
        db.close()


def _conversations_client_as(user_id: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(conversations_router.router)
    fake_user = SimpleNamespace(id=user_id, role="user", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return TestClient(app)


def test_persisted_trace_round_trips_through_conversation_history(owned_conversation):
    owner_id, conversation_id = owned_conversation
    sample_trace = [
        {"agent": "Access", "tool": "authorization", "input": None, "summary": "pass: Employee role permitted to use this assistant (manufacturing)"},
        {"agent": "Guardrails", "tool": "prompt_injection_check", "input": None, "summary": "pass: No injection patterns matched"},
    ]

    db = new_session()
    try:
        add_message(db, conversation_id, role="user", content="Hello")
        add_message(db, conversation_id, role="assistant", content="Hi there", trace=sample_trace)
    finally:
        db.close()

    client = _conversations_client_as(owner_id)
    response = client.get(f"/conversations/{conversation_id}")

    assert response.status_code == 200
    messages = response.json()["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    assistant_msg = next(m for m in messages if m["role"] == "assistant")
    assert user_msg["trace"] is None
    assert assistant_msg["trace"] == sample_trace


def test_add_message_without_trace_still_defaults_to_null(owned_conversation):
    _owner_id, conversation_id = owned_conversation
    db = new_session()
    try:
        msg = add_message(db, conversation_id, role="user", content="No trace here")
    finally:
        db.close()

    assert msg.trace is None
