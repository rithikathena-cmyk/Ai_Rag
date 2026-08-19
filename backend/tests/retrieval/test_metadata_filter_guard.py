"""services/retrieval/metadata_filter.py::resolve_document_ids — the
allow_unfiltered guard (Phase 1 of the /search hardening plan). Without
role/user_id, resolve_document_ids used to silently return every document
unfiltered; it now raises unless the caller explicitly opts in via
allow_unfiltered=True, matching the one legitimate caller
(services/evaluation/runner.py, measuring raw retrieval quality)."""

import uuid

import pytest

from app.models.document import DocumentModel
from app.services.retrieval.metadata_filter import resolve_document_ids


class _FakeDocumentQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, document_ids):
        self._rows = [(d,) for d in document_ids]

    def query(self, *a, **k):
        return _FakeDocumentQuery(self._rows)


def test_no_role_no_user_id_raises_without_allow_unfiltered():
    with pytest.raises(ValueError, match="allow_unfiltered"):
        resolve_document_ids(_FakeDb([uuid.uuid4()]))


def test_allow_unfiltered_true_returns_unfiltered_set():
    doc_ids = [uuid.uuid4(), uuid.uuid4()]
    result = resolve_document_ids(_FakeDb(doc_ids), allow_unfiltered=True)
    assert set(result) == set(doc_ids)


def test_role_only_does_not_raise():
    doc_ids = [uuid.uuid4()]
    # role supplied (user_id still None) — not the unguarded "both None" case.
    result = resolve_document_ids(_FakeDb(doc_ids), role="admin", knowledge_departments=None)
    assert set(result) == set(doc_ids)


def test_user_id_only_does_not_raise(monkeypatch):
    doc_ids = [uuid.uuid4()]
    monkeypatch.setattr(
        "app.services.retrieval.metadata_filter.filter_by_permission",
        lambda db, ids, user_id, role=None: ids,
    )
    result = resolve_document_ids(_FakeDb(doc_ids), user_id=uuid.uuid4())
    assert set(result) == set(doc_ids)


# ------------------------------------------------------- latest-version filter correctness
#
# Investigated as a candidate root cause for a real bug (WM_1.pdf existed in
# Postgres with is_latest_version=True but never came back from any query —
# see tests/ingestion/test_consistency.py/tests/test_reindex_consistency.py
# for the actual root cause, which was Qdrant collection drift, not this
# filter). Ruled out here directly: is_latest_version=True must not be
# excluded by latest_version_only=True — captures the real conditions()
# tuple resolve_document_ids() builds and compiles it to literal SQL rather
# than trusting a fake .filter() that ignores its arguments (this file's
# existing _FakeDb, above, does exactly that — fine for the allow_unfiltered
# guard those tests check, not sufficient to prove filter *correctness*).

class _CapturingQuery:
    def __init__(self, rows):
        self._rows = rows
        self.captured_conditions = None

    def filter(self, *conditions):
        self.captured_conditions = conditions
        return self

    def all(self):
        return self._rows


class _CapturingDb:
    def __init__(self, document_ids):
        self._rows = [(d,) for d in document_ids]
        self.query_obj = None

    def query(self, *a, **k):
        self.query_obj = _CapturingQuery(self._rows)
        return self.query_obj


def _compiled(conditions) -> str:
    from sqlalchemy import and_
    return str(and_(*conditions).compile(compile_kwargs={"literal_binds": True}))


def test_latest_version_only_true_builds_an_is_true_condition_not_an_exclusion():
    db = _CapturingDb([uuid.uuid4()])

    resolve_document_ids(db, role="admin", knowledge_departments=None, latest_version_only=True)

    sql = _compiled(db.query_obj.captured_conditions)
    assert "documents.is_latest_version IS true" in sql


def test_latest_version_only_false_omits_the_condition_entirely():
    db = _CapturingDb([uuid.uuid4()])

    # document_type keeps `conditions` non-empty so the base_ids query stage
    # actually runs (resolve_document_ids short-circuits to base_ids=None,
    # skipping db.query() entirely, when conditions ends up empty) — the
    # thing under test is that is_latest_version is absent, not whether any
    # query ran at all.
    resolve_document_ids(
        db, role="admin", knowledge_departments=None, document_type="manual", latest_version_only=False,
    )

    sql = _compiled(db.query_obj.captured_conditions)
    assert "is_latest_version" not in sql


def test_latest_version_only_defaults_to_true():
    import inspect

    default = inspect.signature(resolve_document_ids).parameters["latest_version_only"].default
    assert default is True
