"""orchestrator_graph.run_input_stage()/run_output_stage() — verifies the
LangGraph wrapper reproduces routers/chat.py's original inline behavior
exactly (this pass's explicit bar — see the approved plan's "Sequencing"
section), by stubbing the underlying check functions the same way
test_pipeline_scope_semantic_shadowing.py stubs pipeline.py's checks:
monkeypatch the orchestrator_graph module's own imported references, not the
origin modules, since orchestrator_graph.py imports each function by name."""

import uuid

import pytest

from app.core.config import settings
from app.services.guardrails import orchestrator_graph
from app.services.guardrails.request_classifier import RequestClassification
from app.services.guardrails.types import GuardrailResult, GuardrailStep


@pytest.fixture(autouse=True)
def _no_real_classifier_call(monkeypatch):
    """request_understanding (see orchestrator_graph.py's own docstring) is
    a sequential, LLM-backed, trace-only node — every existing test in this
    file predates it and asserts nothing about it, so it's mocked to a
    no-op None (the same "gateway unavailable" result classify_request()
    itself returns on failure) by default here, keeping this suite fast and
    offline. Tests that actually exercise the classifier's wiring override
    this explicitly."""
    monkeypatch.setattr(orchestrator_graph, "classify_request", lambda text: None)


def _state(**overrides):
    base = {
        "request_id": str(uuid.uuid4()),
        "user_id": uuid.uuid4(),
        "role": "user",
        "department": "manufacturing",
    }
    base.update(overrides)
    return base


def test_input_stage_allows_a_clean_message(monkeypatch):
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=False, steps=[GuardrailStep("length_check", "pass", "ok")]),
    )
    result = orchestrator_graph.run_input_stage(_state(user_message="What are the PPE requirements?"))

    assert result["policy_decision"].action == "ALLOW"
    assert result["normalized_message"] == "What are the PPE requirements?"
    assert result["risk_findings"].level == "LOW"


def test_input_stage_blocks_and_records_the_block(monkeypatch):
    calls = []
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails",
        lambda text, role=None: GuardrailResult(
            text=text, blocked=True, block_reason="I'm not able to help with that request.",
            steps=[GuardrailStep("secret_detected_check", "block", "AWS key detected")],
            blocking_step_name="secret_detected_check",
        ),
    )
    monkeypatch.setattr(orchestrator_graph, "record_block", lambda user_id: calls.append(user_id))

    user_id = uuid.uuid4()
    result = orchestrator_graph.run_input_stage(_state(user_id=user_id, user_message="here is my AWS key AKIA..."))

    assert result["policy_decision"].action == "BLOCK"
    assert result["reply"] == "I'm not able to help with that request."
    assert result["blocking_step_name"] == "secret_detected_check"
    assert calls == [user_id]


def test_input_stage_bypassed_when_guardrails_globally_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails", lambda text, role=None: called.append(text) or GuardrailResult(text, False),
    )
    original = settings.guardrails_enabled
    settings.guardrails_enabled = False
    try:
        result = orchestrator_graph.run_input_stage(_state(user_message="anything at all"))
    finally:
        settings.guardrails_enabled = original

    assert called == []
    assert result["input_findings"] is None
    assert result["normalized_message"] == "anything at all"
    assert result["policy_decision"].action == "ALLOW"


def test_output_stage_allows_a_clean_reply(monkeypatch):
    monkeypatch.setattr(
        orchestrator_graph, "run_output_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=False, steps=[GuardrailStep("toxicity_check", "pass", "ok")]),
    )
    monkeypatch.setattr(
        orchestrator_graph, "check_citations", lambda reply, sources: GuardrailStep("output_citation_check", "pass", "cites its sources"),
    )
    monkeypatch.setattr(
        orchestrator_graph, "check_groundedness",
        lambda reply, sources: GuardrailStep("groundedness_check", "pass", "appears consistent (score=0.10)"),
    )
    result = orchestrator_graph.run_output_stage(_state(llm_response="The PPE requirements are...", retrieved_documents=[{"text": "..."}]))

    assert result["policy_decision"].action == "ALLOW"
    assert result["reply"] == "The PPE requirements are..."
    assert result["citation_findings"].action == "pass"
    assert result["grounding_findings"].action == "pass"


