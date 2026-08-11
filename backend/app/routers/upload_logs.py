import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.permissions import Permission
from app.db.postgres import get_db
from app.models.upload_log import UploadLogModel
from app.services.auth.rbac import require_permission

# Previously had no auth at all — anyone could list ingestion upload/
# rejection history (filenames, error messages) unauthenticated. This is an
# audit trail of who uploaded what and what failed, so it's gated the same
# as the rest of the audit-log surface (VIEW_AUDIT_LOGS).
router = APIRouter(dependencies=[Depends(require_permission(Permission.VIEW_AUDIT_LOGS))])


class UploadLogResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    filename: str | None
    content_type: str | None
    file_size_bytes: int | None
    outcome: str
    error_code: str | None
    error_message: str | None
    created_at: datetime


class UploadLogListResponse(BaseModel):
    items: list[UploadLogResponse]
    total: int


@router.get("/upload-logs", response_model=UploadLogListResponse)
def list_upload_logs(outcome: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(UploadLogModel)
    if outcome is not None:
        query = query.filter(UploadLogModel.outcome == outcome)
    query = query.order_by(UploadLogModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return UploadLogListResponse(
        items=[
            UploadLogResponse(
                id=r.id, document_id=r.document_id, filename=r.filename, content_type=r.content_type,
                file_size_bytes=r.file_size_bytes, outcome=r.outcome, error_code=r.error_code,
                error_message=r.error_message, created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
    )
