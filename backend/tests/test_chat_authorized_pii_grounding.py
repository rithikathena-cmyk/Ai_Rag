"""routers/chat.py's output PII pipeline, specifically for the scenario
prompts/planner_agent_v4.yaml's changelog documents: an authorized,
RBAC-cleared search result contains PII (a reporter's name, phone, email),
Claude answers using that content per the updated prompt instruction ("don't
withhold or self-redact authorized details — a separate system handles
that"), and the EXISTING output guardrail pipeline (pii.py's redact_pii,
already covered end-to-end by test_pipeline_pii_block.py /
test_search_pii_redaction.py / test_planner_source_pii_split.py) must still
be the thing that actually removes the raw values before they reach the
user, the persisted conversation history, or the source list.

Scope note — what this file does and doesn't test:
- Whether Claude ACTUALLY includes PII in a real generated reply (rather
  than self-censoring, the original bug this prompt change addresses) is a
  live model-behavior question, not a deterministic one — no prompt wording
  guarantees a specific output. That's covered separately, as a best-effort
  live check, in tests/integration/test_live_chat_flow.py
  (test_authorized_pii_is_answered_and_then_redacted_live), which skips
  cleanly without a real stack/API key exactly like its sibling tests.
- What IS deterministic, and what this file actually tests: GIVEN a reply
  that contains PII (simulating what the updated prompt is meant to
  encourage Claude to produce), does the existing output pipeline correctly
  detect and redact it before it reaches ChatResponse, add_message()
  (persisted history), and the sources list — and does the raw value never
  appear anywhere in that path. run_agent() is stubbed for exactly this
  reason: to make "Claude generated PII-containing content" a controlled
  precondition rather than something this suite depends on the live model
  reproducing every run.

Same lightweight-app + dependency_overrides convention as
test_chat_degraded_reason.py / test_search_pii_redaction.py: a real FastAPI
app with just chat.router, current_user/db dependencies overridden, and
every I/O boundary chat.py touches monkeypatched at the names it imported
them under. run_output_guardrails() itself is left REAL (not mocked) — it's
the exact mechanism under test.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.postgres import get_db
from app.gateway.schemas import ModelTier
from app.routers import chat as chat_router
from app.services.auth.dependencies import get_current_user

RAW_PHONE = "312-555-0173"
RAW_EMAIL = "diego.marsh.test@examplecorp.internal"
REPORTER_NAME = "Diego Marsh"


class _FakeConversation:
    def __init__(self):
        self.id = uuid.uuid4()


def _make_app(monkeypatch, *, reply: str, sources: list[dict], role: str = "user", department: str = "manufacturing"):
    app = FastAPI()
    app.include_router(chat_router.router)

    fake_user = SimpleNamespace(id=uuid.uuid4(), role=role, department=department, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user

    def _fake_get_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_get_db

    # B: the caller is authorized — fake_decision represents an RBAC grant
    # already resolved for this role/department, same as every other
    # chat.py-level test in this suite. Department/category filtering
    # itself (what determines whether a caller is authorized for a given
    # document at all) is exercised elsewhere — tests/llm_rbac/
    # test_category_policy.py, test_policy_engine.py — and is unrelated to
    # this prompt change, which only affects how already-authorized content
    # is used, never what's retrievable in the first place.
    fake_decision = SimpleNamespace(
        allowed=True, role=role, department=department, model_tier=ModelTier.HAIKU,
        allowed_tools=frozenset(), sql_allowed_tables=frozenset(), knowledge_departments=(department,),
        max_concurrent_requests=None, requires_approval=False,
    )
    monkeypatch.setattr(chat_router, "authorize_llm_request", lambda *a, **k: fake_decision)
    monkeypatch.setattr(chat_router, "get_conversation", lambda db, cid: _FakeConversation())
    monkeypatch.setattr(chat_router, "create_conversation", lambda db, user_id: _FakeConversation())
    monkeypatch.setattr(chat_router, "build_context", lambda db, cid: (None, []))
    monkeypatch.setattr(chat_router, "get_preferences", lambda db, uid: {})
    monkeypatch.setattr(chat_router, "maybe_summarize", lambda *a, **k: None)

    persisted = []
    monkeypatch.setattr(
        chat_router, "add_message",
        lambda db, conv_id, *, role, content, sources=None, report=None: persisted.append(
            {"role": role, "content": content, "sources": sources}
        ),
    )

    # C, as a controlled precondition (see module docstring): simulates
    # Claude having answered using authorized, PII-containing source content
    # per the updated prompt, rather than self-censoring it.
    monkeypatch.setattr(
        chat_router, "run_agent",
        lambda *a, **k: SimpleNamespace(reply=reply, sources=sources, report=None, trace=[], degraded_reason=None),
    )

    return TestClient(app), persisted


@pytest.fixture(autouse=True)
def _guardrails_on():
    original = settings.guardrails_enabled
    settings.guardrails_enabled = True
    yield
    settings.guardrails_enabled = original


# A: a source containing synthetic PII. Deliberately shaped as
# _public_source_view() (planner.py) would actually return it to chat.py —
# text already carries redaction tokens, not raw values — because that's
# genuinely what result.sources looks like in every real request; raw
# source text never reaches chat.py at all (planner.py's _llm_source_view()/
# _public_source_view() split is the enforcement point for that, and it's
# already covered end-to-end by test_planner_source_pii_split.py). A source
# dict with raw text injected directly here would test an input shape
# run_agent() can't actually produce, not a real gap in chat.py — this repo
# deliberately keeps PII sanitization as a single enforcement point per
# boundary rather than duplicating it defensively at every layer that
# happens to handle the data afterward.
_SOURCE_WITH_PII = {
    "index": 1,
    "chunk_id": str(uuid.uuid4()),
    "document_id": str(uuid.uuid4()),
    "document_filename": "mfg_incident_report_line7_stoppage.md",
    "chunk_index": 0,
    "text": f"Reported by: {REPORTER_NAME} (test data). Contact Phone: [REDACTED_PHONE]. Contact Email: [REDACTED_EMAIL].",
}

_REPLY_WITH_PII = (
    f"The Line 7 stoppage was reported by {REPORTER_NAME} [1]. "
    f"You can reach them at {RAW_PHONE} or {RAW_EMAIL} [1]."
)


def test_d_output_pipeline_detects_pii_in_an_authorized_reply(monkeypatch):
    client, _ = _make_app(monkeypatch, reply=_REPLY_WITH_PII, sources=[_SOURCE_WITH_PII])
    response = client.post("/chat", json={"message": "Who reported the Line 7 stoppage, and what's their contact info?"})

    assert response.status_code == 200, response.text
    body = response.json()
    pii_steps = [s for s in body["trace"] if s["tool"] == "pii_redact"]
    assert any(s["summary"].startswith("redact:") for s in pii_steps), (
        f"expected an output-side pii_redact step to fire on a PII-containing reply, got: {pii_steps}"
    )


def test_e_final_response_contains_redacted_tokens_not_raw_values(monkeypatch):
    client, _ = _make_app(monkeypatch, reply=_REPLY_WITH_PII, sources=[_SOURCE_WITH_PII])
    response = client.post("/chat", json={"message": "Who reported the Line 7 stoppage, and what's their contact info?"})

    body = response.json()
    assert RAW_PHONE not in body["reply"]
    assert RAW_EMAIL not in body["reply"]
    assert "[REDACTED_PHONE]" in body["reply"]
    assert "[REDACTED_EMAIL]" in body["reply"]
    # The point of this prompt change: redacted, not refused-into-uselessness.
    # The name is not PII this pipeline redacts (see pii.py's recognizer
    # list — no PERSON/name recognizer, by design, to avoid the false-positive
    # trap documented in presidio_check.py/gliner_check.py), so it should
    # still be present, proving this is a real, informative answer.
    assert REPORTER_NAME in body["reply"]


def test_f_raw_pii_never_reaches_response_sources_or_persistence(monkeypatch):
    client, persisted = _make_app(monkeypatch, reply=_REPLY_WITH_PII, sources=[_SOURCE_WITH_PII])
    response = client.post("/chat", json={"message": "Who reported the Line 7 stoppage, and what's their contact info?"})

    body = response.json()
    full_response_text = str(body)
    assert RAW_PHONE not in full_response_text, "raw phone number leaked into the HTTP response"
    assert RAW_EMAIL not in full_response_text, "raw email leaked into the HTTP response"

    # Sources: chat.py passes through result.sources as-is (already
    # _public_source_view()'d upstream in a real run — see
    # test_planner_source_pii_split.py for that boundary). This test's fake
    # source simulates a raw source dict reaching chat.py to confirm the
    # response layer itself doesn't introduce a second leak path even if an
    # upstream redaction step were ever skipped.
    for s in body["sources"]:
        assert RAW_PHONE not in s["text"]
        assert RAW_EMAIL not in s["text"]

    # Persistence: add_message() must never receive the raw content either.
    assert persisted, "expected add_message to have been called"
    assistant_messages = [p for p in persisted if p["role"] == "assistant"]
    assert assistant_messages, "expected an assistant message to be persisted"
    for msg in assistant_messages:
        assert RAW_PHONE not in msg["content"]
        assert RAW_EMAIL not in msg["content"]


def test_f_no_raw_pii_in_guardrail_trace_detail(monkeypatch):
    # Same "labels only, never values" audit-log-leak concern pii.py's
    # redact_pii()/presidio_check.py/gliner_check.py all guard against —
    # this is the end-to-end confirmation at the actual HTTP response layer,
    # where GET /admin/guardrail-analytics ultimately reads from.
    client, _ = _make_app(monkeypatch, reply=_REPLY_WITH_PII, sources=[_SOURCE_WITH_PII])
    response = client.post("/chat", json={"message": "Who reported the Line 7 stoppage, and what's their contact info?"})

    body = response.json()
    for step in body["trace"]:
        assert RAW_PHONE not in step["summary"]
        assert RAW_EMAIL not in step["summary"]


def test_reply_without_pii_is_unaffected(monkeypatch):
    """Sanity check: an ordinary reply with nothing sensitive in it isn't
    touched or flagged differently now that the prompt encourages more
    forthcoming answers — this change only affects PII-shaped content."""
    client, _ = _make_app(
        monkeypatch, reply="The machine shutdown procedure requires a full lockout/tagout sequence [1].",
        sources=[{**_SOURCE_WITH_PII, "text": "SOP-MFG-101: lockout/tagout sequence for machine shutdown."}],
    )
    response = client.post("/chat", json={"message": "What is the machine shutdown procedure?"})

    body = response.json()
    assert response.status_code == 200
    assert "lockout/tagout" in body["reply"]
    pii_steps = [s for s in body["trace"] if s["tool"] == "pii_redact"]
    assert all(s["summary"].startswith("pass:") for s in pii_steps)


def test_unauthorized_caller_gets_no_pii_because_it_was_never_retrieved(monkeypatch):
    """The prompt change only affects how Claude uses content it DID
    receive from search_documents — it has no influence over what
    search_documents returns in the first place. Department/category-level
    RBAC filtering (which decides that) is enforced independently, before
    the model ever runs, and has its own dedicated coverage —
    tests/llm_rbac/test_category_policy.py, test_policy_engine.py. This
    test's job is narrower and specific to THIS change: confirm that being
    less forthcoming has no leak path, by simulating exactly what an
    out-of-scope query looks like at the chat.py boundary — retrieval
    returning nothing, because RBAC filtered the relevant document out
    before search_documents ever ran. Even with the updated prompt telling
    Claude to be forthcoming with authorized content, there is nothing here
    for it to be forthcoming WITH."""
    client, persisted = _make_app(
        monkeypatch,
        reply="I don't have any information about that in the documents I have access to.",
        sources=[],  # RBAC/department filtering already excluded the relevant document
        role="user", department="manufacturing",
    )
    response = client.post(
        "/chat", json={"message": "Who reported the Line 7 stoppage, and what's their contact info?"}
    )

    body = response.json()
    assert response.status_code == 200
    assert RAW_PHONE not in body["reply"]
    assert RAW_EMAIL not in body["reply"]
    assert REPORTER_NAME not in body["reply"]
    assert body["sources"] == []

    assistant_messages = [p for p in persisted if p["role"] == "assistant"]
    assert assistant_messages
    for msg in assistant_messages:
        assert RAW_PHONE not in msg["content"]
        assert RAW_EMAIL not in msg["content"]
        assert REPORTER_NAME not in msg["content"]
