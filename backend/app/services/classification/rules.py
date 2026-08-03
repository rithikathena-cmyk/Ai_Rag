import re
from dataclasses import dataclass, field
from typing import Callable

from app.services.ingestion.types import DocumentMetadata

_HEADER_BLOCK_RE = re.compile(r"(?i)\b(from|to|subject|date)\s*:")
TURN_RE = re.compile(r"(?m)^\s*(?:[-*•]\s+)?(\[?\d{1,2}:\d{2}(:\d{2})?\]?\s*)?[\w .]{1,30}:\s")


def _email_header_block_score(text: str, metadata: DocumentMetadata) -> float:
    head_lines = [l for l in text.splitlines() if l.strip()][:15]
    head_text = "\n".join(head_lines)
    hits = len(set(m.group(1).lower() for m in _HEADER_BLOCK_RE.finditer(head_text)))
    return 0.4 if hits >= 2 else 0.0


def _chat_turn_ratio_score(text: str, metadata: DocumentMetadata) -> float:
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return 0.0
    matched = sum(1 for l in lines if TURN_RE.match(l))
    ratio = matched / len(lines)
    if ratio > 0.4 and len(metadata.headings) <= 1:
        return min(0.65, 0.25 + ratio * 0.4)
    return 0.0


def _research_paper_structure_score(text: str, metadata: DocumentMetadata) -> float:
    headings_lower = [h.lower() for h in metadata.headings]
    has_abstract = any("abstract" in h for h in headings_lower)
    has_references = any(h in ("references", "bibliography") or "references" in h for h in headings_lower)
    return 0.25 if has_abstract and has_references else 0.0


def _sop_step_score(text: str, metadata: DocumentMetadata) -> float:
    return 0.25 if len(re.findall(r"(?im)^\s*step\s+\d+", text)) >= 3 else 0.0


def _legal_section_score(text: str, metadata: DocumentMetadata) -> float:
    return 0.2 if len(re.findall(r"(?im)^\s*(section|article)\s+\d+", text)) >= 2 else 0.0


def _faq_heading_score(text: str, metadata: DocumentMetadata) -> float:
    question_headings = sum(1 for h in metadata.headings if h.strip().endswith("?"))
    return min(0.3, question_headings * 0.1)


@dataclass
class RuleSpec:
    text_patterns: list[re.Pattern] = field(default_factory=list)
    heading_keywords: list[str] = field(default_factory=list)
    filename_patterns: list[re.Pattern] = field(default_factory=list)
    structural: Callable[[str, DocumentMetadata], float] | None = None


CATEGORY_RULES: dict[str, RuleSpec] = {
    "SOP": RuleSpec(
        text_patterns=[
            re.compile(r"\bstandard operating procedure\b", re.I),
            re.compile(r"\bSOP[\s\-#]?\d*\b"),
        ],
        heading_keywords=["purpose", "scope", "procedure", "responsibilities", "revision history"],
        filename_patterns=[re.compile(r"sop|procedure", re.I)],
        structural=_sop_step_score,
    ),
    "Legal": RuleSpec(
        text_patterns=[
            re.compile(r"\bwhereas\b", re.I),
            re.compile(r"\bhereby\b", re.I),
            re.compile(r"\bindemnif(y|ication)\b", re.I),
            re.compile(r"\bgoverning law\b", re.I),
        ],
        heading_keywords=["definitions", "governing law", "indemnification", "confidentiality",
                           "term and termination"],
        filename_patterns=[re.compile(r"agreement|contract|nda|terms", re.I)],
        structural=_legal_section_score,
    ),
    "FAQ": RuleSpec(
        text_patterns=[
            re.compile(r"\bfrequently asked questions\b", re.I),
            re.compile(r"(?m)^\s*Q[:.]", re.I),
        ],
        heading_keywords=["faq", "frequently asked questions"],
        filename_patterns=[re.compile(r"faq", re.I)],
        structural=_faq_heading_score,
    ),
    "Email": RuleSpec(
        text_patterns=[
            re.compile(r"(?m)^From:\s*.+$"),
            re.compile(r"(?m)^To:\s*.+$"),
            re.compile(r"(?m)^Subject:\s*.+$"),
        ],
        filename_patterns=[re.compile(r"\.eml$|email|mail", re.I)],
        structural=_email_header_block_score,
    ),
    "Chat Log": RuleSpec(
        filename_patterns=[re.compile(r"chat|transcript|conversation", re.I)],
        structural=_chat_turn_ratio_score,
    ),
    "Company Policy": RuleSpec(
        text_patterns=[
            re.compile(r"\bthis policy\b", re.I),
            re.compile(r"\bcode of conduct\b", re.I),
            re.compile(r"\bemployees (must|shall)\b", re.I),
        ],
        heading_keywords=["policy", "policies", "code of conduct", "compliance"],
        filename_patterns=[re.compile(r"policy|conduct", re.I)],
    ),
    "Manual": RuleSpec(
        text_patterns=[
            re.compile(r"\buser manual\b", re.I),
            re.compile(r"\bgetting started\b", re.I),
            re.compile(r"\btroubleshooting\b", re.I),
        ],
        heading_keywords=["installation", "troubleshooting", "getting started", "chapter", "overview"],
        filename_patterns=[re.compile(r"manual|guide|handbook", re.I)],
    ),
    "Research Paper": RuleSpec(
        text_patterns=[
            re.compile(r"\babstract\b", re.I),
            re.compile(r"\bet al\.", re.I),
            re.compile(r"\bdoi\s*:", re.I),
        ],
        heading_keywords=["abstract", "introduction", "methodology", "conclusion", "references", "related work"],
        filename_patterns=[re.compile(r"paper|study|research", re.I)],
        structural=_research_paper_structure_score,
    ),
}


def score_document(metadata: DocumentMetadata, text: str, filename: str) -> dict[str, float]:
    heading_text = " ".join(metadata.headings).lower()
    scores: dict[str, float] = {}
    for label, spec in CATEGORY_RULES.items():
        score = 0.0
        if spec.text_patterns:
            matches = sum(1 for p in spec.text_patterns if p.search(text))
            score += min(0.3, matches * 0.15)
        if spec.heading_keywords:
            matched = sum(1 for kw in spec.heading_keywords if kw.lower() in heading_text)
            score += min(0.3, matched * 0.15)
        if spec.filename_patterns and any(p.search(filename) for p in spec.filename_patterns):
            score += 0.15
        if spec.structural:
            score += spec.structural(text, metadata)
        scores[label] = min(1.0, score)
    return scores


def best_rule_label(scores: dict[str, float]) -> tuple[str, float]:
    if not scores:
        return "Other", 0.0
    label = max(scores, key=scores.get)
    return label, scores[label]
