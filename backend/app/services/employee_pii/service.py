"""Employee-PII approval domain logic — pure backend business logic, no LLM
anywhere in this file (mirrors services/projects/service.py's shape: the one
place EmployeePIIRecordModel rows are actually written; routers/chat.py and
routers/approvals.py call into it, nothing else does).

A real (unmasked) field value only ever reaches EmployeePIIRecordModel via
apply_decision() below, and only after a human has approved — never from
routers/chat.py's pre-flight branch, which only ever creates the pending
request and shows the requester their own already-masked message back.
"""

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.approval_request import ApprovalRequestModel
from app.models.employee_pii_record import EmployeePIIRecordModel
from app.models.user import UserModel
from app.services.guardrails.pii_intent import EmployeePIIIntent
from app.services.monitoring.metrics import record_guardrail_event

# EmployeePIIRecordModel columns a decider may set via POST
# /approvals/{id}/decide's `values` — deliberately an explicit allowlist
# (not "any key the request body sends"), so a decide call can never target
# an unrelated column (id, status, department, ...) by surprise.
_WRITABLE_FIELDS = ("full_name", "email", "phone", "address", "government_id")


def _get_or_create_placeholder(db: Session, employee_id: str, requester: UserModel) -> EmployeePIIRecordModel:
    record = (
        db.query(EmployeePIIRecordModel).filter(EmployeePIIRecordModel.employee_id == employee_id).first()
    )
    if record is not None:
        return record
    # Placeholder, masked/empty until a human approves — see this module's
    # own docstring and EmployeePIIRecordModel's "pending" status doc.
    # target_id on ApprovalRequestModel is NOT NULL, so even a brand-new
    # employee ("add") needs a real row to point at immediately.
    record = EmployeePIIRecordModel(
        employee_id=employee_id, department=requester.department, status="pending", created_by=requester.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def create_pii_approval_request(db: Session, user: UserModel, intent: EmployeePIIIntent, raw_message: str) -> ApprovalRequestModel:
    """The one place an employee-PII approval request gets created — called
    only from routers/chat.py's pre-flight branch, after
    detect_employee_pii_intent() matched and the caller's role was confirmed
    to hold Permission.MANAGE_EMPLOYEE_PII.

    `raw_message` is stored in `payload` for a decider's eyes only (GET
    /approvals/{id} — Admin/CEO/HR-in-scope, or this request's own
    requester; never the LLM, never the general approvals list) so they can
    read the actual proposed value before confirming it via `values` on
    decide — see this module's docstring for why that's a deliberate design
    choice over auto-parsing a field value out of free text. Everything
    ELSE this function touches (chat history, the immediate response) only
    ever sees `intent.masked_text`.
    """
    record = _get_or_create_placeholder(db, intent.employee_id, user)

    approval = ApprovalRequestModel(
        action=intent.action, target_type="employee_pii", target_id=record.id,
        requested_by=user.id, role=user.role,
        payload={
            "employee_id": intent.employee_id,
            "pii_types": list(intent.pii_types),
            "masked_message": intent.masked_text,
            "raw_message": raw_message,
            "purpose": None,  # no structured "purpose" field exists on a chat message today — best-effort only
            "send_to_llm": False,  # structural for this whole capability — see pii_intent.py's module docstring
            "store_in_db": intent.action in ("add", "modify", "store"),
        },
        status="pending",
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)

    record_guardrail_event(
        "input", "employee_pii_approval_requested", "block",
        f"action={intent.action} employee_id={intent.employee_id} pii_types={','.join(intent.pii_types) or 'none'} "
        f"approval_id={approval.id}",
    )
    return approval


def apply_decision(
    db: Session, approval: ApprovalRequestModel, decision: str, decider: UserModel, values: dict[str, str] | None = None,
) -> ApprovalRequestModel:
    """Called by routers/approvals.py once a target_type="employee_pii"
    ApprovalRequestModel has been decided. The only place a real (unmasked)
    value ever gets written to EmployeePIIRecordModel, or read back out for
    a read/retrieve approval — never routed through the LLM either way.

    Does NOT flip approval.status/decided_by/decided_at/reason — the caller
    (routers/approvals.py::decide_approval(), same as the existing
    project/document branches) owns writing those generic columns after
    this returns, so this function only ever touches the employee_pii-
    specific side effects."""
    record: EmployeePIIRecordModel | None = db.get(EmployeePIIRecordModel, approval.target_id)
    if record is None:
        raise AppError(404, "employee_record_not_found", f"Employee record {approval.target_id} not found")

    payload = dict(approval.payload or {})
    action = approval.action

    if decision == "rejected":
        if record.status == "pending" and action == "add":
            # Nothing was ever exposed or committed — delete the
            # placeholder rather than leaving an orphaned empty row.
            db.delete(record)
            db.commit()
        record_guardrail_event(
            "input", "employee_pii_approval_decided", "rejected",
            f"approval_id={approval.id} action={action} employee_id={payload.get('employee_id')} decided_by={decider.id}",
        )
        return approval

    # approved
    if action in ("add", "modify", "store"):
        for field, value in (values or {}).items():
            if field not in _WRITABLE_FIELDS:
                raise AppError(422, "invalid_field", f"{field!r} is not a writable employee PII field")
            setattr(record, field, value)
        # Recorded into the approval's OWN payload, not just applied to
        # EmployeePIIRecordModel — that target row is mutable and gets
        # overwritten by any later approval, so without this, "what value
        # was approved" has no durable audit trail of its own; only
        # "what's the record's current value right now" would be
        # answerable, and only until the next approval overwrites it.
        payload["approved_values"] = dict(values or {})
        approval.payload = payload
        record.status = "active"
        record.updated_by = decider.id
        db.commit()
    elif action in ("read", "retrieve"):
        # The real value never touches the LLM — stashed directly into this
        # already-privileged, RBAC'd row (GET /approvals/{id}: Admin/CEO/
        # HR-in-scope, or the original requester) for the requester to read
        # back once approved.
        payload["result"] = {f: getattr(record, f) for f in _WRITABLE_FIELDS if getattr(record, f) is not None}
        approval.payload = payload
        db.commit()
    # "other" — approved with nothing further to apply; the audit trail
    # (this event + the ApprovalRequestModel row itself) is the deliverable.

    record_guardrail_event(
        "input", "employee_pii_approval_decided", "approved",
        f"approval_id={approval.id} action={action} employee_id={record.employee_id} decided_by={decider.id}",
    )
    return approval
