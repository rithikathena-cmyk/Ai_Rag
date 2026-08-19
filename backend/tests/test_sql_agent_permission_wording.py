"""services/agents/sql_agent.py::run_analytics_query() error wording.

Regression test: sql_guard.validate_select() raises the same generic
"not permitted for analytics" message whether a table is disallowed by
sql_guard's own security floor (e.g. `users`, never queryable by anyone) or
by a role's narrower sql_allowed_tables (e.g. HR and `documents`) — live-
verified this made the planner describe a permanent, role-based restriction
as if it were a temporary glitch ("isn't coming through right now") instead
of a permission denial. run_analytics_query() now rewords the message only
in the role-narrowing case, so the model has an unambiguous permission
signal to describe accurately.
"""

import pytest

from app.services.agents.sql_agent import SqlAgentError, run_analytics_query
from app.services.guardrails.deterministic.sql_guard import ALLOWED_TABLES


def test_role_narrowed_table_gets_explicit_permission_wording():
    with pytest.raises(SqlAgentError) as exc_info:
        run_analytics_query(
            db=None, sql="SELECT department, COUNT(*) FROM documents GROUP BY department",
            allowed_tables=frozenset({"conversations", "messages", "eval_queries", "eval_runs", "reports"}),
        )
    message = str(exc_info.value)
    assert "Access denied" in message
    assert "does not have permission" in message
    assert "documents" in message
    assert "not a temporary error" in message


def test_globally_disallowed_table_keeps_the_original_message():
    """`users` is excluded from sql_guard's own ALLOWED_TABLES entirely (it
    holds password_hash) — no role narrowing is involved, so this stays the
    original, generic "not permitted for analytics" wording rather than
    being described as a role-specific restriction it isn't."""
    with pytest.raises(SqlAgentError) as exc_info:
        run_analytics_query(db=None, sql="SELECT email, password_hash FROM users", allowed_tables=None)
    message = str(exc_info.value)
    assert "not permitted for analytics" in message
    assert "Access denied" not in message


def test_admin_ceo_unnarrowed_access_keeps_original_message_shape():
    """allowed_tables=None (CEO/Admin's actual call shape) uses sql_guard's
    own full ALLOWED_TABLES default with no narrowing — a table rejected
    here is rejected for everyone, so the message must not claim it's a
    role-specific restriction."""
    with pytest.raises(SqlAgentError) as exc_info:
        run_analytics_query(db=None, sql="SELECT * FROM some_nonexistent_table", allowed_tables=None)
    assert "Access denied" not in str(exc_info.value)


class _StubSession:
    """A minimal stand-in for a real SQLAlchemy Session: .execute() always
    fails (so the test never depends on a real database), but .rollback()
    is a harmless no-op — matching run_analytics_query()'s own finally
    block, which always calls it regardless of outcome."""

    def execute(self, *a, **k):
        raise RuntimeError("no real database in this test")

    def rollback(self):
        pass


def test_valid_table_for_the_narrowed_role_is_unaffected():
    """Sanity check: this fix only touches the error-wording path — a query
    that's actually within a narrowed role's allow-list clears validation
    normally and fails only on the execution step
    (SqlAgentError("Query execution failed: ...")), never on the guard —
    proving validate_select itself passed for an allowed table."""
    with pytest.raises(SqlAgentError) as exc_info:
        run_analytics_query(
            db=_StubSession(), sql="SELECT COUNT(*) FROM reports",
            allowed_tables=frozenset({"conversations", "messages", "eval_queries", "eval_runs", "reports"}),
        )
    message = str(exc_info.value)
    assert "Query execution failed" in message
    assert "Access denied" not in message
    assert "not permitted for analytics" not in message
