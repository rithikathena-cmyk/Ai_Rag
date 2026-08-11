"""services/agents/planner.py — Phase 3B query rewriting integration
(docs/RAG_RETRIEVAL.md): _maybe_rewrite_query() directly, and the
search_documents tool's wiring through _build_tools(). Stubs planner.rewrite_query
and planner.search_documents (both imported names in planner's namespace)
rather than requiring live Qdrant/Postgres/Claude, matching this suite's
established convention. LangChain tool closures expose the original function
via `.func`, used here to invoke search_documents_tool directly in tests.
"""

import itertools
import uuid

from app.core.config import settings
from app.services.agents import planner
from app.services.retrieval.query_rewrite import RewriteOutcome


def _outcome(**overrides) -> RewriteOutcome:
    defaults = dict(
        query="original", rewritten=False, original_query="original", rewritten_query=None,
        latency_ms=1.0, tokens_input=0, tokens_output=0, cost_usd=0.0, fallback_reason=None,
    )
    defaults.update(overrides)
    return RewriteOutcome(**defaults)


# --------------------------------------------------------- _maybe_rewrite_query

def test_disabled_mode_is_a_no_op(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", False)

    def _unexpected(*a, **k):
        raise AssertionError("rewrite_query must not be called when the feature flag is off")

    monkeypatch.setattr(planner, "rewrite_query", _unexpected)

    query, trace_entry = planner._maybe_rewrite_query("original query", conversation_summary=None, request_id=None)

    assert query == "original query"
    assert trace_entry is None


def test_enabled_and_successful_rewrite_returns_rewritten_query_and_trace(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(
        planner, "rewrite_query",
        lambda q, **k: _outcome(query="better search terms", rewritten=True, rewritten_query="better search terms"),
    )

    query, trace_entry = planner._maybe_rewrite_query("vague query", conversation_summary="ctx", request_id="r1")

    assert query == "better search terms"
    assert trace_entry["tool"] == "query_rewrite"
    assert trace_entry["input"] == "vague query"
    assert "rewritten to" in trace_entry["summary"]


def test_enabled_but_fallback_returns_original_query_and_explains_why(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(
        planner, "rewrite_query", lambda q, **k: _outcome(query=q, fallback_reason="gateway error: boom")
    )

    query, trace_entry = planner._maybe_rewrite_query("original query", conversation_summary=None, request_id=None)

    assert query == "original query"
    assert "kept original query" in trace_entry["summary"]
    assert "gateway error" in trace_entry["summary"]


def test_conversation_summary_and_request_id_are_threaded_through(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)

    def _fake(q, *, context, request_id, user_id=None, role=None, department=None):
        captured["context"] = context
        captured["request_id"] = request_id
        return _outcome()

    monkeypatch.setattr(planner, "rewrite_query", _fake)

    planner._maybe_rewrite_query("q", conversation_summary="the conversation so far", request_id="shared-eval-id")

    assert captured["context"] == "the conversation so far"
    assert captured["request_id"] == "shared-eval-id"


# --------------------------------------------------- search_documents tool wiring

class _FakeDb:
    def close(self):
        pass


def _build_search_tool(monkeypatch, captured_kwargs, **build_tools_kwargs):
    def _fake_search_documents(db, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(planner, "search_documents", _fake_search_documents)
    monkeypatch.setattr(planner, "new_session", lambda: _FakeDb())

    trace = []
    tools = planner._build_tools(
        [], itertools.count(1), {"value": None}, trace, **build_tools_kwargs,
    )
    search_tool = next(t for t in tools if t.name == "search_documents")
    return search_tool, trace


def test_disabled_mode_reaches_retrieval_with_the_original_query(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", False)
    captured = {}
    tool, trace = _build_search_tool(monkeypatch, captured, role="hr", knowledge_departments=("hr",))

    tool.func("what is the leave policy?")

    assert captured["query"] == "what is the leave policy?"
    assert not any(t["tool"] == "query_rewrite" for t in trace)


def test_enabled_mode_reaches_retrieval_with_the_rewritten_query(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(planner, "rewrite_query", lambda q, **k: _outcome(query="employee leave policy details", rewritten=True))
    captured = {}
    tool, trace = _build_search_tool(monkeypatch, captured, role="hr", knowledge_departments=("hr",))

    tool.func("what about it?")

    assert captured["query"] == "employee leave policy details"
    assert any(t["tool"] == "query_rewrite" for t in trace)


def test_rbac_and_department_filters_unaffected_by_rewriting(monkeypatch):
    """The rewrite must only ever change `query` — every authorization-
    relevant parameter reaches retrieval_agent.search_documents() unchanged,
    regardless of whether rewriting succeeded, failed, or was never called."""
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(planner, "rewrite_query", lambda q, **k: _outcome(query="rewritten", rewritten=True))
    user_id = uuid.uuid4()
    captured = {}
    tool, _trace = _build_search_tool(
        monkeypatch, captured, role="project_manager", department="engineering",
        knowledge_departments=("engineering",), user_id=user_id,
    )

    tool.func("original", document_type="manual", classification="internal")

    assert captured["role"] == "project_manager"
    assert captured["knowledge_departments"] == ("engineering",)
    assert captured["user_id"] == user_id
    assert captured["document_type"] == "manual"
    assert captured["classification"] == "internal"
    assert captured["query"] == "rewritten"  # only this changed


def test_metadata_filters_preserved_when_rewrite_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", True)
    monkeypatch.setattr(planner, "rewrite_query", lambda q, **k: _outcome(query=q, fallback_reason="timed out"))
    captured = {}
    tool, trace = _build_search_tool(monkeypatch, captured, role="hr", knowledge_departments=("hr",))

    tool.func("original query", document_type="policy")

    assert captured["query"] == "original query"
    assert captured["document_type"] == "policy"
    assert any("kept original query" in t["summary"] for t in trace if t["tool"] == "query_rewrite")
