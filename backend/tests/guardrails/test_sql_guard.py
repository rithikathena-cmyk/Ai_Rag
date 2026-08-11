import pytest

from app.services.guardrails.deterministic.sql_guard import SqlGuardError, validate_select


def test_allows_simple_select():
    assert validate_select("SELECT * FROM documents") == "SELECT * FROM documents"


def test_allows_join_across_allowed_tables():
    sql = "SELECT d.filename, c.text FROM documents d JOIN chunks c ON c.document_id = d.id"
    assert validate_select(sql) == sql


def test_allows_cte_referencing_an_allowed_table():
    sql = "WITH recent AS (SELECT id FROM documents) SELECT * FROM recent"
    assert validate_select(sql) == sql


def test_blocks_non_select_statements():
    with pytest.raises(SqlGuardError):
        validate_select("DELETE FROM documents")


def test_blocks_multiple_statements():
    with pytest.raises(SqlGuardError):
        validate_select("SELECT * FROM documents; SELECT * FROM chunks")


def test_blocks_table_outside_the_allowlist():
    # e.g. `users` holds password_hash — deliberately not analytics-visible.
    with pytest.raises(SqlGuardError):
        validate_select("SELECT * FROM users")


def test_blocks_forbidden_keyword():
    with pytest.raises(SqlGuardError):
        validate_select("SELECT pg_sleep(5)")


def test_blocks_empty_query():
    with pytest.raises(SqlGuardError):
        validate_select("   ")


# ---------------------------------------------------- role-scoped allowed_tables
# services/llm_rbac/engine.py resolves a per-role table set from
# backend/config/llm_rbac.yaml and passes it here — narrower than the
# module's own ALLOWED_TABLES default, never wider.

def test_role_scoped_allowlist_permits_its_own_tables():
    sql = "SELECT id FROM reports"
    assert validate_select(sql, allowed_tables={"reports"}) == sql


def test_role_scoped_allowlist_blocks_tables_outside_the_narrower_set():
    # `documents` is in the module's own ALLOWED_TABLES default but not in
    # this caller-supplied (e.g. HR's) narrower set.
    with pytest.raises(SqlGuardError):
        validate_select("SELECT id FROM documents", allowed_tables={"reports"})


def test_role_scoped_allowlist_cannot_widen_beyond_forbidden_keywords():
    # Narrowing the table set is not a way to bypass the keyword blocklist.
    with pytest.raises(SqlGuardError):
        validate_select("SELECT pg_sleep(5) FROM reports", allowed_tables={"reports"})
