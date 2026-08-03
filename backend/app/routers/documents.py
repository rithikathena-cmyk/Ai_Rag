import shutil
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db.postgres import get_db
from app.models.chunk import ChunkModel
from app.models.chunk_term_frequency import ChunkTermFrequencyModel
from app.models.document import Document
from app.models.entity import EntityModel
from app.models.permission import PermissionModel
from app.models.upload_log import UploadLogModel
from app.models.user import UserModel
from app.services.chunking.dispatcher import chunk_document
from app.services.chunking.persistence import build_chunk_rows
from app.services.classification.classifier import classify
from app.services.embedding.model_loader import embed_texts
from app.services.embedding.qdrant_store import delete_document_points, upsert_chunks
from app.services.entities.persistence import build_entity_rows
from app.services.ingestion import storage
from app.services.ingestion.detector import detect_format
from app.services.ingestion.dispatcher import parse_document
from app.services.ingestion.types import DocumentParsingError
from app.services.sparse.service import build_sparse_index, compute_term_frequencies
from app.services.summarization.extractive import summarize

router = APIRouter()


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
    strategy: str
    extra: dict | None
    keywords: list[str] | None


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


def _to_response(row: Document) -> DocumentResponse:
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
    db: Session = Depends(get_db),
):
    previous_doc = None
    if previous_version_of is not None:
        previous_doc = db.get(Document, previous_version_of)
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

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="file_too_large",
            error_message=f"File exceeds {settings.max_upload_size_mb}MB limit",
        )
        raise AppError(413, "file_too_large", f"File exceeds {settings.max_upload_size_mb}MB limit")

    document_id = uuid.uuid4()
    doc_dir = storage.make_document_dir(document_id)
    original_path = storage.save_original(doc_dir, file.filename, content)

    try:
        parsed = await run_in_threadpool(parse_document, original_path, file.filename, file.content_type)
    except DocumentParsingError as exc:
        _log_upload(
            db, document_id=None, filename=file.filename, content_type=file.content_type,
            file_size_bytes=len(content), outcome="rejected", error_code="parsing_failed",
            error_message=str(exc),
        )
        raise AppError(422, "parsing_failed", str(exc))

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
        classification = await run_in_threadpool(classify, fmt, parsed.metadata, parsed.text, file.filename)
        summary_text = await run_in_threadpool(summarize, parsed.text)
        entity_rows = await run_in_threadpool(build_entity_rows, document_id, parsed.text)
        chunks = await run_in_threadpool(chunk_document, parsed, fmt, classification, file_extension)
        vectors = await run_in_threadpool(embed_texts, [c.text for c in chunks])
        term_freqs = await run_in_threadpool(compute_term_frequencies, [c.text for c in chunks])
        chunk_rows = build_chunk_rows(document_id, chunks, settings.embedding_model_name)
        tf_rows, sparse_vectors = build_sparse_index(db, chunk_rows, term_freqs)
    except Exception as exc:
        status = "degraded"
        error_message = f"classification/chunking/embedding failed: {exc}"
        chunk_rows = []
        vectors = []
        tf_rows = []
        sparse_vectors = []
        entity_rows = []

    lineage_id = previous_doc.lineage_id if previous_doc else document_id
    version_number = (previous_doc.version_number + 1) if previous_doc else 1
    previous_version_id = previous_doc.id if previous_doc else None

    row = Document(
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
        try:
            await run_in_threadpool(upsert_chunks, chunk_rows, vectors, sparse_vectors)
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

    return _to_response(row)


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(Document, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
    return _to_response(row)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    row = db.get(Document, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")

    # Postgres cascades chunks/entities/permissions via existing FK
    # ondelete=CASCADE; upload_logs.document_id is ondelete=SET NULL, so the
    # log entry survives as history. Qdrant and on-disk files need explicit
    # cleanup since neither is tied to Postgres by a real FK.
    delete_document_points(document_id)
    if row.storage_dir:
        shutil.rmtree(row.storage_dir, ignore_errors=True)

    db.delete(row)
    db.commit()


@router.post("/documents/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    """Recomputes embeddings and the sparse (BM25) index for a document's existing chunks and
    re-upserts them into Qdrant — e.g. after an embedding model change. Does not re-parse the
    original file or change chunk boundaries; use re-upload for that."""
    row = db.get(Document, document_id)
    if row is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")

    chunk_rows = (
        db.query(ChunkModel)
        .filter(ChunkModel.document_id == document_id)
        .order_by(ChunkModel.chunk_index)
        .all()
    )
    if not chunk_rows:
        raise AppError(422, "no_chunks", "Document has no chunks to reindex")

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
def list_documents(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    query = db.query(Document).order_by(Document.created_at.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return DocumentListResponse(items=[_to_response(r) for r in rows], total=total)


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
def get_document_chunks(document_id: uuid.UUID, limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
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
            strategy=r.strategy,
            extra=r.extra,
            keywords=r.keywords,
        )
        for r in rows
    ]


@router.get("/documents/{document_id}/versions", response_model=DocumentVersionsResponse)
def get_document_versions(document_id: uuid.UUID, db: Session = Depends(get_db)):
    anchor = db.get(Document, document_id)
    if anchor is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
    rows = (
        db.query(Document)
        .filter(Document.lineage_id == anchor.lineage_id)
        .order_by(Document.version_number)
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
def get_document_entities(document_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
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
def grant_permission(document_id: uuid.UUID, body: PermissionGrantRequest, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
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
        row = PermissionModel(document_id=document_id, user_id=body.user_id, permission_level=body.permission_level)
        db.add(row)
    db.commit()
    db.refresh(row)
    return PermissionResponse(
        id=row.id, document_id=row.document_id, user_id=row.user_id,
        permission_level=row.permission_level, granted_by=row.granted_by, created_at=row.created_at,
    )


@router.get("/documents/{document_id}/permissions", response_model=list[PermissionResponse])
def list_permissions(document_id: uuid.UUID, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise AppError(404, "document_not_found", f"Document {document_id} not found")
    rows = db.query(PermissionModel).filter(PermissionModel.document_id == document_id).all()
    return [
        PermissionResponse(
            id=r.id, document_id=r.document_id, user_id=r.user_id,
            permission_level=r.permission_level, granted_by=r.granted_by, created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/documents/{document_id}/permissions/{user_id}", status_code=204)
def revoke_permission(document_id: uuid.UUID, user_id: uuid.UUID, db: Session = Depends(get_db)):
    row = (
        db.query(PermissionModel)
        .filter(PermissionModel.document_id == document_id, PermissionModel.user_id == user_id)
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "permission_not_found", "No permission grant found for this user/document")
    db.delete(row)
    db.commit()
