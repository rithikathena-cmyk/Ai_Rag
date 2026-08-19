"""What the Copilot knows about the guardrail pipeline.

Every entry describes a check that actually exists — the `name` values match
`NAME` constants in `services/guardrails/*.py`, and the ordering matches
`pipeline.py`'s real execution order. Nothing here is generated: an LLM asked
"what guardrails do you have" would produce a plausible list, and a plausible
list is worse than none when someone is relying on it to reason about
coverage.

If a check is added, removed or reordered in the pipeline, this table is what
must be updated alongside it — `test_knowledge_matches_the_real_pipeline`
fails otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckInfo:
    name: str
    direction: str          # input | output | retrieval | cross-cutting
    kind: str               # rule | regex | config | classifier | NER | embedding | NLI
    catches: str
    on_hit: str


#: Input checks, in real execution order (pipeline.py::run_input_guardrails).
INPUT_CHECKS: tuple[CheckInfo, ...] = (
    CheckInfo("length_check", "input", "rule", "Oversized input used to push instructions out of context", "BLOCK"),
    CheckInfo("secret_detected_check", "input", "rule", "API keys, tokens and credential-shaped strings", "BLOCK"),
    CheckInfo("prompt_injection_check", "input", "regex", "Known injection phrasings", "BLOCK"),
    CheckInfo("destructive_intent_check", "input", "rule", "Requests to delete or damage data", "BLOCK"),
    CheckInfo("custom_word_check", "input", "config", "Admin-configured banned terms", "BLOCK"),
    CheckInfo("custom_regex_check", "input", "config", "Admin-configured banned patterns", "BLOCK"),
    CheckInfo("scope_check", "input", "config", "Off-topic questions, by keyword", "BLOCK"),
    CheckInfo("semantic_risk_check", "input", "embedding", "Messages close in meaning to known unsafe patterns", "BLOCK"),
    CheckInfo("deberta_injection_check", "input", "classifier", "Injection phrasings the regex layer has never seen", "BLOCK"),
    CheckInfo("scope_semantic_check", "input", "embedding", "Off-topic questions worded to dodge the keyword list", "BLOCK"),
    CheckInfo("toxicity_check", "input", "classifier", "Harmful or abusive content", "BLOCK"),
    CheckInfo("presidio_check", "input", "NER", "Structured identifiers Presidio recognises", "BLOCK"),
    CheckInfo("gliner_check", "input", "NER", "Contextual PII with no fixed shape", "BLOCK"),
    CheckInfo("pii_redact", "input", "rule", "Personal data, per the active PII policy", "per policy"),
)

#: Output checks, in real execution order (pipeline.py::run_output_guardrails).
OUTPUT_CHECKS: tuple[CheckInfo, ...] = (
    CheckInfo("prompt_injection_check", "output", "regex",
              "Injection phrasing echoed into the reply (e.g. from a poisoned retrieved document)", "BLOCK"),
    CheckInfo("system_prompt_leak_check", "output", "rule", "The reply disclosing its own instructions", "BLOCK"),
    CheckInfo("toxicity_check", "output", "classifier", "Harmful content the model generated", "BLOCK"),
    CheckInfo("presidio_check", "output", "NER", "Structured identifiers in the reply", "BLOCK"),
    CheckInfo("gliner_check", "output", "NER", "Contextual PII in generated text", "BLOCK"),
    CheckInfo("pii_redact", "output", "rule", "Personal data, per the active PII policy", "per policy"),
)

#: Run in routers/chat.py rather than inside run_output_guardrails(), because
#: both need the retrieved SOURCES and that function takes text only. Listing
#: them as pipeline checks would misdescribe where they sit — caught by
#: test_knowledge_matches_the_real_pipeline, which is exactly why that test
#: compares this table against pipeline.py rather than trusting it.
POST_CHECKS: tuple[CheckInfo, ...] = (
    CheckInfo("output_citation_check", "output", "rule", "Claims with no retrieved source behind them", "BLOCK"),
    CheckInfo("groundedness_check", "output", "NLI", "Replies contradicting the documents they cite", "FLAG only"),
)

#: Not part of either sequential pass.
OTHER_CONTROLS: tuple[CheckInfo, ...] = (
    CheckInfo("retrieval_permission_filter", "retrieval", "rule",
              "Documents the caller has no claim to, removed before they reach the model", "filtered out"),
    CheckInfo("guardrail_escalation", "cross-cutting", "rule",
              "Repeated blocks from one user — 5 within 10 minutes", "lockout"),
)

ALL_CHECKS: tuple[CheckInfo, ...] = INPUT_CHECKS + OUTPUT_CHECKS + POST_CHECKS + OTHER_CONTROLS


def find_check(name: str) -> CheckInfo | None:
    target = name.strip().lower().replace(" ", "_")
    for check in ALL_CHECKS:
        if check.name == target or target in check.name:
            return check
    return None


#: Plain-language names for the coarse permissions, so an answer can say what
#: a permission actually gets you rather than echoing the enum value.
PERMISSION_MEANING: dict[str, str] = {
    "CHAT": "use the assistant",
    "VIEW_CONVERSATIONS": "see their own conversation history",
    "VIEW_DOCUMENTS": "browse the document library",
    "UPLOAD_DOCUMENTS": "upload documents",
    "DELETE_DOCUMENTS": "delete documents",
    "MANAGE_DOCUMENTS": "manage document metadata",
    "VIEW_ANALYTICS": "see the Metrics dashboards",
    "VIEW_USERS": "see the user list",
    "MANAGE_USERS": "create and edit users",
    "VIEW_ROLES": "see role definitions",
    "MANAGE_ROLES": "change role definitions",
    "VIEW_AUDIT_LOGS": "see org-wide audit logs and raw guardrail detail",
    "SYSTEM_SETTINGS": "change system configuration",
    "MANAGE_EMPLOYEE_PII": "decide employee-PII approval requests",
    "MANAGE_GUARDRAIL_POLICIES": "manage guardrail policy",
    "POLICY_READ": "read policy",
    "POLICY_SIMULATE": "simulate policy changes",
    "POLICY_PROPOSE": "propose policy changes",
    "POLICY_APPROVE": "approve and apply policy changes",
}

ROLE_LABELS: dict[str, str] = {
    "user": "Employee",
    "hr": "HR",
    "project_manager": "Project Manager",
    "ceo": "CEO",
    "admin": "Admin",
}

KNOWN_ROLES: tuple[str, ...] = ("user", "hr", "project_manager", "ceo", "admin")
