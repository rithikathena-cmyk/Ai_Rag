"""services/retrieval/search.py::fetch_parent_context() — Phase 3A parent-child
retrieval (docs/RAG_RETRIEVAL.md). Stubs the DB query boundary rather than
requiring a live Postgres, matching this suite's established convention.

Authorization note tested implicitly throughout: fetch_parent_context() takes
no role/department/user_id — by design (see its docstring), since every hit
passed in already survived resolve_document_ids()'s permission filter before
reaching Qdrant, and a parent always belongs to the same document as its
child. There is nothing to re-check here; these tests instead confirm the
function does no independent document lookup at all (only chunk id -> text).

Returns DualText (services/guardrails/pii.py) per chunk, not a plain string —
`.raw` is the original, authorized content for an authorized LLM/agent
context; `.display` is `redact_pii(.raw)` for anything persisted or returned
to a user (see services/agents/planner.py's LLM-payload/public-view split).
"""

import uuid

import pytest

from app.core.config import settings
from app.services.retrieval.search import SearchHit, fetch_parent_context


def _hit(chunk_id=None, parent_chunk_id=None, score=1.0) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id or uuid.uuid4(), document_id=uuid.uuid4(), chunk_index=0,
        parent_chunk_id=parent_chunk_id, text="child text", strategy="parent_child", score=score,
    )


class _FakeChunkQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, parent_rows: dict[uuid.UUID, str]):
        self._rows = list(parent_rows.items())

    def query(self, *a, **k):
        return _FakeChunkQuery(self._rows)


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt = original


def test_resolves_child_to_parent_text():
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()
    db = _FakeDb({parent_id: "full parent section text"})

    result = fetch_parent_context(db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=2000)

    assert result[child_id].raw == "full parent section text"
    assert result[child_id].display == "full parent section text"  # no PII, so raw == display


def test_missing_parent_is_skipped_not_an_error():
    child_id = uuid.uuid4()
    db = _FakeDb({})  # parent row doesn't exist (e.g. deleted)

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=uuid.uuid4())], max_expansions=5, max_chars=2000
    )

    assert result == {}


def test_hits_without_a_parent_are_ignored():
    db = _FakeDb({})

    result = fetch_parent_context(db, [_hit(parent_chunk_id=None)], max_expansions=5, max_chars=2000)

    assert result == {}


def test_duplicate_parents_are_deduplicated_to_the_first_hit_only():
    parent_id = uuid.uuid4()
    first_child, second_child = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb({parent_id: "shared parent text"})

    result = fetch_parent_context(
        db,
        [_hit(chunk_id=first_child, parent_chunk_id=parent_id), _hit(chunk_id=second_child, parent_chunk_id=parent_id)],
        max_expansions=5, max_chars=2000,
    )

    assert result[first_child].raw == "shared parent text"
    assert second_child not in result


def test_max_expansions_limits_distinct_parents_by_hit_order():
    parents = [uuid.uuid4() for _ in range(3)]
    children = [uuid.uuid4() for _ in range(3)]
    db = _FakeDb({p: f"parent {i}" for i, p in enumerate(parents)})
    hits = [_hit(chunk_id=children[i], parent_chunk_id=parents[i]) for i in range(3)]

    result = fetch_parent_context(db, hits, max_expansions=2, max_chars=2000)

    assert len(result) == 2
    assert children[0] in result and children[1] in result
    assert children[2] not in result


def test_max_expansions_zero_returns_empty_without_querying():
    class _ExplodingDb:
        def query(self, *a, **k):
            raise AssertionError("must not query the DB when max_expansions is 0")

    result = fetch_parent_context(
        _ExplodingDb(), [_hit(parent_chunk_id=uuid.uuid4())], max_expansions=0, max_chars=2000
    )

    assert result == {}


def test_parent_text_longer_than_max_chars_is_truncated():
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb({parent_id: "x" * 100})

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=10
    )

    assert len(result[child_id].raw) == 11  # 10 chars + the truncation marker
    assert result[child_id].raw.startswith("x" * 10)
    assert result[child_id].display.startswith("x" * 10)  # no PII in "x"*100, so truncation is the only change


def test_parent_text_within_max_chars_is_not_truncated():
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb({parent_id: "short parent text"})

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=2000
    )

    assert result[child_id].raw == "short parent text"


# --------------------------------------------------- PII redaction (separate
# Postgres read from search_with_reranking's hits, so it needs its own
# DualText/redact_pii() pass — see services/reranking/pipeline.py for the
# primary chunk-text boundary this mirrors).

def test_parent_context_with_pii_is_redacted_in_display_only():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    original = "Full section: contact jane@example.com or SSN 123-45-6789 for verification."
    db = _FakeDb({parent_id: original})

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=2000
    )

    # .raw is untouched — an authorized LLM/agent context still reasons over
    # the real content.
    assert result[child_id].raw == original
    # .display is the only representation allowed into anything persisted or
    # returned to a user.
    assert "jane@example.com" not in result[child_id].display
    assert "123-45-6789" not in result[child_id].display
    assert "[REDACTED_EMAIL]" in result[child_id].display
    assert "[REDACTED_SSN]" in result[child_id].display


def test_parent_context_redaction_applies_after_truncation():
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb({parent_id: "x" * 20 + "jane@example.com"})

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=20
    )

    # Truncated to 20 chars before the email ever appears, so there's
    # nothing left to redact — proves truncation and redaction don't
    # interfere with each other (order: truncate, then redact what remains).
    assert result[child_id].raw == "x" * 20 + "…"
    assert result[child_id].display == "x" * 20 + "…"


def test_parent_context_redaction_disabled_leaves_display_verbatim():
    settings.guardrail_redact_pii = False
    parent_id, child_id = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb({parent_id: "contact jane@example.com"})

    result = fetch_parent_context(
        db, [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], max_expansions=5, max_chars=2000
    )

    assert result[child_id].raw == "contact jane@example.com"
    assert result[child_id].display == "contact jane@example.com"
