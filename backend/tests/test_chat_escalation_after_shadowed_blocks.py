"""End-to-end confirmation that services/guardrails/escalation.py's
repeated-block lockout is unaffected by the scope_semantic_check shadowing
fix (services/guardrails/pipeline.py) — reproduces, through the real
routers/chat.py endpoint, the exact live sequence observed against the
running backend on 2026-08-15: repeated toxic messages, each shadowed by
scope_semantic_check (a bare insult has no request structure, so
check_scope_semantic reports a scope_unclear_* reason rather than letting
toxicity_check's own reason through) before the fix, correctly attributed to
toxicity_check's specific reason after it — and the account still gets
locked out after enough of them, exactly as it did live.

chat.py only ever inspects `input_guardrails.blocked` (a bool) to decide
whether to call record_block() — never which check supplied the reason — so
this is a belt-and-suspenders confirmation of something already provable by
inspection, not a speculative risk: the deferred-block mechanism changes
nothing about the "blocked or not" outcome for the same message.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db
from app.gateway.schemas import ModelTier
from app.routers import chat as chat_router
from app.services.auth.dependencies import get_current_user
from app.services.guardrails import (
    deberta_injection_check, escalation, gliner_check, presidio_check, scope_semantic_check, toxicity_check,
)

TOXIC_MESSAGE = "You're a worthless piece of garbage and I hope your whole team gets fired."


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()


class _FakeMatcher:
    def best_match(self, text):
        return ("how do I request time off", 0.10)  # scores low against every configured topic


class _FakeTogglePipeline:
    def __call__(self, text):
        return [[{"label": "toxic", "score": 0.95}]]  # genuinely toxic, every call


def _make_app(monkeypatch):
    app = FastAPI()
    app.include_router(chat_router.router)

    # Mirrors app/main.py's http_exception_handler — a bare FastAPI()
    # instance doesn't register it, so without this AppError(429, ...)
    # would come back as FastAPI's default {"detail": "..."} shape instead
    # of the real API's {"error": {"code": ..., "message": ...}}.
    @app.exception_handler(HTTPException)
    def _http_exception_handler(request: Request, exc: HTTPException):
        code = getattr(exc, "code", "http_error")
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": exc.detail}})

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
    monkeypatch.setattr(chat_router, "add_message", lambda *a, **k: SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(chat_router, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_router, "run_agent", lambda *a, **k: pytest.fail("run_agent must not be called on a blocked turn")
    )

    # Deterministic shadowing setup: scope_semantic_check scores every
    # message low against its configured topics (so it WOULD produce the
    # generic scope_unclear_* reason on its own), toxicity_check is
    # genuinely, deterministically toxic every call. deberta/presidio/gliner
    # disabled so toxicity_check is unambiguously the specific check that
    # should win.
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config",
        lambda name: {"scope_semantic_check": {"enabled": True, "topics": ["how do I request time off"], "threshold": 0.55}},
    )
    monkeypatch.setattr(scope_semantic_check, "_get_matcher", lambda topics: _FakeMatcher())
    monkeypatch.setattr(
        toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": {"enabled": True, "score_threshold": 0.7}}
    )
    monkeypatch.setattr(toxicity_check, "_get_pipeline", lambda model_name: _FakeTogglePipeline())
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": {"enabled": False}})
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": {"enabled": False}})
    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )

    return TestClient(app), fake_user.id


@pytest.fixture(autouse=True)
def _guardrails_on_and_escalation_state_clean():
    original = settings.guardrails_enabled
    settings.guardrails_enabled = True
    escalation._BLOCK_TIMESTAMPS.clear()
    escalation._LOCKOUT_UNTIL.clear()
    yield
    settings.guardrails_enabled = original
    escalation._BLOCK_TIMESTAMPS.clear()
    escalation._LOCKOUT_UNTIL.clear()


def test_repeated_shadowed_blocks_still_trigger_escalation_lockout(monkeypatch):
    client, _user_id = _make_app(monkeypatch)

    # guardrails.yaml's escalation.block_threshold is 5 in this repo's
    # config — 5 blocked turns should trip the lockout on the 6th request.
    replies = []
    for _ in range(5):
        response = client.post("/chat", json={"message": TOXIC_MESSAGE})
        assert response.status_code == 200, response.text
        body = response.json()
        assert "abusive" in body["reply"].lower(), (
            f"expected toxicity_check's specific reason to win over the shadowing scope block, got: {body['reply']!r}"
        )
        replies.append(body["reply"])

    sixth = client.post("/chat", json={"message": TOXIC_MESSAGE})
    assert sixth.status_code == 429, sixth.text
    assert sixth.json()["error"]["code"] == "guardrail_escalation_lockout"


def test_each_individual_blocked_turn_gets_the_correct_specific_reason(monkeypatch):
    """Not just the LAST one before lockout — every single shadowed block in
    the sequence must carry toxicity_check's reason, never the generic
    scope/unclear one."""
    client, _user_id = _make_app(monkeypatch)

    for _ in range(4):  # stay under the threshold of 5
        response = client.post("/chat", json={"message": TOXIC_MESSAGE})
        assert response.status_code == 200
        body = response.json()
        assert "abusive" in body["reply"].lower()
        assert "not sure" not in body["reply"].lower()
        assert "outside the areas" not in body["reply"].lower()
