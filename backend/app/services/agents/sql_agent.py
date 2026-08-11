from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.guardrails.deterministic.sql_guard import ALLOWED_TABLES, SqlGuardError, validate_select


class SqlAgentError(Exception):
    pass


def run_analytics_query(db: Session, sql: str, *, allowed_tables: frozenset[str] | None = None) -> tuple[list[str], list[list]]:
    """Validates and runs a read-only analytics SELECT, returning (columns, rows).

    Defense in depth: the query is syntax-validated as a single SELECT against
    an allow-listed set of tables (services/guardrails/deterministic/sql_guard.py
    — the deterministic execution rail for this tool), then wrapped in an
    outer SELECT with a hard row cap, then always rolled back after execution
    (never committed) even though it can't mutate anything that passed
    validation.

    `allowed_tables`, when supplied, narrows the table allowlist further —
    services/llm_rbac/engine.py resolves this per-role
    (backend/config/llm_rbac.yaml's `sql_allowed_tables`), e.g. so an HR
    request can't reference `documents`/`chunks` internals it has no reason
    to see. `None` (the default, and the only value CEO/Admin ever passes)
    keeps sql_guard's own full ALLOWED_TABLES default — no narrowing.
    """
    try:
        validated = validate_select(sql, allowed_tables=allowed_tables if allowed_tables is not None else ALLOWED_TABLES)
    except SqlGuardError as exc:
        raise SqlAgentError(str(exc)) from exc
    wrapped = f"SELECT * FROM ({validated}) AS _sql_agent_sub LIMIT {settings.sql_agent_row_limit}"
    try:
        result = db.execute(text(wrapped))
        columns = list(result.keys())
        rows = [list(row) for row in result.fetchall()]
    except Exception as exc:
        raise SqlAgentError(f"Query execution failed: {exc}") from exc
    finally:
        db.rollback()
    return columns, rows
