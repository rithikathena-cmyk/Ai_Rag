import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-null = pinned, sorted to the top of the conversation list; the
    # timestamp itself (not just a bool) lets multiple pinned conversations
    # order by most-recently-pinned rather than an arbitrary/insertion order.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Total messages ever stored (cheap threshold check without a COUNT query).
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # How many of the oldest messages have already been folded into `summary`
    # — lets maybe_summarize() fold only the newly-eligible slice each time
    # instead of resending already-summarized turns back to Claude.
    summarized_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
