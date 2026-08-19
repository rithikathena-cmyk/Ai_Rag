"""PII Access Approval Workflow

Requires manager approval for sensitive PII extraction requests from HR role.
Tracks approval state and audit logs.
"""

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.errors import AppError
from app.models.user import UserModel


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PIIAccessRequest(BaseModel):
    id: uuid.UUID
    requester_id: uuid.UUID
    requester_role: str
    request_type: str  # "employee_contacts", "grievance_reports", etc.
    purpose: str
    scope: str  # "HR documents only", "All documents", etc.
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    approved_by: Optional[uuid.UUID] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


# In-memory storage (would be DB in production)
_PENDING_REQUESTS: dict[uuid.UUID, PIIAccessRequest] = {}
_APPROVED_REQUESTS: dict[tuple[uuid.UUID, str], datetime] = {}  # (user_id, request_type) -> expiry


def request_pii_access(
    user: UserModel,
    request_type: str,
    purpose: str,
    scope: str,
    db: Session
) -> PIIAccessRequest:
    """Create PII access request that requires manager approval.

    Args:
        user: HR user requesting access
        request_type: Type of PII ("employee_contacts", "grievance_reports")
        purpose: Business purpose for access
        scope: What documents/data (e.g., "HR documents only")
        db: Database session

    Returns:
        PIIAccessRequest object (status=PENDING)

    Raises:
        AppError if user is not HR
    """
    if user.role != "hr" and user.role != "admin":
        raise AppError(
            code="pii_access_denied",
            message="Only HR and Admin roles can request PII access"
        )

    if not purpose or len(purpose.strip()) < 10:
        raise AppError(
            code="pii_access_invalid_purpose",
            message="Business purpose required (min 10 characters)"
        )

    request = PIIAccessRequest(
        id=uuid.uuid4(),
        requester_id=user.id,
        requester_role=user.role,
        request_type=request_type,
        purpose=purpose,
        scope=scope,
        status=ApprovalStatus.PENDING,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1)  # 1-hour window
    )

    _PENDING_REQUESTS[request.id] = request

    # Audit log (optional - if audit service available)
    try:
        from app.services.audit import logger as audit_logger
        audit_logger.log_event(
            user_id=user.id,
            event_type="pii_access_requested",
            details={
                "request_id": str(request.id),
                "request_type": request_type,
                "purpose": purpose,
                "scope": scope
            }
        )
    except (ImportError, AttributeError):
        # Audit logging not available, continue without it
        pass

    return request


def check_pii_access(
    user: UserModel,
    request_type: str,
    db: Session
) -> bool:
    """Check if user has approved access to specific PII type.

    Returns True if:
    1. User is Admin (always allowed)
    2. User has recent approved PII access request
    3. Otherwise returns False (requires new approval)
    """
    if user.role == "admin":
        return True

    key = (user.id, request_type)
    if key in _APPROVED_REQUESTS:
        expiry = _APPROVED_REQUESTS[key]
        if datetime.utcnow() < expiry:
            return True
        else:
            # Expired, remove
            del _APPROVED_REQUESTS[key]

    return False


def approve_pii_request(
    request_id: uuid.UUID,
    approved_by: UserModel,
    duration_hours: int = 1
) -> PIIAccessRequest:
    """Approve a PII access request (manager only).

    Args:
        request_id: ID of pending request
        approved_by: Manager/Admin user approving
        duration_hours: How long approval is valid (default 1 hour)

    Returns:
        Updated PIIAccessRequest with status=APPROVED
    """
    if approved_by.role not in ["hr", "admin"]:
        raise AppError(
            code="approval_denied",
            message="Only HR managers and Admins can approve PII access"
        )

    if request_id not in _PENDING_REQUESTS:
        raise AppError(
            code="request_not_found",
            message=f"PII access request {request_id} not found or already processed"
        )

    request = _PENDING_REQUESTS[request_id]

    if request.status != ApprovalStatus.PENDING:
        raise AppError(
            code="request_already_processed",
            message=f"Request already {request.status}"
        )

    # Approve
    request.status = ApprovalStatus.APPROVED
    request.approved_by = approved_by.id
    request.approved_at = datetime.utcnow()

    # Store approval with expiry
    expiry = datetime.utcnow() + timedelta(hours=duration_hours)
    _APPROVED_REQUESTS[(request.requester_id, request.request_type)] = expiry

    # Remove from pending
    del _PENDING_REQUESTS[request_id]

    # Audit log (optional)
    try:
        from app.services.audit import logger as audit_logger
        audit_logger.log_event(
            user_id=approved_by.id,
            event_type="pii_access_approved",
            details={
                "request_id": str(request_id),
                "requester_id": str(request.requester_id),
                "request_type": request.request_type,
                "duration_hours": duration_hours,
                "expires_at": expiry.isoformat()
            }
        )
    except (ImportError, AttributeError):
        pass

    return request


def reject_pii_request(
    request_id: uuid.UUID,
    rejected_by: UserModel,
    reason: str
) -> PIIAccessRequest:
    """Reject a PII access request (manager only).

    Args:
        request_id: ID of pending request
        rejected_by: Manager/Admin user rejecting
        reason: Reason for rejection

    Returns:
        Updated PIIAccessRequest with status=REJECTED
    """
    if rejected_by.role not in ["hr", "admin"]:
        raise AppError(
            code="rejection_denied",
            message="Only HR managers and Admins can reject PII access"
        )

    if request_id not in _PENDING_REQUESTS:
        raise AppError(
            code="request_not_found",
            message=f"PII access request {request_id} not found"
        )

    request = _PENDING_REQUESTS[request_id]

    if request.status != ApprovalStatus.PENDING:
        raise AppError(
            code="request_already_processed",
            message=f"Request already {request.status}"
        )

    # Reject
    request.status = ApprovalStatus.REJECTED
    request.rejection_reason = reason

    # Remove from pending
    del _PENDING_REQUESTS[request_id]

    # Audit log (optional)
    try:
        from app.services.audit import logger as audit_logger
        audit_logger.log_event(
            user_id=rejected_by.id,
            event_type="pii_access_rejected",
            details={
                "request_id": str(request_id),
                "requester_id": str(request.requester_id),
                "request_type": request.request_type,
                "reason": reason
            }
        )
    except (ImportError, AttributeError):
        pass

    return request


def get_pending_requests(manager_id: uuid.UUID) -> list[PIIAccessRequest]:
    """Get all pending PII access requests for a manager to review."""
    return [
        req for req in _PENDING_REQUESTS.values()
        if req.status == ApprovalStatus.PENDING
    ]
