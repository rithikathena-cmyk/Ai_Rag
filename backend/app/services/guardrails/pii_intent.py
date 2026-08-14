"""Detects "this message is asking to read/add/modify/store/retrieve a
specific employee's PII"-shaped intent — the trigger for the human-approval
workflow (docs/GUARDRAILS_ARCHITECTURE.md §14), entirely separate from the
general PII detection this package already does (pii.py, presidio_check.py,
gliner_check.py), which stays wired into pipeline.py exactly as before.

Deterministic, regex-based — no LLM, no embedding model — matching this
package's existing two-stage style (destructive.py's verb+target pattern is
the direct model this follows). That's a deliberate security property, not
just a style choice: keeping detection off any model means raw PII never has
to pass through anything before this function decides whether to mask it,
which is what makes "the LLM never sees raw PII for this capability" a
structural guarantee (routers/chat.py never calls run_agent() on this path
at all) rather than a prompt-level trust assumption.

Fails closed on ambiguity in the safe direction: returning None here is
never a security hole — it just falls through to routers/chat.py's existing,
unchanged behavior (an ordinary chat answer, or today's existing hard PII
block for a message with PII but no recognized employee-record intent).
Only a real match here reaches services/employee_pii/service.py, and only an
approved request there ever writes to EmployeePIIRecordModel — a false
negative can't grant an unmediated write path.
"""

import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.guardrails import pii_patterns
from app.services.guardrails.pii import redact_pii

# Concrete, DB-action-shaped intents this workflow can route to
# services/employee_pii/service.py. The spec's "send PII to the LLM" isn't a
# distinct branch here — this whole capability is structurally LLM-free (see
# module docstring), so payload.send_to_llm is always False regardless of
# which of these fires; "other" is the catch-all for a message that clearly
# targets an employee record but doesn't match a specific verb category.
PII_ACTIONS = ("read", "retrieve", "add", "modify", "store", "other")

# No built-in default shape (EMP-12345 vs EMP12345 vs a different org-specific
# scheme all differ per deployment) when settings.guardrail_employee_id_pattern
# is configured — same reasoning as pii_patterns.compile_employee_id_pattern()'s
# own docstring. Falls back to a generic "EMP" + digits shape only when no
# deployment-specific pattern is set, so this still works out of the box.
_DEFAULT_EMPLOYEE_ID_RE = re.compile(r"\bEMP-?\d{3,}\b", re.IGNORECASE)

# Order matters: checked first-match-wins, most-specific/highest-stakes verb
# category first, so "update EMP001's stored phone number" (contains both
# "update" and, arguably, "stored") classifies as the write action ("modify")
# it actually is rather than the more generic "retrieve".
_ACTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("modify", re.compile(r"\b(update|change|correct|edit|set)\b", re.IGNORECASE)),
    ("add", re.compile(r"\b(add|register|onboard)\b|\bnew employee\b", re.IGNORECASE)),
    ("store", re.compile(r"\b(store|save)\b", re.IGNORECASE)),
    ("retrieve", re.compile(r"\b(retrieve|look\s*up|fetch)\b", re.IGNORECASE)),
    ("read", re.compile(r"\b(what'?s|what is|show|get|find|who is)\b|\bcontact\s*info\b", re.IGNORECASE)),
)

# Parses pii.py::redact_pii()'s own detail-string format ("Redacted:
# EMAIL×1, PHONE×1") — reused as-is rather than re-detecting PII types
# separately, so this module and pii.py can never disagree about what counts
# as PII.
_PII_LABEL_RE = re.compile(r"([A-Z_]+)×\d+")


@dataclass(frozen=True)
class EmployeePIIIntent:
    action: str  # one of PII_ACTIONS
    employee_id: str
    pii_types: tuple[str, ...]
    masked_text: str  # redact_pii()'s output — the ONLY text form of this message anything downstream may use


def _employee_id_pattern() -> re.Pattern:
    if settings.guardrail_employee_id_pattern:
        return pii_patterns.compile_employee_id_pattern(settings.guardrail_employee_id_pattern)
    return _DEFAULT_EMPLOYEE_ID_RE


def _parse_pii_labels(detail: str) -> tuple[str, ...]:
    return tuple(_PII_LABEL_RE.findall(detail))


def detect_employee_pii_intent(text: str) -> EmployeePIIIntent | None:
    """None means "not this flow" — the caller (routers/chat.py) must fall
    through to its existing, unchanged behavior, never treat None as an
    error. Requires a concrete employee-ID-shaped token (there's no target
    to create an ApprovalRequestModel/EmployeePIIRecordModel row against
    without one) AND either a recognized action verb or an actual PII value
    in the message — an EMP-ID mentioned in an unrelated sentence with
    neither ("what team is EMP001 on?" with no PII and no write/read verb)
    isn't enough signal to divert into this flow."""
    if not text or not text.strip():
        return None

    emp_match = _employee_id_pattern().search(text)
    if emp_match is None:
        return None

    masked_text, pii_step = redact_pii(text)
    pii_types = _parse_pii_labels(pii_step.detail)

    action = None
    for name, pattern in _ACTION_PATTERNS:
        if pattern.search(text):
            action = name
            break

    if action is None and not pii_types:
        return None

    return EmployeePIIIntent(
        action=action or "other", employee_id=emp_match.group(0).upper(),
        pii_types=pii_types, masked_text=masked_text,
    )
