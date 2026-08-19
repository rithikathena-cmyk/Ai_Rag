import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.roles import Role
from app.db.postgres import get_db
from app.models.eval_query import EvalQueryModel
from app.models.eval_run import EvalRunModel
from app.services.auth.rbac import require_role
from app.services.evaluation import experiments
from app.services.evaluation.runner import EvaluationRetrievalError, run_evaluation

# Previously had NO auth on any route in this file, including the
# cost-heavy experiment-gate runner (real LLM calls across the whole eval
# set). Gated by role rather than a coarse permission — this is a
# cost-sensitive operational tool (retrieval quality testing), not general
# analytics viewing, so it stays limited to Admin/CEO/Project Manager
# regardless of who else has VIEW_ANALYTICS (e.g. HR).
router = APIRouter(
    prefix="/eval", tags=["evaluation"],
    dependencies=[Depends(require_role(Role.ADMIN, Role.CEO, Role.PROJECT_MANAGER))],
)


class EvalQueryCreateRequest(BaseModel):
    query: str = Field(min_length=1)
    description: str | None = None
    expected_chunk_ids: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class EvalQueryResponse(BaseModel):
    id: uuid.UUID
    query: str
    description: str | None
    expected_chunk_ids: list[str]
    categories: list[str]
    created_at: datetime


class EvalRunResponse(BaseModel):
    id: uuid.UUID
    eval_query_id: uuid.UUID
    k: int
    retrieved_chunk_ids: list[str]
    recall_at_k: float | None
    precision_at_k: float | None
    mrr: float | None
    ndcg_at_k: float | None
    retrieval_latency_ms: float | None
    generated_answer: str | None
    groundedness: float | None
    faithfulness: float | None
    hallucination_rate: float | None
    # Phase 2 evaluation completeness (docs/ARCHITECTURE_ENHANCEMENT_PLAN.md).
    citation_accuracy: float | None
    answer_relevance: float | None
    judge_notes: str | None
    generation_latency_ms: float | None
    total_latency_ms: float | None
    tokens_input: int | None
    tokens_output: int | None
    total_tokens: int | None
    cost_usd: float | None
    model: str | None
    # Phase 3 evaluation gate (services/evaluation/experiments.py) — which
    # experiment configuration produced this run, or null for a run made
    # outside the gate (e.g. the "Run" button on the Eval queries tab).
    experiment_label: str | None
    # Evaluation architecture correction — proof of what the production
    # retrieval path actually did for this run (rewrite/parent-context
    # execution evidence). See services/evaluation/runner.py.
    retrieval_trace: dict | None
    created_at: datetime


class EvalSummaryResponse(BaseModel):
    run_count: int
    avg_recall_at_k: float | None
    avg_precision_at_k: float | None
    avg_mrr: float | None
    avg_ndcg_at_k: float | None
    avg_groundedness: float | None
    avg_faithfulness: float | None
    avg_hallucination_rate: float | None
    avg_retrieval_latency_ms: float | None
    avg_generation_latency_ms: float | None
    # Phase 2 evaluation completeness.
    avg_citation_accuracy: float | None
    avg_answer_relevance: float | None
    avg_total_latency_ms: float | None
    avg_tokens_input: float | None
    avg_tokens_output: float | None
    avg_cost_usd: float | None
    total_cost_usd: float | None


def _query_to_response(row: EvalQueryModel) -> EvalQueryResponse:
    return EvalQueryResponse(
        id=row.id, query=row.query, description=row.description,
        expected_chunk_ids=row.expected_chunk_ids or [], categories=row.categories or [],
        created_at=row.created_at,
    )


