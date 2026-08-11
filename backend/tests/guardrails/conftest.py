"""Shared fixture for tests/guardrails/: semantic_check.py is enabled by
default in production and makes a real BGE-M3 embedding call — most tests in
this directory are about a different check entirely, so this defaults it to
disabled for every test here (matching this suite's established convention
of stubbing the I/O boundary / not loading a real model unless a test is
specifically about that model). test_semantic_check.py re-enables it per
test with a fake embedder, overriding this fixture's monkeypatch.
"""

import pytest

from app.services.guardrails import semantic_check


@pytest.fixture(autouse=True)
def _semantic_check_disabled_by_default(monkeypatch):
    monkeypatch.setattr(semantic_check, "load_yaml_config", lambda name: {"semantic_check": {"enabled": False}})
