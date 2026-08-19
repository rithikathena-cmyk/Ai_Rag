"""Multi-agent supervisor/router wiring in routers/chat.py::_select_agent().
Follows the established chat.py integration-test convention (monkeypatch the
router-imported names wholesale — see test_chat_activity_trace.py) rather
than exercising the real LangGraph loop or a live model call.

Covers the two things that actually matter for security, not just routing
correctness: (1) resolve_agent_tools() intersection is what run_agent()
actually receives as allowed_tools — routing can never grant a tool the
caller's real RBAC decision didn't already grant; (2) a routed agent that
fails the post-hoc agent_allowed_for_role() re-check is downgraded to
general_rag rather than trusted, even though route() itself already only
picks from a reachable list (defense in depth, not redundant with it, since
this test bypasses route()'s own internals entirely by monkeypatching
chat_router.route directly).
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db
from app.gateway.schemas import ModelTier
from app.routers import chat as chat_router
from app.services.agents.router import AgentName, Intent, RoutingDecision
from app.services.auth.dependencies import get_current_user


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()


def _make_app(monkeypatch, *, role="user", department="manufacturing", knowledge_departments=("manufacturing",),
              allowed_tools=frozenset({"search_documents"})):
    app = FastAPI()
    app.include_router(chat_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, department=department, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    def _fake_get_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_get_db

    fake_decision = SimpleNamespace(
        allowed=True, role=role, department=department, model_tier=ModelTier.HAIKU,
        allowed_tools=allowed_tools, sql_allowed_tables=frozenset(), knowledge_departments=knowledge_departments,
        max_concurrent_requests=None, requires_approval=False,
    )
    monkeypatch.setattr(chat_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(chat_router, "get_conversation", lambda db, cid: _FakeConversation())
    monkeypatch.setattr(chat_router, "create_conversation", lambda db, user_id: _FakeConversation())
    monkeypatch.setattr(chat_router, "build_context", lambda db, cid: (None, []))
    monkeypatch.setattr(chat_router, "get_preferences", lambda db, uid: {})
    monkeypatch.setattr(chat_router, "maybe_summarize", lambda *a, **k: None)
    monkeypatch.setattr(
        chat_router, "run_input_stage",
        lambda state: {
            "input_findings": None,
            "normalized_message": state["user_message"],
            "policy_decision": SimpleNamespace(action="ALLOW"),
            "blocking_step_name": None,
            "reply": None,
        },
    )
    monkeypatch.setattr(
        chat_router, "run_output_stage",
        lambda state: {
            "output_findings": None,
            "citation_findings": SimpleNamespace(name="output_citation_check", action="pass", detail="ok"),
            "grounding_findings": SimpleNamespace(name="groundedness_check", action="pass", detail="ok"),
            "reply": state["llm_response"],
            "policy_decision": SimpleNamespace(action="ALLOW"),
        },
    )
    monkeypatch.setattr(chat_router, "audit_logger", SimpleNamespace(log=lambda *a, **k: None))

    persisted = []
    monkeypatch.setattr(
        chat_router, "add_message",
        lambda db, conv_id, *, role, content, sources=None, report=None, trace=None: (
            persisted.append({"role": role, "content": content, "trace": trace}),
            SimpleNamespace(id=uuid.uuid4()),
        )[-1],
    )
    return app, persisted


def _stub_run_agent(monkeypatch, captured):
    def _fake_run_agent(message, **kwargs):
        captured["allowed_tools"] = kwargs.get("allowed_tools")
        captured["agent_name"] = kwargs.get("agent_name")
        return SimpleNamespace(reply="a reply", sources=[], report=None, trace=[], degraded_reason=None)

    monkeypatch.setattr(chat_router, "run_agent", _fake_run_agent)


def test_high_confidence_route_reaches_run_agent_with_the_routed_agent(monkeypatch):
    app, _persisted = _make_app(monkeypatch)
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.PRODUCTION, intent=Intent.DOCUMENT_QUERY, confidence=0.9, reason="manufacturing question",
        ),
    )
    captured = {}
    _stub_run_agent(monkeypatch, captured)

    client = TestClient(app)
    response = client.post("/chat", json={"message": "why did the line stop?"})

    assert response.status_code == 200
    assert captured["agent_name"] == AgentName.PRODUCTION
    trace = response.json()["trace"]
    select_step = next(s for s in trace if s["tool"] == "select_agent")
    assert select_step["agent"] == "Supervisor"
    assert "agent=production" in select_step["summary"]


def test_routed_tools_never_exceed_the_callers_real_rbac_grant(monkeypatch):
    # AGENT_TOOLS[HR] includes query_analytics/generate_report, but this
    # caller's real RBAC grant (allowed_tools) is search_documents only —
    # resolve_agent_tools() must intersect, never union.
    app, _persisted = _make_app(monkeypatch, role="hr", department="hr", knowledge_departments=("hr",),
                                 allowed_tools=frozenset({"search_documents"}))
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.HR, intent=Intent.DATA_LOOKUP, confidence=0.9, reason="hr question",
        ),
    )
    captured = {}
    _stub_run_agent(monkeypatch, captured)

    client = TestClient(app)
    response = client.post("/chat", json={"message": "what's the leave policy?"})

    assert response.status_code == 200
    assert captured["allowed_tools"] == frozenset({"search_documents"})


def test_employee_cannot_reach_hr_agent_even_if_router_names_it(monkeypatch):
    # Bypasses route()'s own reachable-list restriction entirely by
    # monkeypatching chat_router.route directly, to prove the SEPARATE
    # post-hoc agent_allowed_for_role() re-check in _select_agent() is a
    # real, independent boundary and not just decorative.
    app, persisted = _make_app(monkeypatch, role="user", department="manufacturing",
                                knowledge_departments=("manufacturing",),
                                allowed_tools=frozenset({"search_documents"}))
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.HR, intent=Intent.DATA_LOOKUP, confidence=0.95, reason="should never be trusted",
        ),
    )
    captured = {}
    _stub_run_agent(monkeypatch, captured)

    client = TestClient(app)
    response = client.post("/chat", json={"message": "show me HR analytics"})

    assert response.status_code == 200
    assert captured["agent_name"] == AgentName.GENERAL_RAG
    # general_rag's own AGENT_TOOLS is search_documents-only, and the
    # caller's real grant is search_documents-only too — either way, no HR
    # tool (query_analytics/generate_report) ever reaches run_agent().
    assert "query_analytics" not in captured["allowed_tools"]
    assert "generate_report" not in captured["allowed_tools"]
    select_step = next(s for s in persisted[-1]["trace"] if s["tool"] == "select_agent")
    assert "agent=general_rag" in select_step["summary"]


def test_low_confidence_non_fallback_short_circuits_with_a_clarification_reply(monkeypatch):
    app, persisted = _make_app(monkeypatch)
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.PRODUCTION, intent=Intent.DOCUMENT_QUERY, confidence=0.1, reason="ambiguous",
        ),
    )
    monkeypatch.setattr(
        chat_router, "run_agent", lambda *a, **k: pytest.fail("run_agent must not be called below the confidence gate")
    )

    client = TestClient(app)
    response = client.post("/chat", json={"message": "hm"})

    assert response.status_code == 200
    body = response.json()
    assert "clarify" in body["reply"].lower() or "rephrase" in body["reply"].lower()
    assistant_msg = next(p for p in persisted if p["role"] == "assistant")
    assert assistant_msg["content"] == body["reply"]


def test_low_confidence_general_chat_intent_does_not_trigger_clarification(monkeypatch):
    app, _persisted = _make_app(monkeypatch)
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.GENERAL_CONVERSATION, intent=Intent.GENERAL_CHAT, confidence=0.1, reason="small talk",
        ),
    )
    captured = {}
    _stub_run_agent(monkeypatch, captured)

    client = TestClient(app)
    response = client.post("/chat", json={"message": "hey there"})

    assert response.status_code == 200
    assert captured["agent_name"] == AgentName.GENERAL_CONVERSATION


def test_low_confidence_fallback_decision_skips_clarification_and_routes_to_general_rag(monkeypatch):
    app, _persisted = _make_app(monkeypatch)
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.GENERAL_RAG, intent=Intent.GENERAL_CHAT, confidence=0.0,
            reason="malformed JSON response from router", is_fallback=True,
        ),
    )
    captured = {}
    _stub_run_agent(monkeypatch, captured)

    client = TestClient(app)
    response = client.post("/chat", json={"message": "anything"})

    assert response.status_code == 200
    assert captured["agent_name"] == AgentName.GENERAL_RAG


def test_confidence_threshold_setting_is_respected(monkeypatch):
    monkeypatch.setattr(settings, "agent_router_confidence_threshold", 0.95)
    app, _persisted = _make_app(monkeypatch)
    monkeypatch.setattr(
        chat_router, "route",
        lambda *a, **k: RoutingDecision(
            agent=AgentName.PRODUCTION, intent=Intent.DOCUMENT_QUERY, confidence=0.9, reason="just below the raised bar",
        ),
    )
    monkeypatch.setattr(
        chat_router, "run_agent", lambda *a, **k: pytest.fail("run_agent must not be called below the confidence gate")
    )

    client = TestClient(app)
    response = client.post("/chat", json={"message": "why did the line stop?"})

    assert response.status_code == 200
    assert "clarify" in response.json()["reply"].lower() or "rephrase" in response.json()["reply"].lower()
