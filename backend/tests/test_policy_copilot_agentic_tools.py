"""New Policy Copilot capabilities added to close the "not agentic enough"
gap: the deterministic "to/for the user(s)" direction cue, the Stage 2 ->
Stage 3 fallthrough for a recognized-but-incomplete deterministic parse, word
and regex rule creation (CREATE_WORD_RULE / CREATE_REGEX_RULE), and the two
trace-backed read-only tools (EXPLAIN_GUARDRAIL_FAILURE / GUARDRAIL_ACTIVITY).

Every mutating path here still goes through the same validate() -> proposal
-> human approval -> apply.py -> guardrail_policy/service.py pipeline the PII
path already used — see interpreter.py's and trace_lookup.py's own
docstrings for the full security posture. Nothing here bypasses it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.permissions import Permission
from app.services.llm_rbac import policy_loader
from app.services.policy_copilot.interpreter import interpret
from app.services.policy_copilot.schemas import IntentType, PolicyIntent
from app.services.policy_copilot.validation import validate

ADMIN = "admin"


def _fake_llm_result(json_text: str):
    from app.gateway.schemas import GenerateResult, TokenUsage

    return GenerateResult(
        text=json_text, stop_reason="end_turn", usage=TokenUsage(), request_id="test",
        model="test-model", latency_ms=1.0,
    )


def _block_llm(monkeypatch, message="the LLM should not have been called"):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    def _boom(*a, **kw):
        raise AssertionError(message)

    monkeypatch.setattr(gateway_singleton, "generate", _boom)


# --------------------------------------------------------------------------
# "to/for the user(s)" -> OUTPUT (the literal reported bug)
# --------------------------------------------------------------------------

def test_mask_to_user_resolves_deterministically_to_output(monkeypatch):
    _block_llm(monkeypatch, "the widened OUTPUT regex should resolve this without the LLM")

    intent = interpret("mask the full mobile numbers to user")

    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.method == "deterministic"
    assert len(intent.changes) == 1
    change = intent.changes[0]
    assert (change.entity, change.location, change.action) == ("PHONE", "OUTPUT", "MASK")


def test_redact_for_the_user_also_resolves_to_output(monkeypatch):
    _block_llm(monkeypatch)
    intent = interpret("redact email for the user")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.changes[0].location == "OUTPUT"


def test_for_input_is_still_read_as_input_not_output(monkeypatch):
    """The widened OUTPUT alternative must not swallow "for input" — that
    still has its own, more specific match in _INPUT_RE."""
    _block_llm(monkeypatch)
    intent = interpret("block SSN for input")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.changes[0].location == "INPUT"


# --------------------------------------------------------------------------
# Stage 2 -> Stage 3 fallthrough
# --------------------------------------------------------------------------

def test_a_stage_2_clarification_gets_a_stage_3_attempt(monkeypatch):
    """"mask phone numbers" (entity+action, no direction) is Stage 2's own
    CLARIFICATION_NEEDED case — this proves Stage 3 is actually consulted
    (not skipped) before the Copilot gives up, by having the mocked LLM
    resolve what Stage 2 alone could not."""
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = (
        '{"intent":"UPDATE_POLICY","entity":"PHONE","base_action":"MASK","base_location":"OUTPUT",'
        '"base_reveal_last":null,"role_policies":[],"confidence":0.9,"reasoning":"resolved by context"}'
    )
    monkeypatch.setattr(gateway_singleton, "generate", lambda request: _fake_llm_result(llm_json))

    intent = interpret("mask phone numbers")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.method == "llm"
    assert intent.changes[0].location == "OUTPUT"


def test_when_neither_stage_resolves_it_stage_2s_specific_message_wins(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    llm_json = '{"intent":"UNCLEAR","entity":null,"role_policies":[],"confidence":0.1,"reasoning":"still unclear"}'
    monkeypatch.setattr(gateway_singleton, "generate", lambda request: _fake_llm_result(llm_json))

    intent = interpret("mask phone numbers")
    assert intent.intent is IntentType.CLARIFICATION_NEEDED
    # Stage 2's own message won over Stage 3's generic fallback — proven by
    # method staying "deterministic" and the message naming the direction.
    assert intent.method == "deterministic"
    assert "input" in (intent.message or "").lower()


def test_a_confident_deterministic_parse_never_reaches_the_llm(monkeypatch):
    _block_llm(monkeypatch, "a confident deterministic parse must short-circuit before Stage 3")
    intent = interpret("block SSN in input")
    assert intent.intent is IntentType.UPDATE_POLICY
    assert intent.method == "deterministic"


# --------------------------------------------------------------------------
# CREATE_WORD_RULE
# --------------------------------------------------------------------------

def test_word_rule_is_recognized_deterministically(monkeypatch):
    _block_llm(monkeypatch, "the word-rule pattern should resolve this without the LLM")

    intent = interpret("add confidential to blocked words")
    assert intent.intent is IntentType.CREATE_WORD_RULE
    assert intent.method == "deterministic"
    assert intent.word_rule is not None
    assert intent.word_rule.word == "confidential"
    assert intent.word_rule.action == "BLOCK"

    result = validate(intent, role=ADMIN)
    assert result.valid, result.errors


def test_word_rule_alternate_phrasing(monkeypatch):
    _block_llm(monkeypatch)
    intent = interpret("block the word confidential")
    assert intent.intent is IntentType.CREATE_WORD_RULE
    assert intent.word_rule.word == "confidential"


def test_word_rule_without_a_word_is_rejected():
    intent = PolicyIntent(intent=IntentType.CREATE_WORD_RULE, raw_request="x", word_rule=None)
    result = validate(intent, role=ADMIN)
    assert not result.valid


def test_word_rule_apply_delegates_to_create_policy(monkeypatch):
    from app.services.policy_copilot import apply as apply_module

    captured = {}

    def _fake_create(db, *, policy_key, name, description, category, action, priority, configuration, mode, created_by, pre_approved=False):
        captured["policy_key"] = policy_key
        captured["category"] = category
        captured["action"] = action
        captured["configuration"] = configuration
        captured["pre_approved"] = pre_approved
        return SimpleNamespace(policy=SimpleNamespace(policy_key=policy_key, version=1))

    monkeypatch.setattr(apply_module.policy_service, "create_policy", _fake_create)
    monkeypatch.setattr(apply_module.store, "invalidate", lambda: None)

    approver = SimpleNamespace(id="admin-id", role="admin")
    result = apply_module.apply_word_rule(
        db=None,
        word_rule={"word": "confidential", "match_mode": "WORD", "case_sensitive": False, "action": "BLOCK"},
        approver=approver, reason="test",
    )

    assert captured["category"] == "WORD_FILTER"
    assert captured["action"] == "BLOCK"
    assert captured["configuration"] == {"word": "confidential", "match_mode": "WORD", "case_sensitive": False}
    # The approver already reviewed this via the Approvals UI before
    # clicking approve — same reasoning as the PII path's own pre_approved.
    assert captured["pre_approved"] is True
    assert result.operation == "created"


def test_word_rule_apply_surfaces_a_queued_approval_rather_than_pretending_success(monkeypatch):
    from app.core.errors import AppError
    from app.services.policy_copilot import apply as apply_module

    monkeypatch.setattr(
        apply_module.policy_service, "create_policy",
        lambda *a, **kw: SimpleNamespace(policy=None),
    )
    approver = SimpleNamespace(id="admin-id", role="admin")
    with pytest.raises(AppError):
        apply_module.apply_word_rule(
            db=None, word_rule={"word": "x", "match_mode": "WORD", "case_sensitive": False, "action": "BLOCK"},
            approver=approver, reason=None,
        )


# --------------------------------------------------------------------------
# CREATE_REGEX_RULE
# --------------------------------------------------------------------------

def test_regex_rule_with_no_pattern_asks_for_one(monkeypatch):
    """"add employee ID regex" names the rule but supplies no pattern — the
    interpreter must never invent one; validation asks for it explicitly."""
    _block_llm(monkeypatch, "the regex-rule pattern should resolve this without the LLM")

    intent = interpret("add employee ID regex")
    assert intent.intent is IntentType.CREATE_REGEX_RULE
    assert intent.method == "deterministic"
    assert intent.regex_rule is not None
    assert intent.regex_rule.pattern is None
    assert intent.regex_rule.label.lower() == "employee id"

    result = validate(intent, role=ADMIN)
    assert not result.valid
    assert "pattern" in result.errors[0].lower()


def test_regex_rule_with_a_delimited_pattern_is_extracted_and_valid(monkeypatch):
    _block_llm(monkeypatch)
    intent = interpret("add this regex for employee IDs: `EMP-\\d{6}`")
    assert intent.intent is IntentType.CREATE_REGEX_RULE
    assert intent.regex_rule.pattern == "EMP-\\d{6}"
    assert intent.regex_rule.label.lower() == "employee ids"

    result = validate(intent, role=ADMIN)
    assert result.valid, result.errors


def test_regex_rule_rejects_an_unsafe_pattern():
    # Built via the real schema so the ReDoS gate in validation.py actually
    # runs against a genuine nested-quantifier catastrophic-backtracking shape.
    from app.services.policy_copilot.schemas import RegexRuleChange

    intent = PolicyIntent(
        intent=IntentType.CREATE_REGEX_RULE, raw_request="x",
        regex_rule=RegexRuleChange(pattern="(a+)+$", label="evil"),
    )
    result = validate(intent, role=ADMIN)
    assert not result.valid


def test_regex_rule_apply_delegates_to_create_policy(monkeypatch):
    from app.services.policy_copilot import apply as apply_module

    captured = {}

    def _fake_create(db, *, policy_key, name, description, category, action, priority, configuration, mode, created_by, pre_approved=False):
        captured["category"] = category
        captured["configuration"] = configuration
        return SimpleNamespace(policy=SimpleNamespace(policy_key=policy_key, version=1))

    monkeypatch.setattr(apply_module.policy_service, "create_policy", _fake_create)
    monkeypatch.setattr(apply_module.store, "invalidate", lambda: None)

    approver = SimpleNamespace(id="admin-id", role="admin")
    result = apply_module.apply_regex_rule(
        db=None, regex_rule={"pattern": "EMP-\\d{6}", "label": "employee ids", "action": "BLOCK"},
        approver=approver, reason="test",
    )

    assert captured["category"] == "REGEX"
    assert captured["configuration"] == {"pattern": "EMP-\\d{6}", "entity": "EMPLOYEE IDS"}
    assert result.operation == "created"


# --------------------------------------------------------------------------
# RBAC — mutating rule-creation intents are gated exactly like PII changes
# --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["user", "hr", "project_manager"])
def test_unprivileged_roles_cannot_propose_word_or_regex_rules(role):
    granted = policy_loader.role_config(role).granted_permissions
    assert Permission.POLICY_PROPOSE.value not in granted
    assert "*" not in granted

    word_intent = interpret("add confidential to blocked words")
    assert not validate(word_intent, role=role).valid

    regex_intent = interpret("add this regex for employee IDs: `EMP-\\d{6}`")
    assert not validate(regex_intent, role=role).valid


# --------------------------------------------------------------------------
# EXPLAIN_GUARDRAIL_FAILURE / GUARDRAIL_ACTIVITY — routing
# --------------------------------------------------------------------------

def test_why_was_my_request_blocked_routes_to_explain_failure(monkeypatch):
    _block_llm(monkeypatch)
    intent = interpret("why was my request blocked?")
    assert intent.intent is IntentType.EXPLAIN_GUARDRAIL_FAILURE
    assert not intent.is_mutating


def test_todays_failures_routes_to_activity(monkeypatch):
    _block_llm(monkeypatch)
    intent = interpret("show me today's guardrail failures")
    assert intent.intent is IntentType.GUARDRAIL_ACTIVITY
    assert not intent.is_mutating


def test_generic_why_question_still_reads_as_explain_policy(monkeypatch):
    """"why are credit cards redacted?" is about a CONFIGURATION, not a past
    event — must not be captured by the new failure-explanation pattern."""
    _block_llm(monkeypatch)
    intent = interpret("why are credit cards redacted?")
    assert intent.intent is IntentType.EXPLAIN_POLICY


# --------------------------------------------------------------------------
# trace_lookup — real query shape, RBAC-scoped exactly like routers/traces.py
# --------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **kw):
        return self

    def order_by(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def all(self):
        return self._rows


def _mock_failure_lookup(monkeypatch, trace_lookup, msg):
    monkeypatch.setattr(trace_lookup, "_scoped_query", lambda db, user: _FakeQuery([(msg, None)]))
    monkeypatch.setattr(
        trace_lookup.policy_loader, "role_config",
        lambda role: SimpleNamespace(granted_permissions=["VIEW_AUDIT_LOGS"]),
    )


def test_explain_most_recent_failure_reports_the_blocking_check(monkeypatch):
    from app.services.policy_copilot import trace_lookup

    msg = SimpleNamespace(
        trace=[
            {"agent": "Guardrails", "tool": "prompt_injection_check", "summary": "pass: ok"},
            {"agent": "Guardrails", "tool": "toxicity_check", "summary": "block: harmful content"},
        ],
        created_at=None,
    )
    _mock_failure_lookup(monkeypatch, trace_lookup, msg)
    # The LLM explanation is a best-effort addition on top of the
    # deterministic report (see decision_explainer.py) — mocked to fail here
    # so this test only asserts the part that must always be present.
    monkeypatch.setattr(trace_lookup, "explain_decision", lambda **kw: None)

    reply = trace_lookup.explain_most_recent_failure(db=None, user=SimpleNamespace(role="admin"))
    assert "toxicity_check" in reply
    assert "harmful content" in reply


def test_explain_most_recent_failure_includes_the_llm_explanation_when_available(monkeypatch):
    from app.services.policy_copilot import trace_lookup

    msg = SimpleNamespace(
        trace=[{"agent": "Guardrails", "tool": "toxicity_check", "summary": "block: harmful content"}],
        created_at=None,
    )
    _mock_failure_lookup(monkeypatch, trace_lookup, msg)
    monkeypatch.setattr(
        trace_lookup, "explain_decision",
        lambda **kw: "The message was blocked because it contained abusive language.",
    )

    reply = trace_lookup.explain_most_recent_failure(db=None, user=SimpleNamespace(role="admin"))
    assert "abusive language" in reply


def test_explain_most_recent_failure_degrades_cleanly_when_the_llm_is_unavailable(monkeypatch):
    """decision_explainer.explain_decision() returning None (no API key,
    provider error, refusal) must never break or blank the reply — the
    deterministic report is already complete on its own."""
    from app.services.policy_copilot import trace_lookup

    msg = SimpleNamespace(
        trace=[{"agent": "Guardrails", "tool": "toxicity_check", "summary": "block: harmful content"}],
        created_at=None,
    )
    _mock_failure_lookup(monkeypatch, trace_lookup, msg)
    monkeypatch.setattr(trace_lookup, "explain_decision", lambda **kw: None)

    reply = trace_lookup.explain_most_recent_failure(db=None, user=SimpleNamespace(role="admin"))
    assert "toxicity_check" in reply
    assert "harmful content" in reply
    assert "harmful content" in reply


def test_explain_most_recent_failure_reports_none_found(monkeypatch):
    from app.services.policy_copilot import trace_lookup

    monkeypatch.setattr(trace_lookup, "_scoped_query", lambda db, user: _FakeQuery([]))
    user = SimpleNamespace(role="user")
    monkeypatch.setattr(
        trace_lookup.policy_loader, "role_config",
        lambda role: SimpleNamespace(granted_permissions=[]),
    )

    reply = trace_lookup.explain_most_recent_failure(db=None, user=user)
    assert "no blocked" in reply.lower()


def test_activity_summary_counts_blocked_vs_total(monkeypatch):
    from datetime import datetime, timezone

    from app.services.policy_copilot import trace_lookup

    now = datetime.now(timezone.utc)
    blocked_msg = SimpleNamespace(
        trace=[{"agent": "Guardrails", "tool": "presidio_check", "summary": "block: pii"}], created_at=now,
    )
    passed_msg = SimpleNamespace(
        trace=[{"agent": "Guardrails", "tool": "presidio_check", "summary": "pass: ok"}], created_at=now,
    )
    monkeypatch.setattr(
        trace_lookup, "_scoped_query", lambda db, user: _FakeQuery([(blocked_msg, None), (passed_msg, None)]),
    )
    user = SimpleNamespace(role="admin")
    monkeypatch.setattr(
        trace_lookup.policy_loader, "role_config",
        lambda role: SimpleNamespace(granted_permissions=["VIEW_AUDIT_LOGS"]),
    )

    reply = trace_lookup.activity_summary(db=None, user=user, hours=24)
    assert "1 of 2" in reply
    assert "presidio_check" in reply


@pytest.mark.parametrize("role,expected", [("admin", True), ("ceo", True), ("user", False), ("hr", False)])
def test_broad_visibility_matches_real_view_audit_logs_grants(role, expected):
    """The real security boundary — _scoped_query() only ever adds the
    caller's-own-conversations filter when this is False, so this must match
    routers/traces.py's identical VIEW_AUDIT_LOGS check exactly, against the
    REAL role config (no mocking) rather than a stand-in."""
    from app.services.policy_copilot.trace_lookup import _has_broad_visibility

    user = SimpleNamespace(role=role)
    assert _has_broad_visibility(user) is expected


def test_scoped_query_adds_the_own_conversations_filter_for_unprivileged_roles(monkeypatch):
    """Verifies the actual SQLAlchemy call shape, not just the boolean above
    — an unprivileged caller's query must carry an EXTRA filter() call
    (ConversationModel.user_id == user.id) that a privileged caller's does
    not."""
    from app.services.policy_copilot import trace_lookup

    class _RecordingQuery:
        def __init__(self):
            self.filter_calls = 0

        def join(self, *a, **kw):
            return self

        def filter(self, *a, **kw):
            self.filter_calls += 1
            return self

        def order_by(self, *a, **kw):
            return self

    recording = _RecordingQuery()
    fake_db = SimpleNamespace(query=lambda *a, **kw: recording)

    trace_lookup._scoped_query(fake_db, SimpleNamespace(role="user", id="user-123"))
    unprivileged_filters = recording.filter_calls

    recording2 = _RecordingQuery()
    fake_db2 = SimpleNamespace(query=lambda *a, **kw: recording2)
    trace_lookup._scoped_query(fake_db2, SimpleNamespace(role="admin", id="admin-1"))
    privileged_filters = recording2.filter_calls

    assert unprivileged_filters == privileged_filters + 1
