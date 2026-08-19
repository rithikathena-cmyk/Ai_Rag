"""routers/documents.py previously had zero authentication on any route —
this verified gap (docs/ROLE_PERMISSION_MATRIX.md's "Extension point") meant
Employee-must-not-upload/delete was unenforced and the department filtering
chat/search already apply was bypassable via a direct GET. This file follows
tests/test_chat_auth.py's structural-contract convention (no real
Postgres/Qdrant fixture exists in this suite) plus the pure-function
filter/policy tests already established in tests/llm_rbac/test_category_policy.py.
"""

import inspect

from fastapi.params import Depends as DependsMarker

from app.routers import documents
from app.services.auth.dependencies import get_current_user
from app.services.guardrails.retrieval_permissions import filter_by_category

_ROUTES = [
    documents.upload_document,
    documents.get_ingestion_progress,
    documents.get_document,
    documents.get_document_text,
    documents.delete_document,
    documents.reindex_document,
    documents.list_documents,
    documents.get_document_chunks,
    documents.get_document_versions,
    documents.get_document_entities,
    documents.grant_permission,
    documents.list_permissions,
    documents.revoke_permission,
]


def _depends_on_get_current_user(fn) -> bool:
    for param in inspect.signature(fn).parameters.values():
        if isinstance(param.default, DependsMarker) and param.default.dependency is get_current_user:
            return True
    return False


def test_every_documents_route_requires_a_verified_user():
    for route in _ROUTES:
        assert _depends_on_get_current_user(route), f"{route.__name__} is missing get_current_user"


def test_upload_accepts_optional_department_project_classification_fields():
    params = inspect.signature(documents.upload_document).parameters
    assert "department" in params
    assert "project" in params
    assert "security_classification" in params
    for name in ("department", "project", "security_classification"):
        assert params[name].default is not inspect.Parameter.empty, f"{name} must be optional (have a default)"


def test_upload_document_is_rbac_gated_with_the_upload_action():
    source = inspect.getsource(documents.upload_document)
    assert 'action="upload_documents"' in source
    assert "authorize_llm_request" in source


def test_delete_document_is_rbac_gated_with_the_delete_action_and_checks_approval():
    source = inspect.getsource(documents.delete_document)
    assert 'action="delete_documents"' in source
    assert "requires_approval" in source


def test_read_routes_reuse_filter_by_category_for_department_visibility():
    for route in (
        documents.get_document, documents.get_document_text, documents.get_document_chunks, documents.list_documents,
        # Previously missing this check entirely, unlike their siblings above.
        documents.get_document_versions, documents.get_document_entities,
    ):
        source = inspect.getsource(route)
        # The five single-document GET routes now go through the shared
        # _document_is_visible() helper (department AND per-user-grant/
        # security_classification visibility, not just department) instead
        # of calling filter_by_category directly — list_documents still
        # calls both filter_by_category and filter_by_permission inline.
        # See _document_is_visible()'s own docstring for why a single check
        # replaced the two-line pattern every one of these used to repeat.
        assert (
            "filter_by_category" in source
            or "knowledge_departments_for" in source
            or "_document_is_visible" in source
        )


def test_document_is_visible_checks_both_category_and_permission_rails():
    source = inspect.getsource(documents._document_is_visible)
    assert "filter_by_category" in source
    assert "filter_by_permission" in source


# ------------------------------------------------------- coarse require_permission gates

def test_upload_and_delete_have_the_matching_coarse_permission_gate():
    # require_permission(Permission.X) is called inline as a Depends() default
    # factory — inspect the route's own source for the literal call, since
    # the produced closure has no static reference back to which Permission
    # it was built with.
    assert "require_permission(Permission.UPLOAD_DOCUMENTS)" in inspect.getsource(documents.upload_document)
    assert "require_permission(Permission.DELETE_DOCUMENTS)" in inspect.getsource(documents.delete_document)


def test_read_routes_require_view_documents():
    # Employee has CHAT + VIEW_CONVERSATIONS only (llm_rbac.yaml) — not
    # VIEW_DOCUMENTS — so a direct GET must 403 even though filter_by_category
    # would otherwise return an empty/department-scoped result rather than denying.
    for route in (
        documents.get_document, documents.get_document_text, documents.get_document_chunks,
        documents.list_documents, documents.get_document_versions, documents.get_document_entities,
    ):
        assert "require_permission(Permission.VIEW_DOCUMENTS)" in inspect.getsource(route), route.__name__


def test_reindex_and_permission_routes_require_manage_documents():
    # Previously had no auth beyond get_current_user at all — any
    # authenticated user could reindex any document or grant/list/revoke
    # permissions on any document.
    for route in (documents.reindex_document, documents.grant_permission, documents.list_permissions, documents.revoke_permission):
        assert "require_permission(Permission.MANAGE_DOCUMENTS)" in inspect.getsource(route), route.__name__


# ------------------------------------------------------- filter_by_category reuse
# (the underlying rule is already unit-tested end-to-end in
# tests/llm_rbac/test_category_policy.py — this just confirms documents.py's
# reuse of the existing function, not the rule itself)

def test_filter_by_category_is_the_function_documents_py_imports():
    assert documents.filter_by_category is filter_by_category
