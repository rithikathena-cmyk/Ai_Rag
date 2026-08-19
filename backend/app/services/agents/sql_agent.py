from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.guardrails.deterministic.sql_guard import ALLOWED_TABLES, SqlGuardError, validate_select


class SqlAgentError(Exception):
    pass


_DISALLOWED_PREFIX = "Query references table(s) not permitted for analytics: "


def _explain_guard_error(message: str, effective_tables: set[str]) -> str:
    """sql_guard's own message doesn't distinguish "this table isn't
    analytics-queryable at all" (e.g. `users`) from "your role's narrower
    sql_allowed_tables excludes it" (e.g. HR and `documents`) — both come
    back as the same generic "not permitted" wording, which is exactly why
    the planner previously described a permanent, role-based restriction as
    if it were a transient glitch ("isn't coming through right now").
    Rewords to an explicit permission denial only when the rejected table(s)
    would actually have been fine under sql_guard's own full default
    allow-list — i.e. only when a role's narrower list is what's really
    blocking it, not sql_guard's own security floor."""
    if effective_tables is ALLOWED_TABLES or not message.startswith(_DISALLOWED_PREFIX):
        return message
    tables = [t.strip() for t in message[len(_DISALLOWED_PREFIX):].split(",") if t.strip()]
    role_restricted = [t for t in tables if t in ALLOWED_TABLES]
    if not role_restricted:
        return message
    return (
        f"Access denied: your role does not have permission to query "
        f"{', '.join(role_restricted)} for analytics. This is a fixed, role-based "
        f"restriction (not every role can query every table), not a temporary error."
    )


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
    effective_tables = allowed_tables if allowed_tables is not None else ALLOWED_TABLES
    try:
        validated = validate_select(sql, allowed_tables=effective_tables)
    except SqlGuardError as exc:
        raise SqlAgentError(_explain_guard_error(str(exc), effective_tables)) from exc
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
