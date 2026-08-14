"""Deterministic detector for this app's document-ID naming convention —
verified against real seeded documents (GEN-EXEC-KPI-101, GEN-SOP-MFG-101,
GEN-INC-MFG-2026-014, GEN-HR-POL-104, GEN-WI-QA-101, GEN-EHS-SAFE-005): all
follow PREFIX-SEGMENT-SEGMENT(-SEGMENT), uppercase, hyphen-separated, 3-5
segments, ending numeric. Used only to distinguish scope_semantic_check's
UNCLEAR "document_reference" reason from its generic "insufficient_context"
reason — never affects whether a message is blocked, only which
clarification wording is used (see response_generator.py). Narrow by design
(requires >=3 hyphenated segments) specifically to avoid false-triggering on
ordinary text; re-validate against broader real traffic before loosening it,
same methodology this package's other heuristics already document (see
request_structure.py's own calibration note).
"""

import re

_DOC_ID_RE = re.compile(r"\b[A-Z]{2,6}(-[A-Z0-9]{2,10}){2,4}\b")


def looks_like_document_reference(text: str) -> bool:
    return bool(_DOC_ID_RE.search(text))
