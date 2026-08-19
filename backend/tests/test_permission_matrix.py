"""Structural-contract tests (matching tests/test_documents_rbac.py's
convention — no live Postgres fixture in this suite) for the routers newly
gated in the enterprise permission-model pass: conversations.py,
upload_logs.py, evaluation.py, admin.py, and users.py. Each of these
previously had NO auth dependency at all (except users.py's GET/PATCH
/users), which this file guards against regressing back to.

require_permission()/require_role() return a closure (`_check`) whose own
source text is identical for every call site — the actual bound
Permission/Role values live in the closure's cells, not its source — so
these tests inspect closure vars (inspect.getclosurevars) rather than
grepping source text.
"""

import inspect

from fastapi.params import Depends as DependsMarker

from app.core.permissions import Permission
from app.core.roles import Role
from app.routers import admin, conversations, evaluation, upload_logs, users
from app.services.auth.dependencies import get_current_user
from app.services.monitoring.metrics import record_guardrail_event


def _bound_permissions(dependency) -> set:
    nonlocals = inspect.getclosurevars(dependency).nonlocals
    return {nonlocals["perm"]} if "perm" in nonlocals else set()


def _bound_roles(dependency) -> set:
    nonlocals = inspect.getclosurevars(dependency).nonlocals
    return nonlocals.get("allowed", set())


def _depends_on_get_current_user(fn) -> bool:
    for param in inspect.signature(fn).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is get_current_user:
            return True
    return False


def _find_route(router, path: str, method: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"no route found for {method} {path}")


# --------------------------------------------------------------- conversations

def test_conversations_router_requires_view_conversations_permission():
    perms = set()
    for dep in conversations.router.dependencies:
        perms |= _bound_permissions(dep.dependency)
    assert Permission.VIEW_CONVERSATIONS in perms


def test_conversations_routes_require_a_verified_user():
    for route in (conversations.list_conversations, conversations.get_conversation_detail, conversations.delete_conversation):
        assert _depends_on_get_current_user(route), route.__name__


def test_conversations_list_forces_non_privileged_callers_to_their_own_user_id():
    # BROAD_CONVERSATION_VISIBILITY_ROLES/authorize_conversation_access live
    # in services/memory/store.py, not as local/underscore-prefixed helpers
    # in this router — centralized there specifically so routers/chat.py's
    # conversation_id continuation path enforces the same ownership check
    # (see that function's own docstring). This test previously looked for
    # a "_BROAD_VISIBILITY_ROLES" name that was never the actual symbol.
    source = inspect.getsource(conversations.list_conversations)
    assert "BROAD_CONVERSATION_VISIBILITY_ROLES" in source
    assert "current_user.id" in source


def test_conversations_detail_and_delete_check_ownership():
    for route in (conversations.get_conversation_detail, conversations.delete_conversation):
        assert "authorize_conversation_access" in inspect.getsource(route)


# ---------------------------------------------------------------- upload_logs

def test_upload_logs_router_requires_view_audit_logs_permission():
    perms = set()
    for dep in upload_logs.router.dependencies:
        perms |= _bound_permissions(dep.dependency)
    assert Permission.VIEW_AUDIT_LOGS in perms


# ----------------------------------------------------------------- evaluation

def test_evaluation_router_is_admin_ceo_or_project_manager_only():
    roles = set()
    for dep in evaluation.router.dependencies:
        roles |= _bound_roles(dep.dependency)
    assert roles == {Role.ADMIN, Role.CEO, Role.PROJECT_MANAGER}


# ---------------------------------------------------------------------- admin

def test_admin_settings_routes_require_system_settings():
    for path, method in [
        ("/admin/collections", "GET"), ("/admin/collections", "POST"), ("/admin/collections/{name}", "DELETE"),
        ("/admin/model-availability", "GET"), ("/admin/model-availability", "PUT"),
    ]:
        route = _find_route(admin.router, path, method)
        perms = set()
        for dep in route.dependencies:
            perms |= _bound_permissions(dep.dependency)
        assert Permission.SYSTEM_SETTINGS in perms, f"{method} {path}"


