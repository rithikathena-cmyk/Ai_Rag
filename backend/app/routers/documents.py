import asyncio
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.permissions import Permission
from app.db.postgres import get_db
from app.gateway.usage_tracker import record_denied
from app.models.approval_request import ApprovalRequestModel
from app.models.chunk import ChunkModel
from app.models.chunk_term_frequency import ChunkTermFrequencyModel
from app.models.document import DocumentModel
from app.models.entity import EntityModel
from app.models.permission import PermissionModel
from app.models.upload_log import UploadLogModel
from app.models.user import UserModel
from app.services.auth.dependencies import get_current_user
from app.services.auth.rbac import require_permission
from app.services.chunking import text_utils
from app.services.chunking.dispatcher import chunk_document
from app.services.chunking.persistence import build_chunk_rows
from app.services.embedding.model_loader import embed_texts
from app.services.embedding.qdrant_store import delete_document_points, upsert_chunks
from app.services.entities.persistence import build_entity_rows
from app.services.guardrails.retrieval_permissions import filter_by_category
from app.services.ingestion import storage
from app.services.ingestion.detector import detect_format
from app.services.ingestion.dispatcher import parse_document
from app.services.ingestion.types import DocumentParsingError
from app.services.ingestion.upload_validation import validate_mime
from app.services.llm_rbac import policy_loader
from app.services.llm_rbac.engine import authorize_llm_request
from app.services.monitoring import progress
from app.services.monitoring.metrics import record_ingestion_metrics
from app.services.sparse.service import build_sparse_index, compute_term_frequencies
from app.services.summarization.extractive import summarize

router = APIRouter()


def _chunk_with_timing(parsed, fmt, file_extension):
    # reset/read must happen on the SAME thread that runs chunk_document —
    # the tokenize timer is thread-local, and run_in_threadpool hands this
    # whole function to one worker thread as a unit, so bundling reset+call+
    # read together here (rather than around the run_in_threadpool call from
    # the caller's thread) is what makes the measurement land on the right thread.
    text_utils.reset_tokenize_timer()
    chunks = chunk_document(parsed, fmt, file_extension)
    return chunks, text_utils.get_tokenize_time_ms()


class DocumentMetadataResponse(BaseModel):
    title: str | None
    author: str | None
    creation_date: datetime | None
    modified_date: datetime | None
    language: str | None
    page_count: int | None
    headings: list[str]
    keywords: list[str]
    table_count: int
    image_count: int


class DocumentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    document_type: str
    file_size_bytes: int
    status: str
    error_message: str | None
    classification: str | None
    classification_confidence: float | None
    classification_method: str | None
    chunk_count: int
    summary: str | None
    lineage_id: uuid.UUID
    version_number: int
    previous_version_id: uuid.UUID | None
    is_latest_version: bool
    metadata: DocumentMetadataResponse
    created_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int


class ChunkResponse(BaseModel):
    id: uuid.UUID
    chunk_index: int
    parent_chunk_id: uuid.UUID | None
    text: str
    token_count: int
    chunk_size_tokens: int | None
    overlap_tokens: int | None
    strategy: str
    extra: dict | None
    keywords: list[str] | None
    qdrant_point_id: str | None
    embedding_model: str | None


class DocumentVersionSummary(BaseModel):
    id: uuid.UUID
    version_number: int
    filename: str
    status: str
    is_latest_version: bool
    created_at: datetime


class DocumentVersionsResponse(BaseModel):
    lineage_id: uuid.UUID
    versions: list[DocumentVersionSummary]


class PermissionGrantRequest(BaseModel):
    user_id: uuid.UUID
    permission_level: Literal["read", "write", "admin"]


class PermissionResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    user_id: uuid.UUID
    permission_level: str
    granted_by: uuid.UUID | None
    created_at: datetime


class EntityResponse(BaseModel):
    entity_text: str
    entity_label: str
    mention_count: int


