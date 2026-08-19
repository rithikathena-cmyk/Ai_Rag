"""Centralized activity/audit event taxonomy. A name here is a contract:
every AuditLogger.log() call (services/audit/logger.py) must use one of
these, never an ad hoc string, so `GET /audit/events`'s event_type filter
stays meaningful and every future integration point has a name to reach for
instead of inventing its own.

Not every member here has a real call site yet — see the approved plan's
"Explicit non-goals" section for which ones are wired this pass (auth,
guardrail policy denial, document delete) versus reserved for a future
integration pass. An unused enum member costs nothing and documents the
full intended surface area up front.
"""

from enum import StrEnum


class AuditEventType(StrEnum):
    # Authentication
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILURE = "LOGIN_FAILURE"
    LOGOUT = "LOGOUT"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_EXPIRED = "SESSION_EXPIRED"

    # Authorization
    ACCESS_GRANTED = "ACCESS_GRANTED"
    ACCESS_DENIED = "ACCESS_DENIED"
    RBAC_DENIED = "RBAC_DENIED"
    RESOURCE_ACCESS_DENIED = "RESOURCE_ACCESS_DENIED"

    # Documents
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    DOCUMENT_UPLOAD_FAILED = "DOCUMENT_UPLOAD_FAILED"
    DOCUMENT_DELETE = "DOCUMENT_DELETE"
    DOCUMENT_ACCESS = "DOCUMENT_ACCESS"
    DOCUMENT_DOWNLOAD = "DOCUMENT_DOWNLOAD"
    DOCUMENT_PROCESSING_STARTED = "DOCUMENT_PROCESSING_STARTED"
    DOCUMENT_PROCESSING_COMPLETED = "DOCUMENT_PROCESSING_COMPLETED"
    DOCUMENT_PROCESSING_FAILED = "DOCUMENT_PROCESSING_FAILED"

    # RAG
    SEARCH_STARTED = "SEARCH_STARTED"
    SEARCH_COMPLETED = "SEARCH_COMPLETED"
    RETRIEVAL_DENIED = "RETRIEVAL_DENIED"
    DOCUMENT_RETRIEVAL = "DOCUMENT_RETRIEVAL"
    CITATION_GENERATED = "CITATION_GENERATED"

    # Chat / LLM
    CONVERSATION_CREATED = "CONVERSATION_CREATED"
    MESSAGE_SENT = "MESSAGE_SENT"
    LLM_REQUEST = "LLM_REQUEST"
    LLM_RESPONSE = "LLM_RESPONSE"
    LLM_ERROR = "LLM_ERROR"

    # Guardrails
    GUARDRAIL_STARTED = "GUARDRAIL_STARTED"
    GUARDRAIL_PII_DETECTED = "GUARDRAIL_PII_DETECTED"
    GUARDRAIL_INJECTION_DETECTED = "GUARDRAIL_INJECTION_DETECTED"
    GUARDRAIL_SCOPE_DENIED = "GUARDRAIL_SCOPE_DENIED"
    GUARDRAIL_POLICY_DENIED = "GUARDRAIL_POLICY_DENIED"
    GUARDRAIL_OUTPUT_BLOCKED = "GUARDRAIL_OUTPUT_BLOCKED"
    GUARDRAIL_COMPLETED = "GUARDRAIL_COMPLETED"

    # Security
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SUSPICIOUS_ACTIVITY = "SUSPICIOUS_ACTIVITY"
    POLICY_VIOLATION = "POLICY_VIOLATION"

    # Guardrail Policy Center (routers/guardrail_policies.py, policy_copilot.py) —
    # distinct from GUARDRAIL_POLICY_DENIED above, which is a runtime decision on
    # a chat message; these are admin actions on the policy configuration itself.
    POLICY_CREATED = "POLICY_CREATED"
    POLICY_UPDATED = "POLICY_UPDATED"
    POLICY_ENABLED = "POLICY_ENABLED"
    POLICY_DISABLED = "POLICY_DISABLED"
    POLICY_ROLLBACK = "POLICY_ROLLBACK"
    POLICY_TESTED = "POLICY_TESTED"
    POLICY_APPROVED = "POLICY_APPROVED"
    POLICY_REJECTED = "POLICY_REJECTED"
    POLICY_RESEARCHED = "POLICY_RESEARCHED"

    # Multi-agent supervisor/router (services/agents/router.py, policies.py) —
    # AGENT_ROUTING_DECISION is logged for every routed chat turn (which
    # specialist agent was picked and why); AGENT_AUTHORIZATION_DENIED is
    # the defense-in-depth path where a routed agent failed the post-hoc
    # agent_allowed_for_role() check and was downgraded to general_rag.
    AGENT_ROUTING_DECISION = "AGENT_ROUTING_DECISION"
    AGENT_AUTHORIZATION_DENIED = "AGENT_AUTHORIZATION_DENIED"

    # PII_VIEW_RAW (routers/pii_access.py) — every time a permissioned admin
    # reveals the original, pre-redaction value of a detected PII entity.
    # Fired on every successful reveal, unconditionally; never contains the
    # raw value itself (services/audit/logger.py's allowlist+sanitizer would
    # scrub it anyway, but it is never passed in the first place).
    PII_VIEWED = "PII_VIEWED"

    # System
    API_ERROR = "API_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DENIED = "DENIED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


