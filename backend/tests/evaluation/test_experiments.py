"""services/evaluation/experiments.py — Phase 3 evaluation gate. Stubs
run_evaluation() directly (the real function is already tested by
tests/evaluation/test_runner.py) so these tests focus purely on the gate's
own logic: temporary flag isolation, run tagging, comparison/aggregation
math, generation-availability/401 detection, and recommendation reasoning —
not the underlying retrieval/generation pipeline. Matches this suite's
established convention of stubbing the I/O boundary.
"""

import uuid

import pytest

from app.core.config import settings
from app.services.evaluation import experiments


class _FakeDb:
    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass


class _FakeEvalQuery:
    def __init__(self, query_id=None):
        self.id = query_id or uuid.uuid4()
        self.query = "q"
        self.expected_chunk_ids = []


class _FakeEvalRun:
    """Minimal stand-in for EvalRunModel — just the attributes
    experiments.py actually reads or writes."""

    def __init__(self, eval_query_id, **overrides):
        self.eval_query_id = eval_query_id
        self.experiment_label = None
        defaults = dict(
            recall_at_k=None, precision_at_k=None, mrr=None, ndcg_at_k=None,
            citation_accuracy=None, answer_relevance=None, faithfulness=None, hallucination_rate=None,
            retrieval_latency_ms=None, generation_latency_ms=None, total_latency_ms=None,
            tokens_input=None, tokens_output=None, cost_usd=None,
            generated_answer=None, judge_notes=None,
        )
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def _reset_flags():
    """experiments.py's whole safety property is that it never leaves these
    flags changed — reset around every test regardless of what a test does,
    so a failure partway through one test can't leak into the next."""
    original = (settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled)
    yield
    settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled = original


# --------------------------------------------------- experiment configuration

def test_baseline_configuration_values():
    assert experiments.BASELINE.parent_child_retrieval_enabled is False
    assert experiments.BASELINE.query_rewriting_enabled is False


def test_parent_child_configuration_values():
    assert experiments.PARENT_CHILD.parent_child_retrieval_enabled is True
    assert experiments.PARENT_CHILD.query_rewriting_enabled is False


def test_query_rewrite_configuration_values():
    assert experiments.QUERY_REWRITE.parent_child_retrieval_enabled is False
    assert experiments.QUERY_REWRITE.query_rewriting_enabled is True


def test_combined_configuration_values():
    assert experiments.COMBINED.parent_child_retrieval_enabled is True
    assert experiments.COMBINED.query_rewriting_enabled is True


# ------------------------------------------------- configuration isolation

def test_temporary_flags_sets_and_restores_settings():
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False

    with experiments._temporary_flags(experiments.COMBINED):
        assert settings.parent_child_retrieval_enabled is True
        assert settings.query_rewriting_enabled is True

    assert settings.parent_child_retrieval_enabled is False
    assert settings.query_rewriting_enabled is False


def test_temporary_flags_restores_on_exception():
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False

    with pytest.raises(RuntimeError):
        with experiments._temporary_flags(experiments.PARENT_CHILD):
            assert settings.parent_child_retrieval_enabled is True
            raise RuntimeError("boom")

    assert settings.parent_child_retrieval_enabled is False
    assert settings.query_rewriting_enabled is False


def test_temporary_flags_restores_whatever_the_prior_value_actually_was():
    """Not just "back to False" — the true prior value, even if unusual."""
    settings.parent_child_retrieval_enabled = True
    settings.query_rewriting_enabled = False

    with experiments._temporary_flags(experiments.BASELINE):
        assert settings.parent_child_retrieval_enabled is False

    assert settings.parent_child_retrieval_enabled is True
    assert settings.query_rewriting_enabled is False


def test_run_experiment_does_not_permanently_mutate_settings(monkeypatch):
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    monkeypatch.setattr(experiments, "run_evaluation", lambda db, eq, k: _FakeEvalRun(eq.id))

    experiments.run_experiment(_FakeDb(), [_FakeEvalQuery()], experiments.COMBINED, k=5)

    assert settings.parent_child_retrieval_enabled is False
    assert settings.query_rewriting_enabled is False


def test_run_experiment_tags_every_run_with_the_experiment_label(monkeypatch):
    eqs = [_FakeEvalQuery(), _FakeEvalQuery()]
    monkeypatch.setattr(experiments, "run_evaluation", lambda db, eq, k: _FakeEvalRun(eq.id))

    result = experiments.run_experiment(_FakeDb(), eqs, experiments.PARENT_CHILD, k=5)

    assert len(result.runs) == 2
    assert all(r.experiment_label == "parent_child" for r in result.runs)


def test_run_experiment_uses_the_right_flags_for_each_call(monkeypatch):
    captured = []

    def _fake(db, eq, k):
        captured.append((settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled))
        return _FakeEvalRun(eq.id)

    monkeypatch.setattr(experiments, "run_evaluation", _fake)

    experiments.run_experiment(_FakeDb(), [_FakeEvalQuery()], experiments.QUERY_REWRITE, k=5)

    assert captured == [(False, True)]


