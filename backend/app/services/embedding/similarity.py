"""Shared cosine-similarity utilities on top of the embedding model this
codebase already loads for retrieval (model_loader.py::embed_texts) — no
second model, no LLM call. Used by the production semantic guardrail check
(services/guardrails/semantic_check.py, binary similarity-to-known-examples)
so the actual math lives in one place.
"""

from app.services.embedding.model_loader import embed_texts


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MaxSimilarityMatcher:
    """Binary-leaning matcher: caches embeddings for a flat list of "known
    unsafe" example phrases (no per-label grouping) and reports the single
    closest one and its score. Used where the caller only needs "how close
    is this to anything in a known-bad set", not a multi-category label —
    e.g. services/guardrails/semantic_check.py."""

    def __init__(self, examples: tuple[str, ...]):
        self._examples = examples
        self._example_vectors: list[list[float]] | None = None

    def _get_vectors(self) -> list[list[float]]:
        if self._example_vectors is None:
            self._example_vectors = embed_texts(list(self._examples))
        return self._example_vectors

    def best_match(self, text: str) -> tuple[str, float]:
        vector = embed_texts([text])[0]
        scored = [(ex, cosine_similarity(vector, v)) for ex, v in zip(self._examples, self._get_vectors())]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[0]

    def reset_cache(self) -> None:
        self._example_vectors = None
