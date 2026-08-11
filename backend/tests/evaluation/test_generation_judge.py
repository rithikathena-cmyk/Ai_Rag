"""services/evaluation/generation_judge.py — Phase 2 evaluation completeness:
parsing citation_accuracy/answer_relevance from the v2 judge prompt's JSON
response, and threading a caller-supplied request_id into the underlying
gateway call (see services/evaluation/runner.py). Stubs claude_gateway.generate
directly on the singleton instance rather than requiring a live Anthropic
call, matching this suite's established I/O-boundary-stubbing convention.
"""

import json

import pytest

from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerateResult, TokenUsage
from app.services.evaluation import generation_judge
from app.services.evaluation.generation_judge import JudgeError, judge_answer


def _fake_result(payload: dict) -> GenerateResult:
    return GenerateResult(
        text=json.dumps(payload), stop_reason="end_turn", usage=TokenUsage(10, 20),
        request_id="req-1", model="claude-opus-5", latency_ms=123.0,
    )


def test_judge_answer_parses_citation_accuracy_and_answer_relevance(monkeypatch):
    payload = {
        "groundedness": 0.9, "faithfulness": 0.8, "total_claims": 2, "hallucinated_claims": 0,
        "citation_accuracy": 0.75, "answer_relevance": 0.6, "notes": "ok",
    }
    monkeypatch.setattr(generation_judge.claude_gateway, "generate", lambda req: _fake_result(payload))

    verdict = judge_answer("q", "a", ["source text"])

    assert verdict["citation_accuracy"] == 0.75
    assert verdict["answer_relevance"] == 0.6
    # Existing v1 fields must still come through unchanged.
    assert verdict["groundedness"] == 0.9
    assert verdict["faithfulness"] == 0.8


def test_judge_answer_defaults_new_fields_to_zero_when_missing_from_response(monkeypatch):
    payload = {"groundedness": 0.5, "faithfulness": 0.5, "total_claims": 0, "hallucinated_claims": 0, "notes": "n/a"}
    monkeypatch.setattr(generation_judge.claude_gateway, "generate", lambda req: _fake_result(payload))

    verdict = judge_answer("q", "a", [])

    assert verdict["citation_accuracy"] == 0.0
    assert verdict["answer_relevance"] == 0.0


def test_judge_answer_threads_request_id_into_gateway_request(monkeypatch):
    payload = {"groundedness": 1, "faithfulness": 1, "total_claims": 0, "hallucinated_claims": 0, "notes": ""}
    captured = {}

    def _fake_generate(req):
        captured["request_id"] = req.request_id
        return _fake_result(payload)

    monkeypatch.setattr(generation_judge.claude_gateway, "generate", _fake_generate)

    judge_answer("q", "a", [], request_id="shared-eval-id")

    assert captured["request_id"] == "shared-eval-id"


def test_judge_answer_request_id_defaults_to_none_when_not_supplied(monkeypatch):
    payload = {"groundedness": 1, "faithfulness": 1, "total_claims": 0, "hallucinated_claims": 0, "notes": ""}
    captured = {}

    def _fake_generate(req):
        captured["request_id"] = req.request_id
        return _fake_result(payload)

    monkeypatch.setattr(generation_judge.claude_gateway, "generate", _fake_generate)

    judge_answer("q", "a", [])

    assert captured["request_id"] is None


def test_judge_answer_raises_judge_error_on_generation_error(monkeypatch):
    def _raise(req):
        raise GenerationError("no api key")

    monkeypatch.setattr(generation_judge.claude_gateway, "generate", _raise)

    with pytest.raises(JudgeError):
        judge_answer("q", "a", [])


def test_judge_uses_v2_prompt():
    assert "citation_accuracy" in generation_judge.JUDGE_SYSTEM_PROMPT
    assert "answer_relevance" in generation_judge.JUDGE_SYSTEM_PROMPT
