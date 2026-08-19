"""Natural language -> PolicyIntent.

Three stages, in this order, and the order is the security design:

  1. REFUSAL   — patterns that must never become a proposal, checked before
                 anything else parses. A request to grant the caller
                 permissions or disable protection wholesale is refused as a
                 request, not validated into a proposal and rejected later.
  2. DETERMINISTIC — regex/keyword parsing of the phrasings admins actually
                 use. Preferred, because an interpretation reached without
                 consulting a model cannot be steered by text inside the
                 message.
  3. LLM       — reached when stage 2 could not parse a request at all, or
                 could only partly resolve one (recognised an entity/action
                 but not enough to act on) — and only ever producing a
                 `PolicyIntent` that must survive the same strict validation.
                 If the model is unavailable or returns anything unparseable,
                 the result is CLARIFICATION_NEEDED — never a guess.

The interpreter NEVER writes anything. Its entire output is a validated
description of what the admin appears to want.
"""

from __future__ import annotations

import re

from app.services.guardrail_policy.entities import KNOWN_ENTITIES
from app.services.guardrail_policy.pii_policy import resolve_pii_policy
from app.services.policy_copilot.schemas import (
    IntentType, PolicyChange, PolicyIntent, RegexRuleChange, RoleException, WordRuleChange,
)

# --------------------------------------------------------------------------
# Stage 1 — refusals
# --------------------------------------------------------------------------

#: Requests the Copilot refuses outright. These are not "invalid input" — they
#: are attempts to use the policy control plane against itself, and are
#: recorded as REFUSED so they appear in the audit trail.
_REFUSALS: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(r"\b(make|grant|give)\s+(me|myself|us)\b.*\b(admin|ceo|superuser|root|owner)\b", re.I),
        "The Copilot cannot grant roles or permissions. Role membership is managed outside policy "
        "configuration, and no policy change can alter your own authority.",
    ),
    (
        re.compile(r"\b(ignore|disregard|forget|override)\b.*\b(instruction|rule|restriction|polic|guardrail|security)", re.I),
        "That reads as an instruction override rather than a policy change. The Copilot only "
        "interprets policy requests; it has no instructions it can be asked to set aside.",
    ),
    (
        re.compile(r"\b(disable|turn off|remove|delete|drop)\b.*\b(all|every|entire)\b.*\b(guardrail|protection|polic|security|check)", re.I),
        "Disabling protection wholesale is not a supported operation. Individual entities can be "
        "changed one at a time, each with its own impact analysis and approval.",
    ),
    (
        re.compile(r"\ballow\s+all\b.*\bpii\b|\bpii\b.*\ballow\s+all\b", re.I),
        "Permitting every PII entity at once is not a supported operation. Each entity must be "
        "changed individually so its blast radius can be assessed.",
    ),
    (
        re.compile(r"\b(you are|act as|pretend|roleplay|from now on you)\b", re.I),
        "That is a persona instruction, not a policy request.",
    ),
)


def _refusal(text: str) -> PolicyIntent | None:
    for pattern, message in _REFUSALS:
        if pattern.search(text):
            return PolicyIntent(
                intent=IntentType.REFUSED, raw_request=text, message=message, method="refused",
            )
    return None


# --------------------------------------------------------------------------
# Stage 2 — deterministic parsing
# --------------------------------------------------------------------------

_ACTION_WORDS: dict[str, str] = {
    "mask": "MASK", "redact": "REDACT", "block": "BLOCK", "allow": "ALLOW",
    "flag": "FLAG", "escalate": "ESCALATE", "hide": "MASK", "remove": "REDACT",
    "permit": "ALLOW", "reveal": "ALLOW", "show": "ALLOW",
}

