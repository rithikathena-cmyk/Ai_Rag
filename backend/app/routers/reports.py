import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.report import ReportModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.llm_rbac import policy_loader

# Security decision (audited alongside the retrieved-source PII split in
# services/agents/planner.py/services/guardrails/pii.py::DualText — see
# tests/test_reports_rbac.py's module docstring for the full reasoning):
# generated reports are authorized sensitive artifacts, not user-facing chat
# content, and are deliberately NOT run through redact_pii(). A report's data
# was already RBAC-scoped when it was generated (services/llm_rbac/
# report_policy.py::authorize_report()'s row_filter, plus every tool that
# supplies report rows is independently RBAC-filtered at its own source), and
# _visibility_filter() below gates every subsequent read of the artifact
# itself (department + ownership, the same knowledge_departments concept
# resolve_document_ids() uses for documents). Auto-redacting report content
# would defeat the feature for the roles it exists for — an HR
# attendance/employee_summary report is only useful if it shows real
# employee data to an HR user already authorized to see it.
router = APIRouter()

_CONTENT_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class ReportResponse(BaseModel):
    id: uuid.UUID
    title: str
    format: str
    row_count: int
    created_at: datetime


class ReportListResponse(BaseModel):
    items: list[ReportResponse]
    total: int


def _sanitize_filename(title: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")
    return safe or "report"


def _visibility_filter(current_user: UserModel):
    """A report is visible if it predates this feature (department is NULL —
    same opt-in-ACL precedent apply_category_policy() uses for documents:
    nothing currently visible becomes invisible on deploy day), its
    department is one of the caller's role's knowledge_departments, or the
    caller generated it themselves. `None` knowledge_departments (RBAC kill
    switch off) means unrestricted."""
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if knowledge_departments is None:
        return None
    return or_(
        ReportModel.department.is_(None),
        ReportModel.department.in_(knowledge_departments),
        ReportModel.owner_id == current_user.id,
    )


@router.get("/reports", response_model=ReportListResponse)
def list_reports(
    limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
):
    query = db.query(ReportModel)
    condition = _visibility_filter(current_user)
    if condition is not None:
        query = query.filter(condition)
    query = query.order_by(ReportModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return ReportListResponse(
        items=[
            ReportResponse(id=r.id, title=r.title, format=r.format, row_count=r.row_count, created_at=r.created_at)
            for r in rows
        ],
        total=total,
    )


@router.get("/reports/{report_id}/download")
def download_report(
    report_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user)
):
    row = db.get(ReportModel, report_id)
    if row is None:
        raise AppError(404, "report_not_found", f"Report {report_id} not found")

    condition = _visibility_filter(current_user)
    if condition is not None:
        visible = db.query(ReportModel.id).filter(ReportModel.id == report_id, condition).one_or_none()
        if visible is None:
            # 404, not 403 — a caller who can't see this report shouldn't be
            # able to confirm it exists by probing IDs.
            raise AppError(404, "report_not_found", f"Report {report_id} not found")

    filename = f"{_sanitize_filename(row.title)}.{row.format}"
    return FileResponse(
        path=row.file_path,
        media_type=_CONTENT_TYPES.get(row.format, "application/octet-stream"),
        filename=filename,
    )
