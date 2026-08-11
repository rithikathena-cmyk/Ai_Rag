"""services/reranking/pipeline.py::search_with_reranking — reranking is an
enhancement, not load-bearing: a cross-encoder failure must degrade to
serving hybrid_search's own (unranked) ordering rather than failing the
whole search.
"""

import uuid

from app.services.monitoring import metrics
from app.services.reranking import pipeline
from app.services.retrieval.search import SearchHit


def _hit(score) -> SearchHit:
    return SearchHit(
        chunk_id=uuid.uuid4(), document_id=uuid.uuid4(), chunk_index=0,
        parent_chunk_id=None, text="text", strategy="hybrid", score=score,
    )


def test_reranker_failure_returns_unranked_hits(monkeypatch):
    hits = [_hit(0.5), _hit(0.9)]
    monkeypatch.setattr(pipeline, "hybrid_search", lambda db, **k: (hits, {"qdrant_ms": 1.0}))

    def _boom(query, hits):
        raise RuntimeError("cross-encoder blew up")

    monkeypatch.setattr(pipeline, "rerank", _boom)

    result_hits, reranked = pipeline.search_with_reranking(db=None, query="q", request_id="req-2")

    assert reranked is False
    # Order/identity preserved, not dropped — compared by chunk_id rather
    # than full dataclass equality since search_with_reranking() always
    # returns a *copy* with display_text populated (see
    # tests/retrieval/test_source_pii_redaction.py), which legitimately
    # differs from the pre-redaction `hits` fixture's default "".
    assert [h.chunk_id for h in result_hits] == [h.chunk_id for h in hits]
    assert all(h.display_text for h in result_hits)  # still dual-represented even on the fail-open path

    errors = metrics.get_retrieval_errors()
    assert any(e["stage"] == "rerank" and e["request_id"] == "req-2" for e in errors)


def test_reranker_success_still_reranks(monkeypatch):
    hits = [_hit(0.5), _hit(0.9)]
    monkeypatch.setattr(pipeline, "hybrid_search", lambda db, **k: (hits, {"qdrant_ms": 1.0}))
    monkeypatch.setattr(pipeline, "rerank", lambda query, hits: list(reversed(hits)))

    result_hits, reranked = pipeline.search_with_reranking(db=None, query="q")

    assert reranked is True
    assert [h.chunk_id for h in result_hits] == [h.chunk_id for h in reversed(hits)]
