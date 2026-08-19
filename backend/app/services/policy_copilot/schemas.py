"""Strict schemas for Policy Copilot intents.

Everything an LLM produces enters the system through these models. They are
`extra="forbid"` with constrained enums throughout, so an interpreter that
hallucinates a field, an action, or an entity fails validation rather than
reaching the policy layer.

The schema is the trust boundary. Nothing downstream re-checks whether
`action` is a real action — it cannot be anything else by the time it is
constructed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.guardrail_policy import GUARDRAIL_POLICY_ACTIONS
from app.services.guardrail_policy.entities import KNOWN_ENTITIES


class IntentType(StrEnum):
    READ_POLICY = "READ_POLICY"
    LIST_POLICIES = "LIST_POLICIES"
    EXPLAIN_POLICY = "EXPLAIN_POLICY"
    #: "what guardrails do you have", "what does the scope check do"
    EXPLAIN_GUARDRAIL = "EXPLAIN_GUARDRAIL"
    #: "why was my request blocked", "why was this blocked" — a specific past
    #: request, looked up from real trace data (never generated). Distinct
    #: from EXPLAIN_GUARDRAIL, which explains a CHECK in the abstract rather
    #: than a specific thing that already happened.
    EXPLAIN_GUARDRAIL_FAILURE = "EXPLAIN_GUARDRAIL_FAILURE"
    #: "show me today's guardrail failures", "how many requests were blocked
    #: today" — an aggregate over real trace data, never generated.
    GUARDRAIL_ACTIVITY = "GUARDRAIL_ACTIVITY"
    #: "what can HR see", "who can approve policy changes"
    EXPLAIN_ACCESS = "EXPLAIN_ACCESS"
    SIMULATE_POLICY = "SIMULATE_POLICY"
    CREATE_POLICY = "CREATE_POLICY"
    UPDATE_POLICY = "UPDATE_POLICY"
    DISABLE_POLICY = "DISABLE_POLICY"
    ROLLBACK_POLICY = "ROLLBACK_POLICY"
    #: "add X to blocked words" — a new WORD_FILTER guardrail_policy row.
    #: Carried in `word_rule`, never in `changes` (that shape is PII-only).
    CREATE_WORD_RULE = "CREATE_WORD_RULE"
    #: "add this regex for employee IDs" — a new REGEX guardrail_policy row.
    #: Carried in `regex_rule`, never in `changes`.
    CREATE_REGEX_RULE = "CREATE_REGEX_RULE"
    #: The interpreter could not determine intent. Never guessed — the
    #: Copilot asks rather than acting on an assumption, because a wrong
    #: guess here writes security policy.
    CLARIFICATION_NEEDED = "CLARIFICATION_NEEDED"
    #: The request tried to do something the Copilot must refuse outright
    #: (privilege escalation, disabling security wholesale, instruction
    #: override). Recorded as an intent so it is audited, not silently dropped.
    REFUSED = "REFUSED"


#: Intents that mutate security configuration. These may only ever produce a
#: PROPOSAL — never a direct write. Anything not in this set is read-only.
MUTATING_INTENTS = frozenset({
    IntentType.CREATE_POLICY,
    IntentType.UPDATE_POLICY,
    IntentType.DISABLE_POLICY,
    IntentType.ROLLBACK_POLICY,
    IntentType.CREATE_WORD_RULE,
    IntentType.CREATE_REGEX_RULE,
})


PolicyAction = Literal["ALLOW", "FLAG", "MASK", "REDACT", "BLOCK", "ESCALATE"]
PolicyLocation = Literal["INPUT", "OUTPUT"]

# Guards against the constants drifting apart from the schema silently.
assert set(GUARDRAIL_POLICY_ACTIONS) == set(PolicyAction.__args__), (
    "PolicyAction is out of sync with GUARDRAIL_POLICY_ACTIONS"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RoleException(_Strict):
    """One role seeing a different action from everyone else.

    Modelled as an EXCEPTION to the base action rather than a full per-role
    table: "mask phone for everyone, HR sees all" has one base rule and one
    exception, and writing it that way means a role nobody mentioned inherits
    the protected default instead of being absent from a table.
    """

    role: Literal["user", "hr", "project_manager", "ceo", "admin"]
    location: PolicyLocation
    action: PolicyAction
    #: How many trailing characters MASK leaves visible for THIS role, e.g.
    #: "employees see the last 2 digits, HR sees the last 4" — independent of
    #: the base change's own reveal_last. Only meaningful when `action` is
    #: MASK; ignored otherwise (same convention as PolicyChange.reveal_last).
    reveal_last: int | None = Field(default=None, ge=1, le=8)


class PolicyChange(_Strict):
    """One requested action change. `location` is explicit rather than
    defaulted: "block SSN" is ambiguous about direction, and guessing would
    silently apply a change the admin did not ask for."""

    entity: str = Field(min_length=1, max_length=64)
    location: PolicyLocation
    action: PolicyAction
    #: How many trailing characters MASK leaves visible. Only meaningful when
    #: `action` is MASK; ignored otherwise.
    reveal_last: int | None = Field(default=None, ge=1, le=8)
    #: Only meaningful when `entity` has no built-in detector (see
    #: guardrail_policy/detector_capability.py's CONFIGURABLE_ENTITIES) — a
    #: regex/shape pattern the request explicitly supplied to CREATE a
    #: detector for that entity, e.g. "create a CUSTOMER_ID detector
    #: matching pattern CUST-\d{6} and mask it in input". None means no
    #: pattern was stated; validation.py falls back to
    #: DEFAULT_DETECTOR_PATTERNS (IFSC only) or asks for one.
    detector_pattern: str | None = Field(default=None, min_length=1, max_length=200)

    def normalized_entity(self) -> str:
        return self.entity.strip().upper()


class WordRuleChange(_Strict):
    """A new WORD_FILTER guardrail_policy row. Deliberately separate from
    `PolicyChange` — a word rule has no `entity`/`location` (it applies to
    input, matching custom_word_check's own scope), and a REGEX-mode word
    rule is what CREATE_REGEX_RULE is for, not this."""

    word: str = Field(min_length=1, max_length=256)
    match_mode: Literal["EXACT", "WORD", "PHRASE"] = "WORD"
    case_sensitive: bool = False
    action: PolicyAction = "BLOCK"


class RegexRuleChange(_Strict):
    """A new REGEX guardrail_policy row. `pattern` is optional here — the
    interpreter may recognise the request as a regex-rule creation without a
    concrete pattern yet stated ("add employee ID regex"), in which case
    validation asks for it explicitly rather than guessing one."""

    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    label: str = Field(min_length=1, max_length=64)
    action: PolicyAction = "BLOCK"


class PolicyIntent(_Strict):
    """The complete, validated interpretation of one natural-language request.

    Deliberately NOT a free-form command object: there is no field an LLM can
    populate that becomes executable text. Downstream code reads `intent` and
    `changes` and nothing else drives behaviour.
    """

    intent: IntentType
    #: Verbatim user request, retained for audit. Never re-interpreted.
    raw_request: str = Field(max_length=4000)
    changes: tuple[PolicyChange, ...] = ()
    #: Roles that see something different from the base change above.
    role_exceptions: tuple[RoleException, ...] = ()
    #: For READ/EXPLAIN/SIMULATE — which entity the question is about.
    entity: str | None = Field(default=None, max_length=64)
    #: For ROLLBACK_POLICY.
    target_version: int | None = Field(default=None, ge=1)
    #: For EXPLAIN_ACCESS — whichever the question named.
    role: str | None = Field(default=None, max_length=32)
    permission: str | None = Field(default=None, max_length=64)
    #: For EXPLAIN_GUARDRAIL — a specific check, if one was named.
    check: str | None = Field(default=None, max_length=64)
    #: For CREATE_WORD_RULE.
    word_rule: WordRuleChange | None = None
    #: For CREATE_REGEX_RULE.
    regex_rule: RegexRuleChange | None = None
    #: For GUARDRAIL_ACTIVITY — how far back to look; defaults applied by the
    #: answer function itself when absent.
    hours: int | None = Field(default=None, ge=1, le=720)
    #: Populated for CLARIFICATION_NEEDED / REFUSED, shown to the admin.
    message: str | None = Field(default=None, max_length=1000)
    #: How the interpretation was reached. "deterministic" means a pattern
    #: matched and no model was consulted — preferred, because a parse that
    #: never sees an LLM cannot be steered by one.
    method: Literal["deterministic", "llm", "refused"] = "deterministic"
    #: For SIMULATE_POLICY with a literal admin-supplied value ("test what an
    #: employee sees for +91 9876543210") — the value itself, run through the
    #: REAL masking engine under the named role's resolved policy. Never
    #: stored, never a proposal; a query, not a mutation.
    test_value: str | None = Field(default=None, max_length=500)
    #: Self-reported by the LLM interpretation path only; None for
    #: deterministic (exact pattern match — confidence is definitionally 1.0,
    #: not worth a redundant field) and for refused/clarification. Purely
    #: observational — never used to decide whether to apply anything; a low
    #: value already means llm_interpreter.py returned None and this
    #: PolicyIntent was never constructed in the first place.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def is_mutating(self) -> bool:
        return self.intent in MUTATING_INTENTS

    def unknown_entities(self) -> tuple[str, ...]:
        named = {c.normalized_entity() for c in self.changes}
        if self.entity:
            named.add(self.entity.strip().upper())
        return tuple(sorted(named - KNOWN_ENTITIES))
