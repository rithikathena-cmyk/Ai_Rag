from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.guardrails.pii import PIIOccurrenceRecord

GuardrailAction = Literal["pass", "redact", "block"]


@dataclass
class GuardrailStep:
    name: str
    action: GuardrailAction
    detail: str


@dataclass
class GuardrailResult:
    text: str
    blocked: bool
    block_reason: str | None = None
    steps: list[GuardrailStep] = field(default_factory=list)
    # Name of the step that actually produced block_reason — NOT reliably
    # inferable from steps[-1] once a check's block can be deferred
    # (pipeline.py's scope_semantic_check shadowing fix): a later check can
    # still run and append its own "pass" step after the one that ultimately
    # supplied the reason. None when blocked is False.
    blocking_step_name: str | None = None
    # Raw-vs-sanitized pairs captured this call, ONLY when settings.
    # guardrail_pii_raw_capture_enabled is on — empty list otherwise (the
    # default). routers/chat.py persists these to the isolated
    # pii_occurrences table after the message they belong to is written; see
    # pii.py's PIIOccurrenceRecord for why this is the one place a raw value
    # is allowed to leave its detector call at all.
    pii_occurrences: list["PIIOccurrenceRecord"] = field(default_factory=list)
