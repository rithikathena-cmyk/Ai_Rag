import re

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

# Structural, credential-shaped patterns — distinct from _MARKERS above,
# which only catch a verbatim echo of *this app's* system prompt. These
# catch the reply exposing a real secret value regardless of where it came
# from (an env var, a config file the model was never supposed to quote, a
# credential accidentally embedded in an ingested document that got echoed
# back verbatim, ...). Deliberately shape-based (looks like an actual
# populated secret), not keyword-based ("API key" as a bare phrase) — a
# reply that mentions "set your API key in the .env file" as generic advice
# is not a leak and must not block; a reply containing what looks like a
# real key value is. A generic secrets scan, not specific to any one
# provider — covers the credential shapes most likely to end up embedded in
# a config file, README, or support ticket this app might ingest and later
# quote back.
_CREDENTIAL_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\bsk-[A-Za-z0-9_-]{20,}\b",  # Anthropic/OpenAI-style secret key
        r"\bAKIA[A-Z0-9]{16}\b",  # AWS access key id
        r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",  # JWT-shaped (header.payload.signature)
        r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",  # GitHub personal access / OAuth / app token
        r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",  # Slack bot/user/app token
        r"\bAIza[0-9A-Za-z_-]{35}\b",  # Google API key
        r"\bsk_live_[0-9a-zA-Z]{24,}\b",  # Stripe live secret key
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----",  # PEM private key block
    )
)

# Generic leak-framing phrases — distinct from _MARKERS (verbatim echo of
# THIS app's actual prompt text) and _CREDENTIAL_PATTERNS (a real secret
# value). This catches the model *claiming* to reveal/reference its system
# prompt or hidden instructions in its own words, which wouldn't contain any
# of this app's specific prompt text and so wouldn't trip _MARKERS at all —
# "my hidden instructions told me to refuse this" is a leak-shaped response
# regardless of whether the quoted content happens to match verbatim.
# Requires the possessive "my"/"i was instructed by my" framing specifically
# so a genuinely benign, third-person explanation ("What is a system
# prompt? It's the initial instructions given to an AI model.") doesn't
# trip it — that sentence never claims to be quoting its OWN prompt.
_LEAK_FRAMING_RE = re.compile(
    r"\b(my (system prompt|hidden (system )?instructions|internal (system )?(message|instructions)|developer message)"
    r"\s+(says?|tells?|instructs?|told me)|"
    r"i (was|am) instructed by my (hidden|internal|system)|"
    r"here is my (internal|hidden) (system )?(message|instructions|prompt))\b",
    re.IGNORECASE,
)


def check_system_prompt_leak(text: str) -> GuardrailStep:
    if not settings.guardrail_block_system_prompt_leak:
        return GuardrailStep(NAME, "pass", "Check disabled")

    lowered = text.lower()
    for marker in _MARKERS:
        if marker and marker.lower() in lowered:
            return GuardrailStep(NAME, "block", f"Reply contains system prompt fragment: {marker!r}")

    for pattern in _CREDENTIAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailStep(NAME, "block", f"Reply contains a credential-shaped value ({pattern.pattern})")

    match = _LEAK_FRAMING_RE.search(text)
    if match:
        return GuardrailStep(NAME, "block", f"Reply claims to quote its own system/hidden instructions: {match.group(0)!r}")

    return GuardrailStep(NAME, "pass", "No system prompt leak detected")
