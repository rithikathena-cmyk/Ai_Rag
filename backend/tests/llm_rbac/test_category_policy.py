import uuid

from app.services.guardrails import retrieval_permissions as rp


def test_unclassified_document_is_visible_to_every_role():
    doc = uuid.uuid4()
    result = rp.apply_category_policy(
        [doc], doc_departments={doc: None}, doc_access_roles={}, role="user",
        knowledge_departments=frozenset({"manufacturing"}),
    )
    assert result == [doc]


def test_document_outside_role_department_is_hidden():
    doc = uuid.uuid4()
    result = rp.apply_category_policy(
        [doc], doc_departments={doc: "hr"}, doc_access_roles={}, role="user",
        knowledge_departments=frozenset({"manufacturing"}),
    )
    assert result == []


def test_document_in_role_department_is_visible():
    doc = uuid.uuid4()
    result = rp.apply_category_policy(
        [doc], doc_departments={doc: "hr"}, doc_access_roles={}, role="hr",
        knowledge_departments=frozenset({"hr"}),
    )
    assert result == [doc]


def test_access_roles_override_beats_department_mismatch():
    doc = uuid.uuid4()
    result = rp.apply_category_policy(
        [doc], doc_departments={doc: "engineering"}, doc_access_roles={doc: ["user"]}, role="user",
        knowledge_departments=frozenset({"manufacturing"}),
    )
    assert result == [doc]


def test_mixed_candidate_set_keeps_only_visible_documents():
    unclassified, own_dept, other_dept, override = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    result = rp.apply_category_policy(
        [unclassified, own_dept, other_dept, override],
        doc_departments={unclassified: None, own_dept: "manufacturing", other_dept: "hr", override: "hr"},
        doc_access_roles={override: ["user"]},
        role="user",
        knowledge_departments=frozenset({"manufacturing"}),
    )
    assert set(result) == {unclassified, own_dept, override}


def test_admin_department_set_covers_everything_defined():
    # backend/config/llm_rbac.yaml's admin role lists every real department —
    # confirming apply_category_policy() needs no special-casing for admin,
    # the union of departments alone is enough.
    doc = uuid.uuid4()
    result = rp.apply_category_policy(
        [doc], doc_departments={doc: "executive"}, doc_access_roles={}, role="admin",
        knowledge_departments=frozenset({"manufacturing", "hr", "engineering", "executive"}),
    )
    assert result == [doc]


def test_filter_by_category_none_knowledge_departments_means_unrestricted(monkeypatch):
    # None is the RBAC-disabled / not-supplied sentinel (see
    # services/llm_rbac/engine.py's kill-switch branch) — must return the
    # candidate set unchanged without touching the database.
    doc_ids = [uuid.uuid4(), uuid.uuid4()]
    result = rp.filter_by_category(db=None, document_ids=doc_ids, role="user", knowledge_departments=None)
    assert result == doc_ids