def test_output_stage_blocked_reply_skips_citation_and_groundedness_calls(monkeypatch):
    citation_calls = []
    grounding_calls = []
    monkeypatch.setattr(
        orchestrator_graph, "run_output_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=True, block_reason="I can't help with that.", steps=[]),
    )
    monkeypatch.setattr(orchestrator_graph, "check_citations", lambda reply, sources: citation_calls.append(1) or GuardrailStep("x", "pass", "x"))
    monkeypatch.setattr(orchestrator_graph, "check_groundedness", lambda reply, sources: grounding_calls.append(1) or GuardrailStep("x", "pass", "x"))
    monkeypatch.setattr(orchestrator_graph, "record_block", lambda user_id: None)

    result = orchestrator_graph.run_output_stage(_state(llm_response="unsafe content", retrieved_documents=[]))

    assert result["policy_decision"].action == "BLOCK"
    assert result["reply"] == "I can't help with that."
    assert citation_calls == []
    assert grounding_calls == []
    assert "skipped" in result["citation_findings"].detail
    assert "skipped" in result["grounding_findings"].detail


def test_output_stage_groundedness_fail_closed_overrides_an_otherwise_allowed_reply(monkeypatch):
    """Reproduces chat.py's original comment verbatim: check_groundedness()
    only ever returns action == 'block' via its fail_closed detector-failure
    path — this is the one case that must actually withhold an otherwise-
    unblocked reply."""
    calls = []
    monkeypatch.setattr(
        orchestrator_graph, "run_output_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=False, steps=[]),
    )
    monkeypatch.setattr(
        orchestrator_graph, "check_citations", lambda reply, sources: GuardrailStep("output_citation_check", "pass", "cites its sources"),
    )
    monkeypatch.setattr(
        orchestrator_graph, "check_groundedness",
        lambda reply, sources: GuardrailStep("groundedness_check", "block", "check unavailable, failed closed: RuntimeError"),
    )
    monkeypatch.setattr(orchestrator_graph, "record_block", lambda user_id: calls.append(user_id))

    user_id = uuid.uuid4()
    result = orchestrator_graph.run_output_stage(
        _state(user_id=user_id, llm_response="a real answer", retrieved_documents=[{"text": "..."}]),
    )

    assert result["policy_decision"].action == "BLOCK"
    assert result["reply"] != "a real answer"
    assert "skipped" in result["citation_findings"].detail
    assert calls == [user_id]


# --------------------------------------------------------------------------
# request_understanding — the sequential, trace-only classifier node
# --------------------------------------------------------------------------

def test_request_classification_reaches_the_final_state(monkeypatch):
    monkeypatch.setattr(
        orchestrator_graph, "classify_request",
        lambda text: RequestClassification(
            category="PII_SENSITIVE", confidence=0.9,
            relevant_checks=("presidio_check", "gliner_check", "pii_redact"),
        ),
    )
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=False, steps=[GuardrailStep("length_check", "pass", "ok")]),
    )

    result = orchestrator_graph.run_input_stage(_state(user_message="Give me John's phone number"))

    assert result["request_classification"].category == "PII_SENSITIVE"
    assert result["request_classification"].relevant_checks == ("presidio_check", "gliner_check", "pii_redact")


def test_request_classification_never_affects_the_policy_decision(monkeypatch):
    """The core safety property: whatever the classifier says, the real
    checks' findings are the only thing policy_decide() ever sees. A
    PII_SENSITIVE classification on a message the real checks pass cleanly
    must still ALLOW; a classification that disagrees with a genuine block
    must not soften it."""
    monkeypatch.setattr(
        orchestrator_graph, "classify_request",
        lambda text: RequestClassification(category="GENERAL_QUERY", confidence=0.99, relevant_checks=()),
    )
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails",
        lambda text, role=None: GuardrailResult(
            text=text, blocked=True, block_reason="I'm not able to help with that request.",
            steps=[GuardrailStep("secret_detected_check", "block", "AWS key detected")],
            blocking_step_name="secret_detected_check",
        ),
    )
    monkeypatch.setattr(orchestrator_graph, "record_block", lambda user_id: None)

    result = orchestrator_graph.run_input_stage(_state(user_message="here is my AWS key AKIA..."))

    # The classifier confidently called this GENERAL_QUERY (wrong, but
    # irrelevant) — the real check still blocked it, and the decision must
    # reflect that unchanged.
    assert result["policy_decision"].action == "BLOCK"
    assert result["blocking_step_name"] == "secret_detected_check"


def test_a_failed_classification_leaves_the_pipeline_completely_unaffected(monkeypatch):
    """classify_request() returning None (no API key, provider error,
    refusal, bad output — see that module's own docstring) must be
    indistinguishable, from the pipeline's own behavior, from the classifier
    never having been added at all."""
    monkeypatch.setattr(orchestrator_graph, "classify_request", lambda text: None)
    monkeypatch.setattr(
        orchestrator_graph, "run_input_guardrails",
        lambda text, role=None: GuardrailResult(text=text, blocked=False, steps=[GuardrailStep("length_check", "pass", "ok")]),
    )

    result = orchestrator_graph.run_input_stage(_state(user_message="What are the PPE requirements?"))

    assert result["request_classification"] is None
    assert result["policy_decision"].action == "ALLOW"
