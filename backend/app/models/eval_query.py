import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class EvalQueryModel(Base):
    __tablename__ = "eval_queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    # Widened from String(512) to Text (docs/RAG_RETRIEVAL.md "Evaluation
    # Dataset Expansion") — curated cases now carry expected-answer criteria
    # and citation evidence pointers here, which can exceed 512 chars.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ground truth for retrieval metrics: chunk ids (as strings) considered
    # relevant to this query. Empty until an admin curates it, at which point
    # retrieval metrics become computable for this query.
    expected_chunk_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Evaluation Dataset Expansion (docs/RAG_RETRIEVAL.md) — free-form tags
    # (e.g. "direct_fact", "parent_context", "rewrite_candidate") used to
    # break results down by question type rather than only in aggregate.
    # Empty by default; a case may carry zero, one, or several tags — never
    # forced into a category that doesn't genuinely apply.
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
