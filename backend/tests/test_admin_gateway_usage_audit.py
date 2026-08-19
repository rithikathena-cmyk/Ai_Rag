"""routers/admin.py::GET /admin/gateway-usage — GatewayUsageLogModel is
already the durable "who did what, was it allowed" LLM-RBAC audit trail
(see that model's own docstring, and gateway/usage_tracker.py's
record_usage()/record_denied()), but the endpoint reading it back only ever
surfaced cost/latency fields — user_id, role, department, decision, and
denial_reason were written on every row and never read by anything. These
tests cover the extension that actually exposes them: resolved user_email
(batched, same convention as routers/approvals.py::_resolve_emails), the
decision/denial_reason fields themselves, and the `?decision=` filter.

Uses the real Postgres session (matching tests/integration/test_live_chat_flow.py's
throwaway_user convention) rather than mocking the DB — this endpoint's
query shape (samples + batched email resolution + grouped summary + two
scalar aggregates) isn't reasonably fakeable without re-implementing SQLAlchemy.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import new_session
from app.models.gateway_usage_log import GatewayUsageLogModel
from app.models.user import UserModel
from app.routers import admin
from app.services.auth.dependencies import get_current_user
from app.services.auth.password import hash_password


@pytest.fixture
def throwaway_user():
    email = f"gateway-audit-test-{uuid.uuid4().hex[:8]}@example.com"
    db = new_session()
    user = UserModel(
        email=email, display_name="Gateway Audit Test User", password_hash=hash_password("Throwaway-Pass-1!"),
        is_active=True, role="user", department="manufacturing",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    try:
        yield user_id, email
    finally:
        db = new_session()
        db.query(UserModel).filter(UserModel.id == user_id).delete()
        db.commit()
        db.close()


@pytest.fixture
def gateway_log_rows(throwaway_user):
    user_id, _email = throwaway_user
    db = new_session()
    allowed = GatewayUsageLogModel(
        request_id=str(uuid.uuid4()), agent_name="planner_agent", model="claude-sonnet-5", tier="sonnet",
        tokens_input=100, tokens_output=50, latency_ms=250.0, cost_usd=0.01,
        user_id=user_id, role="user", department="manufacturing",
        requested_capability="search_manuals", decision="allowed",
    )
    denied = GatewayUsageLogModel(
        request_id=str(uuid.uuid4()), agent_name="chat_endpoint", model="n/a", tier="n/a",
        tokens_input=0, tokens_output=0, latency_ms=0.0, cost_usd=0.0,
        user_id=user_id, role="user", department="manufacturing",
        requested_capability="hr_report_generation", decision="denied",
        denial_reason="Role 'user' is not permitted to request capability 'hr_report_generation'",
    )
    db.add(allowed)
    db.add(denied)
    db.commit()
    row_ids = [allowed.id, denied.id]
    db.close()
    try:
        yield allowed.request_id, denied.request_id
    finally:
        db = new_session()
        db.query(GatewayUsageLogModel).filter(GatewayUsageLogModel.id.in_(row_ids)).delete(synchronize_session=False)
        db.commit()
        db.close()


def _admin_client():
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_current_user] = lambda: type(
        "FakeAdmin", (), {"id": uuid.uuid4(), "role": "admin", "is_active": True},
    )()
    return TestClient(app)


def test_gateway_usage_resolves_user_email_and_includes_audit_fields(throwaway_user, gateway_log_rows):
    user_id, email = throwaway_user
    allowed_request_id, denied_request_id = gateway_log_rows
    client = _admin_client()

    response = client.get("/admin/gateway-usage", params={"limit": 500})

    assert response.status_code == 200
    body = response.json()
    by_request_id = {s["request_id"]: s for s in body["samples"]}

    allowed_sample = by_request_id[allowed_request_id]
    # `id` is this row's own primary key — distinct from request_id, which
    # is NOT unique per row (one outer user request can fan out into several
    # gateway calls sharing the same request_id). The frontend must key off
    # `id`, not `request_id` — see the regression this covers below.
    assert uuid.UUID(allowed_sample["id"])
    assert allowed_sample["user_id"] == str(user_id)
    assert allowed_sample["user_email"] == email
    assert allowed_sample["role"] == "user"
    assert allowed_sample["department"] == "manufacturing"
    assert allowed_sample["decision"] == "allowed"
    assert allowed_sample["denial_reason"] is None
    assert allowed_sample["requested_capability"] == "search_manuals"

    denied_sample = by_request_id[denied_request_id]
    assert denied_sample["user_email"] == email
    assert denied_sample["decision"] == "denied"
    assert denied_sample["denial_reason"] == "Role 'user' is not permitted to request capability 'hr_report_generation'"
    assert denied_sample["requested_capability"] == "hr_report_generation"


def test_gateway_usage_id_is_unique_even_when_request_id_is_shared(throwaway_user):
    """Live-verified gap: one outer chat request can fan out into several
    Claude Gateway calls (planner turn, tool calls, judge, ...) that all
    share the same request_id for correlation — real production data has
    request_ids repeated 6x. The frontend originally keyed table rows off
    request_id and React logged "two children with the same key" duplicate
    warnings across ~180 rows. `id` (this row's real primary key) must stay
    unique per row even when request_id repeats."""
    user_id, _email = throwaway_user
    shared_request_id = str(uuid.uuid4())
    db = new_session()
    rows = [
        GatewayUsageLogModel(
            request_id=shared_request_id, agent_name=f"agent_{i}", model="claude-sonnet-5", tier="sonnet",
            tokens_input=10, tokens_output=5, latency_ms=100.0, cost_usd=0.001,
            user_id=user_id, role="user", department="manufacturing", decision="allowed",
        )
        for i in range(3)
    ]
    db.add_all(rows)
    db.commit()
    row_ids = [r.id for r in rows]
    db.close()
    try:
        client = _admin_client()
        response = client.get("/admin/gateway-usage", params={"limit": 500})
        assert response.status_code == 200
        matching = [s for s in response.json()["samples"] if s["request_id"] == shared_request_id]
        assert len(matching) == 3
        assert len({s["id"] for s in matching}) == 3, "id must be unique per row even when request_id repeats"
    finally:
        db = new_session()
        db.query(GatewayUsageLogModel).filter(GatewayUsageLogModel.id.in_(row_ids)).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_gateway_usage_decision_filter_returns_only_denied(gateway_log_rows):
    allowed_request_id, denied_request_id = gateway_log_rows
    client = _admin_client()

    response = client.get("/admin/gateway-usage", params={"limit": 500, "decision": "denied"})

    assert response.status_code == 200
    request_ids = {s["request_id"] for s in response.json()["samples"]}
    assert denied_request_id in request_ids
    assert allowed_request_id not in request_ids


def test_gateway_usage_denied_count_reflects_full_table(gateway_log_rows):
    client = _admin_client()

    response = client.get("/admin/gateway-usage", params={"limit": 1})

    assert response.status_code == 200
    # denied_count is aggregated over the full table, not just the 1-row
    # sample window this request asked for — must be at least the 1 denied
    # row this test itself just inserted.
    assert response.json()["denied_count"] >= 1


def test_gateway_usage_row_with_no_user_id_has_null_email():
    """System-internal callers (generation_judge.py, memory/store.py) never
    supply user_id/role — this must degrade to null fields, not 404/500."""
    db = new_session()
    row = GatewayUsageLogModel(
        request_id=str(uuid.uuid4()), agent_name="generation_judge", model="claude-haiku-4-5-20251001", tier="haiku",
        tokens_input=10, tokens_output=5, latency_ms=100.0, cost_usd=0.001, decision="allowed",
    )
    db.add(row)
    db.commit()
    row_id, request_id = row.id, row.request_id
    db.close()
    try:
        client = _admin_client()
        response = client.get("/admin/gateway-usage", params={"limit": 500})
        assert response.status_code == 200
        sample = next(s for s in response.json()["samples"] if s["request_id"] == request_id)
        assert sample["user_id"] is None
        assert sample["user_email"] is None
        assert sample["role"] is None
    finally:
        db = new_session()
        db.query(GatewayUsageLogModel).filter(GatewayUsageLogModel.id == row_id).delete()
        db.commit()
        db.close()
