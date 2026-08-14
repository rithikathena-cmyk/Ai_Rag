"""routers/approvals.py's employee_pii extension + services/employee_pii/
service.py — the human approval workflow for employee PII
(docs/GUARDRAILS_ARCHITECTURE.md §14). Same _FakeSession/_FakeQuery
conventions as tests/test_approvals_rbac.py.

tests/guardrails/test_pii_intent.py covers detect_employee_pii_intent()
itself; this file covers everything downstream of a match: request creation,
RBAC-scoped decisions, and the actual DB read/write apply_decision() performs.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.roles import Role
from app.db.postgres import get_db
from app.models.approval_request import ApprovalRequestModel
from app.models.employee_pii_record import EmployeePIIRecordModel
from app.routers import approvals
from app.services.auth.dependencies import get_current_user
from app.services.employee_pii import service as employee_pii_service


class _FakeQuery:
    def __init__(self, items: list):
        self._items = items

    def filter(self, *a, **k):
        return self

    def join(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self._items[0] if self._items else None

    def count(self) -> int:
        return len(self._items)

    def offset(self, n: int):
        return self

    def limit(self, n: int):
        return self

    def all(self) -> list:
        return self._items


class _FakeSession:
    def __init__(self, approval=None, record=None):
        self._approval = approval
        self._record = record
        self.added: list = []
        self.deleted = None
        self.committed = False

    def get(self, model, id_):
        if model is ApprovalRequestModel:
            return self._approval if self._approval is not None and id_ == self._approval.id else None
        if model is EmployeePIIRecordModel:
            return self._record if self._record is not None and id_ == self._record.id else None
        return None

    def query(self, model):
        if model is EmployeePIIRecordModel and self._record is not None:
            return _FakeQuery([self._record])
        if model is ApprovalRequestModel and self._approval is not None:
            return _FakeQuery([self._approval])
        return _FakeQuery([])

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted = obj

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _record(department="hr", status="pending", **fields) -> EmployeePIIRecordModel:
    return EmployeePIIRecordModel(id=uuid.uuid4(), employee_id="EMP001", department=department, status=status, **fields)


def _approval(record, action="modify", status="pending") -> ApprovalRequestModel:
    return ApprovalRequestModel(
        id=uuid.uuid4(), action=action, target_type="employee_pii", target_id=record.id,
        requested_by=uuid.uuid4(), role="hr", status=status, created_at=datetime.now(timezone.utc),
        payload={"employee_id": "EMP001", "pii_types": [], "masked_message": "x", "raw_message": "x"},
    )


def _build_app(approval, record, role: str, department: str = "hr", requester_id=None):
    app = FastAPI()
    app.include_router(approvals.router)
    fake_db = _FakeSession(approval, record)
    app.dependency_overrides[get_db] = lambda: fake_db
    fake_user = SimpleNamespace(id=requester_id or uuid.uuid4(), role=role, department=department, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return TestClient(app), fake_db, fake_user


# --------------------------------------------------------- service layer ---

def test_apply_decision_approved_write_action_writes_confirmed_values():
    record = _record(status="pending")
    approval = _approval(record, action="modify", status="pending")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    employee_pii_service.apply_decision(db, approval, "approved", decider, values={"phone": "555-0100"})

    assert record.phone == "555-0100"
    assert record.status == "active"
    assert record.updated_by == decider.id


def test_apply_decision_rejects_unwritable_field_name():
    record = _record()
    approval = _approval(record, action="modify")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(AppError) as exc_info:
        employee_pii_service.apply_decision(db, approval, "approved", decider, values={"status": "active"})
    assert exc_info.value.status_code == 422


def test_apply_decision_rejected_add_action_deletes_the_placeholder():
    record = _record(status="pending")
    approval = _approval(record, action="add", status="pending")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    employee_pii_service.apply_decision(db, approval, "rejected", decider)

    assert db.deleted is record


def test_apply_decision_rejected_modify_action_leaves_an_existing_record_untouched():
    record = _record(status="active", full_name="Existing Person")
    approval = _approval(record, action="modify", status="pending")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    employee_pii_service.apply_decision(db, approval, "rejected", decider)

    assert db.deleted is None
    assert record.full_name == "Existing Person"


def test_apply_decision_approved_read_action_stashes_the_real_value_in_payload():
    record = _record(status="active", email="real@example.com", phone="555-1234")
    approval = _approval(record, action="read", status="pending")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    employee_pii_service.apply_decision(db, approval, "approved", decider)

    assert approval.payload["result"]["email"] == "real@example.com"
    assert approval.payload["result"]["phone"] == "555-1234"


def test_apply_decision_rejected_read_action_never_exposes_a_result():
    record = _record(status="active", email="real@example.com")
    approval = _approval(record, action="read", status="pending")
    db = _FakeSession(approval, record)
    decider = SimpleNamespace(id=uuid.uuid4())

    employee_pii_service.apply_decision(db, approval, "rejected", decider)

    assert "result" not in approval.payload


# ------------------------------------------------------------ router RBAC ---

def test_hr_can_decide_employee_pii_request_in_own_department():
    record = _record(department="hr", status="active", phone="555-1111")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.HR.value, department="hr")

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved", "values": {"phone": "555-2222"}})

    assert response.status_code == 200
    assert record.phone == "555-2222"


def test_hr_cannot_decide_employee_pii_request_in_other_department():
    record = _record(department="engineering", status="active")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.HR.value, department="hr")

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved", "values": {"phone": "x"}})

    assert response.status_code == 403


def test_hr_cannot_decide_a_project_approval():
    approval = ApprovalRequestModel(
        id=uuid.uuid4(), action="project_submit", target_type="project", target_id=uuid.uuid4(),
        status="pending", created_at=datetime.now(timezone.utc),
    )
    client, db, _ = _build_app(approval, None, Role.HR.value, department="hr")

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved"})

    assert response.status_code == 403


def test_admin_can_decide_employee_pii_request_in_any_department():
    record = _record(department="engineering", status="active")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.ADMIN.value, department=None)

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved", "values": {"phone": "555-9999"}})

    assert response.status_code == 200
    assert record.phone == "555-9999"


def test_other_role_cannot_decide_employee_pii_request_at_all():
    record = _record(department="hr", status="active")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.PROJECT_MANAGER.value)

    response = client.post(f"/approvals/{approval.id}/decide", json={"decision": "approved", "values": {"phone": "x"}})

    assert response.status_code == 403


def test_original_requester_can_view_their_own_pending_request():
    record = _record(department="hr", status="pending")
    approval = _approval(record, action="modify")
    requester_id = uuid.uuid4()
    approval.requested_by = requester_id
    client, db, _ = _build_app(approval, record, Role.USER.value, department=None, requester_id=requester_id)

    response = client.get(f"/approvals/{approval.id}")

    assert response.status_code == 200
    assert response.json()["payload"] is not None


def test_unrelated_user_cannot_view_someone_elses_approval():
    record = _record(department="hr", status="pending")
    approval = _approval(record, action="modify")
    approval.requested_by = uuid.uuid4()
    client, db, _ = _build_app(approval, record, Role.USER.value, department=None, requester_id=uuid.uuid4())

    response = client.get(f"/approvals/{approval.id}")

    assert response.status_code == 403


def test_hr_can_list_approvals_scoped_query_does_not_403():
    """Regression check for the require_role() widening itself — the real
    department-scoped SQL filtering (the .join() in list_approvals) is
    verified live against Postgres, not this fake session's no-op .join()."""
    record = _record(department="hr", status="pending")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.HR.value, department="hr")

    response = client.get("/approvals")

    assert response.status_code == 200


def test_list_response_never_includes_payload():
    record = _record(department="hr", status="pending")
    approval = _approval(record, action="modify")
    client, db, _ = _build_app(approval, record, Role.ADMIN.value)

    response = client.get("/approvals")

    assert response.status_code == 200
    assert all("payload" not in item or item["payload"] is None for item in response.json()["items"])
