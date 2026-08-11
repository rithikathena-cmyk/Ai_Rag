"""services/retrieval/query_rewrite.py — Phase 3B query rewriting
(docs/RAG_RETRIEVAL.md). Stubs claude_gateway.generate directly on the
singleton instance rather than requiring a live Anthropic call, matching
this suite's established convention (see tests/evaluation/test_generation_judge.py).
"""

import json
import time

import pytest

from app.core.config import settings
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerateResult, TokenUsage
from app.services.retrieval import query_rewrite
from app.services.retrieval.query_rewrite import rewrite_query


def _fake_result(payload: dict, *, model="claude-opus-5") -> GenerateResult:
    return GenerateResult(
        text=json.dumps(payload), stop_reason="end_turn", usage=TokenUsage(15, 8),
        request_id="req-1", model=model, latency_ms=50.0,
    )


def test_successful_rewrite_uses_the_rewritten_query(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(
        query_rewrite.claude_gateway, "generate",
        lambda req: _fake_result({"rewritten_query": "production issues Line 3 last month"}),
    )

    outcome = rewrite_query("What happened to Line 3?", context="user previously asked about March production")

    assert outcome.rewritten is True
    assert outcome.query == "production issues Line 3 last month"
    assert outcome.original_query == "What happened to Line 3?"
    assert outcome.fallback_reason is None
    assert outcome.tokens_input == 15
    assert outcome.tokens_output == 8
    assert outcome.cost_usd >= 0


def test_original_query_is_preserved_in_the_outcome_even_on_success(monkeypatch):
    monkeypatch.setattr(
        query_rewrite.claude_gateway, "generate", lambda req: _fake_result({"rewritten_query": "something else"})
    )

    outcome = rewrite_query("original text")

    assert outcome.original_query == "original text"
    assert outcome.rewritten_query == "something else"


def test_gateway_error_falls_back_to_original_query(monkeypatch):
    def _raise(req):
        raise GenerationError("no api key")

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _raise)

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert outcome.query == "original query"
    assert "gateway error" in outcome.fallback_reason


def test_unexpected_exception_also_falls_back(monkeypatch):
    def _raise(req):
        raise RuntimeError("boom")

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _raise)

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert outcome.query == "original query"
    assert "unexpected error" in outcome.fallback_reason


def test_timeout_falls_back_to_original_query(monkeypatch):
    monkeypatch.setattr(settings, "query_rewrite_timeout_seconds", 0.05)

    def _slow(req):
        time.sleep(0.5)
        return _fake_result({"rewritten_query": "too slow to matter"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _slow)

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert outcome.query == "original query"
    assert "timed out" in outcome.fallback_reason


def test_malformed_json_response_falls_back(monkeypatch):
    def _fake_generate(req):
        return GenerateResult(
            text="not json at all", stop_reason="end_turn", usage=TokenUsage(5, 5),
            request_id="req-1", model="claude-opus-5", latency_ms=10.0,
        )

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert outcome.query == "original query"
    assert "malformed" in outcome.fallback_reason


def test_empty_rewrite_falls_back(monkeypatch):
    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", lambda req: _fake_result({"rewritten_query": ""}))

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert "empty" in outcome.fallback_reason


def test_refusal_falls_back(monkeypatch):
    def _fake_generate(req):
        return GenerateResult(
            text="", stop_reason="refusal", usage=TokenUsage(5, 0),
            request_id="req-1", model="claude-opus-5", latency_ms=10.0,
        )

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert "refused" in outcome.fallback_reason


def test_rewrite_exceeding_max_length_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "query_rewrite_max_chars", 10)
    monkeypatch.setattr(
        query_rewrite.claude_gateway, "generate",
        lambda req: _fake_result({"rewritten_query": "this rewritten query is way too long"}),
    )

    outcome = rewrite_query("original query")

    assert outcome.rewritten is False
    assert outcome.query == "original query"
    assert outcome.rewritten_query == "this rewritten query is way too long"  # kept for observability
    assert "exceeded" in outcome.fallback_reason


def test_conversation_context_is_included_in_the_gateway_message(monkeypatch):
    captured = {}

    def _fake_generate(req):
        captured["messages"] = req.messages
        return _fake_result({"rewritten_query": "rewritten"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    rewrite_query("what about it?", context="earlier: discussed Line 3 downtime")

    assert "earlier: discussed Line 3 downtime" in captured["messages"][0]["content"]
    assert "what about it?" in captured["messages"][0]["content"]


def test_no_context_still_works(monkeypatch):
    captured = {}

    def _fake_generate(req):
        captured["messages"] = req.messages
        return _fake_result({"rewritten_query": "rewritten"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    outcome = rewrite_query("standalone query", context=None)

    assert outcome.rewritten is True
    assert "standalone query" in captured["messages"][0]["content"]


def test_request_id_is_threaded_into_the_gateway_request(monkeypatch):
    captured = {}

    def _fake_generate(req):
        captured["request_id"] = req.request_id
        return _fake_result({"rewritten_query": "rewritten"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    rewrite_query("q", request_id="shared-id-123")

    assert captured["request_id"] == "shared-id-123"


def test_uses_query_rewrite_tier_setting(monkeypatch):
    from app.gateway.schemas import ModelTier

    captured = {}
    monkeypatch.setattr(settings, "query_rewrite_tier", "sonnet")

    def _fake_generate(req):
        captured["tier"] = req.tier
        return _fake_result({"rewritten_query": "rewritten"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    rewrite_query("q")

    assert captured["tier"] == ModelTier.SONNET


def test_invalid_tier_setting_falls_back_to_fast(monkeypatch):
    from app.gateway.schemas import ModelTier

    captured = {}
    monkeypatch.setattr(settings, "query_rewrite_tier", "not-a-real-tier")

    def _fake_generate(req):
        captured["tier"] = req.tier
        return _fake_result({"rewritten_query": "rewritten"})

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _fake_generate)

    rewrite_query("q")

    assert captured["tier"] == ModelTier.FAST
