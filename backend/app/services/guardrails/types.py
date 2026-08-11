from dataclasses import dataclass, field
from typing import Literal

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
