import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class RoleUsageCounterModel(Base):
    """Fast-lookup rollup for daily/monthly quota checks (services/llm_rbac/quotas.py).

    gateway_usage_logs stays the detailed, per-request audit trail; this table is a
    small pre-aggregated counter so services/llm_rbac/engine.py can check a role's
    daily/monthly token & cost budget with one indexed row lookup instead of
    re-aggregating the full log table on every request. Incremented in the same
    step as gateway/usage_tracker.py::record_usage().
    """

    __tablename__ = "role_usage_counters"
    __table_args__ = (UniqueConstraint("user_id", "period_type", "period_start", name="uq_role_usage_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)  # "day" | "month"
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
