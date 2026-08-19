"""services/agents/router.py — multi-agent supervisor/router. Stubs
claude_gateway.generate directly on the singleton instance, matching this
suite's established convention (see tests/retrieval/test_query_rewrite.py,
tests/evaluation/test_generation_judge.py). Every failure mode (malformed
JSON, unrecognized agent/intent, an agent outside the caller's reachable
list, a timeout, a gateway error, an unexpected exception, a refusal) must
fall back to AgentName.GENERAL_RAG with confidence=0.0 and is_fallback=True
— never let an unvalidated value escape route(), per router.py's own
module docstring.
"""

import json
import time

from app.core.config import settings
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerateResult, ModelTier, TokenUsage
from app.services.agents import router
from app.services.agents.router import AgentName, Intent, route

_ALL_AGENTS = list(AgentName)


def _fake_result(payload: dict, *, model="claude-haiku-4-5") -> GenerateResult:
    return GenerateResult(
        text=json.dumps(payload), stop_reason="end_turn", usage=TokenUsage(20, 10),
        request_id="req-1", model=model, latency_ms=40.0,
    )


def test_successful_routing_returns_the_classified_agent_and_intent(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "hr", "intent": "DATA_LOOKUP", "confidence": 0.87,
            "reason": "asks about leave balance", "required_capabilities": ["rag"],
        }),
    )

    decision = route("What's my leave balance?", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.HR
    assert decision.intent == Intent.DATA_LOOKUP
    assert decision.confidence == 0.87
    assert decision.is_fallback is False


def test_unknown_required_capabilities_are_dropped_not_rejected(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "general_rag", "intent": "DOCUMENT_QUERY", "confidence": 0.6,
            "reason": "generic doc question", "required_capabilities": ["rag", "not_a_real_capability"],
        }),
    )

    decision = route("find the SOP", reachable_agents=_ALL_AGENTS)

    assert decision.required_capabilities == ["rag"]
    assert decision.is_fallback is False


def test_malformed_json_falls_back_to_general_rag(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: GenerateResult(
            text="not json", stop_reason="end_turn", usage=TokenUsage(5, 5),
            request_id="req-1", model="claude-haiku-4-5", latency_ms=10.0,
        ),
    )

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.confidence == 0.0
    assert decision.is_fallback is True


def test_unrecognized_agent_name_falls_back(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "some_agent_the_model_invented", "intent": "GENERAL_CHAT", "confidence": 0.9,
            "reason": "hallucinated agent",
        }),
    )

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_unrecognized_intent_falls_back(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "hr", "intent": "NOT_A_REAL_INTENT", "confidence": 0.9, "reason": "bad intent",
        }),
    )

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_agent_outside_reachable_list_falls_back(monkeypatch):
    # A real, valid enum value — just not one this caller's RBAC-resolved
    # knowledge_departments makes reachable. Must be rejected the same as an
    # unrecognized string, never trusted just because it parses.
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "hr", "intent": "DATA_LOOKUP", "confidence": 0.95, "reason": "leave balance",
        }),
    )

    decision = route("what's my leave balance?", reachable_agents=[AgentName.PRODUCTION, AgentName.GENERAL_RAG])

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_empty_reachable_agents_short_circuits_without_calling_the_gateway(monkeypatch):
    called = {"count": 0}

    def _fake_generate(req):
        called["count"] += 1
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _fake_generate)

    decision = route("anything", reachable_agents=[])

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True
    assert called["count"] == 0


def test_gateway_error_falls_back(monkeypatch):
    def _raise(req):
        raise GenerationError("no api key")

    monkeypatch.setattr(router.claude_gateway, "generate", _raise)

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_unexpected_exception_falls_back(monkeypatch):
    def _raise(req):
        raise RuntimeError("boom")

    monkeypatch.setattr(router.claude_gateway, "generate", _raise)

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_refusal_falls_back(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: GenerateResult(
            text="", stop_reason="refusal", usage=TokenUsage(5, 0),
            request_id="req-1", model="claude-haiku-4-5", latency_ms=10.0,
        ),
    )

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "agent_router_timeout_seconds", 0.05)

    def _slow(req):
        time.sleep(0.5)
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _slow)

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_non_numeric_confidence_falls_back(monkeypatch):
    monkeypatch.setattr(
        router.claude_gateway, "generate",
        lambda req: _fake_result({
            "agent": "hr", "intent": "DATA_LOOKUP", "confidence": "very confident", "reason": "x",
        }),
    )

    decision = route("anything", reachable_agents=_ALL_AGENTS)

    assert decision.agent == AgentName.GENERAL_RAG
    assert decision.is_fallback is True


def test_uses_agent_router_tier_setting(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "agent_router_tier", "sonnet")

    def _fake_generate(req):
        captured["tier"] = req.tier
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _fake_generate)

    route("anything", reachable_agents=_ALL_AGENTS)

    assert captured["tier"] == ModelTier.SONNET


def test_invalid_tier_setting_falls_back_to_fast(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "agent_router_tier", "not-a-real-tier")

    def _fake_generate(req):
        captured["tier"] = req.tier
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _fake_generate)

    route("anything", reachable_agents=_ALL_AGENTS)

    assert captured["tier"] == ModelTier.FAST


def test_request_id_role_and_department_are_threaded_into_the_gateway_request(monkeypatch):
    captured = {}

    def _fake_generate(req):
        captured["request_id"] = req.request_id
        captured["role"] = req.role
        captured["department"] = req.department
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _fake_generate)

    route(
        "anything", reachable_agents=_ALL_AGENTS, request_id="shared-id-123",
        role="hr", department="hr",
    )

    assert captured["request_id"] == "shared-id-123"
    assert captured["role"] == "hr"
    assert captured["department"] == "hr"


def test_reachable_agents_are_listed_in_the_gateway_message(monkeypatch):
    captured = {}

    def _fake_generate(req):
        captured["messages"] = req.messages
        return _fake_result({"agent": "general_rag", "intent": "GENERAL_CHAT", "confidence": 0.9, "reason": "x"})

    monkeypatch.setattr(router.claude_gateway, "generate", _fake_generate)

    route("anything", reachable_agents=[AgentName.GENERAL_RAG, AgentName.HR])

    content = captured["messages"][0]["content"]
    assert "general_rag" in content
    assert "hr" in content
    assert "production" not in content
