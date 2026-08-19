"""routers/conversations.py::PATCH /conversations/{id} — added for the chat
UI redesign's rename/pin conversation actions (previously there was no way
to update a conversation's title or pin state at all). Uses the real
Postgres session (matching tests/test_admin_gateway_usage_audit.py's
convention) since authorize_conversation_access() and the ownership check
need a real row to operate on.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import new_session
from app.models.conversation import ConversationModel
from app.models.user import UserModel
from app.routers import conversations
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password


def _fake_user(user_id: uuid.UUID, role: str = "user"):
    return type("FakeUser", (), {"id": user_id, "role": role, "is_active": True})()


@pytest.fixture
def owned_conversation():
    # conversations.user_id is a real FK to users.id, so the owner needs an
    # actual row — a bare SimpleNamespace fake (fine for get_current_user's
    # override) isn't enough here.
    email = f"conversations-test-{uuid.uuid4().hex[:8]}@example.com"
    db = new_session()
    user = UserModel(
        email=email, display_name="Conversations Test User", password_hash=hash_password("Throwaway-Pass-1!"),
        is_active=True, role="user", department="manufacturing",
    )
    db.add(user)
    db.commit()
    owner_id = user.id

    row = ConversationModel(user_id=owner_id, title="Original title")
    db.add(row)
    db.commit()
    conversation_id = row.id
    db.close()
    try:
        yield owner_id, conversation_id
    finally:
        db = new_session()
        db.query(ConversationModel).filter(ConversationModel.id == conversation_id).delete()
        db.query(UserModel).filter(UserModel.id == owner_id).delete()
        db.commit()
        db.close()


def _client_as(user_id: uuid.UUID, role: str = "user") -> TestClient:
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user(user_id, role)
    return TestClient(app)


def test_rename_updates_title_only(owned_conversation):
    owner_id, conversation_id = owned_conversation
    client = _client_as(owner_id)

    response = client.patch(f"/conversations/{conversation_id}", json={"title": "Renamed chat"})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed chat"
    assert body["pinned_at"] is None


def test_pin_updates_pinned_at_only(owned_conversation):
    owner_id, conversation_id = owned_conversation
    client = _client_as(owner_id)

    response = client.patch(f"/conversations/{conversation_id}", json={"pinned": True})

    assert response.status_code == 200
    body = response.json()
    assert body["pinned_at"] is not None
    assert body["title"] == "Original title"  # untouched — title wasn't in the request


def test_unpin_clears_pinned_at(owned_conversation):
    owner_id, conversation_id = owned_conversation
    client = _client_as(owner_id)
    client.patch(f"/conversations/{conversation_id}", json={"pinned": True})

    response = client.patch(f"/conversations/{conversation_id}", json={"pinned": False})

    assert response.status_code == 200
    assert response.json()["pinned_at"] is None


def test_pinned_conversation_sorts_first_in_list(owned_conversation):
    owner_id, conversation_id = owned_conversation
    db = new_session()
    # A second, newer, unpinned conversation for the same owner — would sort
    # first by recency alone, proving the pin actually changes the order.
    newer = ConversationModel(user_id=owner_id, title="Newer, unpinned")
    db.add(newer)
    db.commit()
    newer_id = newer.id
    db.close()

    client = _client_as(owner_id)
    try:
        client.patch(f"/conversations/{conversation_id}", json={"pinned": True})

        response = client.get("/conversations")

        assert response.status_code == 200
        ids_in_order = [item["id"] for item in response.json()["items"]]
        assert ids_in_order.index(str(conversation_id)) < ids_in_order.index(str(newer_id))
    finally:
        db = new_session()
        db.query(ConversationModel).filter(ConversationModel.id == newer_id).delete()
        db.commit()
        db.close()


def test_other_users_conversation_cannot_be_renamed(owned_conversation):
    _owner_id, conversation_id = owned_conversation
    stranger_id = uuid.uuid4()
    client = _client_as(stranger_id)

    response = client.patch(f"/conversations/{conversation_id}", json={"title": "Hijacked"})

    assert response.status_code == 404  # not 403 — matches authorize_conversation_access()'s own convention


def test_ceo_may_rename_any_users_conversation(owned_conversation):
    _owner_id, conversation_id = owned_conversation
    client = _client_as(uuid.uuid4(), role="ceo")

    response = client.patch(f"/conversations/{conversation_id}", json={"title": "Renamed by CEO"})

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed by CEO"


def test_unknown_conversation_returns_404():
    client = _client_as(uuid.uuid4())

    response = client.patch(f"/conversations/{uuid.uuid4()}", json={"title": "x"})

    assert response.status_code == 404
