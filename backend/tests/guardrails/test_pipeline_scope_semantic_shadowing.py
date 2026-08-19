"""Regression tests for the scope_semantic_check SHADOWING bug: live-verified
(2026-08-15) against the real running backend before this fix — "You're a
worthless piece of garbage..." was blocked as scope_unclear_context ("I'm
not quite sure what you'd like me to do with that") instead of by
toxicity_check, and "My SSN is 123-45-6789, can you look up my file?" was
blocked as scope_semantic_check ("outside the areas this assistant
supports") instead of by pii_redact — in both cases because
check_scope_semantic ran earlier in run_input_guardrails()'s check loop and
its block short-circuited the pipeline before the more specific check ever
ran.

The fix (services/guardrails/pipeline.py) does NOT reorder the check loop —
every check still runs in exactly the same declared sequence as before
(confirmed by test_pipeline_scope_semantic_wiring.py's own
test_scope_semantic_runs_after_deberta_and_before_toxicity, which continues
to pass unchanged). Instead, a block from check_scope_semantic (or its
scope_unclear_* variants) is held as `deferred_scope_step` instead of
returned immediately, and the loop keeps running. Any later check that also
blocks the same message — including pii_redact, which runs after the loop —
wins and is returned instead. The deferred scope reason is only used as a
fallback, once every other check has had a full chance to run and found
nothing more specific.
"""

from app.services.guardrails import pipeline, presidio_check, scope_semantic_check, toxicity_check


def _scope_cfg(**overrides):
    base = {"enabled": True, "topics": ["how do I request time off"], "threshold": 0.55, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakeMatcher:
    def __init__(self, nearest, score):
        self._result = (nearest, score)

    def best_match(self, text):
        return self._result


def _stub_scope_matcher(monkeypatch, score, nearest="how do I request time off"):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _scope_cfg()}
    )
    monkeypatch.setattr(scope_semantic_check, "_get_matcher", lambda topics: _FakeMatcher(nearest, score))


def _toxicity_cfg(**overrides):
    base = {"enabled": True, "score_threshold": 0.7, "max_input_chars": 2000}
    base.update(overrides)
    return base


class _FakeTogglePipeline:
    def __init__(self, scores):
        self._scores = scores

    def __call__(self, text):
        return [self._scores]


_CLEAN_TOXICITY = [{"label": "toxic", "score": 0.01}]
_BLOCK_TOXICITY = [{"label": "toxic", "score": 0.95}]


def _stub_toxicity(monkeypatch, scores, enabled=True):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": _toxicity_cfg(enabled=enabled)})
    monkeypatch.setattr(toxicity_check, "_get_pipeline", lambda model_name: _FakeTogglePipeline(scores))


