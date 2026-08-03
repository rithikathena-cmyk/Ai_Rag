import dataclasses

from app.services.reranking.model_loader import get_reranker
from app.services.retrieval.search import SearchHit


def rerank(query: str, hits: list[SearchHit]) -> list[SearchHit]:
    if not hits:
        return hits
    model = get_reranker()
    pairs = [(query, h.text) for h in hits]
    scores = model.predict(pairs)
    scored = sorted(zip(hits, scores), key=lambda pair: pair[1], reverse=True)
    return [dataclasses.replace(hit, score=float(score)) for hit, score in scored]
