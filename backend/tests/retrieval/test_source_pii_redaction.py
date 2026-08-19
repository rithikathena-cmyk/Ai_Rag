"""PII dual-representation boundary for retrieved source/citation text
(services/reranking/pipeline.py::search_with_reranking). SearchHit.text
keeps the original, authorized chunk content (for an authorized LLM/agent
tool context — see services/agents/planner.py's LLM-payload/public-view
split); SearchHit.display_text is `redact_pii(text)` — the only
representation allowed into anything persisted or returned to a user. See
services/guardrails/pii.py::DualText for the shared raw/display contract.

Attack path this closes:
    retrieved document containing PII -> retrieval -> citation -> chat history
must become:
    retrieved document containing PII -> retrieval -> authorized original (LLM only)
                                                     -> PII redaction (display_text) -> sanitized citation/chat history

Stubs hybrid_search/rerank the same way test_pipeline_rerank_failopen.py
does — no Qdrant/Postgres/network required.
"""

import uuid

import pytest

from app.core.config import settings
from app.services.reranking import pipeline
from app.services.retrieval.search import SearchHit


def _hit(text, *, chunk_id=None, document_id=None, chunk_index=0, parent_chunk_id=None, strategy="hybrid", score=0.9) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        chunk_index=chunk_index,
        parent_chunk_id=parent_chunk_id,
        text=text,
        strategy=strategy,
        score=score,
    )


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt)
    yield
    settings.guardrail_redact_pii, settings.guardrail_pii_mode, settings.guardrail_pii_hash_salt = original


def _run(monkeypatch, hits, *, use_reranker=False):
    monkeypatch.setattr(pipeline, "hybrid_search", lambda db, **k: (hits, {"qdrant_ms": 1.0}))
    return pipeline.search_with_reranking(db=None, query="q", use_reranker=use_reranker)


# --------------------------------------------------- individual PII types (display_text)

def test_email_in_source_is_redacted_in_display_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(monkeypatch, [_hit("Contact John at john.smith@company.com for details.")])

    assert "john.smith@company.com" not in hits[0].display_text
    assert "[REDACTED_EMAIL]" in hits[0].display_text


def test_phone_in_source_is_redacted_in_display_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(monkeypatch, [_hit("Call the office at 987-654-3210 to schedule.")])

    assert "987-654-3210" not in hits[0].display_text
    assert "[REDACTED_PHONE]" in hits[0].display_text


def test_ssn_in_source_is_redacted_in_display_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(monkeypatch, [_hit("Employee SSN: 123-45-6789 on file.")])

    assert "123-45-6789" not in hits[0].display_text
    assert "[REDACTED_SSN]" in hits[0].display_text


def test_credit_card_in_source_is_redacted_in_display_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(monkeypatch, [_hit("Card on file: 4111 1111 1111 1111 expires 2027.")])

    assert "4111 1111 1111 1111" not in hits[0].display_text
    assert "[REDACTED_CREDIT_CARD]" in hits[0].display_text