def _run_to_response(row: EvalRunModel) -> EvalRunResponse:
    total_tokens = (
        row.tokens_input + row.tokens_output if row.tokens_input is not None and row.tokens_output is not None
        else None
    )
    return EvalRunResponse(
        id=row.id, eval_query_id=row.eval_query_id, k=row.k,
        retrieved_chunk_ids=row.retrieved_chunk_ids or [],
        recall_at_k=row.recall_at_k, precision_at_k=row.precision_at_k,
        mrr=row.mrr, ndcg_at_k=row.ndcg_at_k, retrieval_latency_ms=row.retrieval_latency_ms,
        generated_answer=row.generated_answer, groundedness=row.groundedness,
        faithfulness=row.faithfulness, hallucination_rate=row.hallucination_rate,
        citation_accuracy=row.citation_accuracy, answer_relevance=row.answer_relevance,
        judge_notes=row.judge_notes, generation_latency_ms=row.generation_latency_ms,
        total_latency_ms=row.total_latency_ms, tokens_input=row.tokens_input, tokens_output=row.tokens_output,
        total_tokens=total_tokens, cost_usd=row.cost_usd, model=row.model,
        experiment_label=row.experiment_label, retrieval_trace=row.retrieval_trace, created_at=row.created_at,
    )


@router.post("/queries", response_model=EvalQueryResponse, status_code=201)
def create_eval_query(body: EvalQueryCreateRequest, db: Session = Depends(get_db)):
    row = EvalQueryModel(
        query=body.query, description=body.description,
        expected_chunk_ids=body.expected_chunk_ids, categories=body.categories,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _query_to_response(row)


@router.get("/queries", response_model=list[EvalQueryResponse])
def list_eval_queries(limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    rows = db.query(EvalQueryModel).order_by(EvalQueryModel.created_at.desc()).offset(offset).limit(limit).all()
    return [_query_to_response(r) for r in rows]


@router.delete("/queries/{query_id}", status_code=204)
def delete_eval_query(query_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(EvalQueryModel, query_id)
    if row is None:
        raise AppError(404, "eval_query_not_found", f"Eval query {query_id} not found")
    db.delete(row)
    db.commit()


@router.post("/queries/{query_id}/run", response_model=EvalRunResponse)
def run_eval_query(query_id: uuid.UUID, k: int = 10, db: Session = Depends(get_db)):
    row = db.get(EvalQueryModel, query_id)
    if row is None:
        raise AppError(404, "eval_query_not_found", f"Eval query {query_id} not found")
    try:
        run = run_evaluation(db, row, k=max(1, min(k, 50)))
    except EvaluationRetrievalError as exc:
        raise AppError(503, "retrieval_unavailable", str(exc)) from exc
    return _run_to_response(run)


@router.get("/runs", response_model=list[EvalRunResponse])
def list_eval_runs(
    eval_query_id: uuid.UUID | None = None, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)
):
    query = db.query(EvalRunModel)
    if eval_query_id is not None:
        query = query.filter(EvalRunModel.eval_query_id == eval_query_id)
    rows = query.order_by(EvalRunModel.created_at.desc()).offset(offset).limit(limit).all()
    return [_run_to_response(r) for r in rows]


@router.get("/summary", response_model=EvalSummaryResponse)
def eval_summary(db: Session = Depends(get_db)):
    rows = db.query(EvalRunModel).all()

    def _avg(field: str) -> float | None:
        values = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        return sum(values) / len(values) if values else None

    def _sum(field: str) -> float | None:
        values = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        return sum(values) if values else None

    return EvalSummaryResponse(
        run_count=len(rows),
        avg_recall_at_k=_avg("recall_at_k"),
        avg_precision_at_k=_avg("precision_at_k"),
        avg_mrr=_avg("mrr"),
        avg_ndcg_at_k=_avg("ndcg_at_k"),
        avg_groundedness=_avg("groundedness"),
        avg_faithfulness=_avg("faithfulness"),
        avg_hallucination_rate=_avg("hallucination_rate"),
        avg_retrieval_latency_ms=_avg("retrieval_latency_ms"),
        avg_generation_latency_ms=_avg("generation_latency_ms"),
        avg_citation_accuracy=_avg("citation_accuracy"),
        avg_answer_relevance=_avg("answer_relevance"),
        avg_total_latency_ms=_avg("total_latency_ms"),
        avg_tokens_input=_avg("tokens_input"),
        avg_tokens_output=_avg("tokens_output"),
        avg_cost_usd=_avg("cost_usd"),
        total_cost_usd=_sum("cost_usd"),
    )


# --------------------------------------------------- Phase 3 evaluation gate

class ExperimentGateRunRequest(BaseModel):
    k: int = 10
    eval_query_ids: list[uuid.UUID] | None = None  # None = use every curated eval query
    include_parent_child: bool = True
    include_query_rewrite: bool = True
    include_combined: bool = False  # combined result has no recommendation — see experiments.py


class MetricComparisonResponse(BaseModel):
    metric: str
    baseline_avg: float | None
    experiment_avg: float | None
    delta: float | None
    delta_pct: float | None
    status: str  # "measured" | "unavailable"


class PairedMetricDeltaResponse(BaseModel):
    metric: str
    improved: int
    degraded: int
    unchanged: int
    skipped_unavailable: int


class FeatureReportResponse(BaseModel):
    feature_name: str
    dataset_size: int
    generation_status: str
    comparisons: list[MetricComparisonResponse]
    paired_deltas: list[PairedMetricDeltaResponse]
    recommendation: str
    recommendation_reasons: list[str]


class ExperimentGateResponse(BaseModel):
    dataset_size: int
    experiments_run: list[str]
    baseline_runs: list[EvalRunResponse]
    parent_child: FeatureReportResponse | None
    query_rewrite: FeatureReportResponse | None
    combined_runs: list[EvalRunResponse] | None


def _feature_report_to_response(report: "experiments.FeatureReport") -> FeatureReportResponse:
    return FeatureReportResponse(
        feature_name=report.feature_name, dataset_size=report.dataset_size,
        generation_status=report.generation_status,
        comparisons=[MetricComparisonResponse(**vars(c)) for c in report.comparisons],
        paired_deltas=[PairedMetricDeltaResponse(**vars(d)) for d in report.paired_deltas],
        recommendation=report.recommendation, recommendation_reasons=report.recommendation_reasons,
    )


@router.post("/experiments/run", response_model=ExperimentGateResponse)
def run_experiment_gate(body: ExperimentGateRunRequest, db: Session = Depends(get_db)):
    """Phase 3 evaluation gate — runs the curated eval dataset under
    baseline/parent-child/query-rewrite (and optionally combined)
    configurations back-to-back, via services/evaluation/experiments.py,
    and returns a full comparison + evidence-based recommendation for each
    feature. Does not enable either feature — application defaults
    (parent_child_retrieval_enabled/query_rewriting_enabled, both False)
    are restored immediately after this request, regardless of outcome."""
    query = db.query(EvalQueryModel)
    if body.eval_query_ids:
        query = query.filter(EvalQueryModel.id.in_(body.eval_query_ids))
    eval_queries = query.order_by(EvalQueryModel.created_at).all()
    if not eval_queries:
        raise AppError(422, "no_eval_queries", "No eval queries available to run the experiment gate against")

    try:
        report = experiments.run_gate(
            db, eval_queries, k=max(1, min(body.k, 50)),
            include_parent_child=body.include_parent_child, include_query_rewrite=body.include_query_rewrite,
            include_combined=body.include_combined,
        )
    except EvaluationRetrievalError as exc:
        raise AppError(503, "retrieval_unavailable", str(exc)) from exc

    return ExperimentGateResponse(
        dataset_size=report.dataset_size, experiments_run=report.experiments_run,
        baseline_runs=[_run_to_response(r) for r in report.baseline.runs],
        parent_child=_feature_report_to_response(report.parent_child) if report.parent_child else None,
        query_rewrite=_feature_report_to_response(report.query_rewrite) if report.query_rewrite else None,
        combined_runs=[_run_to_response(r) for r in report.combined.runs] if report.combined else None,
    )
