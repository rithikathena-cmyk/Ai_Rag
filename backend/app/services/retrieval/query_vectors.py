from functools import lru_cache

from sqlalchemy.orm import Session

from app.models.term import TermModel
from app.services.embedding.model_loader import embed_texts
from app.services.sparse.tokenizer import term_frequencies


@lru_cache(maxsize=2048)
def embed_query(query: str) -> list[float]:
    # Pure function of `query` for a fixed embedding model (same input
    # always yields the same vector), and callers only ever read the
    # returned list (passed straight to Qdrant as a query vector, never
    # mutated) — safe to cache. Cuts redundant sentence-transformer
    # inference for repeated identical queries (eval runs re-testing the
    # same query set, popular questions).
    vectors = embed_texts([query])
    return vectors[0] if vectors else []


def sparse_query_vector(db: Session, query: str) -> dict[int, float]:
    freqs = term_frequencies(query)
    if not freqs:
        return {}
    rows = db.query(TermModel).filter(TermModel.term.in_(freqs.keys())).all()
    return {row.id: float(freqs[row.term]) for row in rows}