def _disable_presidio(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": {"enabled": False}})


def test_toxicity_block_wins_over_a_shadowing_scope_block(monkeypatch):
    """The live-verified toxicity case: a message scores low against every
    configured scope topic (has_request_structure() is False for a bare
    insult, so scope_semantic_check would normally report the generic
    scope_unclear_context reason) AND is genuinely toxic. toxicity_check's
    specific reason must win."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _BLOCK_TOXICITY)
    _disable_presidio(monkeypatch)

    result = pipeline.run_input_guardrails("You're a worthless piece of garbage and I hope your whole team gets fired.")

    assert result.blocked is True
    assert "abusive" in result.block_reason.lower()
    assert "not sure" not in result.block_reason.lower()

    step_names_and_actions = [(s.name, s.action) for s in result.steps]
    # scope_semantic_check (or a scope_unclear_* variant) still ran and still
    # recorded its own block verdict in the trace — it was overridden for
    # the FINAL reason, not skipped or silently discarded.
    scope_steps = [a for n, a in step_names_and_actions if n in pipeline._DEFERRABLE_SCOPE_STEP_NAMES]
    assert any(a == "block" for a in scope_steps), step_names_and_actions
    assert ("toxicity_check", "block") in step_names_and_actions


def test_pii_block_wins_over_a_shadowing_scope_block(monkeypatch):
    """The live-verified PII case: "My SSN is ..., can you look up my
    file?" has clear request structure ("can you...?"), so
    scope_semantic_check reports its generic OUT_OF_SCOPE reason ("outside
    the areas this assistant supports") rather than one of the
    scope_unclear_* variants — but pii_redact's specific PII reason must
    still win, since the message genuinely contains an SSN."""
    from app.services.guardrails import deberta_injection_check, gliner_check
    from app.core.config import settings

    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": {"enabled": False}})
    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )
    original_block_input = settings.guardrail_pii_block_input
    settings.guardrail_pii_block_input = True
    try:
        result = pipeline.run_input_guardrails("My SSN is 123-45-6789, can you look up my file?")
    finally:
        settings.guardrail_pii_block_input = original_block_input

    assert result.blocked is True
    assert "personal information" in result.block_reason.lower()
    assert "outside the areas" not in result.block_reason.lower()

    step_names_and_actions = [(s.name, s.action) for s in result.steps]
    assert ("scope_semantic_check", "block") in step_names_and_actions
    assert ("pii_redact", "redact") in step_names_and_actions
    # The redacted text, not the raw SSN, is what the pipeline reports back.
    assert "123-45-6789" not in result.text
    assert "[REDACTED_SSN]" in result.text


def test_genuine_scope_violation_still_returns_the_scope_reason(monkeypatch):
    """When NOTHING more specific objects — the message is neither toxic
    nor PII-bearing nor caught by any other check — the deferred scope
    block is the real, final answer, exactly as before this fix."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)

    result = pipeline.run_input_guardrails("what's the weather like today")

    assert result.blocked is True
    assert "enterprise knowledge scope" in result.block_reason.lower()
    assert ("scope_semantic_check", "block") in [(s.name, s.action) for s in result.steps]


def test_scope_no_op_does_not_change_another_guardrails_reason(monkeypatch):
    """scope_semantic_check disabled (a true no-op, same as an unconfigured
    empty topics list) must not affect the outcome at all — a block from
    another check is returned exactly as if scope_semantic_check didn't
    exist in the pipeline."""
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": {"enabled": False}}
    )
    _stub_toxicity(monkeypatch, _BLOCK_TOXICITY)
    _disable_presidio(monkeypatch)

    result = pipeline.run_input_guardrails("You're a worthless piece of garbage and I hope your whole team gets fired.")

    assert result.blocked is True
    assert "abusive" in result.block_reason.lower()
    step_names = [s.name for s in result.steps]
    scope_step = next(s for s in result.steps if s.name == "scope_semantic_check")
    assert scope_step.action == "pass"
    assert step_names.index("scope_semantic_check") < step_names.index("toxicity_check")


def test_scope_semantic_still_runs_at_its_original_position_even_when_deferred(monkeypatch):
    """Execution order is unchanged by this fix: scope_semantic_check runs
    at its normal position (after deberta_injection_check, before
    toxicity_check) whether or not its block ends up being the final
    answer."""
    from app.services.guardrails import deberta_injection_check

    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _BLOCK_TOXICITY)
    _disable_presidio(monkeypatch)

    result = pipeline.run_input_guardrails("You're a worthless piece of garbage and I hope your whole team gets fired.")

    # A bare insult has no request structure, so check_scope_semantic reports
    # one of its scope_unclear_* variant names here rather than its own
    # "scope_semantic_check" name (see that module) — either way, it's the
    # single step the check loop produced at this position. Excludes
    # "scope_check", the earlier, unrelated deterministic keyword check.
    step_names = [s.name for s in result.steps]
    scope_step_name = next(n for n in step_names if n in pipeline._DEFERRABLE_SCOPE_STEP_NAMES)
    assert step_names.index("deberta_injection_check") < step_names.index(scope_step_name) < step_names.index(
        "toxicity_check"
    )