def _to_response(row: DocumentModel) -> DocumentResponse:
    return DocumentResponse(
        id=row.id,
        filename=row.filename,
        document_type=row.document_type,
        file_size_bytes=row.file_size_bytes,
        status=row.status,
        error_message=row.error_message,
        classification=row.classification,
        classification_confidence=row.classification_confidence,
        classification_method=row.classification_method,
        chunk_count=row.chunk_count,
        summary=row.summary,
        lineage_id=row.lineage_id,
        version_number=row.version_number,
        previous_version_id=row.previous_version_id,
        is_latest_version=row.is_latest_version,
        metadata=DocumentMetadataResponse(
            title=row.title,
            author=row.author,
            creation_date=row.doc_creation_date,
            modified_date=row.doc_modified_date,
            language=row.language,
            page_count=row.page_count,
            headings=row.headings or [],
            keywords=row.keywords or [],
            table_count=row.table_count,
            image_count=row.image_count,
        ),
        created_at=row.created_at,
    )


def _log_upload(
    db: Session,
    *,
    document_id: uuid.UUID | None,
    filename: str | None,
    content_type: str | None,
    file_size_bytes: int | None,
    outcome: str,
    error_code: str | None,
    error_message: str | None,
) -> None:
    # A logging failure must never break the actual upload response.
    try:
        db.add(
            UploadLogModel(
                document_id=document_id,
                filename=filename,
                content_type=content_type,
                file_size_bytes=file_size_bytes,
                outcome=outcome,
                error_code=error_code,
                error_message=error_message,
            )
        )
        db.commit()
    except Exception:
        db.rollback()


