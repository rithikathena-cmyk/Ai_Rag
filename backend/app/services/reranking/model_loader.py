from app.core.config import settings

_model = None


def get_reranker():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(settings.reranker_model_name, device="cpu")
    return _model
