import re

import sqlparse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

# Analytics is scoped to the RAG system's own metadata tables. `users` is
# deliberately excluded — it holds password_hash, and no legitimate analytics
# query needs it.
ALLOWED_TABLES = {
    "documents",
    "chunks",
    "entities",
    "terms",
    "chunk_term_frequencies",
    "upload_logs",
    "permissions",
    "reports",
}

FORBIDDEN_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|call|execute|merge|"
    r"vacuum|reindex|listen|notify|set|reset|into|"
    r"pg_sleep|pg_read_file|pg_read_binary_file|dblink|lo_import|lo_export|"
    r"information_schema|pg_catalog|pg_proc|pg_shadow|pg_authid"
    r")\b",
    re.IGNORECASE,
)

TABLE_REF_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)

# Matches `<name> AS (` — the shape of a CTE definition (`WITH name AS (...)`),
# distinct from a table alias like `FROM documents AS d` where the alias isn't
# immediately followed by an opening paren. Used so CTE names referenced later
# via `FROM <cte_name>` aren't mistaken for a disallowed real table.
CTE_NAME_PATTERN = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)


class SqlAgentError(Exception):
    pass


def _validate_select(sql: str) -> str:
    sql = sql.strip().rstrip(";")
    if not sql:
        raise SqlAgentError("Empty SQL query")

    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise SqlAgentError("Only a single SELECT statement is allowed")

    if statements[0].get_type() != "SELECT":
        raise SqlAgentError("Only SELECT statements are allowed")

    if FORBIDDEN_PATTERN.search(sql):
        raise SqlAgentError("Query contains a disallowed keyword")

    cte_names = {m.group(1).lower() for m in CTE_NAME_PATTERN.finditer(sql)}
    referenced = {m.group(1).lower() for m in TABLE_REF_PATTERN.finditer(sql)}
    disallowed = referenced - ALLOWED_TABLES - cte_names
    if disallowed:
        raise SqlAgentError(f"Query references table(s) not permitted for analytics: {', '.join(sorted(disallowed))}")

    return sql


def run_analytics_query(db: Session, sql: str) -> tuple[list[str], list[list]]:
    """Validates and runs a read-only analytics SELECT, returning (columns, rows).

    Defense in depth: the query is syntax-validated as a single SELECT against
    an allow-listed set of tables, then wrapped in an outer SELECT with a hard
    row cap, then always rolled back after execution (never committed) even
    though it can't mutate anything that passed validation.
    """
    validated = _validate_select(sql)
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
