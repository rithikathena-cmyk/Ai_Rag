import uuid

from qdrant_client.models import FieldCondition, Filter, MatchAny
from sqlalchemy.orm import Session

from app.models.document import Document


def resolve_document_ids(
    db: Session,
    *,
    document_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    document_type: str | None = None,
    classification: str | None = None,
    language: str | None = None,
    latest_version_only: bool = True,
) -> list[uuid.UUID] | None:
    conditions = []
    if document_id is not None:
        conditions.append(Document.id == document_id)
    if document_ids:
        conditions.append(Document.id.in_(document_ids))
    if document_type is not None:
        conditions.append(Document.document_type == document_type)
    if classification is not None:
        conditions.append(Document.classification == classification)
    if language is not None:
        conditions.append(Document.language == language)
    if latest_version_only:
        conditions.append(Document.is_latest_version.is_(True))

    if not conditions:
        return None
    return [r[0] for r in db.query(Document.id).filter(*conditions).all()]


def build_qdrant_filter(document_ids: list[uuid.UUID] | None) -> Filter | None:
    if document_ids is None:
        return None
    return Filter(must=[FieldCondition(key="document_id", match=MatchAny(any=[str(d) for d in document_ids]))])
