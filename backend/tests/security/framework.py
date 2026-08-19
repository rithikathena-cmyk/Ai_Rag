"""Guardrail Security Evaluation Suite — shared framework.

Design rules this file exists to enforce:

1. **No duplicated security logic.** Every verdict comes from the SAME
   functions the application calls at runtime (`pipeline.run_input_guardrails`,
   `pipeline.run_output_guardrails`, `pii_policy.resolve_pii_policy`,
   `retrieval_permissions.apply_permission_policy`, `agents.policies.*`).
   This module only *reads* what those produce; it never re-implements a
   detector, and it must never be "fixed" by teaching it to recognise PII
   itself.

2. **Blocked is not the same as correct.** A request stopped by the wrong
   check is a WRONG_ATTRIBUTION failure, not a pass. Credit-card text halted
   by the prompt-injection classifier looks green on a pass/fail chart while
   leaving the actual PII rail unverified — and it silently breaks the moment
   that classifier is retuned.

3. **Synthetic values only.** Every constant below is fabricated. The card
   numbers are the standard published test PANs, the SSNs use the 123-45-6789
   reserved shape, and the domains are `example.com` (RFC 2606). Nothing here
   is, or resembles, a real person's data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from app.services.guardrails.pipeline import run_input_guardrails, run_output_guardrails
from app.services.guardrails.types import GuardrailResult, GuardrailStep


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------

class Status(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REGRESSION = "REGRESSION"
    UNEXPECTED_BLOCK = "UNEXPECTED_BLOCK"
    UNEXPECTED_ALLOW = "UNEXPECTED_ALLOW"
    WRONG_ATTRIBUTION = "WRONG_ATTRIBUTION"
    LEAKAGE = "LEAKAGE"
    TRACE_FAILURE = "TRACE_FAILURE"
    AUDIT_FAILURE = "AUDIT_FAILURE"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


#: Statuses that mean the suite found a real problem.
FAILING = {
    Status.FAIL, Status.REGRESSION, Status.UNEXPECTED_BLOCK, Status.UNEXPECTED_ALLOW,
    Status.WRONG_ATTRIBUTION, Status.LEAKAGE, Status.TRACE_FAILURE, Status.AUDIT_FAILURE,
}


@dataclass
class SecurityCase:
    """One scenario. `expected_*` is the security requirement; everything the
    runner fills in is observed from the real pipeline."""

    test_id: str
    category: str
    scenario: str
    input: str
    expected_detection: bool                      # should ANY guardrail fire?
    expected_action: str                          # block | redact | pass
    expected_primary_guardrail: str | None = None
    expected_entity: str | None = None
    #: Synthetic values that must not survive into the returned text.
    must_not_leak: tuple[str, ...] = ()
    severity: Severity = Severity.MEDIUM
    direction: str = "input"                      # input | output
    #: Set when a case documents a CURRENTLY KNOWN defect. The suite reports
    #: it as an expected failure rather than noise, and flips it to
    #: REGRESSION-worthy the moment it starts passing.
    known_gap: str | None = None


@dataclass
class SecurityResult:
    test_id: str
    category: str
    scenario: str
    input: str
    expected_detection: bool
    actual_detection: bool
    expected_primary_guardrail: str | None
    actual_primary_guardrail: str | None
    expected_action: str
    actual_action: str
    expected_entity: str | None
    actual_entity: str | None
    leakage_check: str
    trace_check: str
    audit_check: str
    severity: Severity
    status: Status
    secondary_findings: tuple[str, ...] = ()
    known_gap: str | None = None
    notes: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.PASS


# --------------------------------------------------------------------------
# Reading the real pipeline's output (never re-deriving it)
# --------------------------------------------------------------------------

_ENTITY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

#: Words that appear in detail strings but are not entity labels.
_NOT_ENTITIES = {"PII", "SAFE", "OK", "NER", "SSN_RE", "REDACTED"}


def _entities_from(step: GuardrailStep | None) -> tuple[str, ...]:
    """Entity labels as the pipeline itself reported them, e.g.
    'Redacted: EMAIL x1, PHONE x1' -> ('EMAIL', 'PHONE'). Deliberately parses
    the emitted detail rather than re-detecting: if the pipeline names an
    entity, that is the entity, and a mismatch here is a real reporting bug."""
    if step is None:
        return ()
    found = [m for m in _ENTITY_RE.findall(step.detail) if m not in _NOT_ENTITIES]
    return tuple(sorted(set(found)))


def _acting_steps(result: GuardrailResult) -> list[GuardrailStep]:
    return [s for s in result.steps if s.action in ("block", "redact")]


def primary_guardrail(result: GuardrailResult) -> str | None:
    """The check the pipeline credits with the outcome.

    `blocking_step_name` is authoritative when set — the pipeline records it
    precisely because steps[-1] is unreliable once a block can be deferred.
    Falling back to the first acting step covers redact-and-continue, where
    nothing blocked but a rail still acted."""
    if result.blocking_step_name:
        return result.blocking_step_name
    acting = _acting_steps(result)
    return acting[0].name if acting else None


def _step_named(result: GuardrailResult, name: str) -> GuardrailStep | None:
    return next((s for s in result.steps if s.name == name), None)


def actual_action(result: GuardrailResult) -> str:
    if result.blocked:
        return "block"
    return "redact" if _acting_steps(result) else "pass"


# --------------------------------------------------------------------------
# The three independent verifications
# --------------------------------------------------------------------------

def check_leakage(result: GuardrailResult, values: tuple[str, ...]) -> str:
    """A synthetic value must not survive anywhere the caller can read it.

    Which surfaces those are depends on the verdict, and getting this wrong
    manufactures false positives. On a BLOCK, `routers/chat.py` discards
    `result.text` entirely — it stores either a `_WITHHELD_PLACEHOLDERS`
    entry or a separately-redacted copy of the raw message, and returns the
    canned `block_reason` to the user. So the raw value remaining in `.text`
    of a blocked result is expected and unreachable, and flagging it would
    report a leak that cannot occur.

    On an ALLOW/REDACT, `.text` is exactly what continues to the model and
    the transcript, so it is in scope. `block_reason` and every step detail
    are always in scope — details are persisted to `message.trace` and
    rendered in the admin Traces page.
    """
    if not values:
        return "n/a"
    surfaces = {
        "block_reason": result.block_reason or "",
        "trace_detail": " ".join(s.detail for s in result.steps),
    }
    if not result.blocked:
        surfaces["text"] = result.text or ""
    leaked = [
        f"{value!r} in {where}"
        for value in values
        for where, blob in surfaces.items()
        if value and value in blob
    ]
    return "clean" if not leaked else "LEAKED: " + "; ".join(leaked)


def check_trace(result: GuardrailResult) -> str:
    """The trace must be able to explain the decision after the fact."""
    problems = []
    if not result.steps:
        problems.append("no steps recorded")
    if result.blocked and not result.blocking_step_name:
        problems.append("blocked but blocking_step_name is unset")
    if result.blocked and not result.block_reason:
        problems.append("blocked but no user-facing reason")
    named = {s.name for s in result.steps}
    if result.blocking_step_name and result.blocking_step_name not in named:
        problems.append(f"blocking step {result.blocking_step_name!r} absent from steps")
    for s in result.steps:
        if s.action not in ("pass", "redact", "block"):
            problems.append(f"step {s.name!r} has invalid action {s.action!r}")
    return "ok" if not problems else "TRACE: " + "; ".join(problems)


def check_audit(result: GuardrailResult) -> str:
    """Every step must be individually attributable — a step with no name, or
    a duplicate name carrying a different verdict, makes the audit record
    ambiguous about what actually happened."""
    problems = []
    for s in result.steps:
        if not s.name:
            problems.append("unnamed step")
    seen: dict[str, str] = {}
    for s in result.steps:
        if s.name in seen and seen[s.name] != s.action:
            problems.append(f"{s.name!r} recorded twice with differing actions")
        seen[s.name] = s.action
    return "ok" if not problems else "AUDIT: " + "; ".join(problems)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def _classify(case: SecurityCase, result: GuardrailResult, leakage: str, trace: str, audit: str,
              actual_entities: tuple[str, ...]) -> Status:
    """Order matters: report the most security-relevant failure, not the
    first one encountered. A leak outranks everything — a request can be
    correctly blocked by the correct rail and still be a critical failure if
    the value survived into the trace."""
    if leakage.startswith("LEAKED"):
        return Status.LEAKAGE

    detected = actual_action(result) != "pass"
    if case.expected_detection and not detected:
        return Status.UNEXPECTED_ALLOW
    if not case.expected_detection and detected:
        return Status.UNEXPECTED_BLOCK

    if actual_action(result) != case.expected_action:
        return Status.FAIL

    if case.expected_primary_guardrail and primary_guardrail(result) != case.expected_primary_guardrail:
        return Status.WRONG_ATTRIBUTION

    if case.expected_entity and case.expected_entity not in actual_entities:
        return Status.FAIL

    if trace != "ok":
        return Status.TRACE_FAILURE
    if audit != "ok":
        return Status.AUDIT_FAILURE
    return Status.PASS


def run_case(case: SecurityCase, sources: list[dict] | None = None) -> SecurityResult:
    """Execute one case against the REAL pipeline."""
    if case.direction == "output":
        result = run_output_guardrails(case.input, sources=sources) if sources is not None \
            else run_output_guardrails(case.input)
    else:
        result = run_input_guardrails(case.input)

    leakage = check_leakage(result, case.must_not_leak)
    trace = check_trace(result)
    audit = check_audit(result)

    acting = _acting_steps(result)
    entities: tuple[str, ...] = ()
    for step in acting:
        entities += _entities_from(step)
    entities = tuple(sorted(set(entities)))

    status = _classify(case, result, leakage, trace, audit, entities)
    secondary = tuple(s.name for s in acting if s.name != primary_guardrail(result))

    return SecurityResult(
        test_id=case.test_id,
        category=case.category,
        scenario=case.scenario,
        input=case.input[:80],
        expected_detection=case.expected_detection,
        actual_detection=actual_action(result) != "pass",
        expected_primary_guardrail=case.expected_primary_guardrail,
        actual_primary_guardrail=primary_guardrail(result),
        expected_action=case.expected_action,
        actual_action=actual_action(result),
        expected_entity=case.expected_entity,
        actual_entity=", ".join(entities) or None,
        leakage_check=leakage,
        trace_check=trace,
        audit_check=audit,
        severity=case.severity,
        status=status,
        secondary_findings=secondary,
        known_gap=case.known_gap,
    )


def run_all(cases: list[SecurityCase]) -> list[SecurityResult]:
    return [run_case(c) for c in cases]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def summarize(results: list[SecurityResult]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[str(r.status)] = by_status.get(str(r.status), 0) + 1

    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.category, {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        bucket["passed" if r.ok else "failed"] += 1

    failing = [r for r in results if not r.ok]
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.ok),
        "failed": len(failing),
        "critical": sum(1 for r in failing if r.severity is Severity.CRITICAL),
        "unexpected_allow": by_status.get("UNEXPECTED_ALLOW", 0),
        "unexpected_block": by_status.get("UNEXPECTED_BLOCK", 0),
        "wrong_attribution": by_status.get("WRONG_ATTRIBUTION", 0),
        "leakage": by_status.get("LEAKAGE", 0),
        "regression": by_status.get("REGRESSION", 0),
        "trace_failures": by_status.get("TRACE_FAILURE", 0),
        "audit_failures": by_status.get("AUDIT_FAILURE", 0),
        "by_status": by_status,
        "by_category": by_category,
        "known_gaps": sum(1 for r in failing if r.known_gap),
    }


def format_report(results: list[SecurityResult]) -> str:
    s = summarize(results)
    out: list[str] = []
    w = out.append

    w("=" * 118)
    w("GUARDRAIL SECURITY EVALUATION")
    w("=" * 118)
    w(f"{'id':7} {'category':13} {'scenario':30} {'want':6} {'got':6} {'primary guardrail':26} {'sev':8} status")
    w("-" * 118)
    for r in sorted(results, key=lambda r: (r.category, r.test_id)):
        marker = "" if r.ok else " <<"
        w(f"{r.test_id:7} {r.category:13} {r.scenario[:30]:30} {r.expected_action:6} {r.actual_action:6} "
          f"{str(r.actual_primary_guardrail or '-')[:26]:26} {str(r.severity):8} {r.status}{marker}")

    w("")
    w("=" * 118)
    w("TOTALS")
    w("-" * 118)
    for key in ("total", "passed", "failed", "critical", "unexpected_allow", "unexpected_block",
                "wrong_attribution", "leakage", "regression", "trace_failures", "audit_failures"):
        w(f"  {key.replace('_', ' ').title():22} {s[key]}")

    w("")
    w("BY CATEGORY")
    w("-" * 118)
    for cat, b in sorted(s["by_category"].items()):
        flag = "" if b["failed"] == 0 else f"   <- {b['failed']} failing"
        w(f"  {cat:16} {b['passed']:3}/{b['total']:3} passed{flag}")

    failing = [r for r in results if not r.ok]
    if failing:
        w("")
        w("FAILURES")
        w("-" * 118)
        for r in sorted(failing, key=lambda r: (r.severity, r.category)):
            w(f"  [{r.severity}] {r.test_id} {r.category} — {r.scenario}")
            w(f"      status   : {r.status}")
            w(f"      expected : action={r.expected_action} guardrail={r.expected_primary_guardrail} entity={r.expected_entity}")
            w(f"      actual   : action={r.actual_action} guardrail={r.actual_primary_guardrail} entity={r.actual_entity}")
            if r.secondary_findings:
                w(f"      also fired: {', '.join(r.secondary_findings)}")
            if r.leakage_check.startswith("LEAKED"):
                w(f"      leakage  : {r.leakage_check}")
            if r.known_gap:
                w(f"      known gap: {r.known_gap}")
    return "\n".join(out)
