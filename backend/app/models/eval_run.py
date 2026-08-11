import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class EvalRunModel(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eval_query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_queries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    k: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    # Retrieval metrics — null when the eval query has no curated ground
    # truth (expected_chunk_ids) to score against.
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recall_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    mrr: Mapped[float | None] = mapped_column(Float, nullable=True)
    ndcg_at_k: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Generation metrics — LLM-as-judge scores of the chat agent's answer to
    # this query against whatever sources it actually retrieved.
    generated_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    groundedness: Mapped[float | None] = mapped_column(Float, nullable=True)
    faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    hallucination_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 2 evaluation completeness — citation_accuracy/answer_relevance
    # come from the same judge_answer() call as groundedness/faithfulness
    # above (see generation_judge.py's v2 prompt), not a second judge call.
    citation_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    answer_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Sum of every gateway_usage_logs row sharing this run's request_id
    # (planner tool-loop turns + the judge call) — read back from the
    # existing Claude Gateway audit trail, not re-derived. total_latency_ms
    # = retrieval_latency_ms + generation_latency_ms + judge latency.
    total_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Phase 3 evaluation gate (services/evaluation/experiments.py) — which
    # named feature-flag configuration produced this run ("baseline",
    # "parent_child", "query_rewrite", "combined"), or null for a run made
    # outside the experiment runner (e.g. a manual click on the Evaluation
    # page's "Run" button) — those aren't part of any controlled comparison.
    experiment_label: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Evaluation architecture correction (docs/RAG_RETRIEVAL.md "Evaluation
    # Architecture Correction") — records exactly what the production
    # retrieval path actually did for this run: the original vs. effective
    # (possibly rewritten) query, the real _maybe_rewrite_query() trace entry
    # (or null if query rewriting was off), which flags were in effect, and
    # which retrieved chunks got parent-context expansion. This is the
    # evidence that Phase 3A/3B code actually executed for this run, not a
    # reconstruction after the fact — see runner.py::run_evaluation().
    retrieval_trace: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
