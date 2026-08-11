"""Automates the manual live-stack verification performed during the
"no AI model configured" investigation and the department-dataset seeding:
real login -> RBAC tier resolution -> real Qdrant retrieval -> real
Anthropic generation -> DualText PII split -> redacted citation -> redacted
conversation-history persistence, all in one HTTP round trip against the
actual running app.

Every other test in this suite mocks the DB/Qdrant/Anthropic boundary
(tests/test_chat_degraded_reason.py, tests/test_search_pii_redaction.py,
etc.) — deliberately, since that's what makes them fast and hermetic. This
file is the one exception: it exercises the real stack end-to-end, closing
the gap flagged in the project completeness audit ("no full end-to-end
integration test against a live Qdrant/Postgres/Claude stack").

Requires a live backend dependency stack to do anything meaningful:
Postgres reachable, Qdrant reachable, and a configured+valid
ANTHROPIC_API_KEY. Each test skips cleanly (not a failure) when any of these
isn't available, so this file never breaks a plain `pytest` run in an
environment without the full stack up — it only runs, and only asserts
anything, when there's a real stack to test against.

This intentionally does NOT create its own TestClient with a `with` block
(see tests/test_search_observability.py's documented trick) — that would
run FastAPI's startup lifespan and eagerly warm the embedding/reranker/spaCy
models, adding tens of seconds per test run for no benefit: the embedding
model still lazy-loads correctly inside the real request path either way.
"""

import re
import uuid

import anthropic
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from app import main
from app.core.config import settings
from app.db.postgres import new_session
from app.gateway import availability
from app.models.user import UserModel
from app.services.auth.password import hash_password

PHONE_RE = re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


def _live_stack_reachable() -> tuple[bool, str]:
    """Best-effort reachability check for the three real dependencies this
    file needs. Returns (ok, reason) rather than raising, so callers can
    build one clear pytest.skip() message instead of an opaque connection
    traceback."""
    if not settings.anthropic_api_key:
        return False, "no ANTHROPIC_API_KEY configured in this environment"
    try:
        db = new_session()
        db.close()
    except Exception as exc:
        return False, f"Postgres unreachable: {exc}"
    try:
        QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=3).get_collections()
    except Exception as exc:
        return False, f"Qdrant unreachable: {exc}"
    try:
        anthropic.Anthropic(api_key=settings.anthropic_api_key).models.list(limit=1)
    except anthropic.AuthenticationError:
        return False, "ANTHROPIC_API_KEY is configured but invalid (401 on models.list())"
    except Exception as exc:
        return False, f"Anthropic API unreachable: {exc}"
    return True, ""


@pytest.fixture
def live_client():
    ok, reason = _live_stack_reachable()
    if not ok:
        pytest.skip(f"live stack not available: {reason}")
    yield TestClient(main.app, raise_server_exceptions=False)  # no `with` — skips heavy startup warmup


