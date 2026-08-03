from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chunk import ChunkModel
from app.models.chunk_term_frequency import ChunkTermFrequencyModel
from app.services.sparse.terms import get_or_create_term_ids
from app.services.sparse.tokenizer import term_frequencies, top_keywords


def compute_term_frequencies(texts: list[str]) -> list[dict[str, int]]:
    return [term_frequencies(t) for t in texts]


def _bm25_tf_weight(raw_count: int, doc_length: int) -> float:
    # BM25 term-frequency-saturation component. Qdrant's IDF modifier supplies
    # the other half (IDF, computed live from collection stats) at query time
    # — this function must NOT bake IDF in, only saturation/length-normalization.
    k1 = settings.sparse_bm25_k1
    b = settings.sparse_bm25_b
    avg_len = settings.sparse_bm25_avg_doc_length
    length_norm = 1 - b + b * (doc_length / avg_len if avg_len > 0 else 1.0)
    denom = raw_count + k1 * length_norm
    return (raw_count * (k1 + 1)) / denom if denom > 0 else 0.0


def build_sparse_index(
    db: Session,
    chunk_rows: list[ChunkModel],
    term_freqs: list[dict[str, int]],
) -> tuple[list[ChunkTermFrequencyModel], list[dict[int, float]]]:
    if not chunk_rows:
        return [], []

    all_terms = {term for freqs in term_freqs for term in freqs}
    term_id_map = get_or_create_term_ids(db, all_terms)

    tf_rows: list[ChunkTermFrequencyModel] = []
    sparse_vectors: list[dict[int, float]] = []

    for row, freqs in zip(chunk_rows, term_freqs):
        row.keywords = top_keywords(freqs, settings.sparse_max_keywords_per_chunk)
        sparse_vec: dict[int, float] = {}
        for term, count in freqs.items():
            term_id = term_id_map[term]
            tf_rows.append(ChunkTermFrequencyModel(chunk_id=row.id, term_id=term_id, term_frequency=count))
            sparse_vec[term_id] = _bm25_tf_weight(count, row.token_count or 1)
        sparse_vectors.append(sparse_vec)

    return tf_rows, sparse_vectors