# reason_code values a GUARDRAIL_POLICY_DENIED event may carry — category
# only, never the underlying check's own detail string (score, matched
# pattern, threshold). Maps policy_engine.PolicyDecision.blocking_step_name
# to one of these in services/guardrails/policy_engine.py's audit wiring.
class AuditReasonCode(StrEnum):
    PII_POLICY = "PII_POLICY"
    INJECTION_POLICY = "INJECTION_POLICY"
    SECRET_POLICY = "SECRET_POLICY"
    DESTRUCTIVE_INTENT_POLICY = "DESTRUCTIVE_INTENT_POLICY"
    SCOPE_POLICY = "SCOPE_POLICY"
    TOXICITY_POLICY = "TOXICITY_POLICY"
    GROUNDEDNESS_POLICY = "GROUNDEDNESS_POLICY"
    RBAC_POLICY = "RBAC_POLICY"
    OTHER_POLICY = "OTHER_POLICY"


# guardrail check name -> reason_code category, for the audit trail only —
# never the check's own detail string (score/matched pattern/threshold).
# services/guardrails/orchestrator_graph.py's policy-check nodes look this
# up by PolicyDecision.blocking_step_name. A name not in this map (a new
# check added later, or a scope_unclear_* variant) falls back to
# OTHER_POLICY rather than raising — an audit categorization gap should
# never be able to break the actual guardrail decision it's describing.
_REASON_CODE_BY_CHECK: dict[str, AuditReasonCode] = {
    "secret_detected_check": AuditReasonCode.SECRET_POLICY,
    "prompt_injection_check": AuditReasonCode.INJECTION_POLICY,
    "deberta_injection_check": AuditReasonCode.INJECTION_POLICY,
    "semantic_risk_check": AuditReasonCode.INJECTION_POLICY,
    "destructive_intent_check": AuditReasonCode.DESTRUCTIVE_INTENT_POLICY,
    "scope_check": AuditReasonCode.SCOPE_POLICY,
    "scope_semantic_check": AuditReasonCode.SCOPE_POLICY,
    "scope_unclear_pii": AuditReasonCode.SCOPE_POLICY,
    "scope_unclear_document": AuditReasonCode.SCOPE_POLICY,
    "scope_unclear_context": AuditReasonCode.SCOPE_POLICY,
    "toxicity_check": AuditReasonCode.TOXICITY_POLICY,
    "presidio_check": AuditReasonCode.PII_POLICY,
    "gliner_check": AuditReasonCode.PII_POLICY,
    "pii_redact": AuditReasonCode.PII_POLICY,
    "groundedness_check": AuditReasonCode.GROUNDEDNESS_POLICY,
    "system_prompt_leak_check": AuditReasonCode.OTHER_POLICY,
    "length_check": AuditReasonCode.OTHER_POLICY,
    "custom_regex_check": AuditReasonCode.OTHER_POLICY,
    "custom_word_check": AuditReasonCode.OTHER_POLICY,
}


def reason_code_for_check(check_name: str | None) -> AuditReasonCode:
    if check_name is None:
        return AuditReasonCode.OTHER_POLICY
    return _REASON_CODE_BY_CHECK.get(check_name, AuditReasonCode.OTHER_POLICY)


# Which AuditEventType best describes a block from a given check — the
# spec's own example (§8) walks through GUARDRAIL_PII_DETECTED for a PII
# case specifically, not a flat GUARDRAIL_POLICY_DENIED for everything.
_EVENT_TYPE_BY_REASON: dict[AuditReasonCode, AuditEventType] = {
    AuditReasonCode.PII_POLICY: AuditEventType.GUARDRAIL_PII_DETECTED,
    AuditReasonCode.INJECTION_POLICY: AuditEventType.GUARDRAIL_INJECTION_DETECTED,
    AuditReasonCode.SCOPE_POLICY: AuditEventType.GUARDRAIL_SCOPE_DENIED,
}


def event_type_for_reason(reason_code: AuditReasonCode) -> AuditEventType:
    return _EVENT_TYPE_BY_REASON.get(reason_code, AuditEventType.GUARDRAIL_POLICY_DENIED)