#: Spoken forms -> canonical entity. Only entities the registry knows.
_ENTITY_WORDS: dict[str, str] = {
    "ssn": "SSN", "social security": "SSN", "social security number": "SSN",
    "credit card": "CREDIT_CARD", "card": "CREDIT_CARD", "credit cards": "CREDIT_CARD",
    "aadhaar": "AADHAAR", "aadhar": "AADHAAR",
    "pan": "PAN", "passport": "PASSPORT",
    "phone": "PHONE", "phone number": "PHONE", "phone numbers": "PHONE", "mobile": "PHONE",
    "email": "EMAIL", "emails": "EMAIL", "email address": "EMAIL", "email addresses": "EMAIL",
    "address": "ADDRESS", "home address": "ADDRESS",
    "bank account": "BANK_ACCOUNT", "account number": "BANK_ACCOUNT",
    "ifsc": "IFSC", "date of birth": "DATE_OF_BIRTH", "dob": "DATE_OF_BIRTH",
    "employee id": "EMPLOYEE_ID", "customer id": "CUSTOMER_ID",
    "api key": "API_KEY", "jwt": "JWT", "password": "PASSWORD", "secret": "SECRET",
    "ip address": "IP_ADDRESS", "access token": "ACCESS_TOKEN",
    "vehicle number plate": "VEHICLE_PLATE", "number plate": "VEHICLE_PLATE",
    "license plate": "VEHICLE_PLATE", "licence plate": "VEHICLE_PLATE",
    "vehicle plate": "VEHICLE_PLATE", "vehicle registration plate": "VEHICLE_PLATE",
    "registration plate": "VEHICLE_PLATE",
    "vehicle number plates": "VEHICLE_PLATE", "number plates": "VEHICLE_PLATE",
    "license plates": "VEHICLE_PLATE", "licence plates": "VEHICLE_PLATE",
    "vehicle plates": "VEHICLE_PLATE", "vehicle registration plates": "VEHICLE_PLATE",
    "registration plates": "VEHICLE_PLATE",
}

# Conversational read questions. Checked BEFORE the action/entity parser, so
# "what can HR see?" is answered rather than being read as a policy edit
# because it happens to contain the word "see" (an ALLOW synonym).
_GUARDRAIL_Q_RE = re.compile(
    r"\bwhat\b.*\bguardrail|"
    r"\b(list|show|which)\b.*\b(guardrail|check)s?\b|"
    r"\bwhat does\b.*\b(check|guardrail|rail)\b|"
    r"\bhow (do|does)\b.*\b(guardrail|detection|check)\b",
    re.I,
)
_ACCESS_Q_RE = re.compile(
    r"\bwho can\b|\bwho has\b|"
    r"\bwhat can\b\s+(the\s+)?(employee|hr|human resources|project manager|pm|ceo|admin)|"
    r"\bcan\s+(employees?|hr|project managers?|ceos?|admins?)\b|"
    r"\b(access|permission)s?\s+(matrix|table|list)\b|"
    r"\bwho (is|are) allowed\b",
    re.I,
)
_MATRIX_RE = re.compile(r"\b(matrix|everyone|all roles|each role|every role)\b", re.I)

_CHECK_NAMES = (
    "length_check", "secret_detected_check", "prompt_injection_check", "destructive_intent_check",
    "custom_word_check", "custom_regex_check", "scope_check", "semantic_risk_check",
    "deberta_injection_check", "scope_semantic_check", "toxicity_check", "presidio_check",
    "gliner_check", "pii_redact", "system_prompt_leak_check", "output_citation_check",
    "groundedness_check", "retrieval_permission_filter", "guardrail_escalation",
)

_PERMISSION_WORDS: dict[str, str] = {
    "audit log": "VIEW_AUDIT_LOGS", "audit logs": "VIEW_AUDIT_LOGS",
    "metrics": "VIEW_ANALYTICS", "analytics": "VIEW_ANALYTICS",
    "documents": "VIEW_DOCUMENTS", "users": "VIEW_USERS", "roles": "VIEW_ROLES",
    "system settings": "SYSTEM_SETTINGS",
    "approve policy": "POLICY_APPROVE", "approve policies": "POLICY_APPROVE",
    "policy changes": "POLICY_APPROVE", "guardrail polic": "MANAGE_GUARDRAIL_POLICIES",
}

_ROLE_WORDS: dict[str, str] = {
    "employee": "user", "employees": "user",
    "hr": "hr", "human resources": "hr",
    "project manager": "project_manager", "project managers": "project_manager", "pm": "project_manager",
    "ceo": "ceo", "admin": "admin", "administrator": "admin", "admins": "admin",
}



_REVEAL_RE = re.compile(
    r"(?:show|reveal|visible|keep|display)\D{0,20}?(?:last\s+)?(\d+|one|two|three|four|five|six)\s*"
    r"(?:digits?|characters?|chars?|numbers?)?"
    r"|last\s+(\d+|one|two|three|four|five|six)\s*(?:digits?|characters?|chars?)",
    re.I,
)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

#: One role mention, without the trailing plural 's' consumed — reused both
#: standalone and inside a comma/"and"-joined list ("Admin and CEO...", "HR,
#: PM and CEO...").
_ROLE_TOKEN = r"(?:employee|employees|hr|human resources|project manager|pm|ceo|admin|administrator)"

