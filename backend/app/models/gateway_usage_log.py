import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class GatewayUsageLogModel(Base):
    """One row per Claude Gateway generate()/stream() call — the durable
    counterpart to the in-memory record_token_usage() samples in
    services/monitoring/metrics.py, which still gets written to as well so
    the existing admin dashboard keeps working unchanged.

    Also the LLM-RBAC audit log (docs/AUDIT_LOGGING.md): the user_id/role/
    department/decision/tool_calls/documents_retrieved columns are written by
    services/llm_rbac/engine.py + gateway/usage_tracker.py for both allowed
    and denied requests, so this table is the single durable "who did what,
    was it allowed" record the spec asks for.
    """

    __tablename__ = "gateway_usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The `action` capability name from llm_rbac.yaml's permission catalog
    # (e.g. "workforce_planning"), when the caller supplied one — structured
    # counterpart to denial_reason's free text, per docs/AUDIT_LOGGING.md.
    requested_capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Report-generation audit fields (services/llm_rbac/report_policy.py) —
    # both NULL on a turn that didn't produce/request a report.
    output_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    resource_scope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    documents_retrieved: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="allowed", server_default="allowed")
    denial_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
