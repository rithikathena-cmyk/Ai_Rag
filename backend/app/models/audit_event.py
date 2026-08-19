import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class AuditEventModel(Base):
    """The unified activity/audit trail (docs/AUDIT_LOGGING.md) — every
    AuditLogger.log() call (services/audit/logger.py) writes one row here.
    Distinct from, and does not replace, GatewayUsageLogModel (LLM-RBAC
    gateway calls specifically) or UploadLogModel (document ingestion
    specifically) — those stay as the detailed, high-volume records for
    their own domains; this table is the cross-cutting "who did what, when,
    with what outcome" trail spanning auth/RBAC/documents/RAG/guardrails/
    security/system events, per the taxonomy in event_types.py.

    Append-only by construction: no router exposes PUT/DELETE against this
    table (routers/audit.py is GET-only).

    metadata is a fixed-allowlist, PII-sanitized JSONB blob — never a raw
    request/response object serialized in — see AuditLogger.log()'s own
    docstring for the sanitization pipeline. Named `metadata_` at the Python
    attribute level because `metadata` is reserved by SQLAlchemy's
    Declarative Base; the actual database column is still named `metadata`.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)

    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # No real server-side session concept exists in this app (stateless JWT
    # — see services/auth/jwt.py) — left nullable/unpopulated rather than
    # inventing one. Reserved for if/when one is added.
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Reserved, unpopulated this pass — capturing client IPs at all (even
    # keyed-hashed) is a product/legal scope decision, not defaulted here.
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
