"""Scope classification and enforcement for Guardrail Policy Research Agent.

Deterministic (regex-based) classification ensures GUARDRAIL_ONLY scope.
Security model: Scope rejection happens at HTTP and request layers, not in prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ScopeType(str, Enum):
    GUARDRAIL_POLICY = "guardrail_policy"
    GUARDRAIL_ANALYSIS = "guardrail_analysis"
    PII_ENTITY_CONFIG = "pii_entity_config"
    DETECTOR_CAPABILITY = "detector_capability"
    FORBIDDEN = "forbidden"


@dataclass
class ScopeClassification:
    """Result of scope classification."""
    scope_type: ScopeType
    is_allowed: bool
    reason: str
    inferred_entity: str | None = None


_GUARDRAIL_KEYWORDS = re.compile(
    r"\b(?:mask|redact|block|flag|policy|detector|pii|entity|pattern|format"
    r"|validate|check|configure|setting|rule|action|location|role|exception"
    r"|approval|audit|logging|detection|capture|sensitive|confidential"
    r"|email|phone|ssn|credit\s+card|passport|address|jwt|password"
    r"|token|secret|ipaddress|ip\s*address|date\s*of\s*birth|aadhaar|pan|ifsc"
    r"|customer\s*id|bank\s*account|vehicle\s*plate|license\s*plate)\b",
    re.IGNORECASE | re.MULTILINE,
)

_FORBIDDEN_PATTERNS = re.compile(
    r"\b(?:conversation|message|documents?|users?|employee|database|query"
    r"|retrieve|search|audit\s*log|activity|trace|session"
    r"|credential|billing|financial|payment|file|upload|download|execute"
    r"|script|code|command|shell|system|process|network|connection|deploy)\b",
    re.IGNORECASE | re.MULTILINE,
)


def classify_scope(query: str) -> ScopeClassification:
    """Classify a research query as GUARDRAIL_ONLY or FORBIDDEN.

    Returns ScopeClassification with is_allowed=True only for guardrail-scoped
    queries. Rejects anything mentioning document access, conversation data,
    user information, or system operations.

    This is DETERMINISTIC (regex-based), not LLM-based — matches the existing
    interpreter.py approach for Stage 2 parsing. Guaranteed to reject before
    any LLM call or data access.

    Args:
        query: The research request to classify (typically 1-500 chars)

    Returns:
        ScopeClassification with is_allowed=True only for guardrail queries
    """
    if not query or not isinstance(query, str):
        return ScopeClassification(
            scope_type=ScopeType.FORBIDDEN, is_allowed=False,
            reason="Empty or invalid query",
        )

    query_lower = query.lower()

    # Check for explicit forbidden keywords first
    if _FORBIDDEN_PATTERNS.search(query_lower):
        forbidden_matches = _FORBIDDEN_PATTERNS.findall(query_lower)
        return ScopeClassification(
            scope_type=ScopeType.FORBIDDEN, is_allowed=False,
            reason=f"Query mentions non-guardrail topics: {', '.join(set(forbidden_matches[:3]))}",
        )

    # Check for guardrail keywords
    guardrail_matches = _GUARDRAIL_KEYWORDS.findall(query_lower)
    if not guardrail_matches:
        return ScopeClassification(
            scope_type=ScopeType.FORBIDDEN, is_allowed=False,
            reason="Query does not mention guardrail, PII, or detection concepts",
        )

    # Classify into specific guardrail subtypes (order matters)
    # Check for specific PII entity types first (highest specificity)
    if any(w in query_lower for w in ["email", "phone", "ssn", "credit", "passport", "address"]):
        scope_type = ScopeType.PII_ENTITY_CONFIG
    # Then check for detector-focused queries
    elif any(w in query_lower for w in ["detector", "detection", "detect", "pattern", "format"]):
        scope_type = ScopeType.DETECTOR_CAPABILITY
    # Then check for general analysis/comparison/audit queries
    elif any(w in query_lower for w in ["analyze", "compare", "review", "check", "audit", "assess", "examine", "gap"]):
        scope_type = ScopeType.GUARDRAIL_ANALYSIS
    else:
        scope_type = ScopeType.GUARDRAIL_POLICY

    return ScopeClassification(
        scope_type=scope_type, is_allowed=True,
        reason=f"Guardrail research query ({scope_type.value})",
    )


def assert_guardrail_only_scope(scope: ScopeClassification) -> None:
    """Raise AssertionError if scope is not guardrail-only.

    Used at code layer: even if scope classification somehow fails at HTTP
    or request layers, this provides a last line of defense before any
    resource access (reading policies, entities, detectors, etc.).

    Args:
        scope: ScopeClassification from classify_scope()

    Raises:
        AssertionError: If scope is not allowed

    Security model:
        - Never use this as the only security check (defense in depth)
        - Used *inside* functions that access guardrail resources
        - Always called BEFORE reading any policy/detector/entity data
        - Assertion failure is logged and bubbles to caller
    """
    if not scope.is_allowed:
        raise AssertionError(
            f"SECURITY VIOLATION: Non-guardrail scope rejected. Reason: {scope.reason}"
        )