#: "hr can see all the number" / "employees can only see the last 2 digits" /
#: "admin and ceo can see the full phone number" — a list of one or more
#: roles (comma/"and"-joined) exempted from the base action, either fully
#: (ALLOW) or to a specific reveal count (MASK). Group 1 is the role list as
#: written; group 2 is "all/full/..." OR "last N digit(s)/character(s)".
#: Unifies what used to be two behaviours (a full-visibility-only single-role
#: regex) into one, so "Admin and CEO can see the full phone number" grants
#: BOTH roles instead of only the second one a single-role pattern would
#: happen to match starting mid-sentence.
#:
#: The leading negative lookbehinds stop the role-list from back-scanning
#: across a preposition into an UNRELATED role from a different clause —
#: "...only show last four digits to visible for employee and hr can see all
#: the number" must grant only hr, not employee, even though "employee and
#: hr" alone reads as a valid list: "employee" here is the audience of the
#: PRECEDING reveal clause ("for employee"), not part of "hr can see all".
#: Found live via the existing regression suite, not invented up front.
_ROLE_SCOPE_RE = re.compile(
    rf"(?<!for\s)(?<!to\s)(?<!of\s)"
    rf"((?:{_ROLE_TOKEN}s?\s*(?:,\s*|\s+and\s+))*{_ROLE_TOKEN}s?)\s+"
    r"(?:can|may|should)?\s*(?:only\s+)?(?:be able to\s+)?"
    r"(?:see|view|read|access)\s+(?:only\s+)?"
    r"(?:the\s+)?(all|full|whole|entire|complete|everything|unmasked|masked|raw"
    r"|last\s+(?:\d+|one|two|three|four|five|six)\s*(?:digits?|characters?|chars?|numbers?))",
    re.I,
)
_ROLE_TOKEN_RE = re.compile(_ROLE_TOKEN, re.I)
_TAIL_COUNT_RE = re.compile(r"last\s+(\d+|one|two|three|four|five|six)", re.I)


def _find_reveal_last(text: str) -> int | None:
    """How many trailing characters a MASK should leave visible, when the
    request says so ("show last four digits"). None means the entity's
    built-in mask shape."""
    m = _REVEAL_RE.search(text)
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    if raw is None:
        return None
    value = _WORD_NUMBERS.get(raw.lower()) if not raw.isdigit() else int(raw)
    if value is None or not 1 <= value <= 8:
        return None
    return value


def _word_or_digit(raw: str) -> int | None:
    value = _WORD_NUMBERS.get(raw.lower()) if not raw.isdigit() else int(raw)
    return value if value is not None and 1 <= value <= 8 else None


def _exceptions_for(text: str, base_action: str) -> tuple[tuple[str, str, int | None], ...]:
    """Role exceptions that actually differ from the base.

    "allow employees to see all phone numbers" mentions a role and a full-
    visibility phrase, but the base action it asks for is already ALLOW — so
    the exception says nothing. Storing it anyway would put a redundant
    `role_overrides` entry in the row, which then has to be reasoned about
    every time the base action changes.

    A MASK exception with its OWN reveal count is never dropped as
    redundant even when the action word matches the base — "mask phone,
    everyone sees the shape, employees see only the last 2 digits" is a real
    per-role difference the action alone doesn't capture.
    """
    return tuple(
        (r, a, n) for r, a, n in _find_role_exceptions(text)
        if a != base_action or n is not None
    )


def _find_role_exceptions(text: str) -> tuple[tuple[str, str, int | None], ...]:
    """Roles the request exempts from the base action, as (role, action,
    reveal_last).

    Only two shapes are inferred, both requiring an explicit clause — a role
    merely appearing in a sentence is far too weak a signal to widen anyone's
    access on:
      - "...can see all/full/..." -> ALLOW
      - "...can see the last N digits/characters" -> MASK with that reveal
      - "...can see masked..." -> MASK with no specific reveal count
    A role list ("Admin and CEO", "HR, PM and CEO") shares one clause and
    produces one exception per role named.
    """
    found: list[tuple[str, str, int | None]] = []
    seen: set[str] = set()
    for m in _ROLE_SCOPE_RE.finditer(text):
        tail = m.group(2)
        count_match = _TAIL_COUNT_RE.search(tail)
        is_masked = "masked" in tail.lower()
        action = "ALLOW"
        reveal = None
        if count_match:
            action = "MASK"
            reveal = _word_or_digit(count_match.group(1))
        elif is_masked:
            action = "MASK"
        for role_m in _ROLE_TOKEN_RE.finditer(m.group(1)):
            role = _ROLE_WORDS.get(role_m.group(0).lower())
            if role and role not in seen:
                seen.add(role)
                found.append((role, action, reveal))
    return tuple(found)


def _find_role(text: str) -> str | None:
    lowered = text.lower()
    best, best_len = None, 0
    for phrase, role in _ROLE_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered) and len(phrase) > best_len:
            best, best_len = role, len(phrase)
    return best


