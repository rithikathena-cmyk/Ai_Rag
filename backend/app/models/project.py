import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base

# Kept as plain strings (not a DB enum type) so a new status/priority value
# never needs a migration — services/projects/lifecycle.py is the single
# place that interprets `status` as a state machine.
PROJECT_STATUSES = (
    "draft", "submitted", "approved", "active", "paused", "completed", "closed", "rejected", "cancelled",
)
PROJECT_PRIORITIES = ("low", "medium", "high", "critical")


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # The Project Manager who owns this project — services/projects/service.py
    # scopes a PM's visibility/report data to manager_id == their own id (plus
    # membership, see ProjectMemberModel). SET NULL rather than CASCADE: a
    # deleted user shouldn't take their former projects down with them.
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", server_default="medium")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