def test_admin_analytics_routes_require_view_analytics():
    for path, method in [
        ("/admin/metrics", "GET"), ("/admin/query-metrics", "GET"),
        ("/admin/gateway-usage", "GET"), ("/admin/guardrail-analytics", "GET"),
    ]:
        route = _find_route(admin.router, path, method)
        perms = set()
        for dep in route.dependencies:
            perms |= _bound_permissions(dep.dependency)
        assert Permission.VIEW_ANALYTICS in perms, f"{method} {path}"


# VIEW_ANALYTICS is granted to EVERY role (config/llm_rbac.yaml) so the
# read-only Metrics dashboards are visible org-wide. The two tests below are
# what keep that from also widening the one genuinely sensitive field behind
# them: a guardrail event's raw `detail`, recorded verbatim by
# pipeline.py::_record() and embedding classifier internals (semantic_check's
# "best score=", its matched unsafe-example phrase, scope.py's configured
# deny-keyword) that the chat UI deliberately hides from non-privileged users.

class _StubUser:
    def __init__(self, role: str):
        self.role = role


_SENSITIVE_DETAIL = "Semantically similar (score=0.91) to a known unsafe pattern: 'internal example phrase'"


def _detail_for_role(role: str) -> str:
    record_guardrail_event("input", "semantic_risk_check", "block", _SENSITIVE_DETAIL)
    response = admin.get_guardrail_analytics(current_user=_StubUser(role))
    return response.events[-1].detail


def test_employee_cannot_see_raw_guardrail_detail():
    # Employee holds VIEW_ANALYTICS (so the endpoint is reachable) but not
    # VIEW_AUDIT_LOGS — the score and the matched internal phrase must not
    # survive into the response at all, not merely be hidden client-side.
    detail = _detail_for_role("user")
    assert detail == admin._REDACTED_GUARDRAIL_DETAIL
    assert "0.91" not in detail
    assert "internal example phrase" not in detail


def test_audit_log_roles_still_see_raw_guardrail_detail():
    # Admin holds VIEW_AUDIT_LOGS, the same line that gates org-wide trace
    # visibility — raw detail is intentionally unchanged for them.
    assert _detail_for_role("admin") == _SENSITIVE_DETAIL


# -------------------------------------------------------------------- users

def test_user_profile_route_requires_verified_user():
    assert _depends_on_get_current_user(users.get_user)


def test_user_profile_route_allows_self_or_checks_view_users():
    source = inspect.getsource(users.get_user)
    assert "current_user.id" in source
    assert "VIEW_USERS" in source


def test_preferences_routes_require_self():
    for route in (users.get_user_preferences, users.put_user_preferences):
        assert _depends_on_get_current_user(route), route.__name__
        assert "_require_self" in inspect.getsource(route)


def test_list_users_requires_view_users_permission():
    route = _find_route(users.router, "/users", "GET")
    perms = set()
    for dep in route.dependencies:
        perms |= _bound_permissions(dep.dependency)
    assert Permission.VIEW_USERS in perms


def test_update_user_requires_manage_users_permission():
    route = _find_route(users.router, "/users/{user_id}", "PATCH")
    perms = set()
    for dep in route.dependencies:
        perms |= _bound_permissions(dep.dependency)
    assert Permission.MANAGE_USERS in perms


def test_create_user_is_admin_or_ceo_only():
    # POST /users used to have no auth dependency at all (open self-
    # registration from the login page's "Create account" tab). Account
    # creation is now Admin/CEO-only via require_role — deliberately
    # require_role, not require_permission(MANAGE_USERS): MANAGE_USERS is
    # Admin-only (role/active-status *editing* of existing accounts, see
    # test_update_user_requires_manage_users_permission below), and account
    # creation is a separate, slightly broader grant that also includes CEO.
    roles = set()
    for param in inspect.signature(users.create_user).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is not None:
            roles |= _bound_roles(param.default.dependency)
    assert roles == {Role.ADMIN, Role.CEO}