@router.post("/documents/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    previous_version_of: uuid.UUID | None = Form(None),
    client_document_id: uuid.UUID | None = Form(None),
    department: str | None = Form(None),
    project: str | None = Form(None),
    security_classification: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.UPLOAD_DOCUMENTS)),
):
    # LLM RBAC gate — same pattern as routers/chat.py: a denial here is
    # itself an auditable event, and must happen before any parsing/storage
    # work starts. Employee's llm_rbac.yaml entry denies "upload_documents"
    # explicitly; HR/Project Manager/CEO/Admin allow it. require_permission
    # above is the coarse REST-permission gate (403 before this even runs,
    # no audit row); this inline check is the fine-grained, audited one —
    # both should always agree since rbac_permissions and permissions.allow/
    # deny are kept in sync per role, but the coarse gate is cheaper (no
    # Postgres round-trip) so it runs first.
    try:
        decision = authorize_llm_request(db, current_user, endpoint="documents", action="upload_documents")
    except AppError as exc:
        record_denied(
            agent_name="documents_upload", user_id=current_user.id, role=current_user.role,
            department=current_user.department, denial_reason=str(exc.detail),
            requested_capability="upload_documents",
        )
        raise

    # A caller may leave department unset — default to the uploader's own
    # resolved department so apply_category_policy() has something to filter
    # on immediately, rather than every upload landing in the
    # visible-to-everyone NULL bucket (docs/KNOWLEDGE_ACCESS_CONTROL.md §5).
    resolved_department = department or decision.department

    previous_doc = None
    if previous_version_of is not None:
        previous_doc = db.get(DocumentModel, previous_version_of)
        if previous_doc is None:
            _log_upload(
                db, document_id=None, filename=file.filename, content_type=file.content_type,
                file_size_bytes=None, outcome="rejected", error_code="previous_version_not_found",
                error_message=f"No document found with id {previous_version_of}",
            )
            raise AppError(404, "previous_version_not_found", f"No document found with id {previous_version_of}")

    fmt = detect_format(file.filename, file.content_type) if file.filename else None
    if fmt is None:
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=None, outcome="rejected", error_code="unsupported_format",
            error_message=f"Unsupported file type: {file.filename}",
        )
        raise AppError(415, "unsupported_format", f"Unsupported file type: {file.filename}")

    content = await file.read()
    if not content:
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=0, outcome="rejected", error_code="empty_file",
            error_message="Uploaded file is empty",
        )
        raise AppError(400, "empty_file", "Uploaded file is empty")

    if settings.upload_mime_check_enabled and not validate_mime(fmt, content):
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="mime_mismatch",
            error_message=f"File content doesn't match its extension ({file.filename})",
        )
        raise AppError(415, "mime_mismatch", f"File content doesn't match its extension ({file.filename})")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="file_too_large",
            error_message=f"File exceeds {settings.max_upload_size_mb}MB limit",
        )
        raise AppError(413, "file_too_large", f"File exceeds {settings.max_upload_size_mb}MB limit")

    document_id = client_document_id or uuid.uuid4()
    doc_id_str = str(document_id)
    doc_dir = storage.make_document_dir(document_id)
    original_path = storage.save_original(doc_dir, file.filename, content)

    ingestion_stages: dict[str, float] = {}
    progress.start(doc_id_str, file.filename)

    async def _timed(key: str, fn, *args, timeout: float | None = None):
        stage = key.removesuffix("_ms")
        progress.begin_stage(doc_id_str, stage)
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(run_in_threadpool(fn, *args), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"{key} exceeded {timeout}s timeout") from None
        elapsed = (time.perf_counter() - start) * 1000
        ingestion_stages[key] = elapsed
        progress.end_stage(doc_id_str, stage, elapsed)
        return result

    try:
        parsed = await _timed(
            "parse_ms", parse_document, original_path, file.filename, file.content_type,
            timeout=settings.parse_timeout_seconds,
        )
    except DocumentParsingError as exc:
        progress.finish(doc_id_str, "failed")
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="parsing_failed",
            error_message=str(exc),
        )
        raise AppError(422, "parsing_failed", str(exc))
    except TimeoutError as exc:
        progress.finish(doc_id_str, "failed")
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="parsing_timeout",
            error_message=str(exc),
        )
        raise AppError(422, "parsing_timeout", str(exc))

    text_path = storage.save_text(doc_dir, parsed.text)
    tables_dir = storage.save_tables(doc_dir, parsed.tables)
    images_dir = storage.save_images(doc_dir, parsed.images)
    metadata_path = storage.save_metadata_json(doc_dir, parsed.metadata)

    file_extension = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""

    status = "completed"
    error_message = None
    classification = None
    chunk_rows: list[ChunkModel] = []
    tf_rows = []
    sparse_vectors = []
    entity_rows = []
    summary_text = None

    try:
        summary_text = await _timed("summarize_ms", summarize, parsed.text)
        entity_rows = await _timed("entity_ms", build_entity_rows, document_id, parsed.text)

        chunk_start = time.perf_counter()
        progress.begin_stage(doc_id_str, "chunk")
        try:
            chunks, tokenize_ms = await asyncio.wait_for(
                run_in_threadpool(_chunk_with_timing, parsed, fmt, file_extension),
                timeout=settings.chunk_timeout_seconds,
            )
        except TimeoutError:
            raise TimeoutError(f"chunking exceeded {settings.chunk_timeout_seconds}s timeout") from None
        chunk_elapsed = (time.perf_counter() - chunk_start) * 1000
        ingestion_stages["chunk_ms"] = chunk_elapsed
        ingestion_stages["tokenize_ms"] = tokenize_ms
        progress.end_stage(doc_id_str, "chunk", chunk_elapsed)

        vectors = await _timed(
            "embed_ms", embed_texts, [c.text for c in chunks], timeout=settings.embed_timeout_seconds
        )
        term_freqs = await _timed("sparse_ms", compute_term_frequencies, [c.text for c in chunks])
        chunk_rows = build_chunk_rows(document_id, chunks, settings.embedding_model_name)

        sparse_index_start = time.perf_counter()
        progress.begin_stage(doc_id_str, "sparse_index")
        tf_rows, sparse_vectors = build_sparse_index(db, chunk_rows, term_freqs)
        sparse_index_elapsed = (time.perf_counter() - sparse_index_start) * 1000
        ingestion_stages["sparse_index_ms"] = sparse_index_elapsed
        progress.end_stage(doc_id_str, "sparse_index", sparse_index_elapsed)
    except Exception as exc:
        status = "degraded"
        error_message = f"chunking/embedding failed: {exc}"
        chunk_rows = []
        vectors = []
        tf_rows = []
        sparse_vectors = []
        entity_rows = []

    lineage_id = previous_doc.lineage_id if previous_doc else document_id
    version_number = (previous_doc.version_number + 1) if previous_doc else 1
    previous_version_id = previous_doc.id if previous_doc else None

    row = DocumentModel(
        id=document_id,
        filename=file.filename,
        file_extension=file_extension,
        mime_type=file.content_type,
        document_type=parsed.metadata.document_type,
        file_size_bytes=len(content),
        status=status,
        error_message=error_message,
        storage_dir=str(doc_dir),
        original_file_path=str(original_path),
        text_file_path=str(text_path),
        tables_dir_path=str(tables_dir) if tables_dir else None,
        images_dir_path=str(images_dir) if images_dir else None,
        metadata_file_path=str(metadata_path),
        title=parsed.metadata.title,
        author=parsed.metadata.author,
        doc_creation_date=parsed.metadata.creation_date,
        doc_modified_date=parsed.metadata.modified_date,
        language=parsed.metadata.language,
        page_count=parsed.metadata.page_count,
        headings=parsed.metadata.headings,
        keywords=parsed.metadata.keywords,
        table_count=len(parsed.tables),
        image_count=len(parsed.images),
        classification=classification.label if classification else None,
        classification_confidence=classification.confidence if classification else None,
        classification_method=classification.method if classification else None,
        chunk_count=len(chunk_rows),
        summary=summary_text,
        lineage_id=lineage_id,
        version_number=version_number,
        previous_version_id=previous_version_id,
        is_latest_version=True,
        department=resolved_department,
        project=project,
        security_classification=security_classification or "internal",
        owner_id=current_user.id,
    )
    db.add(row)
    db.flush()
    if previous_doc is not None:
        previous_doc.is_latest_version = False
        db.add(previous_doc)
    if chunk_rows:
        db.add_all(chunk_rows)
        db.flush()
        if tf_rows:
            db.add_all(tf_rows)
    if entity_rows:
        db.add_all(entity_rows)
    db.commit()
    db.refresh(row)

    if chunk_rows:
        upsert_start = time.perf_counter()
        progress.begin_stage(doc_id_str, "qdrant_upsert")
        try:
            await run_in_threadpool(upsert_chunks, chunk_rows, vectors, sparse_vectors)
            progress.end_stage(doc_id_str, "qdrant_upsert", (time.perf_counter() - upsert_start) * 1000)
        except Exception as exc:
            row.status = "degraded"
            row.error_message = f"qdrant upsert failed: {exc}"
            db.commit()
            db.refresh(row)

    _log_upload(
        db, document_id=row.id, filename=file.filename, content_type=file.content_type,
        file_size_bytes=len(content), outcome="success" if row.status == "completed" else "degraded",
        error_code=None, error_message=row.error_message,
    )

    ingestion_stages["total_ms"] = sum(ingestion_stages.values())
    record_ingestion_metrics(file.filename, ingestion_stages, chunk_count=len(chunk_rows))
    progress.finish(doc_id_str, row.status)

    return _to_response(row)


