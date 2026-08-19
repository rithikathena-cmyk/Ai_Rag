"""Centralized, PII-safe activity/audit logger — the single write path for
app/models/audit_event.py::AuditEventModel. Every AuditLogger.log() call
runs its metadata through the SAME sanitizers the rest of this app already
uses for PII/secrets (services/guardrails/pii.py::redact_pii(),
services/guardrails/secrets.py::redact_secrets()) rather than a third,
independent PII-detection implementation — the exact "do not rely on
developers remembering to sanitize fields" requirement this module exists
to satisfy.

Fail-safe by design, matching gateway/usage_tracker.py::record_usage()'s
established pattern: an audit-write failure is logged (logger.exception)
and never raised into the caller. A chat turn, a login, a document delete
must never fail because the audit trail couldn't be written — but the
failure itself is never silently invisible either, since it lands in the
application's own (separate — see module docstring on that distinction)
logs.
"""

import logging
import uuid

from app.db.postgres import new_session
from app.models.audit_event import AuditEventModel
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.guardrails.pii import redact_pii
from app.services.guardrails.secrets import redact_secrets

logger = logging.getLogger(__name__)

# Fixed allowlist of metadata keys any caller may pass — see log()'s
# docstring for why this exists at all: a caller passing an object outside
# this set is a bug to fix at the call site, not something to silently drop
# (dropping would make the field allowlist ineffective — see log()'s
# ValueError branch).
_METADATA_KEY_ALLOWLIST = frozenset({
    "guardrail_category", "check_name", "document_filename", "document_type",
    "capability", "tool_name", "conversation_id", "error_type", "http_status",
    "content_type", "file_size_bytes", "chunk_count", "department", "detail",
    # Multi-agent supervisor/router (routers/chat.py::_select_agent) —
    # agent/denied_agent are AgentName values, intent is an Intent value,
    # both plain strings; confidence/is_fallback are the router's own
    # RoutingDecision fields, never free text.
    "agent", "denied_agent", "intent", "confidence", "is_fallback",
    # Policy Copilot (routers/policy_copilot.py). Value shapes reviewed:
    #   raw_request  free text typed by an Admin/CEO. The ONLY free-text
    #                value in this allowlist, admitted because the request
    #                itself is the security-relevant artifact — "who asked to
    #                weaken what" is unanswerable without it. It is still run
    #                through redact_pii()+redact_secrets() by _sanitize_value()
    #                like every other string here, so an admin who pastes an
    #                example identifier does not thereby write it to the audit
    #                log. Truncated at the call site.
    #   policy_intent / detection_method / risk_level
    #                closed enum values (IntentType, "deterministic|llm|
    #                refused", "LOW|MEDIUM|HIGH|CRITICAL") — never free text.
    #   proposal_id  a UUID string, or None when no proposal was created.
    #   validation_errors
    #                deterministic validator messages, joined to a single
    #                string at the call site because this allowlist is
    #                primitives-only by design.
    "raw_request", "policy_intent", "detection_method", "risk_level",
    "proposal_id", "validation_errors",
    # PII_VIEWED (routers/pii_access.py). entity_id/message_id are UUID
    # strings; pii_type is an entity label (PHONE, EMAIL, ...); policy_version
    # is an int or None; reason is admin-supplied free text — the ONE
    # genuinely free-text value here besides raw_request above, still run
    # through redact_pii()+redact_secrets() by _sanitize_value() like every
    # other string in this allowlist. The raw PII value itself is never
    # passed to this function at all — not merely scrubbed after the fact.
    "entity_id", "message_id", "pii_type", "reason", "policy_version",
})


def _sanitize_value(value: object) -> object:
    """Strings only get run through the PII/secret sanitizers — non-string
    primitives (ints, bools, None, UUID-as-str already sanitized elsewhere)
    pass through unchanged, since redact_pii()/redact_secrets() only operate
    on text. Anything that isn't a primitive at all (a list, a dict, an
    object) is rejected by log()'s allowlist check before this ever runs —
    see log()'s docstring on why arbitrary object serialization is refused
    outright rather than attempted here."""
    if not isinstance(value, str):
        return value
    sanitized, _ = redact_pii(value)
    sanitized, _ = redact_secrets(sanitized)
    return sanitized


def _sanitize_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    unknown = set(metadata) - _METADATA_KEY_ALLOWLIST
    if unknown:
        # Fails loudly at the call site (a programming error to fix), not
        # silently by dropping the field — a silently-dropped field is
        # indistinguishable from "nothing happened," which defeats the
        # point of an allowlist as a safety net. Add the key to
        # _METADATA_KEY_ALLOWLIST above once its value shape is reviewed.
        raise ValueError(f"audit metadata key(s) not in the allowlist: {sorted(unknown)}")
    for value in metadata.values():
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError(
                "audit metadata values must be plain primitives (str/int/float/bool/None), "
                f"not {type(value).__name__} — never serialize a request/response/ORM object into audit metadata"
            )
    return {key: _sanitize_value(value) for key, value in metadata.items()}


def log(
    event_type: AuditEventType,
    *,
    outcome: AuditOutcome,
    request_id: str,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    reason_code: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write one audit event. `metadata` must be a flat dict of plain
    primitives whose keys are all in _METADATA_KEY_ALLOWLIST — this is
    deliberately strict (raises ValueError on an unknown key or a non-
    primitive value) rather than permissive-with-best-effort-sanitization:
    a caller that wants to log a new kind of detail must add that key to
    the allowlist explicitly, so every field that ever reaches the audit
    table has been reviewed for what it can contain, not just trusted to
    already be safe. String VALUES (not keys) are still additionally run
    through redact_pii()/redact_secrets() even after the allowlist check,
    as defense in depth against a value that's legitimately free text
    (e.g. a `detail` string) turning out to contain something it shouldn't.

    The DB write itself never raises — see module docstring."""
    sanitized_metadata = _sanitize_metadata(metadata)

    try:
        db = new_session()
        try:
            db.add(
                AuditEventModel(
                    event_id=uuid.uuid4(),
                    event_type=event_type.value,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    action=action,
                    outcome=outcome.value,
                    reason_code=reason_code,
                    request_id=request_id,
                    metadata_=sanitized_metadata or None,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception(
            "Audit write failed for event_type=%s request_id=%s actor_id=%s", event_type.value, request_id, actor_id,
        )