def _find_permission(text: str) -> str | None:
    lowered = text.lower()
    best, best_len = None, 0
    for phrase, perm in _PERMISSION_WORDS.items():
        if phrase in lowered and len(phrase) > best_len:
            best, best_len = perm, len(phrase)
    return best


def _find_check_name(text: str) -> str | None:
    lowered = text.lower().replace(" ", "_")
    for name in _CHECK_NAMES:
        if name in lowered:
            return name
    # bare words like "scope", "toxicity", "groundedness"
    for word in ("groundedness", "toxicity", "citation", "injection", "scope", "secret", "length"):
        if re.search(rf"\b{word}\b", text, re.I):
            return word
    return None


_LIST_RE = re.compile(
    r"\b(show|list|what are|display)\b.*\bpolic"
    r"|\bwhat\s+pii\b.*\b(can|does|is)\b"
    r"|\bwhich\s+(pii|polic|entit)",
    re.I,
)

#: Bare role mentions (unused by the role-exception/role-scope parsing below,
#: which each name their own roles explicitly) — kept for EXPLAIN_ACCESS's
#: "what can HR see?" lookup just below. A role CAN carry its own PII policy
#: now (see _ROLE_SCOPE_RE / role_overrides), but only from an explicit
#: visibility clause — a role merely appearing in a sentence still never
#: implies a scoped change on its own.
_ROLE_RE = re.compile(
    r"\b(employee|employees|hr|human resources|project manager|pm|ceo|admin|administrator)\b", re.I
)
_EXPLAIN_RE = re.compile(r"\b(why|explain|what is the reason)\b", re.I)
_SIMULATE_RE = re.compile(r"\bwhat (would )?happens? if\b|\bsimulate\b|\bdry[- ]run\b", re.I)
#: "test what an employee sees for +91 9876543210", "simulate how HR sees
#: jane@example.com" — a literal, admin-supplied value run through the REAL
#: masking engine under a named role's CURRENT policy. Read-only: this is a
#: query about live policy, never a proposal (see SIMULATE_POLICY's own
#: docstring on IntentType). Group 1 is the role, group 2 the literal value,
#: trimmed of trailing sentence punctuation.
_ROLE_SIM_VALUE_RE = re.compile(
    r"\b(?:test|simulate|check|show)\b.{0,30}?\b(?:what|how)\b.{0,15}?\b"
    rf"({_ROLE_TOKEN})s?\b.{{0,15}}?\bsees?\b\s*(?:for|with|on|using|is|:)?\s*(.+?)\s*[.?!]*\s*$",
    re.I,
)
_ROLLBACK_RE = re.compile(r"\broll\s?back\b.*?\bversion\s+(\d+)|\bversion\s+(\d+)\b.*\broll\s?back\b", re.I)
_DISABLE_RE = re.compile(r"\bdisable\b|\bturn off\b", re.I)

#: "why was my request blocked", "why was this blocked" — a specific past
#: event, not a general "what does X check do" question (_EXPLAIN_RE /
#: EXPLAIN_POLICY above). Checked before _EXPLAIN_RE so "why was ... blocked"
#: doesn't fall into the generic entity-explanation branch instead.
_EXPLAIN_FAILURE_RE = re.compile(
    r"\bwhy\b.{0,20}?\b(blocked|denied|refused|rejected|failed)\b|"
    r"\bwhy\s+(was|did|wasn'?t|didn'?t)\b.{0,30}?\b(work|go through|pass)\b",
    re.I,
)
_ACTIVITY_RE = re.compile(
    r"\b(guardrail|policy|security)\s+(failures?|blocks?|activity)\b|"
    r"\b(failures?|blocks?)\s+(today|this\s+(week|hour|month))\b|"
    r"\bhow many\b.{0,20}?\bblocked\b",
    re.I,
)
#: "add confidential to blocked words" / "block the word confidential" / "add
#: X as a blocked word". Group 1/2/3 — whichever alternative matched.
_WORD_RULE_RE = re.compile(
    r"\badd\s+[\"']?(?P<word1>[\w][\w \-]{0,60}?)[\"']?\s+(?:to|as)\s+(?:the\s+)?(?:blocked|banned|forbidden)\s+words?\b"
    r"|\bblock\s+the\s+word\s+[\"']?(?P<word2>[\w][\w \-]{0,60}?)[\"']?\s*[.!?]?\s*$"
    r"|\badd\s+[\"']?(?P<word3>[\w][\w \-]{0,60}?)[\"']?\s+as\s+a\s+blocked\s+word\b",
    re.I,
)
#: "add this regex for employee IDs" / "add employee ID regex" — recognises
#: the REQUEST TYPE even with no concrete pattern yet (validation.py asks for
#: one explicitly rather than the interpreter guessing). An explicit pattern,
#: when present, must be delimited (backticks or quotes) — free text after
#: the label is never read as a regex, since that risks capturing part of the
#: sentence itself as a "pattern".
_REGEX_RULE_RE = re.compile(
    r"\badd\s+(?:this\s+|a\s+)?regex\b(?:\s+pattern)?\s+for\s+(?:the\s+)?(?P<label_a>[\w][\w \-]{1,60}?)\s*"
    r"[:\-]?\s*(?:`(?P<pattern_a1>[^`]+)`|\"(?P<pattern_a2>[^\"]+)\")?\s*[.!]?\s*$"
    r"|\badd\s+(?P<label_b>[\w][\w \-]{1,40}?)\s+regex\b\s*"
    r"[:\-]?\s*(?:`(?P<pattern_b1>[^`]+)`|\"(?P<pattern_b2>[^\"]+)\")?\s*[.!]?\s*$",
    re.I,
)

