"""Phase 3 evaluation gate (docs/RAG_RETRIEVAL.md's "Phase 3 Evaluation
Results"). Runs the existing, unmodified evaluation pipeline
(services/evaluation/runner.py::run_evaluation()) under different Phase
3A/3B feature-flag combinations and compares the results.

This module does not touch parent-child or query-rewriting logic at all —
it only toggles the same two app/core/config.py Settings flags those
features already read internally, for the duration of one experiment's
runs, via a context manager that restores their exact prior values
afterward (see _temporary_flags()). Nothing about application configuration
changes permanently as a result of running this; the shipped defaults
(parent_child_retrieval_enabled=False, query_rewriting_enabled=False) are
untouched by importing or running this module.
"""

import statistics
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.eval_query import EvalQueryModel
from app.models.eval_run import EvalRunModel
from app.services.evaluation.runner import run_evaluation

# --------------------------------------------------------------- configs ---


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    parent_child_retrieval_enabled: bool
    query_rewriting_enabled: bool


BASELINE = ExperimentConfig("baseline", parent_child_retrieval_enabled=False, query_rewriting_enabled=False)
PARENT_CHILD = ExperimentConfig("parent_child", parent_child_retrieval_enabled=True, query_rewriting_enabled=False)
QUERY_REWRITE = ExperimentConfig("query_rewrite", parent_child_retrieval_enabled=False, query_rewriting_enabled=True)
COMBINED = ExperimentConfig("combined", parent_child_retrieval_enabled=True, query_rewriting_enabled=True)

CONFIGS_BY_NAME = {c.name: c for c in (BASELINE, PARENT_CHILD, QUERY_REWRITE, COMBINED)}


@contextmanager
def _temporary_flags(config: ExperimentConfig):
    """Sets the two Phase 3 feature flags for the duration of the block,
    restoring their exact prior values afterward — even on exception. This
    is the only application state this module ever changes."""
    original = (settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled)
    settings.parent_child_retrieval_enabled = config.parent_child_retrieval_enabled
    settings.query_rewriting_enabled = config.query_rewriting_enabled
    try:
        yield
    finally:
        settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled = original


# ------------------------------------------------------------- running -----


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    runs: list[EvalRunModel] = field(default_factory=list)


def run_experiment(
    db: Session, eval_queries: list[EvalQueryModel], config: ExperimentConfig, *, k: int = 10
) -> ExperimentResult:
    """Runs every query in `eval_queries` once under `config`, tagging each
    resulting row with experiment_label=config.name. Uses run_evaluation()
    completely unmodified — the identical function Phase 2 built and every
    other evaluation call site already uses — so a comparison here is
    apples-to-apples with a manually-triggered run on the Evaluation page."""
    runs = []
    with _temporary_flags(config):
        for eq in eval_queries:
            run = run_evaluation(db, eq, k=k)
            run.experiment_label = config.name
            db.add(run)
            db.commit()
            db.refresh(run)
            runs.append(run)
    return ExperimentResult(config=config, runs=runs)


# ----------------------------------------------------------- comparison ----

NUMERIC_METRICS = [
    "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
    "citation_accuracy", "answer_relevance", "faithfulness", "hallucination_rate",
    "retrieval_latency_ms", "generation_latency_ms", "total_latency_ms",
    "tokens_input", "tokens_output", "cost_usd",
]

# Direction that counts as "better" — used only by _recommend() below to
# interpret a delta's sign; the raw comparison/paired numbers themselves are
# direction-agnostic (just "experiment minus baseline").
HIGHER_IS_BETTER = frozenset({
    "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k", "citation_accuracy", "answer_relevance", "faithfulness",
})
LOWER_IS_BETTER = frozenset({
    "hallucination_rate", "total_latency_ms", "retrieval_latency_ms", "generation_latency_ms",
    "tokens_input", "tokens_output", "cost_usd",
})


@dataclass
class MetricComparison:
    metric: str
    baseline_avg: float | None
    experiment_avg: float | None
    delta: float | None  # experiment - baseline; None unless both sides measured
    delta_pct: float | None
    status: str  # "measured" | "unavailable"


def _avg(runs: list[EvalRunModel], field_name: str) -> float | None:
    values = [getattr(r, field_name) for r in runs if getattr(r, field_name) is not None]
    return statistics.fmean(values) if values else None


