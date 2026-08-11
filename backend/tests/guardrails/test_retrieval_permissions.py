import uuid

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