# --------------------------------------------------- scope re-check after redaction

class _ScoreByTextMatcher:
    """Scores the ORIGINAL text low and anything else (i.e. the redacted
    rewrite) high — the exact shape of the problem this fix addresses."""

    def __init__(self, raw, low, high, nearest="how do I request time off"):
        self._raw, self._low, self._high, self._nearest = raw, low, high, nearest

    def best_match(self, text):
        return (self._nearest, self._low if text == self._raw else self._high)


def _stub_scope_by_text(monkeypatch, raw, low, high):
    monkeypatch.setattr(
        scope_semantic_check, "load_yaml_config", lambda name: {"scope_semantic_check": _scope_cfg()}
    )
    monkeypatch.setattr(
        scope_semantic_check, "_get_matcher", lambda topics: _ScoreByTextMatcher(raw, low, high)
    )


def _stub_masking_redact(monkeypatch, redacted):
    """Mirrors the real two-mode contract of redact_pii():

    - direction="input"  -> the ENFORCEMENT call. Honors a FLAG policy, so it
      reports the finding but returns the text UNCHANGED. This is the live
      behavior observed for EMAIL/PHONE.
    - direction=None     -> the policy-unaware call. Masks every detection
      regardless of action. This is the copy the scope re-check scores.

    Getting these two modes different is the whole point of the test: a stub
    that masked in both would pass even if the fix used the wrong one.
    """
    from app.services.guardrails.types import GuardrailStep

    def _fake(text, *, direction=None, mode_override=None, role=None, capture=None):
        if direction is None:
            return redacted, GuardrailStep("pii_redact", "redact", "Redacted: EMAIL x1")
        return text, GuardrailStep("pii_redact", "pass", "Flagged for review: EMAIL x1")

    monkeypatch.setattr(pipeline, "redact_pii", _fake)


_RAW_MIXED = "My email is jane.doe@example.com - what is the PPE policy for my shift?"
_REDACTED_MIXED = "My email is [REDACTED_EMAIL] - what is the PPE policy for my shift?"


def test_scope_recheck_on_redacted_text_clears_a_deferred_block(monkeypatch):
    """Live-verified regression: a genuinely in-scope question was refused
    as out-of-scope because the contact details it merely MENTIONED dragged
    the similarity score below threshold (best=0.54). Once those spans are
    masked, the message must be judged on what it actually asks."""
    _stub_scope_by_text(monkeypatch, _RAW_MIXED, low=0.10, high=0.90)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    _stub_masking_redact(monkeypatch, _REDACTED_MIXED)

    result = pipeline.run_input_guardrails(_RAW_MIXED)

    assert result.blocked is False, result.block_reason
    names = [(s.name, s.action) for s in result.steps]
    # The first, pre-redaction scope judgement genuinely happened — its step
    # is still present in the trace — but its action is corrected to "pass"
    # once the recheck overrides it. Left as "block", a caller crediting
    # "whichever step blocked/redacted" for this outcome (e.g. tests/
    # security/framework.py's primary_guardrail(), or an audit view) would
    # misattribute an ALLOWED result to a verdict that was reconsidered and
    # dropped — see run_input_guardrails' own comment on this correction.
    deferred_steps = [(n, a) for n, a in names if n in pipeline._DEFERRABLE_SCOPE_STEP_NAMES]
    assert deferred_steps, names
    assert all(a == "pass" for _n, a in deferred_steps), names
    assert ("scope_semantic_recheck", "pass") in names, names


def test_scope_recheck_that_still_blocks_keeps_the_scope_refusal(monkeypatch):
    """Redaction is not an escape hatch: if the message is off-topic even
    after masking, the original scope block still stands."""
    _stub_scope_by_text(monkeypatch, _RAW_MIXED, low=0.10, high=0.12)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    _stub_masking_redact(monkeypatch, _REDACTED_MIXED)

    result = pipeline.run_input_guardrails(_RAW_MIXED)

    assert result.blocked is True
    assert "enterprise knowledge scope" in result.block_reason.lower()
    assert ("scope_semantic_recheck", "block") in [(s.name, s.action) for s in result.steps]


