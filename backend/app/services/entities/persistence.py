import uuid

from app.models.entity import EntityModel
from app.services.entities.spacy_ner import extract_entities


def build_entity_rows(document_id: uuid.UUID, text: str) -> list[EntityModel]:
    counts: dict[tuple[str, str], int] = {}
    for entity_text, entity_label in extract_entities(text):
        key = (entity_text, entity_label)
        counts[key] = counts.get(key, 0) + 1
    return [
        EntityModel(document_id=document_id, entity_text=t, entity_label=l, mention_count=c)
        for (t, l), c in counts.items()
    ]
