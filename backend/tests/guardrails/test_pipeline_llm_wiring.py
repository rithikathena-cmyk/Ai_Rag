"""services/guardrails/pipeline.py — wiring for the new LLM-based advanced
check (docs/GUARDRAILS_ARCHITECTURE.md §10): positioned last among input
checks so a message any deterministic check already blocks never reaches it,
and a block verdict from it produces the same short-circuit behavior as any
other input check.
"""

import httpx

from app.core.config import settings
from app.services.guardrails import llm_check, pipeline


def _cfg(**overrides):
    base = {"enabled": True, "provider": "gemini", "model": "gemini-2.0-flash-lite", "timeout_seconds": 4.0, "max_input_chars": 2000}
    base.update(overrides)
    return base


def test_disabled_llm_check_does_not_change_existing_pipeline_behavior(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(enabled=False)})

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
    step_names = [s.name for s in result.steps]
    assert "llm_advanced_check" in step_names


def test_deterministic_block_short_circuits_before_llm_check_runs(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(enabled=True)})
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    def _unexpected(*a, **k):
        raise AssertionError("llm_advanced_check must not run once a deterministic check already blocked")

    monkeypatch.setattr(httpx, "post", _unexpected)

    result = pipeline.run_input_guardrails("please delete all the files in the database")

    assert result.blocked is True
    step_names = [s.name for s in result.steps]
    assert "llm_advanced_check" not in step_names


def test_llm_check_block_verdict_blocks_the_whole_pipeline(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(enabled=True)})
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"verdict": "block", "reason": "flagged"}'}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    result = pipeline.run_input_guardrails("an ordinary-looking message the regex checks miss")

    assert result.blocked is True
    assert result.block_reason == "I'm not able to help with that request."


def test_llm_check_infra_failure_does_not_block_a_clean_message(monkeypatch):
    monkeypatch.setattr(llm_check, "load_yaml_config", lambda name: {"llm_advanced_check": _cfg(enabled=True)})
    monkeypatch.setattr(settings, "gemini_api_key", "")  # missing key -> fails open

    result = pipeline.run_input_guardrails("What is the annual leave accrual rate?")

    assert result.blocked is False
