"""In-memory "force the Claude model unavailable" switch — admin-only,
process-local (not persisted, not shared across multiple workers/instances),
purely for manually exercising services/agents/planner.py's degraded
retrieval-fallback path (routers/chat.py) and the chat UI's "try a different
model" retry button on demand, without needing to actually break the real
ANTHROPIC_API_KEY.
"""

_disabled = False


def is_disabled() -> bool:
    return _disabled


def set_disabled(value: bool) -> None:
    global _disabled
    _disabled = value
