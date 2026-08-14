"""routers/chat.py's degraded-response path — proves the fix for the "no AI
model configured" bug: every GenerationError used to collapse into the same
hardcoded message regardless of what actually happened (missing key,
admin-disabled model, provider auth failure, provider rate-limited, or a
completely unrelated bug elsewhere in the agent loop). Now GenerationError
carries a `reason` (gateway/schemas.py::GenerationErrorReason), chat.py
forwards it to run_retrieval_fallback(), and ChatResponse.degraded_reason
lets the client show an accurate, safe message per reason.

Same lightweight-app + dependency_overrides convention as
test_search_pii_redaction.py: a real FastAPI app with just chat.router,
current_user/db dependencies overridden, and every I/O boundary the route
touches (RBAC decision, conversation/memory store, retrieval) monkeypatched
at the names chat.py imported them under. run_retrieval_fallback itself is
left REAL (only planner.search_documents underneath it is stubbed) so this
exercises the actual reason -> message mapping, not a re-implementation of it.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerationErrorReason, ModelTier
from app.routers import chat as chat_router
from app.services.auth.dependencies import get_current_user


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()


def _make_app(monkeypatch, *, run_agent_error: GenerationError | None = None, search_hits: list[dict] | None = None):
    app = FastAPI()
    app.include_router(chat_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role="user", department="engineering", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    def _fake_get_db():
        yield object()  # never touched — every DB-facing call below is mocked

    app.dependency_overrides[get_db] = _fake_get_db

    fake_decision = SimpleNamespace(
        allowed=True, role="user", department="engineering", model_tier=ModelTier.HAIKU,
        allowed_tools=frozenset(), sql_allowed_tables=frozenset(), knowledge_departments=("engineering",),
        max_concurrent_requests=None, requires_approval=False,
    )
    monkeypatch.setattr(chat_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(chat_router, "get_conversation", lambda db, cid: _FakeConversation())
    monkeypatch.setattr(chat_router, "create_conversation", lambda db, user_id: _FakeConversation())
    monkeypatch.setattr(chat_router, "build_context", lambda db, cid: (None, []))
    monkeypatch.setattr(chat_router, "get_preferences", lambda db, uid: {})
    monkeypatch.setattr(chat_router, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(chat_router, "maybe_summarize", lambda *a, **k: None)

    if run_agent_error is not None:
        def _raise(*a, **k):
            raise run_agent_error
        monkeypatch.setattr(chat_router, "run_agent", _raise)
    else:
        monkeypatch.setattr(
            chat_router, "run_agent",
            lambda *a, **k: SimpleNamespace(
                reply="a synthesized answer", sources=[], report=None, trace=[], degraded_reason=None
            ),
        )

    # run_retrieval_fallback is left REAL — only its own search_documents
    # dependency (inside services/agents/planner.py) is stubbed, so the
    # reason -> message mapping under test actually runs.
    from app.services.agents import planner
    monkeypatch.setattr(planner, "search_documents", lambda db, **k: search_hits or [])

    return TestClient(app)


@pytest.fixture(autouse=True)
def _guardrails_off():
    original = settings.guardrails_enabled
    settings.guardrails_enabled = False
    yield
    settings.guardrails_enabled = original


def test_successful_generation_is_not_degraded_and_has_no_reason(monkeypatch):
    client = _make_app(monkeypatch)

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    assert body["degraded_reason"] is None
    assert body["reply"] == "a synthesized answer"


@pytest.mark.parametrize(
    "reason, expected_phrase",
    [
        (GenerationErrorReason.NO_API_KEY, "no AI model is configured"),
        (GenerationErrorReason.MODEL_DISABLED, "temporarily disabled by an administrator"),
        (GenerationErrorReason.AUTH_FAILED, "rejected our credentials"),
        (GenerationErrorReason.PROVIDER_UNAVAILABLE, "temporarily unavailable"),
        (GenerationErrorReason.PROVIDER_ERROR, "could not process this request"),
        (GenerationErrorReason.CAPACITY, "at capacity right now"),
        (GenerationErrorReason.INTERNAL, "model was unavailable"),
    ],
)
def test_each_generation_error_reason_produces_its_own_message(monkeypatch, reason, expected_phrase):
    exc = GenerationError("some internal detail that must never reach the client", reason=reason)
    client = _make_app(monkeypatch, run_agent_error=exc, search_hits=[])

    response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == reason.value
    assert expected_phrase in body["reply"]
    # The raw exception text is server-side only (logged, never returned).
    assert "some internal detail" not in response.text


def test_two_different_reasons_produce_two_different_messages(monkeypatch):
    """The exact regression this fix targets: before it, every reason below
    rendered the identical "no AI model configured" sentence regardless of
    cause. They must now be distinguishable."""
    client_a = _make_app(
        monkeypatch, run_agent_error=GenerationError("x", reason=GenerationErrorReason.NO_API_KEY), search_hits=[]
    )
    reply_a = client_a.post("/chat", json={"message": "hello"}).json()["reply"]

    client_b = _make_app(
        monkeypatch, run_agent_error=GenerationError("y", reason=GenerationErrorReason.PROVIDER_UNAVAILABLE),
        search_hits=[],
    )
    reply_b = client_b.post("/chat", json={"message": "hello"}).json()["reply"]

    assert reply_a != reply_b
    assert "no AI model is configured" in reply_a
    assert "temporarily unavailable" in reply_b


def test_input_guardrail_block_returns_a_valid_response_not_a_500(monkeypatch):
    """Regression test: the blocked-input-guardrail early return in
    routers/chat.py builds its own ChatResponse(...) separately from the
    normal success/degraded path below it — when degraded_reason was added
    as a required field on ChatResponse, this second construction site was
    missed, so every guardrail-blocked message (PII in the user's own
    message, prompt injection, destructive intent, ...) 500'd with a
    pydantic ValidationError instead of returning the intended block
    message. Needs guardrails actually enabled, unlike every other test in
    this file (which disables them to isolate the degraded-response logic
    under test) — this is exactly the path those tests never exercised.

    deberta_injection_check/gliner_check are explicitly disabled here (real
    models, not stubbed elsewhere in this file) so this test exercises the
    SAME check (pii_redact) it always has — gliner_check's broader semantic
    label set ("government identification number") would otherwise
    correctly, but disruptively for this specific assertion, catch the SSN
    first via a different check than the one this regression test is about."""
    from app.services.guardrails import deberta_injection_check, gliner_check

    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": {"enabled": False}})

    settings.guardrails_enabled = True
    try:
        client = _make_app(monkeypatch)
        response = client.post("/chat", json={"message": "My SSN is 123-45-6789, can you update my HR record?"})
    finally:
        settings.guardrails_enabled = False

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["degraded"] is False
    assert body["degraded_reason"] is None
    assert "personal information" in body["reply"]
    guardrail_steps = [s for s in body["trace"] if s["agent"] == "Guardrails"]
    assert any(s["tool"] == "pii_redact" for s in guardrail_steps)


def test_degraded_response_still_surfaces_raw_search_results(monkeypatch):
    """The fallback's actual job — raw sources reach the client even though
    there's no LLM synthesis — must keep working regardless of which reason
    triggered it (requirement: existing retrieval fallback stays functional)."""
    hit = {
        "chunk_id": str(uuid.uuid4()), "document_id": str(uuid.uuid4()), "document_filename": "handbook.pdf",
        "chunk_index": 2, "text": "the matched passage", "score": 0.9,
    }
    exc = GenerationError("provider down", reason=GenerationErrorReason.PROVIDER_UNAVAILABLE)
    client = _make_app(monkeypatch, run_agent_error=exc, search_hits=[hit])

    response = client.post("/chat", json={"message": "hello"})

    body = response.json()
    assert body["degraded"] is True
    assert len(body["sources"]) == 1
    assert body["sources"][0]["text"] == "the matched passage"