class IngestionStageProgress(BaseModel):
    status: str
    elapsed_ms: float | None


class IngestionProgressResponse(BaseModel):
    filename: str
    status: str
    current_stage: str | None
    started_at: float
    stages: dict[str, IngestionStageProgress]


@router.get("/documents/{document_id}/progress", response_model=IngestionProgressResponse)
def get_ingestion_progress(document_id: uuid.UUID, current_user: UserModel = Depends(get_current_user)):
    data = progress.get(str(document_id))
    if data is None:
        raise AppError(404, "progress_not_found", "No ingestion progress recorded for this document")
    return data


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    row = db.get(DocumentModel, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if not filter_by_category(db, [document_id], current_user.role, knowledge_departments):
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    return _to_response(row)


class DocumentTextResponse(BaseModel):
    text: str


@router.get("/documents/{document_id}/text", response_model=DocumentTextResponse)
def get_document_text(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    row = db.get(DocumentModel, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if not filter_by_category(db, [document_id], current_user.role, knowledge_departments):
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    if not row.text_file_path:
        raise AppError(404, "text_not_found", "No parsed text stored for this document")
    try:
        text = Path(row.text_file_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError(404, "text_not_found", f"Parsed text file is missing: {exc}")
    return DocumentTextResponse(text=text)


def delete_document_row(db: Session, document_id: uuid.UUID) -> None:
    """The actual deletion, shared by delete_document() below (immediate
    delete, when no approval is required) and routers/approvals.py's decide
    endpoint (deferred delete, once a pending ApprovalRequestModel targeting
    this document is approved)."""
    row = db.get(DocumentModel, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")

    # Postgres cascades chunks/entities/permissions via existing FK
    # ondelete=CASCADE; upload_logs.document_id is ondelete=SET NULL, so the
    # log entry survives as history. Qdrant and on-disk files need explicit
    # cleanup since neither is tied to Postgres by a real FK.
    delete_document_points(document_id)
    if row.storage_dir:
        shutil.rmtree(row.storage_dir, ignore_errors=True)

    db.delete(row)
    db.commit()


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.DELETE_DOCUMENTS)),
):
    try:
        decision = authorize_llm_request(db, current_user, endpoint="documents", action="delete_documents")
    except AppError as exc:
        record_denied(
            agent_name="documents_delete", user_id=current_user.id, role=current_user.role,
            department=current_user.department, denial_reason=str(exc.detail),
            requested_capability="delete_documents",
        )
        raise

    if db.get(DocumentModel, document_id) is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")

    if decision.requires_approval:
        # llm_rbac.yaml marks this role's delete as approval-gated (currently
        # only project_manager) — queue a real ApprovalRequestModel a CEO/
        # Admin can approve/reject via POST /approvals/{id}/decide
        # (routers/approvals.py), rather than either silently allowing or
        # hard-blocking with no way to ever proceed.
        approval = ApprovalRequestModel(
            action="delete_document", target_type="document", target_id=document_id,
            requested_by=current_user.id, role=current_user.role, status="pending",
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return JSONResponse(
            status_code=202,
            content={"approval_request_id": str(approval.id), "status": "pending"},
        )

    delete_document_row(db, document_id)
    return Response(status_code=204)


@router.post("/documents/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.MANAGE_DOCUMENTS)),
):
    """Recomputes embeddings and the sparse (BM25) index for a document's existing chunks and
    re-upserts them into Qdrant — e.g. after an embedding model change. Does not re-parse the
    original file or change chunk boundaries; use re-upload for that.

    Previously had no auth beyond get_current_user at all (any authenticated
    user could reindex any document) — now requires MANAGE_DOCUMENTS."""
    row = db.get(DocumentModel, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")

    chunk_rows = (
        db.query(ChunkModel)
        .filter(ChunkModel.document_id == document_id)
        .order_by(ChunkModel.chunk_index)
        .all()
    )
    if not chunk_rows:
        raise AppError(422, "no_chunks", "DocumentModel has no chunks to reindex")

    texts = [c.text for c in chunk_rows]
    try:
        vectors = await run_in_threadpool(embed_texts, texts)
        term_freqs = await run_in_threadpool(compute_term_frequencies, texts)
    except Exception as exc:
        raise AppError(502, "reindex_failed", f"Re-embedding failed: {exc}")

    db.query(ChunkTermFrequencyModel).filter(
        ChunkTermFrequencyModel.chunk_id.in_([c.id for c in chunk_rows])
    ).delete(synchronize_session=False)
    tf_rows, sparse_vectors = build_sparse_index(db, chunk_rows, term_freqs)
    if tf_rows:
        db.add_all(tf_rows)

    row.status = "completed"
    row.error_message = None
    db.commit()
    db.refresh(row)

    try:
        await run_in_threadpool(upsert_chunks, chunk_rows, vectors, sparse_vectors)
    except Exception as exc:
        row.status = "degraded"
        row.error_message = f"qdrant upsert failed: {exc}"
        db.commit()
        db.refresh(row)

    return _to_response(row)


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    limit: int = 50, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if knowledge_departments is None:
        query = db.query(DocumentModel)
    else:
        visible_ids = filter_by_category(db, None, current_user.role, knowledge_departments)
        query = db.query(DocumentModel).filter(DocumentModel.id.in_(visible_ids))
    query = query.order_by(DocumentModel.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return DocumentListResponse(items=[_to_response(r) for r in rows], total=total)


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
def get_document_chunks(
    document_id: uuid.UUID, limit: int = 200, offset: int = 0, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    if db.get(DocumentModel, document_id) is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if not filter_by_category(db, [document_id], current_user.role, knowledge_departments):
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    rows = (
        db.query(ChunkModel)
        .filter(ChunkModel.document_id == document_id)
        .order_by(ChunkModel.chunk_index)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        ChunkResponse(
            id=r.id,
            chunk_index=r.chunk_index,
            parent_chunk_id=r.parent_chunk_id,
            text=r.text,
            token_count=r.token_count,
            chunk_size_tokens=r.chunk_size_tokens,
            overlap_tokens=r.overlap_tokens,
            strategy=r.strategy,
            extra=r.extra,
            keywords=r.keywords,
            qdrant_point_id=r.qdrant_point_id,
            embedding_model=r.embedding_model,
        )
        for r in rows
    ]


@router.get("/documents/{document_id}/versions", response_model=DocumentVersionsResponse)
def get_document_versions(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    anchor = db.get(DocumentModel, document_id)
    if anchor is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    # Previously had no visibility check at all, unlike every sibling GET
    # route above — a role outside this document's department could see its
    # full version lineage. Now consistent with get_document/get_document_text.
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if not filter_by_category(db, [document_id], current_user.role, knowledge_departments):
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    rows = (
        db.query(DocumentModel)
        .filter(DocumentModel.lineage_id == anchor.lineage_id)
        .order_by(DocumentModel.version_number)
        .all()
    )
    return DocumentVersionsResponse(
        lineage_id=anchor.lineage_id,
        versions=[
            DocumentVersionSummary(
                id=r.id, version_number=r.version_number, filename=r.filename,
                status=r.status, is_latest_version=r.is_latest_version, created_at=r.created_at,
            )
            for r in rows
        ],
    )


@router.get("/documents/{document_id}/entities", response_model=list[EntityResponse])
def get_document_entities(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.VIEW_DOCUMENTS)),
):
    if db.get(DocumentModel, document_id) is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    # Same gap as /versions above — add the visibility check every sibling
    # GET route already has.
    knowledge_departments = policy_loader.knowledge_departments_for(current_user.role)
    if not filter_by_category(db, [document_id], current_user.role, knowledge_departments):
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    rows = (
        db.query(EntityModel)
        .filter(EntityModel.document_id == document_id)
        .order_by(EntityModel.mention_count.desc())
        .all()
    )
    return [
        EntityResponse(entity_text=r.entity_text, entity_label=r.entity_label, mention_count=r.mention_count)
        for r in rows
    ]


@router.post("/documents/{document_id}/permissions", response_model=PermissionResponse, status_code=201)
def grant_permission(
    document_id: uuid.UUID, body: PermissionGrantRequest, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.MANAGE_DOCUMENTS)),
):
    # Previously had no auth beyond get_current_user — any authenticated
    # user could grant/list/revoke document permissions for anyone, on any
    # document. Granting access is itself a privileged action.
    if db.get(DocumentModel, document_id) is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    if db.get(UserModel, body.user_id) is None:
        raise AppError(404, "user_not_found", f"User {body.user_id} not found")

    existing = (
        db.query(PermissionModel)
        .filter(PermissionModel.document_id == document_id, PermissionModel.user_id == body.user_id)
        .one_or_none()
    )
    if existing is not None:
        existing.permission_level = body.permission_level
        row = existing
    else:
        row = PermissionModel(
            document_id=document_id, user_id=body.user_id, permission_level=body.permission_level,
            granted_by=current_user.id,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return PermissionResponse(
        id=row.id, document_id=row.document_id, user_id=row.user_id,
        permission_level=row.permission_level, granted_by=row.granted_by, created_at=row.created_at,
    )


@router.get("/documents/{document_id}/permissions", response_model=list[PermissionResponse])
def list_permissions(
    document_id: uuid.UUID, db: Session = Depends(get_db), current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.MANAGE_DOCUMENTS)),
):
    if db.get(DocumentModel, document_id) is None:
        raise AppError(404, "document_not_found", f"DocumentModel {document_id} not found")
    rows = db.query(PermissionModel).filter(PermissionModel.document_id == document_id).all()
    return [
        PermissionResponse(
            id=r.id, document_id=r.document_id, user_id=r.user_id,
            permission_level=r.permission_level, granted_by=r.granted_by, created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/documents/{document_id}/permissions/{user_id}", status_code=204)
def revoke_permission(
    document_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_user),
    _permission: UserModel = Depends(require_permission(Permission.MANAGE_DOCUMENTS)),
):
    row = (
        db.query(PermissionModel)
        .filter(PermissionModel.document_id == document_id, PermissionModel.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "permission_not_found", "No permission grant found for this user/document")
    db.delete(row)
    db.commit()
