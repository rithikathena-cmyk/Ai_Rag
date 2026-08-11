import re

import sqlparse

NAME = "sql_guard"

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


class SqlGuardError(Exception):
    pass


def validate_select(sql: str, *, allowed_tables: set[str] = ALLOWED_TABLES) -> str:
    """Deterministic execution-rail check for read-only analytics SQL:
    single SELECT statement, no DDL/DML/superuser-function keywords, and
    every referenced table (other than a query's own CTEs) must be in
    `allowed_tables`. Raises SqlGuardError on any violation; returns the
    (stripped) validated SQL on success."""
    sql = sql.strip().rstrip(";")
    if not sql:
        raise SqlGuardError("Empty SQL query")

    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise SqlGuardError("Only a single SELECT statement is allowed")

    if statements[0].get_type() != "SELECT":
        raise SqlGuardError("Only SELECT statements are allowed")

    if FORBIDDEN_PATTERN.search(sql):
        raise SqlGuardError("Query contains a disallowed keyword")

    cte_names = {m.group(1).lower() for m in CTE_NAME_PATTERN.finditer(sql)}
    referenced = {m.group(1).lower() for m in TABLE_REF_PATTERN.finditer(sql)}
    disallowed = referenced - allowed_tables - cte_names
    if disallowed:
        raise SqlGuardError(f"Query references table(s) not permitted for analytics: {', '.join(sorted(disallowed))}")

    return sql