#: "matching pattern `...`" / "matching pattern "..."" / "in the format `...`"
#: — an explicit regex/shape pattern for CREATING a detector for an entity
#: with none yet (BANK_ACCOUNT/IFSC/CUSTOMER_ID). Delimited only, same
#: discipline _REGEX_RULE_RE already applies: free text after the phrase is
#: never read as a pattern, since that risks capturing part of the sentence
#: itself. Absence is completely normal (most requests, including "create an
#: IFSC detector and mask it in output", state no pattern at all — a known
#: default or an explicit ask-for-one happens downstream, in validation.py).
_DETECTOR_PATTERN_RE = re.compile(
    r"(?:matching\s+pattern|in\s+the\s+format)\s+(?:`(?P<pattern1>[^`]+)`|\"(?P<pattern2>[^\"]+)\")",
    re.I,
)


def _find_detector_pattern(text: str) -> str | None:
    m = _DETECTOR_PATTERN_RE.search(text)
    if not m:
        return None
    return (m.group("pattern1") or m.group("pattern2") or "").strip() or None


_INPUT_RE = re.compile(r"\b(in|on|for|from)\s+(user\s+)?(input|request|message|prompt)s?\b", re.I)
_OUTPUT_RE = re.compile(
    r"\b(in|on|for|from)\s+(model\s+|the\s+)?(output|response|repl(y|ies)|answer)s?\b"
    # "mask X to user(s)" / "mask X for the user" — what a MASK/REDACT/etc.
    # leaves visible "to" or "for" the user is, by definition, what comes
    # back in the response; distinct from _INPUT_RE's "for input/request/
    # message/prompt", which is deliberately not matched by this alternative.
    r"|\b(?:to|for)\s+(?:the\s+)?users?\b",
    re.I,
)

#: Role-audience marker: "for/to role(s)" when no direction is already given.
#: Disjoint vocabulary from _OUTPUT_RE's literal "for/to user(s)" — role words
#: are never user/users, so this won't conflict with existing direction parsing.
_ROLE_AUDIENCE_RE = re.compile(
    rf"\b(?:for|to)\s+(?:the\s+)?((?:{_ROLE_TOKEN}s?\s*(?:,\s*|\s+and\s+))*{_ROLE_TOKEN}s?)\b",
    re.I,
)


def _find_entity(text: str) -> str | None:
    """Longest match wins, so 'credit card' is not read as 'card'."""
    lowered = text.lower()
    best: str | None = None
    best_len = 0
    for phrase, entity in _ENTITY_WORDS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered) and len(phrase) > best_len:
            best, best_len = entity, len(phrase)
    return best


def _find_action(text: str) -> str | None:
    lowered = text.lower()
    for word, action in _ACTION_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return action
    return None


def _find_locations(text: str) -> tuple[str, ...]:
    """Absent an explicit direction, return both — the caller turns that into
    a clarification rather than assuming one."""
    inp, out = bool(_INPUT_RE.search(text)), bool(_OUTPUT_RE.search(text))
    if inp and not out:
        return ("INPUT",)
    if out and not inp:
        return ("OUTPUT",)
    if inp and out:
        return ("INPUT", "OUTPUT")
    return ()


