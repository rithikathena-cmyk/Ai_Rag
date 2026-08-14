"""services/guardrails/scope.py — keyword allow/deny scope check. No
implementation change here (inspection found the existing mechanism sound
for what it does); this file just closes the pre-existing zero-test-coverage
gap. Role-specific scoping ("Employee -> manufacturing", "HR -> hr", ...) is
a *separate*, already-existing mechanism — llm_rbac.yaml's
knowledge_departments, enforced server-side in
services/guardrails/retrieval_permissions.py — not something scope.py
duplicates; scope.py is a plain topic keyword filter, orthogonal to that."""

import pytest

from app.core.config import settings
from app.services.guardrails.scope import check_scope


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_scope_deny_keywords, settings.guardrail_scope_allow_keywords)
    yield
    settings.guardrail_scope_deny_keywords, settings.guardrail_scope_allow_keywords = original


def test_no_restrictions_configured_passes_everything():
    settings.guardrail_scope_deny_keywords = ""
    settings.guardrail_scope_allow_keywords = ""
    assert check_scope("anything at all").action == "pass"


def test_deny_keyword_blocks():
    settings.guardrail_scope_deny_keywords = "politics, weather"
    settings.guardrail_scope_allow_keywords = ""
    assert check_scope("what's your opinion on politics?").action == "block"


def test_deny_keyword_is_case_insensitive():
    settings.guardrail_scope_deny_keywords = "politics"
    settings.guardrail_scope_allow_keywords = ""
    assert check_scope("Let's talk POLITICS").action == "block"


def test_message_without_deny_keyword_passes():
    settings.guardrail_scope_deny_keywords = "politics"
    settings.guardrail_scope_allow_keywords = ""
    assert check_scope("what is the leave policy?").action == "pass"


def test_allow_list_blocks_anything_not_matching():
    settings.guardrail_scope_deny_keywords = ""
    settings.guardrail_scope_allow_keywords = "manufacturing, safety"
    assert check_scope("what's the weather today?").action == "block"


def test_allow_list_permits_matching_topic():
    settings.guardrail_scope_deny_keywords = ""
    settings.guardrail_scope_allow_keywords = "manufacturing, safety"
    assert check_scope("what's the safety procedure for line 3?").action == "pass"


def test_deny_takes_precedence_over_allow():
    settings.guardrail_scope_deny_keywords = "confidential"
    settings.guardrail_scope_allow_keywords = "manufacturing"
    assert check_scope("manufacturing confidential data").action == "block"
