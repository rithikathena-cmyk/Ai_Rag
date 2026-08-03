from app.core.config import settings

_nlp = None


def get_nlp():
    global _nlp
    if _nlp is None:
        import spacy

        _nlp = spacy.load(settings.spacy_model_name, disable=["tagger", "parser", "attribute_ruler", "lemmatizer"])
    return _nlp


def extract_entities(text: str) -> list[tuple[str, str]]:
    truncated = text[: settings.entity_extraction_max_chars]
    if not truncated.strip():
        return []
    doc = get_nlp()(truncated)
    return [(ent.text.strip(), ent.label_) for ent in doc.ents if ent.text.strip()]