def compare(baseline: ExperimentResult, experiment: ExperimentResult) -> list[MetricComparison]:
    """Average-based comparison across every run in each ExperimentResult.
    A metric with no measurable value on either side is "unavailable", never
    coerced to 0 — an unavailable generation metric must never look like a
    measured zero."""
    comparisons = []
    for metric in NUMERIC_METRICS:
        b = _avg(baseline.runs, metric)
        e = _avg(experiment.runs, metric)
        measured = b is not None and e is not None
        delta = (e - b) if measured else None
        delta_pct = (delta / b * 100) if (measured and b != 0) else None
        comparisons.append(MetricComparison(
            metric=metric, baseline_avg=b, experiment_avg=e, delta=delta, delta_pct=delta_pct,
            status="measured" if measured else "unavailable",
        ))
    return comparisons


@dataclass
class PairedMetricDelta:
    metric: str
    improved: int
    degraded: int
    unchanged: int
    skipped_unavailable: int


def paired_comparison(baseline: ExperimentResult, experiment: ExperimentResult, metric: str) -> PairedMetricDelta:
    """Per-question comparison, paired by eval_query_id. "improved" means
    the experiment's value was numerically higher than baseline's for that
    specific question — direction-agnostic (see HIGHER_IS_BETTER/
    LOWER_IS_BETTER, only consulted by _recommend()). A pair where either
    side is unavailable is counted separately and excluded from
    improved/degraded/unchanged — never treated as a tie."""
    baseline_by_query = {r.eval_query_id: r for r in baseline.runs}
    experiment_by_query = {r.eval_query_id: r for r in experiment.runs}
    improved = degraded = unchanged = skipped = 0
    for query_id, b_run in baseline_by_query.items():
        e_run = experiment_by_query.get(query_id)
        if e_run is None:
            continue
        b_val, e_val = getattr(b_run, metric), getattr(e_run, metric)
        if b_val is None or e_val is None:
            skipped += 1
        elif e_val > b_val:
            improved += 1
        elif e_val < b_val:
            degraded += 1
        else:
            unchanged += 1
    return PairedMetricDelta(metric=metric, improved=improved, degraded=degraded, unchanged=unchanged, skipped_unavailable=skipped)


# ------------------------------------------------------- generation status -

