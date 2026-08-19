"""services/audit/logger.py + routers/audit.py — the new centralized
Activity & Audit Logging system (docs/AUDIT_LOGGING.md). Covers exactly the
categories the approved plan's "Tests" item calls for: metadata sanitization
(PII/secrets never persisted raw), the fixed field allowlist, fail-safe
writes, RBAC on the read API, pagination hard-capping, filtering, and
request_id correlation. Uses the real Postgres session, matching
test_conversations_update.py's convention, since AuditEventModel rows are
real durable state, not something worth mocking.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.postgres import new_session
from app.models.audit_event import AuditEventModel
from app.routers import audit
from app.services.audit import logger as audit_logger
from app.services.audit.event_types import AuditEventType, AuditOutcome
from app.services.auth.dependencies import get_current_user


def _fake_user(role: str = "admin"):
    return type("FakeUser", (), {"id": uuid.uuid4(), "role": role, "is_active": True})()


def _client_as(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(audit.router)
    app.dependency_overrides[get_current_user] = lambda: _fake_user(role)
    return TestClient(app)


def _new_request_id() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


def _cleanup(request_id: str) -> None:
    db = new_session()
    db.query(AuditEventModel).filter(AuditEventModel.request_id == request_id).delete()
    db.commit()
    db.close()


def _row_for(request_id: str) -> AuditEventModel:
    db = new_session()
    try:
        return db.query(AuditEventModel).filter(AuditEventModel.request_id == request_id).one()
    finally:
        db.close()


# ---- Sanitization -----------------------------------------------------


def test_log_redacts_ssn_in_metadata_detail():
    request_id = _new_request_id()
    try:
        audit_logger.log(
            AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id=request_id,
            metadata={"detail": "Caller provided SSN 123-45-6789 in the request body"},
        )

        row = _row_for(request_id)

        assert "123-45-6789" not in row.metadata_["detail"]
        assert "[REDACTED_SSN]" in row.metadata_["detail"]
    finally:
        _cleanup(request_id)


def test_log_redacts_api_key_shaped_value_in_metadata_detail():
    request_id = _new_request_id()
    try:
        audit_logger.log(
            AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id=request_id,
            metadata={"detail": "Message contained sk-abcdefghijklmnopqrstuvwxyz123456"},
        )

        row = _row_for(request_id)

        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in row.metadata_["detail"]
        assert "[REDACTED_SECRET]" in row.metadata_["detail"]
    finally:
        _cleanup(request_id)


def test_log_rejects_unknown_metadata_key():
    with pytest.raises(ValueError):
        audit_logger.log(
            AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id=_new_request_id(),
            metadata={"raw_request_body": "x"},
        )


def test_log_rejects_non_primitive_metadata_value():
    with pytest.raises(ValueError):
        audit_logger.log(
            AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id=_new_request_id(),
            metadata={"detail": {"nested": "object"}},
        )


# ---- Fail-safe write ----------------------------------------------------


def test_log_swallows_db_failure_instead_of_raising(monkeypatch):
    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(audit_logger, "new_session", _boom)

    # Must not raise — an audit-write failure can never break the real
    # request it's describing (mirrors gateway/usage_tracker.py's own
    # established fail-safe behavior).
    audit_logger.log(AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id="test-failsafe")


# ---- RBAC ----------------------------------------------------------------


def test_role_with_view_audit_logs_permission_can_list_events():
    client = _client_as(role="admin")

    response = client.get("/audit/events")

    assert response.status_code == 200


def test_role_without_view_audit_logs_permission_gets_403():
    client = _client_as(role="user")

    response = client.get("/audit/events")

    assert response.status_code == 403


# ---- Pagination ------------------------------------------------------


def test_limit_is_hard_capped_server_side(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_LIMIT", 5)
    request_id = _new_request_id()
    try:
        for _ in range(8):
            audit_logger.log(AuditEventType.SUSPICIOUS_ACTIVITY, outcome=AuditOutcome.BLOCKED, request_id=request_id)

        client = _client_as(role="admin")
        response = client.get("/audit/events", params={"request_id": request_id, "limit": 9999})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 8  # true count, unaffected by the cap
        assert len(body["items"]) == 5  # returned rows, hard-capped
    finally:
        _cleanup(request_id)


# ---- Filtering -------------------------------------------------------


def test_event_type_filter_narrows_results():
    request_id = _new_request_id()
    try:
        audit_logger.log(AuditEventType.LOGIN_SUCCESS, outcome=AuditOutcome.SUCCESS, request_id=request_id)
        audit_logger.log(AuditEventType.LOGIN_FAILURE, outcome=AuditOutcome.FAILURE, request_id=request_id)

        client = _client_as(role="admin")
        response = client.get("/audit/events", params={"request_id": request_id, "event_type": "LOGIN_SUCCESS"})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["event_type"] == "LOGIN_SUCCESS"
    finally:
        _cleanup(request_id)


def test_outcome_filter_narrows_results():
    request_id = _new_request_id()
    try:
        audit_logger.log(AuditEventType.LOGIN_SUCCESS, outcome=AuditOutcome.SUCCESS, request_id=request_id)
        audit_logger.log(AuditEventType.LOGIN_FAILURE, outcome=AuditOutcome.FAILURE, request_id=request_id)

        client = _client_as(role="admin")
        response = client.get("/audit/events", params={"request_id": request_id, "outcome": "FAILURE"})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["outcome"] == "FAILURE"
    finally:
        _cleanup(request_id)


# ---- Request-id correlation -------------------------------------------


def test_request_id_correlates_events_across_categories():
    request_id = _new_request_id()
    try:
        audit_logger.log(AuditEventType.LOGIN_SUCCESS, outcome=AuditOutcome.SUCCESS, request_id=request_id)
        audit_logger.log(
            AuditEventType.GUARDRAIL_POLICY_DENIED, outcome=AuditOutcome.BLOCKED, request_id=request_id,
            reason_code="PII_POLICY",
        )

        client = _client_as(role="admin")
        response = client.get("/audit/events", params={"request_id": request_id})

        assert response.status_code == 200
        body = response.json()
        event_types = {item["event_type"] for item in body["items"]}
        assert event_types == {"LOGIN_SUCCESS", "GUARDRAIL_POLICY_DENIED"}
        assert all(item["request_id"] == request_id for item in body["items"])
    finally:
        _cleanup(request_id)


# ---- Detail endpoint ---------------------------------------------------


def test_get_single_event_by_id():
    request_id = _new_request_id()
    try:
        audit_logger.log(AuditEventType.DOCUMENT_DELETE, outcome=AuditOutcome.SUCCESS, request_id=request_id)
        event_id = _row_for(request_id).event_id

        client = _client_as(role="admin")
        response = client.get(f"/audit/events/{event_id}")

        assert response.status_code == 200
        assert response.json()["event_id"] == str(event_id)
    finally:
        _cleanup(request_id)


def test_get_unknown_event_id_returns_404():
    client = _client_as(role="admin")

    response = client.get(f"/audit/events/{uuid.uuid4()}")

    assert response.status_code == 404


# ---- Append-only ---------------------------------------------------------


def test_audit_events_endpoint_rejects_put_and_delete():
    client = _client_as(role="admin")
    event_id = uuid.uuid4()

    assert client.put(f"/audit/events/{event_id}", json={}).status_code == 405
    assert client.delete(f"/audit/events/{event_id}").status_code == 405
