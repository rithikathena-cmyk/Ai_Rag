import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user", server_default="user")
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Per-user overrides of the role's default token quotas (llm_rbac.yaml).
    # NULL means "use the role default" — set by Admin/CEO via
    # PUT /users/{id}/token-limit, see routers/users.py.
    daily_token_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_token_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
