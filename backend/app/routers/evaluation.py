import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.eval_query import EvalQueryModel
from app.models.eval_run import EvalRunModel
from app.services.evaluation.runner import run_evaluation

router = APIRouter(prefix="/eval", tags=["evaluation"])


class EvalQueryCreateRequest(BaseModel):
    query: str = Field(min_length=1)
    description: str | None = None
    expected_chunk_ids: list[str] = Field(default_factory=list)


class EvalQueryResponse(BaseModel):
    id: uuid.UUID
    query: str
    description: str | None
    expected_chunk_ids: list[str]
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
    judge_notes: str | None
    generation_latency_ms: float | None
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


def _query_to_response(row: EvalQueryModel) -> EvalQueryResponse:
    return EvalQueryResponse(
        id=row.id, query=row.query, description=row.description,
        expected_chunk_ids=row.expected_chunk_ids or [], created_at=row.created_at,
    )


def _run_to_response(row: EvalRunModel) -> EvalRunResponse:
    return EvalRunResponse(
        id=row.id, eval_query_id=row.eval_query_id, k=row.k,
        retrieved_chunk_ids=row.retrieved_chunk_ids or [],
        recall_at_k=row.recall_at_k, precision_at_k=row.precision_at_k,
        mrr=row.mrr, ndcg_at_k=row.ndcg_at_k, retrieval_latency_ms=row.retrieval_latency_ms,
        generated_answer=row.generated_answer, groundedness=row.groundedness,
        faithfulness=row.faithfulness, hallucination_rate=row.hallucination_rate,
        judge_notes=row.judge_notes, generation_latency_ms=row.generation_latency_ms,
        created_at=row.created_at,
    )


@router.post("/queries", response_model=EvalQueryResponse, status_code=201)
def create_eval_query(body: EvalQueryCreateRequest, db: Session = Depends(get_db)):
    row = EvalQueryModel(query=body.query, description=body.description, expected_chunk_ids=body.expected_chunk_ids)
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
    run = run_evaluation(db, row, k=max(1, min(k, 50)))
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
    )
