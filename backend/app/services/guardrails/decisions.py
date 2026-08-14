"""Structured guardrail decision — separates WHETHER a request is allowed
(decision) from WHY (reason) from HOW that's explained to the user (see
response_generator.py). A GuardrailStep's `detail` string stays the
technical, score-bearing audit record (unchanged — still flows into the chat
trace and GET /admin/guardrail-analytics); GuardrailDecision is a separate,
smaller structure response_generator.py consumes to pick user-facing
wording. Only ever constructed from pipeline.py's fixed reason tables —
nothing downstream can invent one on its own.
"""

from dataclasses import dataclass
from typing import Literal

Decision = Literal["IN_SCOPE", "OUT_OF_SCOPE", "UNCLEAR", "BLOCKED"]


@dataclass(frozen=True)
class GuardrailDecision:
    decision: Decision
    reason: str
