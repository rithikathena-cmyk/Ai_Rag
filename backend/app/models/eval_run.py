import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text, func
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
