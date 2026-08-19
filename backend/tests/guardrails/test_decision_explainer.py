"""decision_explainer.py — the one place an LLM participates in the
guardrail pipeline, strictly after a PolicyDecision already exists. Every
test here mocks the gateway (never a real network call) and checks the same
property from a different angle: this module can only ever ADD prose on top
of an already-final decision, never influence one, and any failure degrades
to None rather than raising or fabricating a decision-shaped result.
"""

from __future__ import annotations

from app.services.guardrails.decision_explainer import explain_decision


def _fake_result(text: str, stop_reason: str = "end_turn"):
    from app.gateway.schemas import GenerateResult, TokenUsage

    return GenerateResult(
        text=text, stop_reason=stop_reason, usage=TokenUsage(), request_id="test",
        model="test-model", latency_ms=1.0,
    )


def test_returns_the_models_prose_on_success(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    monkeypatch.setattr(
        gateway_singleton, "generate",
        lambda request: _fake_result("Blocked because the message matched a known injection phrasing."),
    )

    explanation = explain_decision(blocking_check="prompt_injection_check", action="BLOCK", detail="matched pattern")
    assert explanation == "Blocked because the message matched a known injection phrasing."


def test_sends_only_labels_never_the_original_message(monkeypatch):
    """The security-relevant property: this module has no parameter for raw
    user text or PII, so it structurally cannot leak either to the model."""
    import inspect

    params = set(inspect.signature(explain_decision).parameters)
    assert params == {"blocking_check", "action", "detail"}
    assert "text" not in params and "message" not in params and "content" not in params


def test_returns_none_when_the_gateway_is_unavailable(monkeypatch):
    from app.gateway.claude_gateway import GenerationError, claude_gateway as gateway_singleton
    from app.gateway.schemas import GenerationErrorReason

    def _raise(request):
        raise GenerationError("no key configured", reason=GenerationErrorReason.NO_API_KEY)

    monkeypatch.setattr(gateway_singleton, "generate", _raise)

    assert explain_decision(blocking_check="toxicity_check", action="BLOCK") is None


def test_returns_none_on_a_model_refusal(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    monkeypatch.setattr(gateway_singleton, "generate", lambda request: _fake_result("", stop_reason="refusal"))

    assert explain_decision(blocking_check="toxicity_check", action="BLOCK") is None


def test_returns_none_for_an_empty_reply(monkeypatch):
    from app.gateway.claude_gateway import claude_gateway as gateway_singleton

    monkeypatch.setattr(gateway_singleton, "generate", lambda request: _fake_result("   "))

    assert explain_decision(blocking_check="toxicity_check", action="BLOCK") is None
