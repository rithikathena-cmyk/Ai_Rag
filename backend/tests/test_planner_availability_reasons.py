"""services/agents/planner.py — run_agent()'s early availability checks and
run_retrieval_fallback()'s reason -> message mapping. Complements
tests/gateway/test_claude_gateway.py (which pins the Anthropic-error ->
reason classification) and tests/test_chat_degraded_reason.py (which proves
the reason survives the HTTP round trip) by covering the two GenerationError
sites that live in planner.py itself: the missing-API-key check and the
admin kill switch (gateway/availability.py).
"""

import uuid

import pytest

from app.core.config import settings
from app.gateway import availability
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerationErrorReason
from app.services.agents import planner


@pytest.fixture(autouse=True)
def _restore_globals():
    original_key = settings.anthropic_api_key
    original_disabled = availability.is_disabled()
    yield
    settings.anthropic_api_key = original_key
    availability.set_disabled(original_disabled)


def test_run_agent_raises_no_api_key_reason_when_key_missing():
    settings.anthropic_api_key = ""
    availability.set_disabled(False)

    with pytest.raises(GenerationError) as exc_info:
        planner.run_agent("does this route reach Claude?")

    assert exc_info.value.reason is GenerationErrorReason.NO_API_KEY


def test_run_agent_raises_model_disabled_reason_when_admin_kill_switch_is_on():
    settings.anthropic_api_key = "sk-ant-configured-for-this-test"
    availability.set_disabled(True)

    with pytest.raises(GenerationError) as exc_info:
        planner.run_agent("does this route reach Claude?")

    assert exc_info.value.reason is GenerationErrorReason.MODEL_DISABLED


def test_no_api_key_check_runs_before_the_kill_switch_check():
    """Order matters for an honest message: a deployment with no key
    configured at all should be told that, not that an admin disabled it —
    even if the in-memory kill switch also happens to be set."""
    settings.anthropic_api_key = ""
    availability.set_disabled(True)

    with pytest.raises(GenerationError) as exc_info:
        planner.run_agent("query")

    assert exc_info.value.reason is GenerationErrorReason.NO_API_KEY


class _FakeDb:
    def close(self):
        pass


def _hit(text="a matched passage"):
    return {
        "chunk_id": str(uuid.uuid4()), "document_id": str(uuid.uuid4()), "document_filename": "handbook.pdf",
        "chunk_index": 0, "text": text, "score": 0.9,
    }


@pytest.mark.parametrize(
    "reason, expected_phrase, forbidden_phrase",
    [
        (GenerationErrorReason.NO_API_KEY, "no AI model is configured", "administrator"),
        (GenerationErrorReason.MODEL_DISABLED, "disabled by an administrator", "no AI model is configured"),
        (GenerationErrorReason.AUTH_FAILED, "rejected our credentials", "no AI model is configured"),
        (GenerationErrorReason.PROVIDER_UNAVAILABLE, "temporarily unavailable", "no AI model is configured"),
        (GenerationErrorReason.PROVIDER_ERROR, "could not process this request", "no AI model is configured"),
        (GenerationErrorReason.CAPACITY, "at capacity right now", "no AI model is configured"),
        (GenerationErrorReason.INTERNAL, "was unavailable for this reply", "no AI model is configured"),
    ],
)
def test_run_retrieval_fallback_message_matches_the_triggering_reason(monkeypatch, reason, expected_phrase, forbidden_phrase):
    monkeypatch.setattr(planner, "search_documents", lambda db, **k: [_hit()])

    result = planner.run_retrieval_fallback("warranty support", _FakeDb(), reason=reason)

    assert result.degraded_reason == reason.value
    assert expected_phrase in result.reply
    assert forbidden_phrase not in result.reply
    # The degraded path still does its actual job — raw sources reach the
    # caller even though there's no LLM synthesis — regardless of reason.
    assert len(result.sources) == 1
    assert result.sources[0]["text"] == "a matched passage"


def test_run_retrieval_fallback_defaults_to_internal_reason_when_unspecified(monkeypatch):
    monkeypatch.setattr(planner, "search_documents", lambda db, **k: [])

    result = planner.run_retrieval_fallback("query with no hits", _FakeDb())

    assert result.degraded_reason == GenerationErrorReason.INTERNAL.value
    assert "No matching content found" in result.reply


def test_successful_run_agent_result_has_no_degraded_reason():
    """AgentRunResult.degraded_reason must stay None on the real synthesis
    path — only run_retrieval_fallback() ever sets it."""
    assert planner.AgentRunResult(reply="ok").degraded_reason is None
