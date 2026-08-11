import uuid

from sqlalchemy.orm import Session

from app.core.yaml_config import load_yaml_config
from app.models.document import DocumentModel
from app.models.permission import PermissionModel

NAME = "retrieval_permission_filter"


def _enabled() -> bool:
    return bool(load_yaml_config("guardrails.yaml").get("retrieval", {}).get("permission_filtering_enabled", True))


def apply_permission_policy(
    candidate_ids: list[uuid.UUID], restricted_ids: set[uuid.UUID], granted_ids: set[uuid.UUID]
) -> list[uuid.UUID]:
    """The actual ACL rule, isolated from data access so it's unit-testable
    without a database: a document is visible if it has no permission rows
    at all (not in `restricted_ids`) or the caller holds an explicit grant
    for it (in `granted_ids`)."""
    return [d for d in candidate_ids if d not in restricted_ids or d in granted_ids]


def filter_by_permission(db: Session, document_ids: list[uuid.UUID] | None, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Narrows a resolved document-ID set to ones `user_id` may see.

    A document with no PermissionModel rows at all is treated as public —
    PermissionModel is an opt-in ACL (see the existing grant/revoke API in
    routers/documents.py), not a default-deny gate, so this matches that
    API's existing semantics rather than inventing a stricter policy nothing
    else in the app enforces. A document becomes visible to a *specific*
    user once any permission row exists for it if — and only if — that user
    holds one of those rows. See apply_permission_policy() for the rule
    itself.
    """
    if not _enabled():
        return document_ids if document_ids is not None else _all_document_ids(db)

    restricted_ids = {r[0] for r in db.query(PermissionModel.document_id).distinct().all()}
    if not restricted_ids:
        return document_ids if document_ids is not None else _all_document_ids(db)

    granted_ids = {
        r[0] for r in db.query(PermissionModel.document_id).filter(PermissionModel.user_id == user_id).all()
    }
    candidate_ids = document_ids if document_ids is not None else _all_document_ids(db)
    return apply_permission_policy(candidate_ids, restricted_ids, granted_ids)


def _all_document_ids(db: Session) -> list[uuid.UUID]:
    return [r[0] for r in db.query(DocumentModel.id).all()]


def apply_category_policy(
    candidate_ids: list[uuid.UUID],
    doc_departments: dict[uuid.UUID, str | None],
    doc_access_roles: dict[uuid.UUID, list[str] | None],
    role: str,
    knowledge_departments: frozenset[str],
) -> list[uuid.UUID]:
    """The LLM-RBAC department/role-access rule (docs/KNOWLEDGE_ACCESS_CONTROL.md),
    isolated from data access exactly like apply_permission_policy() above so
    it's unit-testable without a database: a document is visible to `role` if
    it has no `department` set at all (unclassified — same opt-in-ACL
    semantics as the permission rail: nothing currently visible becomes
    invisible just because this rail shipped), its department is one of the
    role's `knowledge_departments` (backend/config/llm_rbac.yaml), or the
    document's own `access_roles` override explicitly lists this role."""
    return [
        d
        for d in candidate_ids
        if doc_departments.get(d) is None
        or doc_departments.get(d) in knowledge_departments
        or role in (doc_access_roles.get(d) or [])
    ]


def filter_by_category(
    db: Session, document_ids: list[uuid.UUID] | None, role: str, knowledge_departments: tuple[str, ...] | None
) -> list[uuid.UUID]:
    """Narrows a resolved document-ID set to ones `role` may see, by
    department. `knowledge_departments=None` means "no restriction" (the
    LLM-RBAC kill switch is off, or the caller doesn't pass one) — matches
    filter_by_permission()'s existing opt-in shape: a caller that doesn't
    supply the filtering input gets the pre-existing, unfiltered behavior."""
    candidate_ids = document_ids if document_ids is not None else _all_document_ids(db)
    if knowledge_departments is None:
        return candidate_ids

    rows = db.query(DocumentModel.id, DocumentModel.department, DocumentModel.access_roles).filter(
        DocumentModel.id.in_(candidate_ids)
    ).all()
    doc_departments = {r[0]: r[1] for r in rows}
    doc_access_roles = {r[0]: r[2] for r in rows}
    return apply_category_policy(candidate_ids, doc_departments, doc_access_roles, role, frozenset(knowledge_departments))