def test_multiple_pii_types_in_one_source_all_redacted_in_display_text(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(
        monkeypatch,
        [_hit("Contact John at john.smith@company.com or call 987-654-3210. Employee SSN: 123-45-6789.")],
    )

    text = hits[0].display_text
    assert "john.smith@company.com" not in text
    assert "987-654-3210" not in text
    assert "123-45-6789" not in text
    assert "[REDACTED_EMAIL]" in text
    assert "[REDACTED_PHONE]" in text
    assert "[REDACTED_SSN]" in text


def test_hash_mode_is_used_when_configured(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "hash"
    settings.guardrail_pii_hash_salt = "test-salt"
    hits, _ = _run(monkeypatch, [_hit("email jane@example.com")])

    assert "jane@example.com" not in hits[0].display_text
    assert "[REDACTED_EMAIL_" in hits[0].display_text  # same hash-token format as redact_pii() everywhere else


# --------------------------------------------------- dual representation: text stays raw

def test_text_field_keeps_the_original_authorized_content(monkeypatch):
    """The core contract this whole module exists to prove: `.text` is never
    redacted, regardless of what PII it contains — only `.display_text` is.
    An authorized LLM/agent execution reasons over `.text`; anything
    persisted or returned to a user must use `.display_text` instead (see
    services/agents/planner.py's _llm_source_view/_public_source_view)."""
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(monkeypatch, [_hit("Contact John at john.smith@company.com or SSN 123-45-6789.")])

    assert hits[0].text == "Contact John at john.smith@company.com or SSN 123-45-6789."
    assert "john.smith@company.com" not in hits[0].display_text
    assert "123-45-6789" not in hits[0].display_text


# --------------------------------------------------- non-PII / disabled behavior

def test_non_pii_source_text_is_unchanged_in_both_fields(monkeypatch):
    settings.guardrail_redact_pii = True
    hits, _ = _run(monkeypatch, [_hit("What is the annual leave accrual rate for full-time staff?")])

    assert hits[0].text == "What is the annual leave accrual rate for full-time staff?"
    assert hits[0].display_text == "What is the annual leave accrual rate for full-time staff?"


def test_redaction_disabled_leaves_display_text_verbatim(monkeypatch):
    settings.guardrail_redact_pii = False
    hits, _ = _run(monkeypatch, [_hit("Contact john.smith@company.com")])

    assert hits[0].text == "Contact john.smith@company.com"
    assert hits[0].display_text == "Contact john.smith@company.com"


# --------------------------------------------------- metadata preservation

def test_citation_metadata_is_preserved_alongside_redaction(monkeypatch):
    settings.guardrail_redact_pii = True
    chunk_id, document_id, parent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    hits, _ = _run(monkeypatch, [
        _hit(
            "SSN 123-45-6789", chunk_id=chunk_id, document_id=document_id, chunk_index=7,
            parent_chunk_id=parent_id, strategy="hybrid", score=0.87,
        )
    ])

    hit = hits[0]
    assert "123-45-6789" not in hit.display_text  # display content sanitized...
    assert hit.chunk_id == chunk_id  # ...but every metadata field is untouched
    assert hit.document_id == document_id
    assert hit.chunk_index == 7
    assert hit.parent_chunk_id == parent_id
    assert hit.strategy == "hybrid"
    assert hit.score == 0.87


# --------------------------------------------------- original object is not mutated

def test_original_searchhit_object_is_not_mutated(monkeypatch):
    settings.guardrail_redact_pii = True
    original = _hit("email jane@example.com")
    monkeypatch.setattr(pipeline, "hybrid_search", lambda db, **k: ([original], {"qdrant_ms": 1.0}))

    result_hits, _ = pipeline.search_with_reranking(db=None, query="q", use_reranker=False)

    # search_with_reranking returns a *new* SearchHit with display_text
    # populated — the object hybrid_search produced (and anything else
    # already holding a reference to it) is left exactly as it was.
    assert original.text == "email jane@example.com"
    assert original.display_text == ""  # never populated on the original
    assert result_hits[0] is not original
    assert result_hits[0].text == original.text  # raw content identical...
    assert result_hits[0].display_text != ""  # ...but the copy also carries the redacted view


# --------------------------------------------------- redaction happens after reranking, not before

def test_reranker_sees_real_text_not_redacted_text(monkeypatch):
    # Regression guard for the ordering decision itself: if display_text were
    # computed before rerank() and rerank() were changed to use it, the
    # cross-encoder would score relevance against "[REDACTED_EMAIL]" instead
    # of the real chunk content, degrading ranking quality for any query
    # whose best match happens to contain PII. rerank() always receives
    # hits straight from hybrid_search(), before display_text exists at all.
    settings.guardrail_redact_pii = True
    hits = [_hit("email jane@example.com")]
    monkeypatch.setattr(pipeline, "hybrid_search", lambda db, **k: (hits, {"qdrant_ms": 1.0}))

    seen_by_reranker = {}

    def _fake_rerank(query, hits):
        seen_by_reranker["text"] = hits[0].text
        seen_by_reranker["display_text"] = hits[0].display_text
        return hits

    monkeypatch.setattr(pipeline, "rerank", _fake_rerank)

    result_hits, reranked = pipeline.search_with_reranking(db=None, query="q", use_reranker=True)

    assert seen_by_reranker["text"] == "email jane@example.com"  # reranker saw the real text
    assert seen_by_reranker["display_text"] == ""  # display_text doesn't exist yet at rerank time
    assert result_hits[0].text == "email jane@example.com"  # LLM-facing content unchanged
    assert "jane@example.com" not in result_hits[0].display_text  # display view is redacted


# --------------------------------------------------- secret redaction (both text AND display_text)
#
# Found during the guardrails audit: a document a user is genuinely
# authorized to retrieve can still contain a real embedded credential (an
# ingested config file, README, or support ticket) — pii.py's recognizers
# have no coverage for credential shapes (API keys, AWS keys, JWTs, private
# keys) at all, so before this fix such a value flowed into BOTH the LLM's
# context (`.text`) and the user-facing citation (`.display_text`)
# unredacted. Unlike PII, secrets are redacted from `.text` too — see
# services/guardrails/secrets.py::redact_secrets()'s docstring for why
# there's no equivalent "authorized lookup" case for a live credential.


def test_secret_in_source_is_redacted_in_both_text_and_display_text(monkeypatch):
    hits, _ = _run(monkeypatch, [_hit("Deploy key: AKIAABCDEFGHIJKLMNOP for the staging bucket.")])

    assert "AKIAABCDEFGHIJKLMNOP" not in hits[0].text
    assert "AKIAABCDEFGHIJKLMNOP" not in hits[0].display_text
    assert "[REDACTED_SECRET]" in hits[0].text
    assert "[REDACTED_SECRET]" in hits[0].display_text


def test_secret_and_pii_in_the_same_source_are_both_redacted(monkeypatch):
    settings.guardrail_redact_pii = True
    settings.guardrail_pii_mode = "placeholder"
    hits, _ = _run(
        monkeypatch,
        [_hit("Contact jane@example.com. Legacy key AKIAABCDEFGHIJKLMNOP still active.")],
    )

    assert "AKIAABCDEFGHIJKLMNOP" not in hits[0].text
    assert "[REDACTED_SECRET]" in hits[0].text
    assert "jane@example.com" not in hits[0].display_text
    assert "[REDACTED_EMAIL]" in hits[0].display_text


def test_non_secret_source_text_is_unchanged(monkeypatch):
    hits, _ = _run(monkeypatch, [_hit("What is the annual leave accrual rate for full-time staff?")])

    assert hits[0].text == "What is the annual leave accrual rate for full-time staff?"