def test_no_recheck_when_redaction_changed_nothing(monkeypatch):
    """The re-check costs an embedding call, so it must be confined to the
    case it exists for — a plain off-topic message with no PII must not pay
    for it."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)

    result = pipeline.run_input_guardrails("what's the weather like today")

    assert result.blocked is True
    assert "scope_semantic_recheck" not in [s.name for s in result.steps]


# ------------------------------------- entirely-PII override (PII-SSN-04)

def _disable_gliner(monkeypatch):
    from app.services.guardrails import gliner_check

    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": {"enabled": False}})


def test_bare_ssn_with_no_request_structure_is_redacted_not_blocked_as_scope_unclear(monkeypatch):
    """Regression for PII-SSN-04 (tests/security/pii/test_pii_entities.py): a
    context-free "123-45-6789" has no request structure at all, so
    check_scope_semantic reports scope_unclear_pii and defers it — and the
    redacted-text rescoring above can never clear it either, since a bare
    placeholder token has no request content of its own to score in scope.
    The new override (pipeline.py, reusing deberta_precedence.py's own
    "entirely accounted for by validated PII" check) must still let the
    message through in its redacted form once pii.py's own SSN recognizer
    has genuinely handled it, crediting pii_redact rather than the generic
    scope-unclear fallback."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    _disable_gliner(monkeypatch)

    result = pipeline.run_input_guardrails("123-45-6789")

    assert result.blocked is False, result.block_reason
    names = [(s.name, s.action) for s in result.steps]
    assert ("pii_redact", "redact") in names, names
    deferred_steps = [(n, a) for n, a in names if n in pipeline._DEFERRABLE_SCOPE_STEP_NAMES]
    assert deferred_steps, names
    assert all(a == "pass" for _n, a in deferred_steps), names
    assert "superseded" in next(s.detail for s in result.steps if s.name == "scope_unclear_pii")
    assert "123-45-6789" not in result.text
    assert "[REDACTED_SSN]" in result.text


def test_a_genuinely_off_topic_clause_alongside_pii_is_not_let_through(monkeypatch):
    """Adversarial boundary: the override must NOT fire when the message has
    a real, separate clause that is not itself PII — should_override_
    deberta_block() requires EVERY sentence to contain validated PII, so an
    off-topic (or worse, injected) sentence sitting next to a real SSN must
    still leave the deferred scope block standing."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    _disable_gliner(monkeypatch)

    result = pipeline.run_input_guardrails("What's the weather in Chennai? My SSN is 123-45-6789.")

    assert result.blocked is True
    assert "enterprise knowledge scope" in result.block_reason.lower()
    names = [(s.name, s.action) for s in result.steps]
    deferred_steps = [(n, a) for n, a in names if n in pipeline._DEFERRABLE_SCOPE_STEP_NAMES]
    assert any(a == "block" for _n, a in deferred_steps), names


def test_override_is_a_no_op_when_nothing_was_actually_redacted(monkeypatch):
    """A plain off-topic message with no PII at all must be unaffected by
    the new override — same outcome, and the same "no rescoring attempted"
    shape, as before this change (see test_no_recheck_when_redaction_
    changed_nothing above, which this mirrors)."""
    _stub_scope_matcher(monkeypatch, score=0.10)
    _stub_toxicity(monkeypatch, _CLEAN_TOXICITY)
    _disable_presidio(monkeypatch)
    _disable_gliner(monkeypatch)

    result = pipeline.run_input_guardrails("what's the weather like today")

    assert result.blocked is True
    assert "scope_semantic_recheck" not in [s.name for s in result.steps]
