"""One row per PII entity a guardrail check actually redacted (or blocked)
in a real chat turn — the raw value, kept SEPARATE from ordinary chat
storage so it is never returned by anything that reads `messages.content`
or `messages.trace`. Never written unless `settings.
guardrail_pii_raw_capture_enabled` is on (default OFF) — see pii.py's
PIIOccurrenceRecord and pipeline.py's threading of it for where these rows
originate.

The only reader is routers/pii_access.py, gated on Permission.PII_VIEW_RAW.
No other code in this app queries this table.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class PiiOccurrenceModel(Base):
    __tablename__ = "pii_occurrences"

    # This row's own id IS the "entity_id" the privileged reveal endpoint
    # takes: GET /admin/traces/{message_id}/pii/{entity_id}.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # "trace_id" in this app's real schema — messages.trace is a column on
    # MessageModel, one JSON blob per assistant reply, not a separate table
    # (see routers/traces.py). Deleting the message deletes its captured
    # raw PII with it — there is no legitimate reason for a raw value to
    # outlive the message it was extracted from.
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized from messages.conversation_id — lets the reveal endpoint
    # authorize with the SAME authorize_conversation_access() every other
    # conversation-scoped read already uses, without an extra join on the
    # hot path of a privileged, audited request.
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "input" | "output"
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # PHONE, EMAIL, SSN, ...
    # Which detector produced this occurrence — "regex" (pii.py's own
    # recognizers) or "gliner" (gliner_check.py's span-based redaction).
    # presidio_check.py is detect-and-BLOCK only (see that module's return
    # type — GuardrailStep, no redacted text), so it never substitutes
    # anything and has nothing to pair a raw/sanitized value for.
    detector: Mapped[str] = mapped_column(String(16), nullable=False)

    # No existing recognizer in this codebase determines a country for any
    # entity — pii_validators.py's own docstring: "the codebase has no
    # general per-country trunk-prefix table" (India-specific trunk/country
    # digit STRIPPING for normalization is the only country-adjacent logic
    # that exists, and it's not exposed as a classification). Always NULL
    # today rather than a fabricated value — populate it only if a real
    # per-country detector is added later.
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # The actual matched span, exactly as it appeared in the message — kept
    # in its own table, its own column, read by exactly one gated endpoint.
    # Not encrypted at the application layer: this deployment's Postgres
    # connection is already the trust boundary for `messages.content`
    # itself (see docs/AUDIT_LOGGING.md); a deployment that wants
    # column-level encryption at rest can add it via Postgres itself
    # (pgcrypto) without changing this model's shape.
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    # The token that actually replaced it in the stored message — i.e. what
    # every non-privileged reader already sees today. Stored here too so a
    # privileged viewer's "masked by default" view (see pii_access.py) never
    # needs to re-derive or guess the sanitized form.
    sanitized_value: Mapped[str] = mapped_column(Text, nullable=False)

    # guardrail_policy.GuardrailPolicyModel.version at resolution time, when
    # a custom Policy Center row governed this entity; NULL when the
    # built-in safe default applied (no custom row) — see pii_policy.py's
    # PIIPolicyResolution.policy_version.
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
