from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

Base = declarative_base()

_engine = None
_SessionLocal = None
_schema_ready = False


def _dsn() -> str:
    # User/password must be percent-encoded before going into a URL — a raw
    # "@" (or ":", "/", etc.) in either one collides with the DSN's own
    # delimiters. The local dev default password has no such characters, so
    # this was latent until a real managed-Postgres password (e.g.
    # Supabase's, which can contain "@") broke it: the credentials parser
    # split on the wrong "@" and tried to resolve part of the password as
    # the hostname.
    user = quote(settings.postgres_user, safe="")
    password = quote(settings.postgres_password, safe="")
    return (
        f"postgresql+psycopg://{user}:{password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def get_engine():
    global _engine
    if _engine is None:
        connect_args = {
            "connect_timeout": settings.postgres_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.postgres_statement_timeout_ms}",
        }
        if settings.postgres_sslmode:
            connect_args["sslmode"] = settings.postgres_sslmode
        _engine = create_engine(
            _dsn(),
            pool_pre_ping=True,
            pool_size=settings.postgres_pool_size,
            max_overflow=settings.postgres_max_overflow,
            pool_timeout=settings.postgres_pool_timeout_seconds,
            connect_args=connect_args,
        )
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
    import app.models.document  # noqa: F401  (registers DocumentModel on Base.metadata)
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
    import app.models.gateway_usage_log  # noqa: F401  (registers GatewayUsageLogModel on Base.metadata)
    import app.models.role_usage_counter  # noqa: F401  (registers RoleUsageCounterModel on Base.metadata)
    import app.models.project  # noqa: F401  (registers ProjectModel on Base.metadata)
    import app.models.project_member  # noqa: F401  (registers ProjectMemberModel on Base.metadata)
    import app.models.approval_request  # noqa: F401  (registers ApprovalRequestModel on Base.metadata)
    import app.models.employee_pii_record  # noqa: F401  (registers EmployeePIIRecordModel on Base.metadata)

    Base.metadata.create_all(bind=get_engine())
    _run_light_migrations()
    _bootstrap_admin_user()
    _schema_ready = True


def _run_light_migrations() -> None:
    """Additive, idempotent column adds for tables that predate a model change.

    There's no migration framework here (see create_all above) — this covers
    just the gap create_all leaves: it creates missing tables but never alters
    existing ones. Keep this list small and append-only.
    """
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_size_tokens INTEGER",
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS overlap_tokens INTEGER",
        # LLM RBAC (docs/LLM_RBAC_ARCHITECTURE.md) — additive columns only.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS department VARCHAR(64)",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS department VARCHAR(64)",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS access_roles JSONB",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS security_classification VARCHAR(32) NOT NULL DEFAULT 'internal'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS project VARCHAR(256)",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32) NOT NULL DEFAULT 'approved'",
        "CREATE INDEX IF NOT EXISTS ix_documents_department ON documents (department)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS role VARCHAR(32)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS department VARCHAR(64)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(32)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS tool_calls JSONB",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS documents_retrieved JSONB",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS decision VARCHAR(16) NOT NULL DEFAULT 'allowed'",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS denial_reason VARCHAR(256)",
        "CREATE INDEX IF NOT EXISTS ix_gateway_usage_logs_user_id ON gateway_usage_logs (user_id)",
        # Report RBAC (docs/AUDIT_LOGGING.md, docs/KNOWLEDGE_ACCESS_CONTROL.md) —
        # reports previously had no owner/department, so routers/reports.py
        # couldn't scope who may list/download one.
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS department VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_reports_department ON reports (department)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS requested_capability VARCHAR(64)",
        # User-wise report RBAC (docs/USER_WISE_REPORT_RBAC.md) — report-generation audit fields.
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS output_format VARCHAR(8)",
        "ALTER TABLE gateway_usage_logs ADD COLUMN IF NOT EXISTS resource_scope JSONB",
        # Phase 2 evaluation completeness (docs/ARCHITECTURE_ENHANCEMENT_PLAN.md) —
        # citation accuracy, answer relevance, total latency, and token/cost/model
        # read back from gateway_usage_logs for the run's shared request_id.
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS citation_accuracy DOUBLE PRECISION",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS answer_relevance DOUBLE PRECISION",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS total_latency_ms DOUBLE PRECISION",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS tokens_input INTEGER",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS tokens_output INTEGER",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION",
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS model VARCHAR(64)",
        # Phase 3 evaluation gate (docs/RAG_RETRIEVAL.md) — tags which
        # experiment configuration produced a run.
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS experiment_label VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS ix_eval_runs_experiment_label ON eval_runs (experiment_label)",
        # Evaluation architecture correction (docs/RAG_RETRIEVAL.md) — proof
        # trace of what the production retrieval path actually executed.
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS retrieval_trace JSONB",
        # Evaluation Dataset Expansion (docs/RAG_RETRIEVAL.md) — category tags
        # for per-question-type analysis, and a widened description column
        # for expected-answer-criteria/citation-evidence notes.
        "ALTER TABLE eval_queries ADD COLUMN IF NOT EXISTS categories JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE eval_queries ALTER COLUMN description TYPE TEXT",
        # Enterprise permission model (docs on the CEO/Admin role split) — CEO
        # was split out from admin (previously one combined "CEO/Admin" role);
        # self-heals any already-seeded ceoN@mail.com rows (scripts/seed_users.py's
        # own naming convention) that are still on the old combined role, on
        # next boot, idempotently (a no-op once none match).
        r"UPDATE users SET role = 'ceo' WHERE role = 'admin' AND email ~ '^ceo[0-9]+@mail\.com$'",
        # Companion fixup: those rows' display_name was stamped "CEO/Admin N"
        # at original insert time (the old combined role's display name) —
        # relabel to match ceo's new standalone display_name in llm_rbac.yaml.
        r"UPDATE users SET display_name = REPLACE(display_name, 'CEO/Admin', 'CEO') WHERE role = 'ceo' AND display_name LIKE 'CEO/Admin%'",
        # Per-user token limit overrides (routers/users.py PUT
        # /users/{id}/token-limit) — Admin/CEO can cap an individual user's
        # daily/monthly token budget below their role's default. NULL means
        # "use the role default", see services/llm_rbac/engine.py.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_token_limit_override INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_token_limit_override INTEGER",
    ]
    with get_engine().begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _bootstrap_admin_user() -> None:
    """Seeds one admin account from settings.bootstrap_admin_email/_password
    on startup, idempotently (no-op once that email exists). Solves the
    chicken-and-egg problem: PATCH /users/{id} (the only way to grant the
    admin role) itself requires an existing admin, so without this there'd
    be no way to create the first one. No-op when either setting is unset
    (the default)."""
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return

    from app.core.roles import Role
    from app.models.user import UserModel
    from app.services.auth.password import hash_password

    db = _get_sessionmaker()()
    try:
        email = settings.bootstrap_admin_email.strip().lower()
        if db.query(UserModel).filter(UserModel.email == email).one_or_none() is not None:
            return
        db.add(UserModel(email=email, password_hash=hash_password(settings.bootstrap_admin_password), role=Role.ADMIN.value))
        db.commit()
    finally:
        db.close()


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
