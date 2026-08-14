import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base

# "pending" — a placeholder row created the moment an approval request is
# queued (so ApprovalRequestModel.target_id, which is NOT NULL, always has a
# real row to point at, even for an "add new employee" request where no
# record existed before). Fields hold only masked/empty values until a human
# approves. "active" — at least one approval has been granted for this
# employee_id; real values exist. A rejected placeholder is deleted outright
# rather than kept as a third status — see services/employee_pii/service.py.
EMPLOYEE_PII_RECORD_STATUSES = ("pending", "active")


class EmployeePIIRecordModel(Base):
    """The structured store the "update EMP001's phone number"-shaped
    employee-PII approval workflow needs — doesn't exist anywhere else in
    this app; every other PII this codebase handles lives as unstructured
    text inside ingested documents (see services/guardrails/pii.py), not a
    mutable per-employee record. See docs/GUARDRAILS_ARCHITECTURE.md §14 for
    the full request -> approval -> write flow this table is the target of.

    Real (unmasked) field values only ever get written here from
    services/employee_pii/service.py::apply_decision(), and only after a
    human approval — never directly from routers/chat.py's pre-flight
    branch, which only ever sees/writes masked text.
    """

    __tablename__ = "employee_pii_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    government_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Drives HR's approval scope check (routers/approvals.py) — an HR
    # decider may only act on a request whose target record's department
    # matches their own (or is unset). Admin/CEO are unscoped, matching how
    # those two roles are already modeled everywhere else in this RBAC
    # system (see require_permission()'s "*" wildcard convention).
    department: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
