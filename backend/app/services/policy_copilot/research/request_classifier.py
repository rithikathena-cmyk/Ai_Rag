"""Request intent classification for research queries.

Determines what type of guardrail research the user is asking for:
- ANALYZE: Examine current policies and gaps
- COMPARE: Compare policies across entities or roles
- OPTIMIZE: Suggest improvements to existing policies
- AUDIT: Review compliance or consistency
- DESIGN: Create new policy from scratch

Deterministic classification (regex-based) matching interpreter.py's Stage 2
approach — no LLM, deterministic output, fast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ResearchIntent(str, Enum):
    ANALYZE = "analyze"
    COMPARE = "compare"
    OPTIMIZE = "optimize"
    AUDIT = "audit"
    DESIGN = "design"
    UNCLEAR = "unclear"


@dataclass
class ClassifiedRequest:
    """Result of request classification."""
    intent: ResearchIntent
    entity: str | None
    focus_area: str | None
    confidence: float


# Intent detection patterns (ordered by specificity)
_ANALYZE_PATTERNS = [
    r"(?:what|analyze|examine|review|assess|evaluate).{0,30}(?:current|existing|policy|policies|configuration)",
    r"(?:how|what).{0,30}(?:policy|configured|set|defined|works|currently)",
    r"(?:show|list|display).{0,30}(?:all|current).{0,30}(?:policies|entities)",
]

_COMPARE_PATTERNS = [
    r"(?:compare|difference|vs|versus|contrast).{0,50}(?:policy|policies|role|roles|entities)",
    r"(?:how does|does|is).{0,50}(?:compare|different).{0,50}(?:between|across)",
]

_OPTIMIZE_PATTERNS = [
    r"(?:improve|optimize|enhance|refactor|better|suggestion|recommend).{0,50}(?:policy|policies|configuration|rules)",
    r"(?:how can|should|could).{0,30}(?:improve|optimize|enhance|simplify)",
    r"(?:reduce|minimize|streamline).{0,30}(?:policy|rules|exceptions)",
]

_AUDIT_PATTERNS = [
    r"(?:audit|check|verify|validate|ensure|compliance).{0,50}(?:policy|policies|rules|configuration)",
    r"(?:are|is).{0,50}(?:compliant|consistent|aligned|correct)",
    r"(?:find|identify|detect).{0,30}(?:inconsistency|conflict|gap|missing)",
]

_DESIGN_PATTERNS = [
    r"(?:create|design|add|new|build).{0,50}(?:policy|rule|detector|entity|configuration)",
    r"(?:should we|let's).{0,30}(?:add|create|design|implement)",
    r"(?:design|strategy|approach).{0,30}(?:for|to).{0,30}(?:protect|detect|mask|block)",
]

_ENTITY_PATTERNS = {
    "EMAIL": [r"\bemail\b", r"\be[\-\.]{0,2}mail\b"],
    "PHONE": [r"\bphone\b", r"\bmobile\b", r"\btelephone\b", r"\bsms\b"],
    "SSN": [r"\bssn\b", r"\bsocial.?security"],
    "CREDIT_CARD": [r"\bcredit.?card\b", r"\bpayment\b"],
    "PASSPORT": [r"\bpassport\b"],
    "ADDRESS": [r"\baddress\b", r"\blocation\b"],
    "API_KEY": [r"\bapi.?key\b", r"\bapikey\b"],
    "JWT": [r"\bjwt\b", r"\btoken\b"],
    "VEHICLE_PLATE": [r"\bvehicle\b.*?\bplate\b", r"\blicense\b.*?\bplate\b", r"\bregistration\b.*?\bplate\b"],
}

_FOCUS_AREAS = {
    "masking": [r"\bmask", r"\bob[fuscate]*\b"],
    "blocking": [r"\bblock", r"\bredact\b"],
    "roles": [r"\brole", r"\bpermission", r"\baccess"],
    "detectors": [r"\bdetector", r"\bdetection", r"\bpattern\b"],
    "compliance": [r"\bcompli", r"\bregulatory", r"\brequirement"],
}


def classify_request(query: str) -> ClassifiedRequest:
    """Classify a research request into an intent type.

    Returns ClassifiedRequest with:
    - intent: ANALYZE, COMPARE, OPTIMIZE, AUDIT, DESIGN, or UNCLEAR
    - entity: If a specific PII entity is mentioned (EMAIL, PHONE, etc.)
    - focus_area: If a specific aspect is mentioned (masking, roles, etc.)
    - confidence: 0.0-1.0 score

    Args:
        query: Research request (typically 10-500 chars)

    Returns:
        ClassifiedRequest with deterministic intent classification
    """
    if not query or len(query.strip()) < 3:
        return ClassifiedRequest(
            intent=ResearchIntent.UNCLEAR, entity=None, focus_area=None,
            confidence=0.0,
        )

    query_lower = query.lower()

    # Detect intent (order matters — more specific patterns first)
    intent_scores: dict[ResearchIntent, float] = {
        ResearchIntent.ANALYZE: 0.0,
        ResearchIntent.COMPARE: 0.0,
        ResearchIntent.OPTIMIZE: 0.0,
        ResearchIntent.AUDIT: 0.0,
        ResearchIntent.DESIGN: 0.0,
    }

    for pattern in _ANALYZE_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            intent_scores[ResearchIntent.ANALYZE] += 0.5
    for pattern in _COMPARE_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            intent_scores[ResearchIntent.COMPARE] += 0.5
    for pattern in _OPTIMIZE_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            intent_scores[ResearchIntent.OPTIMIZE] += 0.5
    for pattern in _AUDIT_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            intent_scores[ResearchIntent.AUDIT] += 0.5
    for pattern in _DESIGN_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            intent_scores[ResearchIntent.DESIGN] += 0.5

    # Pick the highest-scoring intent
    max_score = max(intent_scores.values())
    if max_score < 0.25:
        intent = ResearchIntent.UNCLEAR
        confidence = 0.0
    else:
        intent = max(intent_scores, key=intent_scores.get)
        confidence = min(0.95, max_score)  # Cap at 0.95

    # Detect entity
    entity: str | None = None
    for ent, patterns in _ENTITY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                entity = ent
                break
        if entity:
            break

    # Detect focus area
    focus_area: str | None = None
    for area, patterns in _FOCUS_AREAS.items():
        for pattern in patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                focus_area = area
                break
        if focus_area:
            break

    return ClassifiedRequest(
        intent=intent, entity=entity, focus_area=focus_area,
        confidence=confidence,
    )
