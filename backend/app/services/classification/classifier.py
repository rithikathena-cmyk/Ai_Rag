from app.core.config import settings
from app.services.classification import rules, zero_shot
from app.services.classification.types import ClassificationResult
from app.services.ingestion.detector import DocumentFormat
from app.services.ingestion.types import DocumentMetadata

CONTENT_CATEGORIES = [
    "Research Paper", "Company Policy", "Manual", "SOP",
    "Legal", "FAQ", "Chat Log", "Email",
]

FORMAT_DRIVEN_FORMATS = {
    DocumentFormat.HTML,
    DocumentFormat.MARKDOWN,
    DocumentFormat.XLSX,
    DocumentFormat.CSV,
    DocumentFormat.SQL,
    DocumentFormat.CODE,
}


def classify(fmt: DocumentFormat, metadata: DocumentMetadata, text: str, filename: str) -> ClassificationResult | None:
    if fmt in FORMAT_DRIVEN_FORMATS:
        return None

    scores = rules.score_document(metadata, text, filename)
    label, score = rules.best_rule_label(scores)
    if score >= settings.classification_rule_confidence_threshold:
        return ClassificationResult(label=label, confidence=score, method="rule")

    zs_label, zs_score = zero_shot.classify_zero_shot(text, CONTENT_CATEGORIES)
    if zs_score >= settings.classification_zero_shot_confidence_threshold:
        return ClassificationResult(label=zs_label, confidence=zs_score, method="zero_shot")

    return ClassificationResult(label="Other", confidence=zs_score, method="zero_shot")
