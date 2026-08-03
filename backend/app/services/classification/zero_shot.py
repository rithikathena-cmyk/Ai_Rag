from app.core.config import settings

_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        from transformers import pipeline

        _pipeline = pipeline("zero-shot-classification", model=settings.zero_shot_model_name)
    return _pipeline


def classify_zero_shot(text: str, candidate_labels: list[str]) -> tuple[str, float]:
    truncated = text[:2000]
    if not truncated.strip():
        return "Other", 0.0
    result = get_pipeline()(truncated, candidate_labels=candidate_labels, multi_label=False)
    return result["labels"][0], float(result["scores"][0])
