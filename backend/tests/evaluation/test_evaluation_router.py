"""routers/evaluation.py — Phase 2 evaluation completeness: the new
EvalRunResponse/EvalSummaryResponse fields serialize correctly and the
existing response contract (old field names/values) is unchanged. Tests the
pure conversion/aggregation logic directly with fake ORM-row objects rather
than a live DB, matching this suite's established convention.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.routers import evaluation


class _FakeEvalRun:
    def __init__(self, **overrides):
        defaults = dict(
            id=uuid.uuid4(), eval_query_id=uuid.uuid4(), k=10, retrieved_chunk_ids=["a", "b"],
            recall_at_k=0.5, precision_at_k=0.5, mrr=0.5, ndcg_at_k=0.5, retrieval_latency_ms=12.0,
            generated_answer="the answer", groundedness=0.9, faithfulness=0.9, hallucination_rate=0.0,
            citation_accuracy=0.8, answer_relevance=0.7, judge_notes="fine",
            generation_latency_ms=200.0, total_latency_ms=250.0,
            tokens_input=100, tokens_output=50, cost_usd=0.01, model="claude-opus-5",
            experiment_label=None,
            retrieval_trace=None,
            created_at=datetime.now(timezone.utc),
        )
        defaults.update(overrides)
        for k, v in defaults.items():
            setattr(self, k, v)


def test_run_to_response_computes_total_tokens():
    row = _FakeEvalRun(tokens_input=100, tokens_output=50)
    response = evaluation._run_to_response(row)
    assert response.total_tokens == 150
    assert response.tokens_input == 100
    assert response.tokens_output == 50


def test_run_to_response_total_tokens_none_when_either_side_missing():
    assert evaluation._run_to_response(_FakeEvalRun(tokens_input=None, tokens_output=50)).total_tokens is None
    assert evaluation._run_to_response(_FakeEvalRun(tokens_input=100, tokens_output=None)).total_tokens is None
    assert evaluation._run_to_response(_FakeEvalRun(tokens_input=None, tokens_output=None)).total_tokens is None


def test_run_to_response_preserves_existing_v1_fields():
    row = _FakeEvalRun(recall_at_k=0.42, faithfulness=0.77, generated_answer="hello")
    response = evaluation._run_to_response(row)
    assert response.recall_at_k == 0.42
    assert response.faithfulness == 0.77
    assert response.generated_answer == "hello"


def test_run_to_response_carries_new_phase2_fields():
    row = _FakeEvalRun(citation_accuracy=0.33, answer_relevance=0.66, cost_usd=0.005, model="claude-sonnet-5")
    response = evaluation._run_to_response(row)
    assert response.citation_accuracy == 0.33
    assert response.answer_relevance == 0.66
    assert response.cost_usd == 0.005
    assert response.model == "claude-sonnet-5"


def test_run_to_response_carries_retrieval_trace():
    trace = {
        "original_query": "q", "effective_query": "q", "query_rewriting_enabled": False,
        "rewrite_trace": None, "parent_child_retrieval_enabled": True,
        "parent_context_chunk_ids": ["abc"], "retrieved_chunk_ids": ["abc", "def"],
        "retrieval_latency_ms": 12.0, "generation_available": True,
    }
    row = _FakeEvalRun(retrieval_trace=trace)
    response = evaluation._run_to_response(row)
    assert response.retrieval_trace == trace


def test_run_to_response_retrieval_trace_none_for_pre_correction_rows():
    row = _FakeEvalRun(retrieval_trace=None)
    response = evaluation._run_to_response(row)
    assert response.retrieval_trace is None


class _FakeRunQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **k):
        return _FakeRunQuery(self._rows)


def test_eval_summary_averages_and_sums_new_fields():
    rows = [
        _FakeEvalRun(citation_accuracy=0.8, answer_relevance=0.6, total_latency_ms=100.0,
                     tokens_input=100, tokens_output=50, cost_usd=0.01),
        _FakeEvalRun(citation_accuracy=0.4, answer_relevance=1.0, total_latency_ms=300.0,
                     tokens_input=200, tokens_output=100, cost_usd=0.03),
    ]
    summary = evaluation.eval_summary(db=_FakeDb(rows))

    assert summary.run_count == 2
    assert summary.avg_citation_accuracy == pytest.approx(0.6)
    assert summary.avg_answer_relevance == pytest.approx(0.8)
    assert summary.avg_total_latency_ms == pytest.approx(200.0)
    assert summary.avg_tokens_input == pytest.approx(150.0)
    assert summary.avg_tokens_output == pytest.approx(75.0)
    assert summary.avg_cost_usd == pytest.approx(0.02)
    assert summary.total_cost_usd == pytest.approx(0.04)


def test_eval_summary_handles_runs_with_no_phase2_data_yet():
    """A run created before this migration has every new field NULL — the
    summary must skip it for those averages, not crash or treat None as 0."""
    rows = [_FakeEvalRun(citation_accuracy=None, answer_relevance=None, cost_usd=None, tokens_input=None, tokens_output=None)]
    summary = evaluation.eval_summary(db=_FakeDb(rows))

    assert summary.avg_citation_accuracy is None
    assert summary.avg_cost_usd is None
    assert summary.total_cost_usd is None
