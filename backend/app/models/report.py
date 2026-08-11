import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class ReportModel(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # LLM-RBAC access-control fields (see docs/KNOWLEDGE_ACCESS_CONTROL.md's
    # report-visibility rule, the same NULL-is-public precedent
    # apply_category_policy() uses for documents): `owner_id` is the user
    # whose planner turn generated this report; `department` is that user's
    # resolved department at generation time, denormalized so a later
    # department change never rewrites which reports are visible in
    # hindsight. NULL on both = a pre-existing report generated before this
    # column existed, treated as visible to everyone (nothing currently
    # visible becomes invisible on deploy day).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    department: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
