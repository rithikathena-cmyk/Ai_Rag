"""gateway/usage_tracker.py's LLM-RBAC audit-log fields (docs/AUDIT_LOGGING.md).

Follows this suite's established convention (tests/test_rbac.py's fake user)
of stubbing the I/O boundary
rather than requiring a real Postgres instance — new_session() is
monkeypatched to a small in-memory stand-in that just records what was
add()-ed, so these tests verify the row *shape* the tracker builds, not a
real database round-trip.
"""

import uuid

from app.gateway import usage_tracker
from app.gateway.schemas import TokenUsage


class _FakeSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        pass


def test_record_usage_writes_an_allowed_row_with_the_new_audit_fields(monkeypatch):
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)
    monkeypatch.setattr(usage_tracker, "record_token_usage", lambda *a, **k: None)
    increment_calls = []
    monkeypatch.setattr(usage_tracker.llm_rbac_quotas, "increment_usage", lambda *a, **k: increment_calls.append((a, k)))

    user_id = uuid.uuid4()
    usage_tracker.record_usage(
        request_id="req-1",
        agent_name="planner_agent",
        model="claude-sonnet-5",
        tier="sonnet",
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        latency_ms=250.0,
        user_id=user_id,
        role="hr",
        department="hr",
        prompt_version="v1",
        tool_calls=["search_documents"],
        documents_retrieved=["11111111-1111-1111-1111-111111111111"],
        requested_capability="hr_document_search",
        output_format="xlsx",
        resource_scope={"knowledge_departments": ["hr"]},
    )

    assert fake_db.committed
    row = fake_db.added[0]
    assert row.decision == "allowed"
    assert row.user_id == user_id
    assert row.role == "hr"
    assert row.department == "hr"
    assert row.prompt_version == "v1"
    assert row.tool_calls == ["search_documents"]
    assert row.documents_retrieved == ["11111111-1111-1111-1111-111111111111"]
    assert row.tokens_input == 100
    assert row.tokens_output == 50
    assert row.requested_capability == "hr_document_search"
    assert row.output_format == "xlsx"
    assert row.resource_scope == {"knowledge_departments": ["hr"]}
    # user_id supplied -> this is an LLM-RBAC-governed request -> its quota
    # counters must advance in the same step (docs/AUDIT_LOGGING.md §2).
    assert len(increment_calls) == 1


def test_record_usage_without_user_id_skips_the_quota_increment(monkeypatch):
    # generation_judge.py / memory/store.py — internal callers not driven by
    # an end-user role — must not touch role_usage_counters at all.
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)
    monkeypatch.setattr(usage_tracker, "record_token_usage", lambda *a, **k: None)
    increment_calls = []
    monkeypatch.setattr(usage_tracker.llm_rbac_quotas, "increment_usage", lambda *a, **k: increment_calls.append((a, k)))

    usage_tracker.record_usage(
        request_id="req-2", agent_name="eval_judge", model="claude-opus-5", tier="fast",
        usage=TokenUsage(input_tokens=10, output_tokens=5), latency_ms=10.0,
    )

    assert fake_db.added[0].decision == "allowed"
    assert fake_db.added[0].user_id is None
    assert increment_calls == []


def test_record_denied_writes_a_zero_token_denied_row(monkeypatch):
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)

    user_id = uuid.uuid4()
    usage_tracker.record_denied(
        agent_name="planner_agent",
        user_id=user_id,
        role="user",
        department="manufacturing",
        denial_reason="'workforce_planning' is not permitted for role 'user'",
        requested_capability="workforce_planning",
    )

    assert fake_db.committed
    row = fake_db.added[0]
    assert row.decision == "denied"
    assert row.tokens_input == 0
    assert row.tokens_output == 0
    assert row.cost_usd == 0.0
    assert row.user_id == user_id
    assert row.role == "user"
    assert row.denial_reason.startswith("'workforce_planning'")
    assert row.requested_capability == "workforce_planning"


def test_record_denied_truncates_an_overlong_reason(monkeypatch):
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)

    usage_tracker.record_denied(
        agent_name="planner_agent", user_id=uuid.uuid4(), role="user", department=None,
        denial_reason="x" * 500,
    )

    assert len(fake_db.added[0].denial_reason) == 256


def test_record_search_writes_an_allowed_row_and_increments_daily_requests(monkeypatch):
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)
    increment_calls = []
    monkeypatch.setattr(usage_tracker.llm_rbac_quotas, "increment_usage", lambda *a, **k: increment_calls.append((a, k)))

    user_id = uuid.uuid4()
    usage_tracker.record_search(
        request_id="req-search-1", user_id=user_id, role="user", department="engineering",
        latency_ms=42.0, requested_capability="doc_search",
        documents_retrieved=["11111111-1111-1111-1111-111111111111"],
    )

    assert fake_db.committed
    row = fake_db.added[0]
    assert row.decision == "allowed"
    assert row.agent_name == "search_endpoint"
    assert row.model == "n/a"
    assert row.tier == "n/a"
    assert row.tokens_input == 0
    assert row.tokens_output == 0
    assert row.cost_usd == 0.0
    assert row.user_id == user_id
    assert row.role == "user"
    assert row.department == "engineering"
    assert row.documents_retrieved == ["11111111-1111-1111-1111-111111111111"]
    # search traffic didn't advance daily_requests before this — confirms it
    # now does, in the same step as every other allowed-request audit row.
    assert len(increment_calls) == 1


def test_record_search_does_not_store_raw_query_text(monkeypatch):
    # No `query` kwarg exists on record_search at all — asserting the row has
    # no such attribute guards against a future accidental re-introduction.
    fake_db = _FakeSession()
    monkeypatch.setattr(usage_tracker, "new_session", lambda: fake_db)
    monkeypatch.setattr(usage_tracker.llm_rbac_quotas, "increment_usage", lambda *a, **k: None)

    usage_tracker.record_search(
        request_id="req-search-2", user_id=uuid.uuid4(), role="user", department=None, latency_ms=1.0,
    )

    assert not hasattr(fake_db.added[0], "query")


def test_record_search_failure_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(usage_tracker, "new_session", _boom)

    usage_tracker.record_search(request_id="req-search-3", user_id=uuid.uuid4(), role="user", department=None, latency_ms=1.0)


def test_a_tracking_failure_never_raises(monkeypatch):
    # Same best-effort contract record_usage() already documented before
    # this pass — a Postgres hiccup must never break the caller's actual
    # LLM response.
    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(usage_tracker, "new_session", _boom)
    monkeypatch.setattr(usage_tracker, "record_token_usage", lambda *a, **k: None)

    usage_tracker.record_usage(
        request_id="req-3", agent_name="planner_agent", model="claude-sonnet-5", tier="sonnet",
        usage=TokenUsage(input_tokens=1, output_tokens=1), latency_ms=1.0,
    )
    usage_tracker.record_denied(agent_name="planner_agent", user_id=uuid.uuid4(), role="user", department=None, denial_reason="x")
