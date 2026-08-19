"""Policy Copilot orchestration.

    interpret -> validate -> impact + simulate -> PROPOSAL

A proposal is an `ApprovalRequestModel` row, deliberately reusing the existing
approval infrastructure rather than introducing a parallel one: approvals are
already listed, decided, role-scoped and audited, and a second workflow would
mean two places to look for "what is pending".

**This module never writes policy.** It produces proposals. Applying one goes
through `guardrail_policy/service.py`, which performs its own independent
validation and its own critical-entity approval gating — so a defect here
cannot become an unreviewed policy change.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from app.models.approval_request import ApprovalRequestModel
from app.models.user import UserModel
from app.services.policy_copilot import answers, trace_lookup
from app.services.policy_copilot.impact import ImpactReport, analyze, overall_risk
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType, PolicyIntent
from app.services.policy_copilot.validation import ValidationResult, validate

#: `target_type` marking an approval row as a Copilot proposal, so the
#: existing approvals UI can distinguish them from document deletions.
PROPOSAL_TARGET_TYPE = "policy_proposal"


@dataclass
class TraceStage:
    stage: str
    status: str          # OK | FAILED | SKIPPED
    detail: str = ""


@dataclass
class CopilotResult:
    """Everything the caller needs to render one turn, including the full
    stage trace — a proposal that cannot be explained cannot be approved
    responsibly."""

    reply: str
    intent: PolicyIntent
    validation: ValidationResult
    trace: list[TraceStage] = field(default_factory=list)
    impacts: list[ImpactReport] = field(default_factory=list)
    risk: str = "LOW"
    proposal_id: uuid.UUID | None = None
    requires_approval: bool = False

    def as_dict(self) -> dict:
        return {
            "reply": self.reply,
            "intent": self.intent.intent.value,
            "method": self.intent.method,
            "changes": [
                {
                    "entity": c.normalized_entity(), "location": c.location,
                    "action": c.action, "reveal_last": c.reveal_last,
                    "detector_pattern": c.detector_pattern,
                }
                for c in self.intent.changes
            ],
            "role_exceptions": [
                {"role": e.role, "location": e.location, "action": e.action}
                for e in self.intent.role_exceptions
            ],
            "word_rule": (
                {
                    "word": self.intent.word_rule.word, "match_mode": self.intent.word_rule.match_mode,
                    "case_sensitive": self.intent.word_rule.case_sensitive, "action": self.intent.word_rule.action,
                }
                if self.intent.word_rule else None
            ),
            "regex_rule": (
                {
                    "pattern": self.intent.regex_rule.pattern, "label": self.intent.regex_rule.label,
                    "action": self.intent.regex_rule.action,
                }
                if self.intent.regex_rule else None
            ),
            "valid": self.validation.valid,
            "errors": self.validation.errors,
            "warnings": self.validation.warnings,
            "risk": self.risk,
            "impacts": [asdict(i) for i in self.impacts],
            "proposal_id": str(self.proposal_id) if self.proposal_id else None,
            "requires_approval": self.requires_approval,
            "trace": [asdict(t) for t in self.trace],
        }


def _summary(impacts: list[ImpactReport]) -> str:
    parts = []
    for i in impacts:
        line = f"{i.entity} {i.location}: {i.current_action} -> {i.proposed_action}"
        if i.reveal_last:
            line += f" (last {i.reveal_last} visible)"
        exempt = [e.label for e in i.role_effects if e.is_exception]
        if exempt:
            line += f"; exempt: {', '.join(exempt)}"
        parts.append(f"{line} ({i.direction}, risk {i.risk})")
    return "; ".join(parts)


def handle(message: str, *, user: UserModel, db: Session) -> CopilotResult:
    trace: list[TraceStage] = []

    # ---- interpret -------------------------------------------------------
    intent = interpret(message)
    trace.append(TraceStage(
        "intent_interpretation", "OK",
        f"{intent.intent.value} via {intent.method}",
    ))

    if intent.intent is IntentType.REFUSED:
        # Recorded as a completed stage, not an error: refusing is the
        # correct outcome and must be visible in the trace and audit.
        trace.append(TraceStage("policy_validation", "SKIPPED", "request refused before validation"))
        result = CopilotResult(
            reply=intent.message or "That request is not permitted.",
            intent=intent,
            validation=ValidationResult(valid=False, errors=[intent.message or "refused"]),
            trace=trace,
        )
        return result

    # ---- validate --------------------------------------------------------
    validation = validate(intent, role=user.role)
    trace.append(TraceStage(
        "policy_validation", "OK" if validation.valid else "FAILED",
        "; ".join(validation.errors) or "passed",
    ))

    if not validation.valid:
        return CopilotResult(
            reply=validation.errors[0] if validation.errors else "That request could not be validated.",
            intent=intent, validation=validation, trace=trace,
        )

    # ---- read-only intents end here -------------------------------------
    if not intent.is_mutating:
        trace.append(TraceStage("impact_analysis", "SKIPPED", "read-only request"))
        return CopilotResult(
            reply=_read_only_reply(intent, user=user, db=db), intent=intent, validation=validation, trace=trace,
        )

    # ---- impact + simulation --------------------------------------------
    # Word/regex rules have no PII `changes` to diff against a "before" state
    # (analyze() is PII-specific — see impact.py) — a new rule is always
    # additive/restrictive, never a weakening, so it carries no risk beyond
    # the deterministic LOW default and skips impact analysis outright rather
    # than reporting a misleading "no measurable change".
    if intent.intent in (IntentType.CREATE_WORD_RULE, IntentType.CREATE_REGEX_RULE):
        impacts: list[ImpactReport] = []
        risk = "LOW"
        trace.append(TraceStage("impact_analysis", "SKIPPED", "new rule — nothing to diff against"))
        trace.append(TraceStage("simulation", "SKIPPED", "not applicable to word/regex rules"))
    else:
        impacts = analyze(intent)
        risk = overall_risk(impacts)
        trace.append(TraceStage("impact_analysis", "OK", _summary(impacts) or "no measurable change"))
        trace.append(TraceStage(
            "simulation", "OK" if any(i.current_sample for i in impacts) else "SKIPPED",
            "synthetic values only",
        ))

    # ---- proposal --------------------------------------------------------
    requires_approval = validation.requires_approval or risk in ("HIGH", "CRITICAL")
    proposal = ApprovalRequestModel(
        action=intent.intent.value.lower(),
        target_type=PROPOSAL_TARGET_TYPE,
        # No policy row exists yet for a proposal, so this identifies the
        # proposal itself. The applying code resolves the real target from
        # the payload's entity/location.
        target_id=uuid.uuid4(),
        requested_by=user.id,
        role=user.role,
        payload={
            "raw_request": intent.raw_request,
            "intent": intent.intent.value,
            "method": intent.method,
            # Top-level, not just per-change: DISABLE_POLICY/ROLLBACK_POLICY
            # carry their target here instead of in `changes` (which is empty
            # for both — see validation.py's exemption for those two intents)
            # — apply.py's rollback path reads this to know WHICH policy
            # row's history to roll back.
            "entity": intent.entity,
            # word_rule/regex_rule carry CREATE_WORD_RULE/CREATE_REGEX_RULE's
            # own target — apply.py's apply_word_rule/apply_regex_rule read
            # these directly, the same way rollback reads `entity` above.
            "word_rule": (
                {
                    "word": intent.word_rule.word, "match_mode": intent.word_rule.match_mode,
                    "case_sensitive": intent.word_rule.case_sensitive, "action": intent.word_rule.action,
                }
                if intent.word_rule else None
            ),
            "regex_rule": (
                {
                    "pattern": intent.regex_rule.pattern, "label": intent.regex_rule.label,
                    "action": intent.regex_rule.action,
                }
                if intent.regex_rule else None
            ),
            "changes": [
                {
                    "entity": c.normalized_entity(), "location": c.location,
                    "action": c.action, "reveal_last": c.reveal_last,
                    "detector_pattern": c.detector_pattern,
                }
                for c in intent.changes
            ],
            # Stored alongside the changes rather than folded into them: an
            # approver reading a stored proposal must be able to see WHO was
            # exempted, and applying it later must not have to re-parse the
            # original sentence.
            "role_exceptions": [
                {"role": e.role, "location": e.location, "action": e.action}
                for e in intent.role_exceptions
            ],
            "target_version": intent.target_version,
            "risk": risk,
            "impact_summary": _summary(impacts),
            "warnings": validation.warnings,
            # Rendered from synthetic values only — never a real one.
            "simulation": [
                {"entity": i.entity, "current": i.current_sample, "proposed": i.proposed_sample}
                for i in impacts
            ],
        },
        status="pending",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    trace.append(TraceStage("proposal_created", "OK", str(proposal.id)))
    trace.append(TraceStage(
        "approval", "PENDING" if requires_approval else "OK",
        "explicit approval required" if requires_approval else "standard approval",
    ))

    lead = (
        f"I have prepared a policy proposal ({risk} risk). It has NOT been applied."
        if requires_approval
        else "I have prepared a policy proposal. It has NOT been applied."
    )
    return CopilotResult(
        reply=f"{lead} Review the impact and simulation below, then approve or reject.",
        intent=intent, validation=validation, trace=trace, impacts=impacts, risk=risk,
        proposal_id=proposal.id, requires_approval=requires_approval,
    )


def _read_only_reply(intent: PolicyIntent, *, user: UserModel, db: Session) -> str:
    """Real answers, assembled from live configuration.

    Every branch reads the actual resolver, role config, check table, or (for
    the two trace-backed intents) real persisted trace data. None of this is
    generated: an administrator asking "what can HR see?" is asking something
    with one correct answer that already exists in configuration, and a
    generated answer would read as authoritative whether or not it matched.
    """
    if intent.intent is IntentType.EXPLAIN_GUARDRAIL_FAILURE:
        return trace_lookup.explain_most_recent_failure(db, user)

    if intent.intent is IntentType.GUARDRAIL_ACTIVITY:
        return trace_lookup.activity_summary(db, user, intent.hours)

    if intent.intent is IntentType.LIST_POLICIES:
        return answers.list_policies(intent.entity)

    if intent.intent is IntentType.EXPLAIN_POLICY:
        return answers.explain_policy(intent.entity)

    if intent.intent is IntentType.EXPLAIN_GUARDRAIL:
        return answers.explain_guardrails(intent.check)

    if intent.intent is IntentType.EXPLAIN_ACCESS:
        if intent.permission:
            return answers.explain_access(permission=intent.permission)
        if intent.role:
            return answers.explain_access(role=intent.role)
        return answers.access_matrix()

    if intent.intent is IntentType.SIMULATE_POLICY:
        # "Test what an employee sees for +91 9876543210" — a literal,
        # admin-supplied value, run through the real engine for a named
        # role. Checked first: this is a different question from "what
        # happens to the entity in general" below (which shows the CURRENT
        # handling, not a proposed change — a proposed action only exists
        # once the admin states one, which turns the request into an
        # UPDATE_POLICY proposal with its own simulation).
        if intent.test_value:
            return answers.simulate_literal_value(intent.test_value, intent.role, intent.entity)
        if intent.entity:
            return (
                answers.explain_policy(intent.entity)
                + "\n\nTo see a specific change simulated, say for example "
                  f"\"allow {intent.entity.lower()} in output\" — I will show the before and after "
                  "without applying anything."
            )
        return "Which entity would you like simulated? For example: \"what happens if I allow SSN in output?\""

    return answers.list_policies(intent.entity)
