from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

Base = declarative_base()

_engine = None
_SessionLocal = None
_schema_ready = False


def _dsn() -> str:
    return (
        f"postgresql+psycopg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(_dsn(), pool_pre_ping=True)
    return _engine


def _get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    import app.models.document  # noqa: F401  (registers Document on Base.metadata)
    import app.models.chunk  # noqa: F401  (registers ChunkModel on Base.metadata)
    import app.models.term  # noqa: F401  (registers TermModel on Base.metadata)
    import app.models.chunk_term_frequency  # noqa: F401  (registers ChunkTermFrequencyModel on Base.metadata)
    import app.models.user  # noqa: F401  (registers UserModel on Base.metadata)
    import app.models.permission  # noqa: F401  (registers PermissionModel on Base.metadata)
    import app.models.upload_log  # noqa: F401  (registers UploadLogModel on Base.metadata)
    import app.models.entity  # noqa: F401  (registers EntityModel on Base.metadata)
    import app.models.report  # noqa: F401  (registers ReportModel on Base.metadata)
    import app.models.conversation  # noqa: F401  (registers ConversationModel on Base.metadata)
    import app.models.message  # noqa: F401  (registers MessageModel on Base.metadata)
    import app.models.eval_query  # noqa: F401  (registers EvalQueryModel on Base.metadata)
    import app.models.eval_run  # noqa: F401  (registers EvalRunModel on Base.metadata)

    Base.metadata.create_all(bind=get_engine())
    _run_light_migrations()
    _schema_ready = True


def _run_light_migrations() -> None:
    """Additive, idempotent column adds for tables that predate a model change.

    There's no migration framework here (see create_all above) — this covers
    just the gap create_all leaves: it creates missing tables but never alters
    existing ones. Keep this list small and append-only.
    """
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
    ]
    with get_engine().begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def get_db():
    ensure_schema()
    db = _get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def new_session():
    # For callers that need their own short-lived Session outside the
    # request-scoped get_db() dependency — e.g. one per concurrently
    # executing agent tool call, since Session objects are not thread-safe.
    ensure_schema()
    return _get_sessionmaker()()
