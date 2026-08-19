import uuid

from qdrant_client.models import FieldCondition, Filter, MatchAny
from sqlalchemy.orm import Session

from app.models.document import DocumentModel
from app.services.guardrails.retrieval_permissions import filter_by_category, filter_by_permission


def resolve_document_ids(
    db: Session,
    *,
    document_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    document_type: str | None = None,
    classification: str | None = None,
    language: str | None = None,
    latest_version_only: bool = True,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
    knowledge_departments: tuple[str, ...] | None = None,
    allow_unfiltered: bool = False,
) -> list[uuid.UUID] | None:
    if role is None and user_id is None and not allow_unfiltered:
        raise ValueError(
            "resolve_document_ids: role and user_id are both None, which would return every "
            "document with no RBAC narrowing. This is a programmer-contract check, not an "
            "HTTP-reachable error — pass allow_unfiltered=True only for internal callers that "
            "intentionally bypass RBAC (e.g. services/evaluation/runner.py measuring raw "
            "retrieval quality), otherwise pass the caller's user_id/role."
        )

    conditions = []
    if document_id is not None:
        conditions.append(DocumentModel.id == document_id)
    if document_ids:
        conditions.append(DocumentModel.id.in_(document_ids))
    if document_type is not None:
        conditions.append(DocumentModel.document_type == document_type)
    if classification is not None:
        conditions.append(DocumentModel.classification == classification)
    if language is not None:
        conditions.append(DocumentModel.language == language)
    if latest_version_only:
        conditions.append(DocumentModel.is_latest_version.is_(True))

    base_ids = [r[0] for r in db.query(DocumentModel.id).filter(*conditions).all()] if conditions else None

    # Both role and user_id are opt-in — callers that don't pass them keep
    # exactly the pre-existing, unfiltered behavior. Category policy (role/
    # department) narrows first, then the existing per-user grant system
    # narrows further (docs/KNOWLEDGE_ACCESS_CONTROL.md's two-stage order).
    if role is not None:
        base_ids = filter_by_category(db, base_ids, role, knowledge_departments)

    if user_id is None:
        return base_ids
    return filter_by_permission(db, base_ids, user_id, role)


def build_qdrant_filter(document_ids: list[uuid.UUID] | None) -> Filter | None:
    if document_ids is None:
        return None
    return Filter(must=[FieldCondition(key="document_id", match=MatchAny(any=[str(d) for d in document_ids]))])
