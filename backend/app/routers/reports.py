import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.report import ReportModel

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


@router.get("/reports", response_model=ReportListResponse)
def list_reports(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(ReportModel).order_by(ReportModel.created_at.desc())
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
def download_report(report_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(ReportModel, report_id)
    if row is None:
        raise AppError(404, "report_not_found", f"Report {report_id} not found")
    filename = f"{_sanitize_filename(row.title)}.{row.format}"
    return FileResponse(
        path=row.file_path,
        media_type=_CONTENT_TYPES.get(row.format, "application/octet-stream"),
        filename=filename,
    )