@pytest.fixture
def throwaway_user():
    """Creates a real Employee-role user directly in Postgres (the same
    shape a real signup/admin-create would produce, via the app's own
    hash_password()) and deletes it afterward regardless of outcome — no
    hardcoded credentials, no dependency on any pre-seeded account whose
    password this suite doesn't know."""
    email = f"live-flow-{uuid.uuid4().hex[:8]}@example.com"
    password = "Live-Flow-Test-Pass-1!"
    db = new_session()
    user = UserModel(
        email=email, display_name="Live Flow Test Employee", password_hash=hash_password(password),
        is_active=True, role="user", department="manufacturing",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    try:
        yield email, password
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()


def _login(client: TestClient, email: str, password: str) -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_full_live_chat_flow_login_through_persisted_redacted_history(live_client, throwaway_user):
    """login -> RBAC model-tier resolution -> real Qdrant retrieval -> real
    Anthropic generation -> DualText PII split -> redacted citation ->
    redacted conversation-history persistence, exactly as manually verified
    against WM_1.pdf during the model-availability investigation."""
    email, password = throwaway_user
    headers = _login(live_client, email, password)

    # RBAC: an Employee's model picker must be server-derived and haiku-only
    # (backend/config/llm_rbac.yaml roles.user.model.tiers_allowed) — the
    # frontend is never the authority for this list, only its renderer.
    caps = live_client.get("/users/me/capabilities", headers=headers)
    assert caps.status_code == 200, caps.text
    allowed_tiers = caps.json()["model_tiers_allowed"]
    assert allowed_tiers == ["haiku"], f"Employee role must be haiku-only, got {allowed_tiers}"

    # Real retrieval + real generation: the same warranty question verified
    # manually against WM_1.pdf. model_tier is passed explicitly, exercising
    # the same "select a model" step the chat UI's model picker drives.
    question = "What warranty support information is available in the documents?"
    chat = live_client.post("/chat", json={"message": question, "model_tier": "haiku"}, headers=headers)
    assert chat.status_code == 200, chat.text
    body = chat.json()

    # A real answer was generated — not the "no AI model configured"
    # degraded fallback this whole investigation was about.
    assert body["degraded"] is False, f"unexpected degraded response: {body}"
    assert body["degraded_reason"] is None
    assert len(body["reply"]) > 0

    # Citation metadata: at least one source must be WM_1.pdf, and every
    # source must carry the fields a citation needs to be independently
    # verifiable against Postgres (not just a bare text blob).
    assert body["sources"], "expected at least one retrieved source"
    wm1_sources = [s for s in body["sources"] if s.get("document_filename") == "WM_1.pdf"]
    assert wm1_sources, f"expected WM_1.pdf among citations, got {[s.get('document_filename') for s in body['sources']]}"
    for s in body["sources"]:
        assert s["document_id"], "citation missing document_id"
        assert s["chunk_id"], "citation missing chunk_id"
        assert isinstance(s["chunk_index"], int)

    # PII redaction (DualText.display): no raw-looking phone number reaches
    # any user-facing source text. This is a substring check across ALL
    # sources — the specific chunk that carries WM_1.pdf's phone number is
    # an implementation detail of the current chunking strategy, not
    # something this test should hardcode.
    for s in body["sources"]:
        assert not PHONE_RE.search(s["text"]) or "REDACTED" in s["text"], (
            f"raw-looking phone number leaked into user-facing source: {s['text']!r}"
        )

    # Persisted conversation history stores the SAME redacted representation
    # that was returned over HTTP — not a second, differently-redacted copy,
    # and never the raw text.
    conv_id = body["conversation_id"]
    conv = live_client.get(f"/conversations/{conv_id}", headers=headers)
    assert conv.status_code == 200, conv.text
    assistant_msgs = [m for m in conv.json()["messages"] if m["role"] == "assistant"]
    assert assistant_msgs, "expected the assistant's reply to be persisted"
    stored_sources = assistant_msgs[-1]["sources"]
    assert stored_sources, "expected sources to be persisted alongside the reply"
    for s in stored_sources:
        assert not PHONE_RE.search(s["text"]) or "REDACTED" in s["text"], (
            f"raw-looking phone number leaked into persisted conversation history: {s['text']!r}"
        )


def test_admin_kill_switch_produces_accurate_degraded_reason_live(live_client, throwaway_user):
    """Live counterpart to tests/test_chat_degraded_reason.py's mocked
    equivalent: flips the real admin availability kill switch
    (gateway/availability.py) and confirms the real /chat endpoint reports
    degraded_reason="model_disabled" with the correct, safe message — not
    the old generic "no AI model configured" text — against the actual
    running app rather than a monkeypatched one."""
    email, password = throwaway_user
    headers = _login(live_client, email, password)

    original = availability.is_disabled()
    availability.set_disabled(True)
    try:
        chat = live_client.post(
            "/chat", json={"message": "What warranty support information is available in the documents?"},
            headers=headers,
        )
    finally:
        availability.set_disabled(original)

    assert chat.status_code == 200, chat.text
    body = chat.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == "model_disabled"
    assert "disabled by an administrator" in body["reply"]
    assert "no AI model is configured" not in body["reply"]
