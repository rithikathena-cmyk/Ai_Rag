from sqlalchemy.orm import Session

from app.models.term import TermModel
from app.services.embedding.model_loader import embed_texts
from app.services.sparse.tokenizer import term_frequencies


def embed_query(query: str) -> list[float]:
    vectors = embed_texts([query])
    return vectors[0] if vectors else []


def sparse_query_vector(db: Session, query: str) -> dict[int, float]:
    freqs = term_frequencies(query)
    if not freqs:
        return {}
    rows = db.query(TermModel).filter(TermModel.term.in_(freqs.keys())).all()
    return {row.id: float(freqs[row.term]) for row in rows}
