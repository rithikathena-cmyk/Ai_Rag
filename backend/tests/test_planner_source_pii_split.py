"""services/agents/planner.py — the LLM-facing vs. persisted/public source
split (services/guardrails/pii.py::DualText). search_documents_tool's return
value (what Claude actually reads to formulate its answer) must carry the
original, authorized content; all_sources (-> AgentRunResult.sources ->
ChatResponse.sources -> routers/chat.py's add_message() persisted chat
history) must carry only the redacted display representation. Reuses
tests/test_planner_query_rewrite.py's `tool.func(...)` invocation pattern
and monkeypatches planner.search_documents (never a real Qdrant/Postgres/
Claude call) — no external HTTP anywhere in this file.
"""

import itertools
import json
import uuid

import pytest

from app.core.config import settings
from app.services.agents import planner


class _FakeDb:
    def close(self):
        pass


def _fake_source(
    text, display_text, *, chunk_id=None, document_id=None, score=0.9,
    parent_context=None, parent_context_display=None,
):
    item = {
        "chunk_id": chunk_id or str(uuid.uuid4()),
        "document_id": document_id or str(uuid.uuid4()),
        "document_filename": "handbook.pdf",
        "chunk_index": 3,
        "text": text,
        "display_text": display_text,
        "score": score,
    }
    if parent_context is not None:
        item["parent_context"] = parent_context
        item["parent_context_display"] = parent_context_display
    return item


def _build_search_tool(monkeypatch, raw_results, **build_tools_kwargs):
    monkeypatch.setattr(planner, "search_documents", lambda db, **k: raw_results)
    monkeypatch.setattr(planner, "new_session", lambda: _FakeDb())

    all_sources: list[dict] = []
    trace: list[dict] = []
    retrieved_doc_ids: list[str] = []
    tools = planner._build_tools(
        all_sources, itertools.count(1), {"value": None}, trace,
        retrieved_doc_ids=retrieved_doc_ids, **build_tools_kwargs,
    )
    search_tool = next(t for t in tools if t.name == "search_documents")
    return search_tool, all_sources, retrieved_doc_ids


@pytest.fixture(autouse=True)
def _disable_rewrite(monkeypatch):
    monkeypatch.setattr(settings, "query_rewriting_enabled", False)


# --------------------------------------------------- LLM receives original content when policy permits

def test_llm_payload_contains_the_original_authorized_text(monkeypatch):
    raw = [_fake_source("Contact John at john.smith@company.com.", "Contact John at [REDACTED_EMAIL].")]
    tool, _all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    llm_payload = json.loads(tool.func("who do I contact?"))

    assert llm_payload[0]["text"] == "Contact John at john.smith@company.com."
    assert "display_text" not in llm_payload[0]  # not just unused — dropped entirely


def test_llm_payload_includes_raw_parent_context(monkeypatch):
    raw = [_fake_source(
        "the precise sentence", "the precise sentence",
        parent_context="broader section with jane@example.com",
        parent_context_display="broader section with [REDACTED_EMAIL]",
    )]
    tool, _all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    llm_payload = json.loads(tool.func("q"))

    assert llm_payload[0]["parent_context"] == "broader section with jane@example.com"
    assert "parent_context_display" not in llm_payload[0]


# --------------------------------------------------- persisted/public source contains only redacted PII

def test_all_sources_contains_only_redacted_text(monkeypatch):
    raw = [_fake_source("SSN 123-45-6789 on file.", "SSN [REDACTED_SSN] on file.")]
    tool, all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    tool.func("what's the SSN?")

    assert all_sources[0]["text"] == "SSN [REDACTED_SSN] on file."
    assert "123-45-6789" not in json.dumps(all_sources)  # nowhere in the persisted payload at all
    assert "display_text" not in all_sources[0]


def test_all_sources_parent_context_is_redacted_too(monkeypatch):
    raw = [_fake_source(
        "the precise sentence", "the precise sentence",
        parent_context="broader section with jane@example.com",
        parent_context_display="broader section with [REDACTED_EMAIL]",
    )]
    tool, all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    tool.func("q")

    assert all_sources[0]["parent_context"] == "broader section with [REDACTED_EMAIL]"
    assert "jane@example.com" not in json.dumps(all_sources)
    assert "parent_context_display" not in all_sources[0]


# --------------------------------------------------- citation index consistency

def test_citation_index_matches_between_llm_payload_and_public_sources(monkeypatch):
    raw = [_fake_source("first chunk", "first chunk"), _fake_source("second chunk", "second chunk")]
    tool, all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    llm_payload = json.loads(tool.func("q"))

    assert [item["index"] for item in llm_payload] == [item["index"] for item in all_sources]


# --------------------------------------------------- metadata preserved in the public view

