"""Shared fixtures for tests/guardrails/: semantic_check.py,
presidio_check.py, gliner_check.py, deberta_injection_check.py,
toxicity_check.py, and groundedness_check.py are all enabled by default in
production and all load a real model on first use (BGE-M3 embedding / spaCy
NER / GLiNER NER / a HuggingFace transformers classifier / another HF
classifier / an NLI cross-encoder, respectively) — most tests in this
directory are about a different check entirely, so all six default to
disabled for every test here (matching this suite's established convention
of stubbing the I/O boundary / not loading a real model unless a test is
specifically about that model). scope_semantic_check.py is NOT in this list
— it's already a no-op by default (empty `topics` config), so it never loads
its embedding matcher unless a test explicitly configures topics. Each
check's own test file (test_semantic_check.py / test_presidio_check.py /
test_gliner_check.py / test_deberta_injection_check.py /
test_toxicity_check.py / test_groundedness_check.py / the corresponding
test_pipeline_*_wiring.py files) re-enables and monkeypatches its own model
boundary per test, overriding these defaults — every test in those files
already calls monkeypatch.setattr on the relevant load_yaml_config/model
loader explicitly, so this fixture never fights them.

Deliberately scoped to ONLY this directory, not a suite-wide tests/conftest.py
— an earlier version of this fix used a top-level conftest.py and it broke
tests/llm_rbac/test_policy_engine.py in a subtle way: that file's own
_clear_caches fixture calls policy_loader._raw.cache_clear() after yield,
which silently assumes pytest's built-in `monkeypatch` fixture has ALREADY
reverted a same-test monkeypatch.setattr(policy_loader, "_raw", ...) by the
time that teardown code runs. Adding a new autouse fixture at an ANCESTOR
conftest level shifted fixture finalization order enough to break that
assumption (AttributeError: 'function' object has no attribute
'cache_clear'), even though nothing about llm_rbac itself was touched.
Scoping this file to tests/guardrails/ only avoids that entirely, since
tests/llm_rbac/ is a sibling directory, not a descendant. The two chat-flow
tests outside this directory that also needed deberta/gliner disabled
(tests/test_chat_degraded_reason.py, tests/test_chat_nemo_integration.py)
instead monkeypatch locally inside the one test function each that needs it
— see those files for the reasoning.

Without this, any test elsewhere in this directory that exercises either
guardrail pipeline function would silently pay a real model load/download on
first call — from tens of seconds (spaCy) to minutes (a fresh GLiNER/DeBERTa
download) — same class of problem semantic_check's disable already existed
to avoid.
"""

import pytest

from app.services.guardrails import (
    deberta_injection_check,
    gliner_check,
    groundedness_check,
    presidio_check,
    semantic_check,
    toxicity_check,
)


@pytest.fixture(autouse=True)
def _semantic_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(semantic_check, "load_yaml_config", lambda name: {"semantic_check": {"enabled": False}})


@pytest.fixture(autouse=True)
def _presidio_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(presidio_check, "load_yaml_config", lambda name: {"presidio_check": {"enabled": False}})


@pytest.fixture(autouse=True)
def _gliner_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(gliner_check, "load_yaml_config", lambda name: {"gliner_check": {"enabled": False}})


@pytest.fixture(autouse=True)
def _deberta_injection_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        deberta_injection_check, "load_yaml_config", lambda name: {"deberta_injection_check": {"enabled": False}}
    )


@pytest.fixture(autouse=True)
def _toxicity_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(toxicity_check, "load_yaml_config", lambda name: {"toxicity_check": {"enabled": False}})


@pytest.fixture(autouse=True)
def _groundedness_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        groundedness_check, "load_yaml_config", lambda name: {"groundedness_check": {"enabled": False}}
    )