# --------------------------------------------------------------- compare()

def test_compare_computes_average_and_delta():
    q1, q2 = uuid.uuid4(), uuid.uuid4()
    baseline = experiments.ExperimentResult(experiments.BASELINE, [
        _FakeEvalRun(q1, recall_at_k=0.5), _FakeEvalRun(q2, recall_at_k=0.7),
    ])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [
        _FakeEvalRun(q1, recall_at_k=0.6), _FakeEvalRun(q2, recall_at_k=0.9),
    ])

    comparisons = experiments.compare(baseline, experiment)
    recall = next(c for c in comparisons if c.metric == "recall_at_k")

    assert recall.baseline_avg == pytest.approx(0.6)
    assert recall.experiment_avg == pytest.approx(0.75)
    assert recall.delta == pytest.approx(0.15)
    assert recall.delta_pct == pytest.approx(25.0)
    assert recall.status == "measured"


def test_compare_marks_metric_unavailable_when_not_measured_on_either_side():
    q1 = uuid.uuid4()
    baseline = experiments.ExperimentResult(experiments.BASELINE, [_FakeEvalRun(q1, faithfulness=None)])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [_FakeEvalRun(q1, faithfulness=None)])

    comparisons = experiments.compare(baseline, experiment)
    faithfulness = next(c for c in comparisons if c.metric == "faithfulness")

    assert faithfulness.status == "unavailable"
    assert faithfulness.delta is None
    assert faithfulness.baseline_avg is None
    assert faithfulness.experiment_avg is None


def test_compare_never_treats_unavailable_as_zero():
    q1 = uuid.uuid4()
    baseline = experiments.ExperimentResult(experiments.BASELINE, [_FakeEvalRun(q1, cost_usd=0.02)])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [_FakeEvalRun(q1, cost_usd=None)])

    comparisons = experiments.compare(baseline, experiment)
    cost = next(c for c in comparisons if c.metric == "cost_usd")

    assert cost.status == "unavailable"
    assert cost.delta is None  # not -0.02, which is what silently treating None as 0 would produce


# --------------------------------------------------------- paired_comparison()

def test_paired_comparison_counts_improved_degraded_unchanged():
    q1, q2, q3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    baseline = experiments.ExperimentResult(experiments.BASELINE, [
        _FakeEvalRun(q1, recall_at_k=0.5), _FakeEvalRun(q2, recall_at_k=0.5), _FakeEvalRun(q3, recall_at_k=0.5),
    ])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [
        _FakeEvalRun(q1, recall_at_k=0.8), _FakeEvalRun(q2, recall_at_k=0.2), _FakeEvalRun(q3, recall_at_k=0.5),
    ])

    delta = experiments.paired_comparison(baseline, experiment, "recall_at_k")

    assert delta.improved == 1
    assert delta.degraded == 1
    assert delta.unchanged == 1
    assert delta.skipped_unavailable == 0


def test_paired_comparison_skips_pairs_missing_either_side():
    q1, q2 = uuid.uuid4(), uuid.uuid4()
    baseline = experiments.ExperimentResult(experiments.BASELINE, [
        _FakeEvalRun(q1, faithfulness=0.9), _FakeEvalRun(q2, faithfulness=None),
    ])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [
        _FakeEvalRun(q1, faithfulness=None), _FakeEvalRun(q2, faithfulness=0.9),
    ])

    delta = experiments.paired_comparison(baseline, experiment, "faithfulness")

    assert delta.improved == 0
    assert delta.degraded == 0
    assert delta.unchanged == 0
    assert delta.skipped_unavailable == 2


# ---------------------------------------------- generation_availability() / 401

def test_generation_availability_detects_401_in_generated_answer():
    runs = [_FakeEvalRun(uuid.uuid4(), generated_answer="[generation failed: Error code: 401 - authentication_error]")]

    assert experiments.generation_availability(runs) == "Generation evaluation unavailable: Anthropic authentication failed."


def test_generation_availability_detects_401_in_judge_notes_only():
    runs = [_FakeEvalRun(uuid.uuid4(), generated_answer="a real answer", judge_notes="judge failed: 401 authentication_error")]

    assert "unavailable" in experiments.generation_availability(runs)


def test_generation_availability_reports_partial_failure():
    runs = [
        _FakeEvalRun(uuid.uuid4(), generated_answer="[generation failed: 401 authentication_error]"),
        _FakeEvalRun(uuid.uuid4(), generated_answer="a real, successful answer", judge_notes=None),
    ]

    result = experiments.generation_availability(runs)

    assert "partially unavailable" in result
    assert "1/2" in result


def test_generation_availability_reports_success_when_no_failures():
    runs = [_FakeEvalRun(uuid.uuid4(), generated_answer="a fine answer", judge_notes="looks good")]

    assert experiments.generation_availability(runs) == "Generation evaluation available."


def test_generation_availability_empty_runs():
    assert experiments.generation_availability([]) == "No runs to evaluate."


# --------------------------------------------------------------- _recommend()

