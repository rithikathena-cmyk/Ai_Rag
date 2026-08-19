import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base

# Categories with a real, currently-hard-coded config surface this phase
# makes DB-backed and admin-editable — see the approved plan's "What's
# already built" table for exactly which Python/YAML source each category
# used to be the sole source of truth for. Not every guardrail check in this
# app has a category here yet (rate limits, groundedness, escalation stay
# where they are this phase — see the plan's "Explicit non-goals").
GUARDRAIL_POLICY_CATEGORIES = ("PII", "REGEX", "WORD_FILTER", "SEMANTIC", "PROMPT_INJECTION", "MESSAGE_LIMIT")
# FLAG replaces the earlier "WARN" name (same "continue, record a security
# event" meaning — nothing branched on the string, so this was a pure
# rename). MASK is new: a partial-reveal redaction distinct from REDACT's
# full replacement — see services/guardrails/pii.py's _mask_phone/_mask_email
# for the two entity types with a real masking scheme; every other label
# falls back to the same placeholder REDACT uses.
GUARDRAIL_POLICY_ACTIONS = ("ALLOW", "FLAG", "MASK", "REDACT", "BLOCK", "ESCALATE")
GUARDRAIL_POLICY_MODES = ("ENFORCE", "DRY_RUN")


class GuardrailPolicyModel(Base):
    """One admin-editable guardrail policy row. `configuration` is a
    category-specific JSONB blob whose shape is validated against a
    per-category Pydantic model (services/guardrail_policy/validation.py)
    BEFORE it ever reaches this table — this model itself does not (and
    cannot, at the ORM layer) enforce that shape, so every write path must
    go through that validation layer, never a raw ORM construction from
    unvalidated request data.

    `version` is an optimistic-locking counter (see routers/
    guardrail_policies.py's PATCH handler) — no existing table in this app
    does true compare-and-swap (DocumentModel's version_number is an
    append-new-row scheme, not conflict detection), so this is genuinely new
    here, not reused. Every version bump writes a
    GuardrailPolicyVersionModel row in the same transaction — history is
    never overwritten.
    """

    __tablename__ = "guardrail_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="ENFORCE", server_default="ENFORCE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class GuardrailPolicyVersionModel(Base):
    """Append-only history row for a GuardrailPolicyModel change — written in
    the same transaction as every create/update/rollback, never mutated
    afterward. `previous_configuration` is null only for the version-1 row
    created alongside a brand-new policy (nothing to diff against)."""

    __tablename__ = "guardrail_policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("guardrail_policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    previous_configuration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
