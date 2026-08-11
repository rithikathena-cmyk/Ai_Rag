from app.core.config import settings
from app.services.agents.planner import PLANNER_SYSTEM_PROMPT
from app.services.guardrails.types import GuardrailStep

NAME = "system_prompt_leak_check"

# Derived (not hardcoded) from the live system prompt so these markers can't
# drift out of sync with it: the opening sentence plus each tool's name/label
# from its bullet line — distinctive phrases a legitimate answer about
# documents wouldn't naturally reuse verbatim.
_MARKERS = (PLANNER_SYSTEM_PROMPT.split(".")[0].strip(),) + tuple(
    line.split(":")[0].lstrip("- ").strip()
    for line in PLANNER_SYSTEM_PROMPT.splitlines()
    if line.startswith("- ")
)


def check_system_prompt_leak(text: str) -> GuardrailStep:
    if not settings.guardrail_block_system_prompt_leak:
        return GuardrailStep(NAME, "pass", "Check disabled")

    lowered = text.lower()
    for marker in _MARKERS:
        if marker and marker.lower() in lowered:
            return GuardrailStep(NAME, "block", f"Reply contains system prompt fragment: {marker!r}")

    return GuardrailStep(NAME, "pass", "No system prompt leak detected")