def test_public_source_metadata_is_preserved(monkeypatch):
    chunk_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
    raw = [_fake_source(
        "SSN 123-45-6789", "SSN [REDACTED_SSN]", chunk_id=chunk_id, document_id=document_id, score=0.83,
    )]
    tool, all_sources, _ = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    tool.func("q")

    assert all_sources[0]["chunk_id"] == chunk_id
    assert all_sources[0]["document_id"] == document_id
    assert all_sources[0]["document_filename"] == "handbook.pdf"
    assert all_sources[0]["chunk_index"] == 3
    assert all_sources[0]["score"] == 0.83


# --------------------------------------------------- audit log never receives raw source text

def test_retrieved_doc_ids_never_contains_source_text(monkeypatch):
    raw = [_fake_source(
        "Contact jane@example.com for details.", "Contact [REDACTED_EMAIL] for details.", document_id="doc-1",
    )]
    tool, _all_sources, retrieved_doc_ids = _build_search_tool(monkeypatch, raw, role="hr", knowledge_departments=("hr",))

    tool.func("q")

    # retrieved_doc_ids feeds gateway/usage_tracker.py::record_usage()'s
    # documents_retrieved column (the audit trail) — only ever document ids,
    # never chunk content.
    assert retrieved_doc_ids == ["doc-1"]
    assert "jane@example.com" not in retrieved_doc_ids


# --------------------------------------------------- no-LLM fallback path only ever returns the public view

def test_retrieval_fallback_never_returns_raw_text(monkeypatch):
    raw = [_fake_source("Contact jane@example.com for details.", "Contact [REDACTED_EMAIL] for details.")]
    monkeypatch.setattr(planner, "search_documents", lambda db, **k: raw)
    monkeypatch.setattr(settings, "fallback_retrieval_top_k", 5)
    monkeypatch.setattr(settings, "fallback_chunk_char_limit", 2000)

    result = planner.run_retrieval_fallback("q", _FakeDb())

    assert result.sources[0]["text"] == "Contact [REDACTED_EMAIL] for details."
    assert "jane@example.com" not in json.dumps(result.sources)
    assert "display_text" not in result.sources[0]


# --------------------------------------------------- unauthorized retrieval never reaches either representation

# --------------------------------------------------- generated reports never leak row content into chat/API surfaces
#
# Reports themselves are a separate, deliberate design decision (see
# tests/test_reports_rbac.py's module docstring): they're authorized
# sensitive artifacts, not redacted like chat/citation content, and are
# access-controlled at rest instead. What's verified here is narrower — that
# the *info* about a report surfaced through chat (trace/AgentRunResult.report
# -> ChatResponse.report -> add_message()'s persisted `report` column) never
# itself carries row content, regardless of what the report contains. The
# actual report content only ever reaches a caller through the separately
# authorized GET /reports/{id}/download endpoint.

class _FakeGeneratedReport:
    def __init__(self):
        self.id = uuid.uuid4()
        self.title = "Contact List"
        self.format = "csv"
        self.row_count = 2


def test_generate_report_tool_response_never_includes_row_content(monkeypatch):
    monkeypatch.setattr(planner, "generate_report", lambda db, **k: _FakeGeneratedReport())
    monkeypatch.setattr(planner, "new_session", lambda: _FakeDb())

    report_holder = {"value": None}
    tools = planner._build_tools([], itertools.count(1), report_holder, [], role="hr")
    tool = next(t for t in tools if t.name == "generate_report")

    result_json = tool.func(
        title="Contact List", format="csv", columns=["name", "email"],
        rows=[["John", "john.smith@company.com"], ["Jane", "jane@example.com"]],
    )
    info = json.loads(result_json)

    assert "john.smith@company.com" not in result_json
    assert set(info.keys()) == {"id", "title", "format", "row_count", "download_url"}
    # Same info object becomes AgentRunResult.report -> ChatResponse.report
    # -> add_message()'s persisted `report` column (see planner.py's
    # report_holder["value"] = info) — proving that path is equally clean.
    assert report_holder["value"] == info


def test_no_authorized_hits_means_no_source_in_either_view(monkeypatch):
    """When retrieval_agent.search_documents() returns nothing (e.g. every
    candidate document was filtered out by resolve_document_ids/
    apply_category_policy before this tool ever ran — see
    tests/retrieval/test_department_isolation.py and
    tests/llm_rbac/test_category_policy.py for that filtering itself, which
    this refactor does not touch), neither the LLM payload nor the persisted
    sources gets anything — content this role isn't authorized for never
    reaches either representation, raw or redacted."""
    tool, all_sources, retrieved_doc_ids = _build_search_tool(
        monkeypatch, [], role="user", knowledge_departments=("manufacturing",),
    )

    llm_payload = json.loads(tool.func("q"))

    assert llm_payload == []
    assert all_sources == []
    assert retrieved_doc_ids == []
