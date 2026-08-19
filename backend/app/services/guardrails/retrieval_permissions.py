import uuid

from sqlalchemy.orm import Session

from app.core.yaml_config import load_yaml_config
from app.models.document import DocumentModel
from app.models.permission import PermissionModel

NAME = "retrieval_permission_filter"

# Values of DocumentModel.security_classification that require an explicit
# PermissionModel grant regardless of department/category visibility — see
# filter_by_permission()'s docstring. Every other value (including the
# "internal" default) keeps the pre-existing department-driven behavior;
# only this tier forces the stricter default-deny rule below.
_CLASSIFICATION_REQUIRES_GRANT = frozenset({"restricted"})


def _enabled() -> bool:
    return bool(load_yaml_config("guardrails.yaml").get("retrieval", {}).get("permission_filtering_enabled", True))


def _role_bypasses_grant_requirement(role: str | None) -> bool:
    """CEO/Admin (llm_rbac.yaml roles with permissions_allow: ["*"]) still see
    a "restricted"-classified document with no grant of their own — the same
    wildcard already used everywhere else in this codebase to mean "this
    role's access isn't narrowed by the ordinary rails" (see
    routers/users.py's `all_capabilities = "*" in cfg.permissions_allow`).
    Reusing it here instead of inventing a second "elevated roles" list."""
    if role is None:
        return False
    from app.services.llm_rbac import policy_loader

    return "*" in policy_loader.role_config(role).permissions_allow


def apply_permission_policy(
    candidate_ids: list[uuid.UUID], restricted_ids: set[uuid.UUID], granted_ids: set[uuid.UUID]
) -> list[uuid.UUID]:
    """The actual ACL rule, isolated from data access so it's unit-testable
    without a database: a document is visible if it has no permission rows
    at all (not in `restricted_ids`) or the caller holds an explicit grant
    for it (in `granted_ids`)."""
    return [d for d in candidate_ids if d not in restricted_ids or d in granted_ids]


def filter_by_permission(
    db: Session, document_ids: list[uuid.UUID] | None, user_id: uuid.UUID, role: str | None = None,
) -> list[uuid.UUID]:
    """Narrows a resolved document-ID set to ones `user_id` may see.

    A document with no PermissionModel rows at all is treated as public —
    PermissionModel is an opt-in ACL (see the existing grant/revoke API in
    routers/documents.py), not a default-deny gate, so this matches that
    API's existing semantics rather than inventing a stricter policy nothing
    else in the app enforces. A document becomes visible to a *specific*
    user once any permission row exists for it if — and only if — that user
    holds one of those rows. See apply_permission_policy() for the rule
    itself.

    A document whose `security_classification` is "restricted" is folded
    into the SAME restricted set even with zero PermissionModel rows —
    before this, `security_classification` was written on upload
    (routers/documents.py) and never read anywhere, so labeling a document
    "restricted" had no actual effect on who could retrieve or open it
    (apply_category_policy() only ever consulted `department`/
    `access_roles`). This makes the label a real access boundary: a
    "restricted" document now requires an explicit grant from whoever
    uploaded/owns it, is the uploader themselves (DocumentModel.owner_id —
    also captured on upload and, until now, equally never read; without this
    an uploader would be locked out of their own "restricted" document), or
    is an elevated `role` (see _role_bypasses_grant_requirement()) —
    regardless of department match.
    """
    if not _enabled():
        return document_ids if document_ids is not None else _all_document_ids(db)

    if _role_bypasses_grant_requirement(role):
        return document_ids if document_ids is not None else _all_document_ids(db)

    permission_ids = {r[0] for r in db.query(PermissionModel.document_id).distinct().all()}
    classification_rows = (
        db.query(DocumentModel.id, DocumentModel.owner_id)
        .filter(DocumentModel.security_classification.in_(_CLASSIFICATION_REQUIRES_GRANT))
        .all()
    )
    classified_ids = {r[0] for r in classification_rows}
    owned_classified_ids = {r[0] for r in classification_rows if r[1] == user_id}
    restricted_ids = permission_ids | classified_ids
    if not restricted_ids:
        return document_ids if document_ids is not None else _all_document_ids(db)

    granted_ids = {
        r[0] for r in db.query(PermissionModel.document_id).filter(PermissionModel.user_id == user_id).all()
    } | owned_classified_ids
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
