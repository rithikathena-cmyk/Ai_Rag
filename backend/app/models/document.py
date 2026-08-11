import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class DocumentModel(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    storage_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    text_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tables_dir_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    images_dir_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    metadata_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(512), nullable=True)
    doc_creation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    doc_modified_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    is_latest_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # LLM-RBAC access-control fields. `department` + `access_roles` drive
    # apply_category_policy() (services/guardrails/retrieval_permissions.py);
    # `security_classification` is deliberately distinct from `classification`
    # above, which is a content-taxonomy label, not an access-control one —
    # see docs/KNOWLEDGE_ACCESS_CONTROL.md.
    department: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    access_roles: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    security_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal", server_default="internal"
    )
    project: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="approved", server_default="approved"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