def _looks_like_auth_failure(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return "authentication_error" in lowered or "401" in text


def generation_availability(runs: list[EvalRunModel]) -> str:
    """A clean, explicit statement of whether generation-quality metrics
    could actually be measured — per instruction, an Anthropic auth failure
    must be reported as exactly this, never silently left as null with no
    explanation."""
    if not runs:
        return "No runs to evaluate."
    failures = [r for r in runs if _looks_like_auth_failure(r.generated_answer) or _looks_like_auth_failure(r.judge_notes)]
    if len(failures) == len(runs):
        return "Generation evaluation unavailable: Anthropic authentication failed."
    if failures:
        return f"Generation evaluation partially unavailable: {len(failures)}/{len(runs)} run(s) hit an Anthropic authentication failure."
    if any(r.generated_answer and "generation failed" in r.generated_answer for r in runs):
        return "Generation evaluation partially unavailable: one or more runs failed for a non-authentication reason — see judge_notes/generated_answer."
    return "Generation evaluation available."


# ------------------------------------------------------------- reporting ---

MIN_DATASET_SIZE_FOR_A_VERDICT = 5


@dataclass
class FeatureReport:
    feature_name: str
    dataset_size: int
    generation_status: str
    comparisons: list[MetricComparison]
    paired_deltas: list[PairedMetricDelta]
    recommendation: str  # "RECOMMEND ENABLE" | "KEEP DISABLED" | "INSUFFICIENT EVIDENCE"
    recommendation_reasons: list[str]


def _recommend(comparisons: list[MetricComparison], dataset_size: int, generation_status: str) -> tuple[str, list[str]]:
    """Transparent, auditable heuristic — every branch appends a plain-
    English reason, so the verdict is never a black box. Deliberately
    conservative: any missing prerequisite (dataset size, measurable
    quality metrics) is an automatic INSUFFICIENT EVIDENCE, not a guess."""
    reasons = []

    if dataset_size < MIN_DATASET_SIZE_FOR_A_VERDICT:
        reasons.append(
            f"Only {dataset_size} eval quer{'y' if dataset_size == 1 else 'ies'} in the dataset — "
            f"too small for a reliable conclusion (would want at least {MIN_DATASET_SIZE_FOR_A_VERDICT})."
        )

    quality = {c.metric: c for c in comparisons if c.metric in HIGHER_IS_BETTER}
    measured_quality = {m: c for m, c in quality.items() if c.status == "measured"}
    if not measured_quality:
        reasons.append(f"No generation-quality metrics were measurable ({generation_status}) — cannot assess answer-quality impact.")

    if dataset_size < MIN_DATASET_SIZE_FOR_A_VERDICT or not measured_quality:
        return "INSUFFICIENT EVIDENCE", reasons

    improved = [c for c in measured_quality.values() if c.delta is not None and c.delta > 0]
    degraded = [c for c in measured_quality.values() if c.delta is not None and c.delta < 0]
    reasons.append(f"{len(improved)}/{len(measured_quality)} measured quality metrics improved, {len(degraded)}/{len(measured_quality)} degraded.")

    regressions = {c.metric: c for c in comparisons if c.metric in LOWER_IS_BETTER and c.status == "measured"}
    worsened = [c for c in regressions.values() if c.delta is not None and c.delta > 0]
    if worsened:
        reasons.append("Cost/latency/hallucination increased on: " + ", ".join(c.metric for c in worsened) + ".")

    if degraded:
        reasons.append("At least one quality metric got worse — not a clean win.")
        return "KEEP DISABLED", reasons
    if improved and worsened:
        reasons.append("Quality improved but at a measured cost — judgment call, leaning toward caution.")
        return "KEEP DISABLED", reasons
    if improved:
        reasons.append("Quality improved with no measured regression.")
        return "RECOMMEND ENABLE", reasons

    reasons.append("No clear directional signal either way.")
    return "INSUFFICIENT EVIDENCE", reasons


def build_feature_report(feature_name: str, baseline: ExperimentResult, experiment: ExperimentResult) -> FeatureReport:
    comparisons = compare(baseline, experiment)
    paired = [paired_comparison(baseline, experiment, m) for m in NUMERIC_METRICS]
    gen_status = generation_availability(experiment.runs)
    dataset_size = len(experiment.runs)
    recommendation, reasons = _recommend(comparisons, dataset_size, gen_status)
    return FeatureReport(
        feature_name=feature_name, dataset_size=dataset_size, generation_status=gen_status,
        comparisons=comparisons, paired_deltas=paired, recommendation=recommendation,
        recommendation_reasons=reasons,
    )


@dataclass
class GateReport:
    dataset_size: int
    experiments_run: list[str]
    baseline: ExperimentResult
    parent_child: FeatureReport | None
    query_rewrite: FeatureReport | None
    combined: ExperimentResult | None = None


def run_gate(
    db: Session, eval_queries: list[EvalQueryModel], *, k: int = 10,
    include_parent_child: bool = True, include_query_rewrite: bool = True, include_combined: bool = False,
) -> GateReport:
    """Runs baseline + whichever experiments are requested against the same
    `eval_queries`, in the same db session, back-to-back: the identical
    dataset/questions/expected-documents/model-tier ('fast', unless a caller
    of run_evaluation() elsewhere changes that default)/judge-prompt-version/
    top-K/reranker/permission-context (run_evaluation() never passes a
    role/department — every condition is equally unrestricted)/database-state
    for every condition. The only thing that varies across conditions is the
    feature-flag combination itself.

    combined's result is intentionally returned without a FeatureReport/
    recommendation — a combined win or loss says nothing about either
    feature in isolation, per instruction."""
    baseline_result = run_experiment(db, eval_queries, BASELINE, k=k)
    experiments_run = [BASELINE.name]

    parent_child_report = None
    if include_parent_child:
        pc_result = run_experiment(db, eval_queries, PARENT_CHILD, k=k)
        experiments_run.append(PARENT_CHILD.name)
        parent_child_report = build_feature_report("parent_child_retrieval", baseline_result, pc_result)

    query_rewrite_report = None
    if include_query_rewrite:
        qr_result = run_experiment(db, eval_queries, QUERY_REWRITE, k=k)
        experiments_run.append(QUERY_REWRITE.name)
        query_rewrite_report = build_feature_report("query_rewriting", baseline_result, qr_result)

    combined_result = None
    if include_combined:
        combined_result = run_experiment(db, eval_queries, COMBINED, k=k)
        experiments_run.append(COMBINED.name)

    return GateReport(
        dataset_size=len(eval_queries), experiments_run=experiments_run, baseline=baseline_result,
        parent_child=parent_child_report, query_rewrite=query_rewrite_report, combined=combined_result,
    )
