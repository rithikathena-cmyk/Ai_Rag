import uuid

from app.models.document import DocumentModel
from app.models.permission import PermissionModel
from app.services.guardrails import retrieval_permissions as rp


def test_document_with_no_permission_rows_is_public():
    doc = uuid.uuid4()
    assert rp.apply_permission_policy([doc], restricted_ids=set(), granted_ids=set()) == [doc]


def test_restricted_document_hidden_without_a_grant():
    doc = uuid.uuid4()
    assert rp.apply_permission_policy([doc], restricted_ids={doc}, granted_ids=set()) == []


def test_restricted_document_visible_with_a_grant():
    doc = uuid.uuid4()
    assert rp.apply_permission_policy([doc], restricted_ids={doc}, granted_ids={doc}) == [doc]


def test_mixed_candidate_set_keeps_only_visible_documents():
    public, restricted_no_grant, restricted_with_grant = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    result = rp.apply_permission_policy(
        [public, restricted_no_grant, restricted_with_grant],
        restricted_ids={restricted_no_grant, restricted_with_grant},
        granted_ids={restricted_with_grant},
    )
    assert set(result) == {public, restricted_with_grant}


def test_enabled_flag_reads_from_guardrails_yaml(monkeypatch):
    monkeypatch.setattr(rp, "load_yaml_config", lambda _name: {"retrieval": {"permission_filtering_enabled": False}})
    assert rp._enabled() is False

    monkeypatch.setattr(rp, "load_yaml_config", lambda _name: {"retrieval": {"permission_filtering_enabled": True}})
    assert rp._enabled() is True

    monkeypatch.setattr(rp, "load_yaml_config", lambda _name: {})
    assert rp._enabled() is True  # missing config defaults open, matching current unfiltered behavior


# --------------------------------------------------- security_classification enforcement
#
# Found during the guardrails audit: DocumentModel.security_classification
# was written on upload (routers/documents.py) and never read anywhere —
# labeling a document "restricted" had zero effect on who could retrieve or
# open it. filter_by_permission() now folds "restricted"-classified
# documents into the same restricted set PermissionModel grants use, even
# with zero grant rows of their own.


class _PermissionQuery:
    """Models the two distinct PermissionModel.document_id query shapes
    filter_by_permission() issues: an unfiltered `.distinct().all()` (every
    document with ANY grant row) and a `.filter(user_id == ...).all()`
    (this user's own grants) — routed by whether .filter() was called."""

    def __init__(self, all_ids: set[uuid.UUID], granted_ids: set[uuid.UUID]):
        self._all_ids = all_ids
        self._granted_ids = granted_ids
        self._filtered = False

    def distinct(self):
        return self

    def filter(self, *a, **k):
        self._filtered = True
        return self

    def all(self):
        ids = self._granted_ids if self._filtered else self._all_ids
        return [(d,) for d in ids]


class _ClassificationQuery:
    def __init__(self, rows: list[tuple[uuid.UUID, uuid.UUID | None]]):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(
        self,
        permission_doc_ids: set[uuid.UUID] = frozenset(),
        granted_doc_ids: set[uuid.UUID] = frozenset(),
        restricted_classification_ids: set[uuid.UUID] = frozenset(),
        owner_by_doc: dict[uuid.UUID, uuid.UUID] = None,
    ):
        self._permission_doc_ids = permission_doc_ids
        self._granted_doc_ids = granted_doc_ids
        self._restricted_classification_ids = restricted_classification_ids
        self._owner_by_doc = owner_by_doc or {}

    def query(self, *entities):
        if entities and entities[0] is PermissionModel.document_id:
            return _PermissionQuery(self._permission_doc_ids, self._granted_doc_ids)
        if entities and entities[0] is DocumentModel.id:
            rows = [(d, self._owner_by_doc.get(d)) for d in self._restricted_classification_ids]
            return _ClassificationQuery(rows)
        raise AssertionError(f"unexpected query entities in test fake: {entities}")


def test_restricted_classification_hides_document_with_no_permission_rows_at_all():
    doc = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc})
    result = rp.filter_by_permission(db, [doc], user_id=uuid.uuid4())
    assert result == []


def test_restricted_classification_document_visible_with_an_explicit_grant():
    doc = uuid.uuid4()
    user_id = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc}, granted_doc_ids={doc})
    result = rp.filter_by_permission(db, [doc], user_id=user_id)
    assert result == [doc]


def test_internal_classification_document_is_unaffected():
    # Only "restricted" forces the grant requirement — the default
    # "internal" classification keeps today's opt-in-ACL behavior exactly.
    doc = uuid.uuid4()
    db = _FakeDb()
    result = rp.filter_by_permission(db, [doc], user_id=uuid.uuid4())
    assert result == [doc]


def test_elevated_role_bypasses_the_restricted_classification_requirement(monkeypatch):
    from app.services.llm_rbac import policy_loader

    doc = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc})
    monkeypatch.setattr(
        policy_loader, "role_config", lambda role: type("Cfg", (), {"permissions_allow": frozenset({"*"})})()
    )
    result = rp.filter_by_permission(db, [doc], user_id=uuid.uuid4(), role="admin")
    assert result == [doc]


def test_ordinary_role_does_not_bypass_the_restricted_classification_requirement(monkeypatch):
    from app.services.llm_rbac import policy_loader

    doc = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc})
    monkeypatch.setattr(
        policy_loader, "role_config", lambda role: type("Cfg", (), {"permissions_allow": frozenset({"chat"})})()
    )
    result = rp.filter_by_permission(db, [doc], user_id=uuid.uuid4(), role="user")
    assert result == []


def test_no_role_argument_does_not_bypass_restriction():
    doc = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc})
    result = rp.filter_by_permission(db, [doc], user_id=uuid.uuid4())
    assert result == []


def test_owner_of_a_restricted_document_retains_access_with_no_explicit_grant():
    # DocumentModel.owner_id is set to the uploader on upload
    # (routers/documents.py) and was, like security_classification itself,
    # never read anywhere before this fix — without this, an uploader would
    # be locked out of their own document the moment they marked it
    # "restricted", with no grant/revoke action available to fix it (the
    # PermissionModel grant API only lets OTHER users be added, see
    # routers/documents.py's grant_permission()).
    doc = uuid.uuid4()
    owner_id = uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc}, owner_by_doc={doc: owner_id})
    result = rp.filter_by_permission(db, [doc], user_id=owner_id)
    assert result == [doc]


def test_non_owner_of_a_restricted_document_still_needs_a_grant():
    doc = uuid.uuid4()
    owner_id, other_user_id = uuid.uuid4(), uuid.uuid4()
    db = _FakeDb(restricted_classification_ids={doc}, owner_by_doc={doc: owner_id})
    result = rp.filter_by_permission(db, [doc], user_id=other_user_id)
    assert result == []
