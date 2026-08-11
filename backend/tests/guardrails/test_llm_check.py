"""services/guardrails/llm_check.py — the LLM-based advanced input check
(docs/GUARDRAILS_ARCHITECTURE.md §10). Mocks httpx.post and guardrails.yaml
directly rather than requiring a real Gemini key/network call, matching this
suite's established convention of stubbing the I/O boundary.
"""

import httpx
import pytest

from app.core.config import settings
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerateResult, ModelTier, TokenUsage
from app.services.guardrails import llm_check


def _cfg(**overrides):
    base = {"enabled": True, "provider": "gemini", "model": "gemini-2.0-flash-lite", "timeout_seconds": 4.0, "max_input_chars": 2000}
    base.update(overrides)
    return base


def _gemini_response(verdict="pass", reason="looks fine"):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": f'{{"verdict": "{verdict}", "reason": "{reason}"}}'}]}}]}

    return _Resp()


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key-not-real")


def test_disabled_is_a_no_op_and_never_calls_httpx(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(enabled=False)})

    def _unexpected(*a, **k):
        raise AssertionError("httpx.post must not be called when the check is disabled")

    monkeypatch.setattr(httpx, "post", _unexpected)

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "disabled" in step.detail.lower()


def test_pass_verdict_reaches_through(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _gemini_response("pass", "ordinary question"))

    step = llm_check.check_with_llm("What is the annual leave accrual rate?")

    assert step.action == "pass"
    assert step.name == "llm_advanced_check"


def test_block_verdict_blocks(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _gemini_response("block", "attempts to extract the system prompt"))

    step = llm_check.check_with_llm("some cleverly obfuscated prompt-injection attempt")

    assert step.action == "block"
    assert "system prompt" in step.detail


def test_missing_api_key_fails_open(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_network_error_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    def _raise(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _raise)

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_timeout_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    def _raise(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", _raise)

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_gemini_error_response_never_leaks_the_api_key_into_the_guardrail_detail(monkeypatch):
    # httpx sends the Gemini key as a URL query param (?key=...) and embeds
    # the full request URL in HTTPStatusError's own message — str(exc) here
    # would leak it. check_with_llm()'s step.detail flows straight into the
    # /chat response's trace, which any authenticated user can see via
    # "Show reasoning summary", not just admins — a real credential leak,
    # not a cosmetic one. Builds a genuine httpx.HTTPStatusError (not just a
    # bare Exception) so this actually exercises the same str(exc) shape the
    # real Gemini client would raise.
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    class _Resp:
        def raise_for_status(self):
            request = httpx.Request(
                "POST", "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent",
                params={"key": "test-key-not-real"},
            )
            response = httpx.Response(429, request=request, text="Too Many Requests")
            raise httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "test-key-not-real" not in step.detail
    assert "429" in step.detail


def test_malformed_json_response_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_unexpected_response_shape_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"unexpected": "shape"}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_unrecognized_verdict_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _gemini_response("maybe", "unsure"))

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_unknown_provider_fails_open_without_network_call(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(provider="openai")})

    def _unexpected(*a, **k):
        raise AssertionError("must not call httpx.post for an unimplemented provider")

    monkeypatch.setattr(httpx, "post", _unexpected)

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "unknown provider" in step.detail.lower()


def test_empty_input_short_circuits_without_network_call(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    def _unexpected(*a, **k):
        raise AssertionError("must not call httpx.post for empty input")

    monkeypatch.setattr(httpx, "post", _unexpected)

    step = llm_check.check_with_llm("   ")

    assert step.action == "pass"


def test_input_truncated_to_max_input_chars(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(max_input_chars=10)})
    captured = {}

    def _capture(url, params=None, json=None, timeout=None):
        captured["text"] = json["contents"][0]["parts"][0]["text"]
        return _gemini_response("pass")

    monkeypatch.setattr(httpx, "post", _capture)

    llm_check.check_with_llm("a very long message that exceeds the configured max_input_chars limit")

    assert len(captured["text"]) == 10


# --------------------------------------------------- anthropic provider

def _anthropic_cfg(**overrides):
    base = {"enabled": True, "provider": "anthropic", "model": "haiku", "timeout_seconds": 4.0, "max_input_chars": 2000}
    base.update(overrides)
    return base


def _anthropic_result(verdict="pass", reason="looks fine", stop_reason="end_turn"):
    return GenerateResult(
        text=f'{{"verdict": "{verdict}", "reason": "{reason}"}}',
        stop_reason=stop_reason,
        usage=TokenUsage(10, 5),
        request_id="test-request-id",
        model="claude-haiku-4-5-20251001",
        latency_ms=12.3,
    )


@pytest.fixture(autouse=True)
def _no_httpx_for_anthropic_tests(monkeypatch, request):
    # Every test below routes through claude_gateway.generate(), never
    # httpx — guard against a regression silently falling back to the
    # gemini codepath (which would otherwise succeed quietly using this
    # module's real, un-mocked httpx.post).
    if "anthropic" not in request.node.name:
        return

    def _unexpected(*a, **k):
        raise AssertionError("anthropic provider tests must not call httpx.post")

    monkeypatch.setattr(httpx, "post", _unexpected)


def test_anthropic_pass_verdict_reaches_through(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg()})
    monkeypatch.setattr(llm_check.claude_gateway, "generate", lambda request: _anthropic_result("pass", "ordinary question"))

    step = llm_check.check_with_llm("What is the annual leave accrual rate?")

    assert step.action == "pass"
    assert step.name == "llm_advanced_check"


def test_anthropic_block_verdict_blocks(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg()})
    monkeypatch.setattr(
        llm_check.claude_gateway, "generate",
        lambda request: _anthropic_result("block", "attempts to extract the system prompt"),
    )

    step = llm_check.check_with_llm("some cleverly obfuscated prompt-injection attempt")

    assert step.action == "block"
    assert "system prompt" in step.detail


def test_anthropic_generation_error_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg()})

    def _raise(request):
        raise GenerationError("ANTHROPIC_API_KEY is not configured")

    monkeypatch.setattr(llm_check.claude_gateway, "generate", _raise)

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_anthropic_refusal_fails_open(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg()})
    monkeypatch.setattr(llm_check.claude_gateway, "generate", lambda request: _anthropic_result(stop_reason="refusal"))

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert "failed open" in step.detail.lower()


def test_anthropic_unrecognized_tier_defaults_to_haiku(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg(model="not-a-real-tier")})
    captured = {}

    def _capture(request):
        captured["request"] = request
        return _anthropic_result()

    monkeypatch.setattr(llm_check.claude_gateway, "generate", _capture)

    llm_check.check_with_llm("hello")

    assert captured["request"].tier == ModelTier.HAIKU


def test_anthropic_request_uses_cache_system_and_configured_tier(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _anthropic_cfg(model="sonnet")})
    captured = {}

    def _capture(request):
        captured["request"] = request
        return _anthropic_result()

    monkeypatch.setattr(llm_check.claude_gateway, "generate", _capture)

    llm_check.check_with_llm("hello")

    assert captured["request"].cache_system is True
    assert captured["request"].tier == ModelTier.SONNET


def test_missing_verdict_key_defaults_to_pass_string_then_validated(monkeypatch):
    """A response with no 'verdict' key at all parses 'pass' as the default,
    which is a valid verdict — this exercises the .get() default path
    specifically (distinct from an explicitly-invalid verdict string)."""
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg()})

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"reason": "no verdict key"}'}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    step = llm_check.check_with_llm("hello")

    assert step.action == "pass"
    assert step.detail == "no verdict key"