def _find_role_audience(text: str) -> tuple[str, ...]:
    """Roles mentioned after 'for/to', when no explicit direction is given.

    Returns the canonical role identifiers (e.g., 'user', 'hr', 'admin') that
    appear in audience-marking phrases like "mask email for employees" or
    "flag ssn to hr". Empty if no role audience is found.
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in _ROLE_AUDIENCE_RE.finditer(text):
        roles_text = m.group(1)
        for role_m in _ROLE_TOKEN_RE.finditer(roles_text):
            role = _ROLE_WORDS.get(role_m.group(0).lower())
            if role and role not in seen:
                seen.add(role)
                found.append(role)
    return tuple(found)


def _find_action_location_pairs(text: str) -> tuple[tuple[str, str], ...]:
    """Pair each action word with the direction that follows it.

    Handles "block SSN in input and redact it in output", where a single
    action lookup would apply `block` to both directions and quietly get the
    output rule wrong. Each action claims the first direction appearing after
    it; actions with no following direction are dropped rather than guessed.
    """
    lowered = text.lower()
    actions: list[tuple[int, str]] = []
    for word, action in _ACTION_WORDS.items():
        for m in re.finditer(rf"\b{re.escape(word)}\b", lowered):
            actions.append((m.start(), action))
    locations: list[tuple[int, str]] = []
    for pattern, loc in ((_INPUT_RE, "INPUT"), (_OUTPUT_RE, "OUTPUT")):
        for m in pattern.finditer(text):
            locations.append((m.start(), loc))
    if not actions or not locations:
        return ()

    actions.sort()
    locations.sort()
    pairs: list[tuple[str, str]] = []
    claimed: set[int] = set()
    for pos, action in actions:
        following = [(lpos, loc) for lpos, loc in locations if lpos > pos and lpos not in claimed]
        if not following:
            continue
        lpos, loc = following[0]
        claimed.add(lpos)
        pairs.append((action, loc))

    # Only meaningful when the directions genuinely differ; otherwise the
    # simpler single-action path handles it.
    if len({loc for _, loc in pairs}) < 2:
        return ()
    return tuple(pairs)


def _deterministic(text: str) -> PolicyIntent | None:
    entity = _find_entity(text)

    # Activity/failure reads come before _GUARDRAIL_Q_RE/_EXPLAIN_RE below —
    # "show me today's guardrail failures" contains both "show" and
    # "guardrail", which would otherwise match _GUARDRAIL_Q_RE's generic
    # "what checks exist" branch; "why was my request blocked" contains
    # "why", which _EXPLAIN_RE would otherwise read as a generic
    # explain-this-entity question. Both are about a specific past event,
    # answered from real trace data — never generated.
    if _ACTIVITY_RE.search(text):
        return PolicyIntent(intent=IntentType.GUARDRAIL_ACTIVITY, raw_request=text)
    if _EXPLAIN_FAILURE_RE.search(text):
        return PolicyIntent(intent=IntentType.EXPLAIN_GUARDRAIL_FAILURE, raw_request=text)

    # Conversational reads come first. "What can HR see?" contains "see" — an
    # ALLOW synonym — and "HR", so the mutation parser below would otherwise
    # read it as a request to permit something.
    if _GUARDRAIL_Q_RE.search(text):
        return PolicyIntent(
            intent=IntentType.EXPLAIN_GUARDRAIL, raw_request=text, check=_find_check_name(text),
        )

    if _ACCESS_Q_RE.search(text):
        if _MATRIX_RE.search(text):
            return PolicyIntent(intent=IntentType.EXPLAIN_ACCESS, raw_request=text)
        return PolicyIntent(
            intent=IntentType.EXPLAIN_ACCESS, raw_request=text,
            role=_find_role(text), permission=_find_permission(text),
        )

    # "test what an employee sees for +91 9876543210" — checked before the
    # generic simulate/entity parsing below, since this is a specific,
    # unambiguous trigger combination (test/simulate + what/how + role +
    # sees) that names its own literal value rather than an entity to reason
    # about abstractly.
    sim_value = _ROLE_SIM_VALUE_RE.search(text)
    if sim_value:
        role = _ROLE_WORDS.get(sim_value.group(1).lower())
        value = sim_value.group(2).strip().strip("'\"")
        if role and value:
            return PolicyIntent(
                intent=IntentType.SIMULATE_POLICY, raw_request=text,
                entity=_find_entity(text), role=role, test_value=value,
            )

    rollback = _ROLLBACK_RE.search(text)
    if rollback:
        version = next((g for g in rollback.groups() if g), None)
        return PolicyIntent(
            intent=IntentType.ROLLBACK_POLICY, raw_request=text, entity=entity,
            target_version=int(version) if version else None,
        )

    if _SIMULATE_RE.search(text):
        return PolicyIntent(intent=IntentType.SIMULATE_POLICY, raw_request=text, entity=entity)

    if _LIST_RE.search(text):
        return PolicyIntent(intent=IntentType.LIST_POLICIES, raw_request=text, entity=entity)

    if _EXPLAIN_RE.search(text):
        return PolicyIntent(intent=IntentType.EXPLAIN_POLICY, raw_request=text, entity=entity)

    if _DISABLE_RE.search(text) and entity:
        return PolicyIntent(intent=IntentType.DISABLE_POLICY, raw_request=text, entity=entity)

    # "block SSN in input and redact it in output" — two different actions in
    # one sentence. Handled before the single-action path, because collapsing
    # it to one action would silently apply the wrong rule to one direction.
    paired = _find_action_location_pairs(text)
    if entity and len(paired) > 1:
        reveal = _find_reveal_last(text)
        return PolicyIntent(
            intent=IntentType.UPDATE_POLICY, raw_request=text, entity=entity,
            changes=tuple(
                # A reveal count belongs to whichever direction is MASKed; the
                # other direction here has a different action by definition.
                PolicyChange(
                    entity=entity, location=loc, action=act,
                    reveal_last=reveal if act == "MASK" else None,
                )
                for act, loc in paired
            ),
            role_exceptions=tuple(
                RoleException(role=r, location=loc, action=a, reveal_last=n)
                for act, loc in paired
                for (r, a, n) in _exceptions_for(text, act)
            ),
        )

    action = _find_action(text)
    if entity and action:
        locations = _find_locations(text)
        role_audience = _find_role_audience(text) if not locations else ()
        if not locations and not role_audience:
            return PolicyIntent(
                intent=IntentType.CLARIFICATION_NEEDED, raw_request=text, entity=entity,
                message=(
                    f"Should {action} apply to {entity} in user input, in model output, or both? "
                    f"Say for example \"{action.lower()} {entity.lower()} in output\"."
                ),
            )
        if role_audience and not locations:
            return PolicyIntent(
                intent=IntentType.UPDATE_POLICY, raw_request=text, entity=entity,
                changes=(PolicyChange(entity=entity, location="OUTPUT", action=action),),
                role_exceptions=tuple(
                    RoleException(role=r, location="OUTPUT", action=action, reveal_last=None)
                    for r in role_audience
                ),
            )
        reveal = _find_reveal_last(text) if action == "MASK" else None
        detector_pattern = _find_detector_pattern(text)
        exceptions = _exceptions_for(text, action)
        return PolicyIntent(
            intent=IntentType.UPDATE_POLICY, raw_request=text, entity=entity,
            changes=tuple(
                PolicyChange(
                    entity=entity, location=loc, action=action, reveal_last=reveal,
                    detector_pattern=detector_pattern,
                )
                for loc in locations
            ),
            role_exceptions=tuple(
                RoleException(role=r, location=loc, action=a, reveal_last=n)
                for (r, a, n) in exceptions for loc in locations
            ),
        )

    # A pure role-visibility statement with no separate base directive —
    # "Employees can only see the last 2 digits of phone numbers.",
    # "Admin and CEO can see the full phone number." There is no "for
    # everyone" clause to parse; the sentence IS the per-role policy. "What a
    # role sees" is read as the rendered response (OUTPUT) unless the
    # sentence names a direction, since these statements describe what comes
    # back to that role, not what they type in. The base action is read from
    # the CURRENT resolved policy rather than invented — a read, not a
    # write, so this stays inside the interpreter's "never writes" contract
    # — so the proposal's base shows UNCHANGED and the real content lives
    # entirely in the role exceptions, exactly matching what was asked.
    role_exceptions = _find_role_exceptions(text)
    if entity and role_exceptions:
        locations = _find_locations(text) or ("OUTPUT",)
        current = {
            loc: (
                resolve_pii_policy(entity).input_action if loc == "INPUT"
                else resolve_pii_policy(entity).output_action
            )
            for loc in locations
        }
        return PolicyIntent(
            intent=IntentType.UPDATE_POLICY, raw_request=text, entity=entity,
            changes=tuple(PolicyChange(entity=entity, location=loc, action=current[loc]) for loc in locations),
            role_exceptions=tuple(
                RoleException(role=r, location=loc, action=a, reveal_last=n)
                for (r, a, n) in role_exceptions for loc in locations
                if a != current[loc] or n is not None
            ),
        )

    # Word-policy and regex-policy rules — checked last: both patterns are
    # narrow and specific enough (an explicit "blocked words" / "regex"
    # phrase) that they are most useful as a final, high-precision catch
    # rather than competing with the PII entity/action parsing above.
    word_match = _WORD_RULE_RE.search(text)
    if word_match:
        word = next(
            (g for g in (word_match.group("word1"), word_match.group("word2"), word_match.group("word3")) if g),
            None,
        )
        if word:
            return PolicyIntent(
                intent=IntentType.CREATE_WORD_RULE, raw_request=text,
                word_rule=WordRuleChange(word=word.strip()),
            )

    regex_match = _REGEX_RULE_RE.search(text)
    if regex_match:
        label = regex_match.group("label_a") or regex_match.group("label_b")
        pattern = next(
            (
                g for g in (
                    regex_match.group("pattern_a1"), regex_match.group("pattern_a2"),
                    regex_match.group("pattern_b1"), regex_match.group("pattern_b2"),
                )
                if g
            ),
            None,
        )
        if label:
            return PolicyIntent(
                intent=IntentType.CREATE_REGEX_RULE, raw_request=text,
                regex_rule=RegexRuleChange(pattern=pattern.strip() if pattern else None, label=label.strip()),
            )

    return None


# --------------------------------------------------------------------------
# Stage 3 — LLM fallback
# --------------------------------------------------------------------------

def _llm(text: str) -> PolicyIntent:
    """Reached for phrasings Stage 2 could not parse at all, and for ones it
    could only partly resolve (entity/action found, but not enough to act —
    see interpret()'s dispatch). Delegates to llm_interpreter.py (see that
    module's docstring for the full security
    posture: refusal patterns already ran before this is reached, the
    request is sent as data never instructions, the reply is validated
    against the same closed schema/vocabulary the deterministic parser
    uses, and whatever comes back goes through the same validate() ->
    risk -> approval -> apply pipeline either path produces).

    On any failure there — no API key, a provider error, unparseable or
    schema-invalid output, an unrecognised entity/role/action, or low
    self-reported confidence — `interpret_with_llm` returns None and this
    stays the same CLARIFICATION_NEEDED it always was: asking the
    administrator to rephrase is always safe; acting on a guess is not.
    """
    from app.services.policy_copilot.llm_interpreter import interpret_with_llm

    llm_intent = interpret_with_llm(text)
    if llm_intent is not None:
        return llm_intent

    return PolicyIntent(
        intent=IntentType.CLARIFICATION_NEEDED, raw_request=text, method="llm",
        message=(
            "I could not determine a specific policy change from that. Try naming the entity, "
            "the action and the direction — for example \"mask phone numbers in output\" or "
            "\"block SSN in input\"."
        ),
    )


# --------------------------------------------------------------------------

def interpret(text: str) -> PolicyIntent:
    """The only entry point. Always returns a validated PolicyIntent; never
    raises on hostile input, because refusing is itself an outcome worth
    recording."""
    text = (text or "").strip()
    if not text:
        return PolicyIntent(
            intent=IntentType.CLARIFICATION_NEEDED, raw_request="",
            message="Tell me which policy you would like to inspect or change.",
        )
    if len(text) > 4000:
        text = text[:4000]

    refused = _refusal(text)
    if refused is not None:
        return refused

    parsed = _deterministic(text)
    # A confident deterministic parse is returned as-is, unconsulted by any
    # model — the preferred path (see module docstring). A deterministic
    # CLARIFICATION_NEEDED is different: Stage 2 recognised something (an
    # entity and an action, usually) but a rigid pattern couldn't resolve the
    # rest — "mask the full mobile numbers to user" has an unambiguous
    # meaning a human reads instantly, but no _INPUT_RE/_OUTPUT_RE phrasing
    # matched it. Rather than asking immediately, Stage 3 gets a chance to
    # finish resolving the SAME sentence a rigid pattern only partly
    # understood, per the "if deterministic parser cannot understand the
    # request, use the LLM agent" requirement.
    #
    # Also route to Stage 3 if Stage 2 found a configurable entity with no
    # pattern — the LLM might be able to extract/generate one from the request.
    from app.services.guardrail_policy.detector_capability import CONFIGURABLE_ENTITIES

    needs_llm_for_pattern = (
        parsed is not None
        and parsed.intent is IntentType.UPDATE_POLICY
        and parsed.entity
        and parsed.entity.upper() in CONFIGURABLE_ENTITIES
        and not any(c.detector_pattern for c in parsed.changes)
    )

    if parsed is not None and parsed.intent is not IntentType.CLARIFICATION_NEEDED and not needs_llm_for_pattern:
        return parsed

    llm_result = _llm(text)
    if llm_result.intent is not IntentType.CLARIFICATION_NEEDED:
        return llm_result

    # Neither stage could resolve it. Stage 2's clarification (when it has
    # one) already names the specific entity/action it recognised, which is
    # more useful to the admin than Stage 3's fully generic fallback.
    return parsed or llm_result
