"""Verifies /chat and /search are actually gated behind get_current_user and
no longer accept a client-supplied user_id — the "close the auth gap" change
docs/AGENT_SECURITY_MODEL.md flagged as the prerequisite for LLM RBAC to be a
real security boundary (see docs/LLM_RBAC_ARCHITECTURE.md §1).

This repo's existing test suite has no real-Postgres fixture (every test is
either a pure function or fakes its I/O boundary — see tests/test_rbac.py) —
a full end-to-end /chat call touches Postgres, Qdrant, and the Claude
Gateway, none of which are stood up for tests here. Rather than build that
infrastructure for this one check, these tests
verify the structural contract directly: the route declares get_current_user
as a required dependency, and the request models no longer expose the field
that used to let a caller assert an arbitrary identity.
"""

import inspect

from fastapi.params import Depends as DependsMarker

from app.routers import chat, search
from app.services.auth.dependencies import get_current_user


def _depends_on_get_current_user(fn) -> bool:
    for param in inspect.signature(fn).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is get_current_user:
            return True
    return False


def test_chat_endpoint_requires_a_verified_user():
    assert _depends_on_get_current_user(chat.chat)


def test_search_endpoint_requires_a_verified_user():
    assert _depends_on_get_current_user(search.search)


def test_chat_request_no_longer_accepts_a_client_supplied_user_id():
    assert "user_id" not in chat.ChatRequest.model_fields


def test_search_request_no_longer_accepts_a_client_supplied_user_id():
    assert "user_id" not in search.SearchRequest.model_fields


def test_chat_request_accepts_an_optional_action_for_the_rbac_permission_catalog():
    assert "action" in chat.ChatRequest.model_fields
    assert chat.ChatRequest.model_fields["action"].is_required() is False


def test_search_request_accepts_an_optional_action_for_the_rbac_permission_catalog():
    assert "action" in search.SearchRequest.model_fields
    assert search.SearchRequest.model_fields["action"].is_required() is False


def test_chat_request_accepts_an_optional_report_type():
    assert "report_type" in chat.ChatRequest.model_fields
    assert chat.ChatRequest.model_fields["report_type"].is_required() is False