def test_recommend_insufficient_evidence_for_small_dataset():
    baseline = experiments.ExperimentResult(experiments.BASELINE, [_FakeEvalRun(uuid.uuid4(), faithfulness=0.9)])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [_FakeEvalRun(uuid.uuid4(), faithfulness=0.95)])

    report = experiments.build_feature_report("parent_child_retrieval", baseline, experiment)

    assert report.recommendation == "INSUFFICIENT EVIDENCE"
    assert any("too small" in r for r in report.recommendation_reasons)


def test_recommend_insufficient_evidence_when_no_generation_metrics_measured():
    baseline_runs = [_FakeEvalRun(uuid.uuid4()) for _ in range(6)]
    experiment_runs = [_FakeEvalRun(r.eval_query_id) for r in baseline_runs]  # same query ids; all quality fields None
    baseline = experiments.ExperimentResult(experiments.BASELINE, baseline_runs)
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, experiment_runs)

    report = experiments.build_feature_report("parent_child_retrieval", baseline, experiment)

    assert report.recommendation == "INSUFFICIENT EVIDENCE"
    assert any("No generation-quality metrics" in r for r in report.recommendation_reasons)


def test_recommend_enable_when_quality_improves_with_no_regression():
    ids = [uuid.uuid4() for _ in range(6)]
    baseline = experiments.ExperimentResult(experiments.BASELINE, [
        _FakeEvalRun(i, faithfulness=0.7, hallucination_rate=0.1, cost_usd=0.01) for i in ids
    ])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [
        _FakeEvalRun(i, faithfulness=0.9, hallucination_rate=0.05, cost_usd=0.01) for i in ids
    ])

    report = experiments.build_feature_report("parent_child_retrieval", baseline, experiment)

    assert report.recommendation == "RECOMMEND ENABLE"


def test_recommend_keep_disabled_when_quality_degrades():
    ids = [uuid.uuid4() for _ in range(6)]
    baseline = experiments.ExperimentResult(experiments.BASELINE, [_FakeEvalRun(i, faithfulness=0.9) for i in ids])
    experiment = experiments.ExperimentResult(experiments.PARENT_CHILD, [_FakeEvalRun(i, faithfulness=0.5) for i in ids])

    report = experiments.build_feature_report("parent_child_retrieval", baseline, experiment)

    assert report.recommendation == "KEEP DISABLED"


def test_recommend_keep_disabled_when_quality_improves_but_cost_regresses():
    ids = [uuid.uuid4() for _ in range(6)]
    baseline = experiments.ExperimentResult(experiments.BASELINE, [
        _FakeEvalRun(i, faithfulness=0.7, cost_usd=0.01) for i in ids
    ])
    experiment = experiments.ExperimentResult(experiments.QUERY_REWRITE, [
        _FakeEvalRun(i, faithfulness=0.9, cost_usd=0.05) for i in ids
    ])

    report = experiments.build_feature_report("query_rewriting", baseline, experiment)

    assert report.recommendation == "KEEP DISABLED"
    assert any("measured cost" in r for r in report.recommendation_reasons)


# -------------------------------------------------------------------- run_gate()

def test_run_gate_runs_baseline_plus_requested_experiments(monkeypatch):
    calls = []

    def _fake_run_evaluation(db, eq, k):
        calls.append((settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled))
        return _FakeEvalRun(eq.id)

    monkeypatch.setattr(experiments, "run_evaluation", _fake_run_evaluation)
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False

    report = experiments.run_gate(_FakeDb(), [_FakeEvalQuery()], k=5, include_combined=True)

    assert report.experiments_run == ["baseline", "parent_child", "query_rewrite", "combined"]
    assert calls == [(False, False), (True, False), (False, True), (True, True)]
    assert settings.parent_child_retrieval_enabled is False
    assert settings.query_rewriting_enabled is False


def test_run_gate_combined_result_has_no_recommendation(monkeypatch):
    monkeypatch.setattr(experiments, "run_evaluation", lambda db, eq, k: _FakeEvalRun(eq.id))

    report = experiments.run_gate(_FakeDb(), [_FakeEvalQuery()], k=5, include_combined=True)

    assert report.combined is not None
    assert not hasattr(report.combined, "recommendation")  # it's an ExperimentResult, not a FeatureReport


def test_run_gate_can_skip_experiments(monkeypatch):
    monkeypatch.setattr(experiments, "run_evaluation", lambda db, eq, k: _FakeEvalRun(eq.id))

    report = experiments.run_gate(
        _FakeDb(), [_FakeEvalQuery()], k=5, include_parent_child=False, include_query_rewrite=True,
    )

    assert report.parent_child is None
    assert report.query_rewrite is not None
    assert report.experiments_run == ["baseline", "query_rewrite"]


def test_run_gate_defaults_do_not_run_combined(monkeypatch):
    monkeypatch.setattr(experiments, "run_evaluation", lambda db, eq, k: _FakeEvalRun(eq.id))

    report = experiments.run_gate(_FakeDb(), [_FakeEvalQuery()], k=5)

    assert report.combined is None
    assert "combined" not in report.experiments_run
