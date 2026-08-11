"""services/evaluation/runner.py — Phase 2 evaluation completeness additions
(citation_accuracy/answer_relevance/total_latency_ms/tokens/cost/model) plus
the evaluation architecture correction (docs/RAG_RETRIEVAL.md "Evaluation
Architecture Correction"): retrieval metrics now come from the production
retrieval boundary (planner._maybe_rewrite_query() + retrieval_agent.
search_documents()), not a direct search_with_reranking()/hybrid_search()
call. Stubs every I/O boundary (retrieval, rewriting, the planner, the judge,
and the DB session) rather than requiring a running Postgres/Qdrant/Claude
stack, matching this suite's established convention (see
tests/llm_rbac/test_quotas.py). Live-boundary integration coverage lives in
tests/evaluation/test_evaluation_integration.py.
"""

import uuid

import pytest

from app.core.config import settings
from app.services.agents.planner import AgentRunResult
from app.services.evaluation import runner
from app.services.evaluation.generation_judge import JudgeError


class _FakeUsageRow:
    def __init__(self, tokens_input, tokens_output, cost_usd, model):
        self.tokens_input = tokens_input
        self.tokens_output = tokens_output
        self.cost_usd = cost_usd
        self.model = model


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Just enough of SQLAlchemy's Session for run_evaluation(): a canned
    answer to db.query(GatewayUsageLogModel).filter(...).all(), and no-op
    add/commit/refresh that captures the added row."""

    def __init__(self, usage_rows):
        self._usage_rows = usage_rows
        self.added = None

    def query(self, *a, **k):
        return _FakeQuery(self._usage_rows)

    def add(self, obj):
        self.added = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


class _FakeEvalQuery:
    def __init__(self, query="What happened to Line 3?", expected_chunk_ids=None):
        self.id = uuid.uuid4()
        self.query = query
        self.expected_chunk_ids = expected_chunk_ids or []


def _default_verdict(**overrides):
    verdict = {
        "groundedness": 0.9, "faithfulness": 0.85, "hallucination_rate": 0.0,
        "citation_accuracy": 0.8, "answer_relevance": 0.95, "notes": "looks good",
    }
    verdict.update(overrides)
    return verdict


def _result(chunk_id=None, parent_context=None, **overrides):
    """A search_documents()-shaped result dict — chunk_id is a string, exactly
    what retrieval_agent.search_documents() returns."""
    item = {
        "chunk_id": chunk_id or str(uuid.uuid4()), "document_id": str(uuid.uuid4()),
        "document_filename": "f.pdf", "chunk_index": 0, "text": "chunk text", "score": 0.9,
    }
    if parent_context is not None:
        item["parent_context"] = parent_context
    item.update(overrides)
    return item


def _patch_pipeline(
    monkeypatch, *, agent_result=None, verdict=None, judge_error=None,
    search_results=None, rewrite_effect=None,
):
    """rewrite_effect: None => passthrough no-op (as if query rewriting is
    off); otherwise a (effective_query, trace_entry) tuple to return
    unconditionally, standing in for a real _maybe_rewrite_query() call."""
    monkeypatch.setattr(
        runner, "_maybe_rewrite_query",
        (lambda query, **k: rewrite_effect) if rewrite_effect is not None
        else (lambda query, **k: (query, None)),
    )
    monkeypatch.setattr(runner, "search_documents", lambda db, **k: search_results if search_results is not None else [])
    monkeypatch.setattr(
        runner, "run_agent", lambda query, **k: agent_result or AgentRunResult(reply="answer", sources=[])
    )
    if judge_error is not None:
        def _raise(*a, **k):
            raise judge_error
        monkeypatch.setattr(runner, "judge_answer", _raise)
    else:
        monkeypatch.setattr(runner, "judge_answer", lambda *a, **k: verdict)
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _reset_flags():
    original = (settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled)
    yield
    settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled = original


def test_run_evaluation_aggregates_tokens_cost_and_model_from_shared_request_id(monkeypatch):
    usage_rows = [
        _FakeUsageRow(tokens_input=100, tokens_output=50, cost_usd=0.01, model="claude-opus-5"),
        _FakeUsageRow(tokens_input=40, tokens_output=20, cost_usd=0.002, model="claude-opus-5"),
    ]
    _patch_pipeline(monkeypatch, verdict=_default_verdict())
    db = _FakeSession(usage_rows)

    run = runner.run_evaluation(db, _FakeEvalQuery(), k=5)

    assert run.tokens_input == 140
    assert run.tokens_output == 70
    assert run.cost_usd == pytest.approx(0.012)
    assert run.model == "claude-opus-5"
    assert db.added is run


def test_run_evaluation_computes_total_latency_as_sum_of_stages(monkeypatch):
    _patch_pipeline(monkeypatch, verdict=_default_verdict())
    db = _FakeSession([])

    run = runner.run_evaluation(db, _FakeEvalQuery(), k=5)

    # total must be at least retrieval+generation (the judge stage only adds time on top)
    assert run.total_latency_ms >= run.retrieval_latency_ms + run.generation_latency_ms


def test_run_evaluation_carries_citation_accuracy_and_answer_relevance_from_judge(monkeypatch):
    _patch_pipeline(monkeypatch, verdict=_default_verdict(citation_accuracy=0.42, answer_relevance=0.77))
    db = _FakeSession([])

    run = runner.run_evaluation(db, _FakeEvalQuery(), k=5)

    assert run.citation_accuracy == 0.42
    assert run.answer_relevance == 0.77


def test_run_evaluation_leaves_token_cost_model_none_when_no_gateway_rows(monkeypatch):
    _patch_pipeline(monkeypatch, verdict=_default_verdict())
    db = _FakeSession([])  # no gateway_usage_logs rows for this request_id

    run = runner.run_evaluation(db, _FakeEvalQuery(), k=5)

    assert run.tokens_input is None
    assert run.tokens_output is None
    assert run.cost_usd is None
    assert run.model is None


def test_run_evaluation_survives_judge_failure_leaving_new_fields_none(monkeypatch):
    _patch_pipeline(monkeypatch, judge_error=JudgeError("boom"))
    db = _FakeSession([])

    run = runner.run_evaluation(db, _FakeEvalQuery(), k=5)

    assert run.citation_accuracy is None
    assert run.answer_relevance is None
    assert "judge failed" in run.judge_notes
    # retrieval/generation latency were still captured before the judge failed.
    assert run.total_latency_ms == pytest.approx(run.retrieval_latency_ms + run.generation_latency_ms)


def test_run_evaluation_still_computes_existing_retrieval_metrics_unchanged(monkeypatch):
    """Guards against a regression in the pre-existing recall/precision/mrr/ndcg math
    now that retrieved ids come from search_documents() instead of search_with_reranking()."""
    chunk_id = str(uuid.uuid4())
    _patch_pipeline(monkeypatch, verdict=_default_verdict(), search_results=[_result(chunk_id=chunk_id)])
    db = _FakeSession([])

    run = runner.run_evaluation(db, _FakeEvalQuery(expected_chunk_ids=[chunk_id]), k=5)

    assert run.recall_at_k == 1.0
    assert run.precision_at_k == 1.0
    assert run.mrr == 1.0
    assert run.retrieved_chunk_ids == [chunk_id]


# ------------------------------------------- evaluation architecture correction


def test_run_evaluation_calls_the_real_production_retrieval_functions(monkeypatch):
    """The whole point of the fix: retrieval metrics must come from
    _maybe_rewrite_query() + retrieval_agent.search_documents() — the exact
    functions planner.py's search_documents tool calls — never a direct
    search_with_reranking()/hybrid_search() call, and never a
    reimplementation of either."""
    calls = []

    def _fake_rewrite(query, **kwargs):
        calls.append(("rewrite", query, kwargs))
        return query, None

    def _fake_search(db, **kwargs):
        calls.append(("search", kwargs))
        return []

    monkeypatch.setattr(runner, "_maybe_rewrite_query", _fake_rewrite)
    monkeypatch.setattr(runner, "search_documents", _fake_search)
    monkeypatch.setattr(runner, "run_agent", lambda query, **k: AgentRunResult(reply="answer", sources=[]))
    monkeypatch.setattr(runner, "judge_answer", lambda *a, **k: _default_verdict())
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    eq = _FakeEvalQuery(query="find line 3 status")
    runner.run_evaluation(_FakeSession([]), eq, k=7)

    assert calls[0][0] == "rewrite" and calls[0][1] == "find line 3 status"
    assert calls[1][0] == "search" and calls[1][1]["query"] == "find line 3 status" and calls[1][1]["top_k"] == 7
    # search_with_reranking must not exist as an attribute run_evaluation reaches for anymore.
    assert not hasattr(runner, "search_with_reranking")


def test_run_evaluation_reaches_query_rewriting_when_enabled(monkeypatch):
    """settings.query_rewriting_enabled=True must make it all the way to a
    rewritten query actually being used for retrieval — proven here by a
    fake _maybe_rewrite_query() standing in for the real one (unit level);
    the real, unmocked function is exercised in test_evaluation_integration.py."""
    settings.query_rewriting_enabled = True
    rewrite_trace_entry = {
        "agent": "Retrieval Agent", "tool": "query_rewrite",
        "input": "original", "summary": "rewritten to: 'better search terms'",
    }
    search_calls = []

    def _fake_search(db, **kwargs):
        search_calls.append(kwargs["query"])
        return []

    _patch_pipeline(
        monkeypatch, verdict=_default_verdict(),
        rewrite_effect=("better search terms", rewrite_trace_entry),
    )
    monkeypatch.setattr(runner, "search_documents", _fake_search)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(query="original"), k=5)

    assert search_calls == ["better search terms"]
    assert run.retrieval_trace["effective_query"] == "better search terms"
    assert run.retrieval_trace["original_query"] == "original"
    assert run.retrieval_trace["query_rewriting_enabled"] is True
    assert run.retrieval_trace["rewrite_trace"] == rewrite_trace_entry


def test_run_evaluation_original_query_reaches_retrieval_when_rewriting_disabled(monkeypatch):
    settings.query_rewriting_enabled = False
    search_calls = []

    def _fake_search(db, **kwargs):
        search_calls.append(kwargs["query"])
        return []

    _patch_pipeline(monkeypatch, verdict=_default_verdict())  # default rewrite_effect=None => passthrough
    monkeypatch.setattr(runner, "search_documents", _fake_search)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(query="unchanged query"), k=5)

    assert search_calls == ["unchanged query"]
    assert run.retrieval_trace["effective_query"] == "unchanged query"
    assert run.retrieval_trace["rewrite_trace"] is None


def test_run_evaluation_falls_back_to_original_query_when_rewrite_fails(monkeypatch):
    """When _maybe_rewrite_query()'s underlying rewrite_query() call fails
    (gateway error, timeout, malformed output, etc.) it returns the original
    query unchanged, per its own contract — this must reach retrieval."""
    settings.query_rewriting_enabled = True
    fallback_trace_entry = {
        "agent": "Retrieval Agent", "tool": "query_rewrite",
        "input": "original", "summary": "kept original query (gateway error: 401)",
    }
    search_calls = []

    def _fake_search(db, **kwargs):
        search_calls.append(kwargs["query"])
        return []

    _patch_pipeline(monkeypatch, verdict=_default_verdict(), rewrite_effect=("original", fallback_trace_entry))
    monkeypatch.setattr(runner, "search_documents", _fake_search)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(query="original"), k=5)

    assert search_calls == ["original"]
    assert run.retrieval_trace["effective_query"] == "original"
    assert "kept original query" in run.retrieval_trace["rewrite_trace"]["summary"]


def test_run_evaluation_records_parent_context_chunk_ids_when_phase_3a_enriches_a_hit(monkeypatch):
    """Proof Phase 3A executed: search_documents() only attaches
    parent_context when fetch_parent_context() found and attached one."""
    settings.parent_child_retrieval_enabled = True
    child_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    _patch_pipeline(
        monkeypatch, verdict=_default_verdict(),
        search_results=[
            _result(chunk_id=child_id, parent_context="broader section context"),
            _result(chunk_id=other_id),
        ],
    )

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["parent_child_retrieval_enabled"] is True
    assert run.retrieval_trace["parent_context_chunk_ids"] == [child_id]
    assert run.retrieved_chunk_ids == [child_id, other_id]


def test_run_evaluation_no_parent_context_chunk_ids_when_phase_3a_disabled(monkeypatch):
    settings.parent_child_retrieval_enabled = False
    _patch_pipeline(monkeypatch, verdict=_default_verdict(), search_results=[_result()])

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["parent_child_retrieval_enabled"] is False
    assert run.retrieval_trace["parent_context_chunk_ids"] == []


def test_run_evaluation_trace_reports_generation_available_true_on_success(monkeypatch):
    _patch_pipeline(monkeypatch, verdict=_default_verdict(), agent_result=AgentRunResult(reply="a real answer", sources=[]))

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["generation_available"] is True


def test_run_evaluation_trace_reports_generation_available_false_when_claude_unavailable(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(runner, "_maybe_rewrite_query", lambda query, **k: (query, None))
    monkeypatch.setattr(runner, "search_documents", lambda db, **k: [])
    monkeypatch.setattr(runner, "run_agent", _raise)
    monkeypatch.setattr(runner, "judge_answer", lambda *a, **k: _default_verdict())
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["generation_available"] is False
    assert run.generated_answer.startswith("[generation failed:")


# ------------------------------------------------------ retrieval failure paths


def test_run_evaluation_raises_clear_error_when_search_documents_fails(monkeypatch):
    """A Qdrant/PostgreSQL outage inside search_documents() must surface as a
    distinct, clearly-typed failure — never silently recorded as an empty/
    zero-scoring retrieval, which would misrepresent what happened."""
    def _boom(db, **kwargs):
        raise ConnectionError("Qdrant unavailable: connection refused")

    monkeypatch.setattr(runner, "_maybe_rewrite_query", lambda query, **k: (query, None))
    monkeypatch.setattr(runner, "search_documents", _boom)
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    with pytest.raises(runner.EvaluationRetrievalError) as exc_info:
        runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert "Qdrant unavailable" in str(exc_info.value)


def test_run_evaluation_raises_clear_error_when_rewrite_step_itself_raises(monkeypatch):
    """_maybe_rewrite_query() is designed to never raise (see planner.py) —
    but if the retrieval stage as a whole fails before search_documents() is
    even reached, that must still surface as EvaluationRetrievalError, not an
    unlabelled crash."""
    def _boom(query, **kwargs):
        raise RuntimeError("PostgreSQL unavailable: connection refused")

    monkeypatch.setattr(runner, "_maybe_rewrite_query", _boom)
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    with pytest.raises(runner.EvaluationRetrievalError):
        runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)


def test_run_evaluation_empty_retrieval_is_handled_not_an_error(monkeypatch):
    """An empty result set (no matching documents) is a legitimate outcome,
    not a failure — distinct from the connection-failure tests above."""
    _patch_pipeline(monkeypatch, verdict=_default_verdict(), search_results=[])
    chunk_id = str(uuid.uuid4())

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(expected_chunk_ids=[chunk_id]), k=5)

    assert run.retrieved_chunk_ids == []
    assert run.recall_at_k == 0.0
    assert run.precision_at_k == 0.0


# ------------------------------------------------------ eval-output PII redaction (persisted answer only)
#
# Separate concern from the retrieved-source dual-representation split
# (services/agents/planner.py's _llm_source_view/_public_source_view):
# run_evaluation() calls run_agent() directly, bypassing routers/chat.py's
# run_output_guardrails() entirely — so unlike a real chat turn, the model's
# own generated reply could reach EvalRunModel.generated_answer with literal
# PII still in it. Fixed in isolation (redact only what gets persisted,
# leave judge scoring untouched) per the explicit instruction not to mix
# this into the retrieval architecture change above.

@pytest.fixture(autouse=True)
def _reset_pii_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode = original


def test_run_evaluation_redacts_pii_from_persisted_generated_answer(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    _patch_pipeline(
        monkeypatch, verdict=_default_verdict(),
        agent_result=AgentRunResult(reply="Contact John at john.smith@company.com for details.", sources=[]),
    )

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert "john.smith@company.com" not in run.generated_answer
    assert "[REDACTED_EMAIL]" in run.generated_answer


def test_run_evaluation_judge_still_scores_the_unredacted_answer(monkeypatch):
    """The redaction fix only changes what's persisted — judge_answer() must
    keep scoring against the real generated text (an eval-quality concern
    deliberately left untouched by this isolated fix; changing it would mix
    scoring-accuracy considerations into a straightforward persistence-leak
    fix)."""
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    captured = {}

    def _fake_judge(question, answer, sources, **k):
        captured["answer"] = answer
        return _default_verdict()

    monkeypatch.setattr(runner, "_maybe_rewrite_query", lambda query, **k: (query, None))
    monkeypatch.setattr(runner, "search_documents", lambda db, **k: [])
    monkeypatch.setattr(
        runner, "run_agent",
        lambda query, **k: AgentRunResult(reply="Contact jane@example.com for details.", sources=[]),
    )
    monkeypatch.setattr(runner, "judge_answer", _fake_judge)
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert captured["answer"] == "Contact jane@example.com for details."  # judge saw the real text
    assert "jane@example.com" not in run.generated_answer  # persisted copy is still redacted


def test_run_evaluation_answer_without_pii_is_unaffected(monkeypatch):
    settings.guardrail_redact_pii = True
    _patch_pipeline(
        monkeypatch, verdict=_default_verdict(),
        agent_result=AgentRunResult(reply="Line 3 was down for scheduled maintenance.", sources=[]),
    )

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.generated_answer == "Line 3 was down for scheduled maintenance."


def test_run_evaluation_generation_failed_placeholder_is_not_mangled_by_redaction(monkeypatch):
    """The `[generation failed: ...]` placeholder (see
    test_run_evaluation_trace_reports_generation_available_false_when_claude_unavailable)
    must survive the new redaction pass unchanged — it's diagnostic text,
    not document content, and happens to contain no PII patterns."""
    settings.guardrail_redact_pii = True

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(runner, "_maybe_rewrite_query", lambda query, **k: (query, None))
    monkeypatch.setattr(runner, "search_documents", lambda db, **k: [])
    monkeypatch.setattr(runner, "run_agent", _raise)
    monkeypatch.setattr(runner, "judge_answer", lambda *a, **k: _default_verdict())
    monkeypatch.setattr(runner, "record_latency", lambda *a, **k: None)

    run = runner.run_evaluation(_FakeSession([]), _FakeEvalQuery(), k=5)

    assert run.generated_answer.startswith("[generation failed:")
