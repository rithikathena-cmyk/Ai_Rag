"""services/agents/retrieval_agent.py::search_documents() — Phase 3A parent-
child retrieval wiring (docs/RAG_RETRIEVAL.md). Stubs search_with_reranking
and fetch_parent_context directly rather than requiring live Qdrant/Postgres,
matching this suite's established convention.

Every result item carries both `text`/`display_text` (and, when a parent
chunk was attached, `parent_context`/`parent_context_display`) — see
services/guardrails/pii.py::DualText. This module doesn't resolve which one
is "correct" to use; that split happens one layer up in
services/agents/planner.py (see tests/test_planner_query_rewrite.py and the
LLM-vs-public source tests there).
"""

import uuid

import pytest

from app.core.config import settings
from app.services.agents import retrieval_agent
from app.services.guardrails.pii import DualText
from app.services.reranking import pipeline as reranking_pipeline
from app.services.retrieval.search import SearchHit


def _hit(chunk_id=None, parent_chunk_id=None, text="child text") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), chunk_index=0,
        parent_chunk_id=parent_chunk_id, text=text, strategy="parent_child", score=0.9,
    )


class _FakeFilenameQuery:
    def filter(self, *a, **k):
        return self

    def all(self):
        return []


class _FakeDb:
    def query(self, *a, **k):
        return _FakeFilenameQuery()


def test_parent_context_attached_when_enabled_and_present(monkeypatch):
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", True)
    child_id = uuid.uuid4()
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit(chunk_id=child_id)], True))
    monkeypatch.setattr(
        retrieval_agent, "fetch_parent_context",
        lambda db, hits, **k: {child_id: DualText(raw="broader section context", display="broader section context")},
    )

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    assert results[0]["parent_context"] == "broader section context"
    assert results[0]["parent_context_display"] == "broader section context"


def test_parent_context_absent_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", False)
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit(parent_chunk_id=uuid.uuid4())], True))

    def _unexpected(*a, **k):
        raise AssertionError("fetch_parent_context must not be called when the feature flag is off")

    monkeypatch.setattr(retrieval_agent, "fetch_parent_context", _unexpected)

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    assert "parent_context" not in results[0]
    assert "parent_context_display" not in results[0]


def test_parent_context_absent_when_no_expansion_available(monkeypatch):
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", True)
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit(parent_chunk_id=None)], True))
    monkeypatch.setattr(retrieval_agent, "fetch_parent_context", lambda db, hits, **k: {})

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    assert "parent_context" not in results[0]


def test_citation_text_stays_the_childs_own_precise_text(monkeypatch):
    """parent_context is additive background, never a replacement for the
    precisely-cited evidence in `text` — the citation must keep pointing at
    exactly what was matched, not the expanded parent."""
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", True)
    child_id = uuid.uuid4()
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: ([_hit(chunk_id=child_id, text="the precise matched sentence")], True),
    )
    monkeypatch.setattr(
        retrieval_agent, "fetch_parent_context",
        lambda db, hits, **k: {child_id: DualText(raw="much longer surrounding context", display="much longer surrounding context")},
    )

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    assert results[0]["text"] == "the precise matched sentence"
    assert results[0]["parent_context"] == "much longer surrounding context"


def test_disabled_mode_output_shape_matches_pre_phase3a_contract(monkeypatch):
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", False)
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit()], True))

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    assert set(results[0].keys()) == {
        "chunk_id", "document_id", "document_filename", "chunk_index", "text", "display_text", "score",
    }


def test_empty_hits_returns_empty_list_regardless_of_flag(monkeypatch):
    monkeypatch.setattr(settings, "parent_child_retrieval_enabled", True)
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([], False))

    assert retrieval_agent.search_documents(_FakeDb(), query="q") == []


# --------------------------------------------------- PII dual representation reaches search_documents()'s output

@pytest.fixture
def _reset_pii_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


def test_pii_in_retrieved_source_text_is_redacted_in_display_text_end_to_end(monkeypatch, _reset_pii_settings):
    """Unlike the tests above, this does NOT monkeypatch
    retrieval_agent.search_with_reranking — it exercises the real function
    (only hybrid_search/rerank stubbed, the same I/O boundary
    test_pipeline_rerank_failopen.py stubs) to prove search_documents()'s
    output — which feeds both the LLM-facing tool payload and (via
    services/agents/planner.py's public-view projection)
    AgentRunResult.sources / persisted chat history — carries both
    representations correctly by the time it reaches this function's caller,
    not just that the isolated redaction function works in unit isolation."""
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hit = _hit(text="Contact John at john.smith@company.com about this.")
    monkeypatch.setattr(reranking_pipeline, "hybrid_search", lambda db, **k: ([hit], {"qdrant_ms": 1.0}))
    monkeypatch.setattr(reranking_pipeline, "rerank", lambda query, hits: hits)

    results = retrieval_agent.search_documents(_FakeDb(), query="q")

    # `text` — the LLM-facing field — keeps the original, authorized content.
    assert results[0]["text"] == "Contact John at john.smith@company.com about this."
    # `display_text` — the only field allowed into anything persisted or
    # returned to a user — is redacted.
    assert "john.smith@company.com" not in results[0]["display_text"]
    assert "[REDACTED_EMAIL]" in results[0]["display_text"]
