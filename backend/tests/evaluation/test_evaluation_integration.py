"""Evaluation architecture correction — integration tests
(docs/RAG_RETRIEVAL.md "Evaluation Architecture Correction").

Unlike test_runner.py (which stubs runner._maybe_rewrite_query and
runner.search_documents directly, at the same level run_evaluation() calls
them), these tests leave that orchestration real and mock only the actual
external I/O boundaries underneath it:

    runner.run_evaluation()
      -> planner._maybe_rewrite_query()          [REAL]
           -> query_rewrite.rewrite_query()       [REAL]
                -> claude_gateway.generate()       <- mocked (Anthropic HTTP)
      -> retrieval_agent.search_documents()       [REAL]
           -> search_with_reranking()              <- mocked (Qdrant / BGE-M3)
           -> fetch_parent_context()               [REAL, against a fake DB session]

This is what proves Phase 3A (fetch_parent_context) and Phase 3B
(_maybe_rewrite_query/rewrite_query) actually execute end-to-end during
evaluation rather than a mock standing in for them — the exact gap the
evaluation architecture correction exists to close. See runner.py's own
docstring/comments for the production call chain this mirrors.
"""

import time
import uuid

import anthropic
import httpx
import pytest

from app.core.config import settings
from app.gateway.claude_gateway import GenerationError
from app.gateway.schemas import GenerateResult, TokenUsage
from app.models.chunk import ChunkModel
from app.models.document import DocumentModel
from app.services.agents import planner, retrieval_agent
from app.services.agents.planner import AgentRunResult
from app.services.evaluation import experiments, runner
from app.services.retrieval import query_rewrite
from app.services.retrieval.search import SearchHit

# --------------------------------------------------------------- fixtures ---


class _FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    """Serves the query shapes run_evaluation()'s real call chain issues:
    DocumentModel filenames (search_documents), ChunkModel parent text
    (fetch_parent_context), and GatewayUsageLogModel rows (runner.py
    itself) — routed by which entity was queried, the same dispatch a real
    Session provides. SQLAlchemy mapped-column class attributes are stable
    objects, so `is`-identity dispatch on the first queried entity works."""

    def __init__(self, filenames=None, parent_texts=None):
        self._filenames = filenames or {}
        self._parent_texts = parent_texts or {}
        self.added = None

    def query(self, *entities, **kwargs):
        first = entities[0] if entities else None
        if first is DocumentModel.id:
            return _FakeQueryResult(list(self._filenames.items()))
        if first is ChunkModel.id:
            return _FakeQueryResult(list(self._parent_texts.items()))
        return _FakeQueryResult([])  # GatewayUsageLogModel — no real Claude calls recorded in these tests

    def add(self, obj):
        self.added = obj

    def commit(self):
        pass

    def refresh(self, obj):
        pass


class _FakeEvalQuery:
    def __init__(self, query="How is in-home service handled?", expected_chunk_ids=None):
        self.id = uuid.uuid4()
        self.query = query
        self.expected_chunk_ids = expected_chunk_ids or []


def _hit(chunk_id=None, parent_chunk_id=None, text="the precisely matched sentence", document_id=None):
    return SearchHit(
        chunk_id=chunk_id or uuid.uuid4(), document_id=document_id or uuid.uuid4(), chunk_index=0,
        parent_chunk_id=parent_chunk_id, text=text, strategy="parent_child", score=0.9,
    )


def _rewrite_result(rewritten_query: str, *, model="claude-fast-5") -> GenerateResult:
    return GenerateResult(
        text='{"rewritten_query": "%s"}' % rewritten_query, stop_reason="end_turn",
        usage=TokenUsage(input_tokens=20, output_tokens=8), request_id="r", model=model, latency_ms=5.0,
    )


@pytest.fixture(autouse=True)
def _reset_flags_and_timeout():
    original = (
        settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled,
        settings.query_rewrite_timeout_seconds,
    )
    yield
    (
        settings.parent_child_retrieval_enabled, settings.query_rewriting_enabled,
        settings.query_rewrite_timeout_seconds,
    ) = original


def _mock_generation(monkeypatch, reply="a synthesized answer"):
    monkeypatch.setattr(runner, "run_agent", lambda query, **k: AgentRunResult(reply=reply, sources=[]))
    monkeypatch.setattr(runner, "judge_answer", lambda *a, **k: {
        "groundedness": 0.9, "faithfulness": 0.9, "hallucination_rate": 0.0,
        "citation_accuracy": 0.8, "answer_relevance": 0.8, "notes": "ok",
    })


# ---------------------------------------------------- single source of truth


def test_runner_calls_the_exact_production_functions_not_copies():
    """Regression guard for the bug this task fixes: runner.py must import
    and call the *same* function objects planner.py's search_documents tool
    uses — never a reimplementation or a second retrieval code path."""
    assert runner.search_documents is retrieval_agent.search_documents
    assert runner._maybe_rewrite_query is planner._maybe_rewrite_query
    assert not hasattr(runner, "search_with_reranking")


# ----------------------------------------------------------------- baseline


def test_baseline_real_chain_original_query_no_parent_expansion(monkeypatch):
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)
    child_id = uuid.uuid4()
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: ([_hit(chunk_id=child_id, parent_chunk_id=uuid.uuid4())], True),
    )

    def _unexpected_rewrite(*a, **k):
        raise AssertionError("claude_gateway.generate must not be called when query_rewriting_enabled is False")

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _unexpected_rewrite)

    eq = _FakeEvalQuery(query="How is in-home service handled?")
    run = runner.run_evaluation(_FakeDb(), eq, k=5)

    assert run.retrieval_trace["effective_query"] == eq.query
    assert run.retrieval_trace["rewrite_trace"] is None
    assert run.retrieval_trace["parent_context_chunk_ids"] == []
    assert run.retrieved_chunk_ids == [str(child_id)]


# ------------------------------------------------------------- parent-child


def test_parent_child_real_chain_expands_parent_context(monkeypatch):
    """Proves Phase 3A (fetch_parent_context) executes for real inside
    run_evaluation() when parent_child_retrieval_enabled=True — mocked only
    at the search_with_reranking()/DB-row level, never at
    search_documents()/fetch_parent_context() itself."""
    settings.parent_child_retrieval_enabled = True
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)

    child_id, parent_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: ([_hit(chunk_id=child_id, parent_chunk_id=parent_id, text="In-Home Service")], True),
    )

    eq = _FakeEvalQuery(query="How is in-home service handled?")
    db = _FakeDb(parent_texts={parent_id: "Full In-Home Service section text, much longer than the heading."})
    run = runner.run_evaluation(db, eq, k=5)

    assert run.retrieval_trace["parent_child_retrieval_enabled"] is True
    assert run.retrieval_trace["parent_context_chunk_ids"] == [str(child_id)]
    # citation identity is untouched by parent expansion — only the child chunk is ever the cited id
    assert run.retrieved_chunk_ids == [str(child_id)]


def test_parent_child_missing_parent_row_retrieval_still_succeeds(monkeypatch):
    """fetch_parent_context() silently skips a parent that no longer exists
    (e.g. deleted) — the child's own text still stands on its own and
    evaluation must not fail."""
    settings.parent_child_retrieval_enabled = True
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)

    child_id, missing_parent_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: ([_hit(chunk_id=child_id, parent_chunk_id=missing_parent_id)], True),
    )

    eq = _FakeEvalQuery()
    run = runner.run_evaluation(_FakeDb(parent_texts={}), eq, k=5)  # no row for missing_parent_id

    assert run.retrieval_trace["parent_context_chunk_ids"] == []
    assert run.retrieved_chunk_ids == [str(child_id)]


# -------------------------------------------------------------- query rewrite


def test_query_rewrite_real_chain_rewritten_query_reaches_retrieval(monkeypatch):
    """Proves Phase 3B (_maybe_rewrite_query -> rewrite_query) executes for
    real — only claude_gateway.generate (the Anthropic HTTP boundary) is
    mocked; JSON parsing, RewriteOutcome construction, and the trace-entry
    text are all real query_rewrite.py/planner.py code."""
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = True
    _mock_generation(monkeypatch)

    monkeypatch.setattr(
        query_rewrite.claude_gateway, "generate", lambda req: _rewrite_result("in-home service cost and process")
    )

    search_calls = []
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: (search_calls.append(k["query"]) or [_hit()], True),
    )

    eq = _FakeEvalQuery(query="what about fixing it at my house")
    run = runner.run_evaluation(_FakeDb(), eq, k=5)

    assert search_calls == ["in-home service cost and process"]
    assert run.retrieval_trace["effective_query"] == "in-home service cost and process"
    assert run.retrieval_trace["original_query"] == "what about fixing it at my house"
    assert "rewritten to" in run.retrieval_trace["rewrite_trace"]["summary"]


def test_query_rewrite_real_gateway_failure_falls_back_to_original_query(monkeypatch):
    """query_rewrite.rewrite_query()'s own fallback branch, exercised for
    real (not mocked away) via a real GenerationError raised from the
    Anthropic HTTP boundary."""
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = True
    _mock_generation(monkeypatch)

    def _raise(req):
        raise GenerationError("Error code: 401 - authentication_error")

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _raise)

    search_calls = []
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: (search_calls.append(k["query"]) or [], True),
    )

    eq = _FakeEvalQuery(query="original question")
    run = runner.run_evaluation(_FakeDb(), eq, k=5)

    assert search_calls == ["original question"]
    assert run.retrieval_trace["effective_query"] == "original question"
    assert "kept original query" in run.retrieval_trace["rewrite_trace"]["summary"]
    assert "gateway error" in run.retrieval_trace["rewrite_trace"]["summary"]


def test_query_rewrite_real_timeout_falls_back_to_original_query(monkeypatch):
    """rewrite_query()'s ThreadPoolExecutor timeout path, exercised for real
    with a genuinely slow (mocked) Anthropic call and a shortened timeout."""
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = True
    settings.query_rewrite_timeout_seconds = 0.05
    _mock_generation(monkeypatch)

    def _slow(req):
        time.sleep(0.3)
        return _rewrite_result("too late")

    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", _slow)

    search_calls = []
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: (search_calls.append(k["query"]) or [], True),
    )

    eq = _FakeEvalQuery(query="original question")
    run = runner.run_evaluation(_FakeDb(), eq, k=5)

    assert search_calls == ["original question"]
    assert run.retrieval_trace["effective_query"] == "original question"
    assert "timed out" in run.retrieval_trace["rewrite_trace"]["summary"]


# ----------------------------------------------------------------- combined


def test_combined_real_chain_rewrite_and_parent_expansion_both_execute(monkeypatch):
    settings.parent_child_retrieval_enabled = True
    settings.query_rewriting_enabled = True
    _mock_generation(monkeypatch)

    monkeypatch.setattr(
        query_rewrite.claude_gateway, "generate", lambda req: _rewrite_result("rewritten combined query")
    )

    child_id, parent_id = uuid.uuid4(), uuid.uuid4()
    search_calls = []
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking",
        lambda db, **k: (
            search_calls.append(k["query"]) or [_hit(chunk_id=child_id, parent_chunk_id=parent_id)], True,
        ),
    )

    eq = _FakeEvalQuery(query="original combined question")
    db = _FakeDb(parent_texts={parent_id: "expanded parent text"})
    run = runner.run_evaluation(db, eq, k=5)

    assert search_calls == ["rewritten combined query"]
    assert run.retrieval_trace["effective_query"] == "rewritten combined query"
    assert run.retrieval_trace["parent_context_chunk_ids"] == [str(child_id)]


# ------------------------------------------------------ all four configs via the gate


def test_run_experiment_toggles_flags_through_the_real_retrieval_chain(monkeypatch):
    """experiments.py's _temporary_flags() must actually control what the
    real _maybe_rewrite_query()/search_documents() chain does per
    configuration — not just what settings *say* while a lower-level mock
    ignores them. Covers baseline / parent-child / query-rewrite / combined
    in one pass, mirroring run_gate()."""
    _mock_generation(monkeypatch)
    monkeypatch.setattr(query_rewrite.claude_gateway, "generate", lambda req: _rewrite_result("rewritten"))
    parent_id = uuid.uuid4()
    monkeypatch.setattr(
        retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit(parent_chunk_id=parent_id)], True)
    )

    db = _FakeDb(parent_texts={parent_id: "parent text"})
    eq = _FakeEvalQuery()

    baseline = experiments.run_experiment(db, [eq], experiments.BASELINE, k=5)
    parent_child = experiments.run_experiment(db, [eq], experiments.PARENT_CHILD, k=5)
    rewrite_only = experiments.run_experiment(db, [eq], experiments.QUERY_REWRITE, k=5)
    combined = experiments.run_experiment(db, [eq], experiments.COMBINED, k=5)

    assert baseline.runs[0].retrieval_trace["parent_context_chunk_ids"] == []
    assert baseline.runs[0].retrieval_trace["rewrite_trace"] is None

    assert parent_child.runs[0].retrieval_trace["parent_context_chunk_ids"] != []
    assert parent_child.runs[0].retrieval_trace["rewrite_trace"] is None

    assert rewrite_only.runs[0].retrieval_trace["parent_context_chunk_ids"] == []
    assert rewrite_only.runs[0].retrieval_trace["effective_query"] == "rewritten"

    assert combined.runs[0].retrieval_trace["parent_context_chunk_ids"] != []
    assert combined.runs[0].retrieval_trace["effective_query"] == "rewritten"

    # flags are restored, exactly as experiments.py already guarantees
    assert settings.parent_child_retrieval_enabled is False
    assert settings.query_rewriting_enabled is False


# ------------------------------------------------------------- other failure paths


def test_empty_retrieval_handled_not_an_error(monkeypatch):
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([], True))

    chunk_id = str(uuid.uuid4())
    run = runner.run_evaluation(_FakeDb(), _FakeEvalQuery(expected_chunk_ids=[chunk_id]), k=5)

    assert run.retrieved_chunk_ids == []
    assert run.recall_at_k == 0.0


def test_qdrant_unavailable_raises_clear_evaluation_failure(monkeypatch):
    """Simulates the real condition observed in this session's own
    environment (Qdrant unreachable at 127.0.0.1:6333) deterministically, so
    the test is reproducible regardless of what infrastructure happens to be
    up wherever it runs."""
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)

    def _boom(db, **k):
        raise ConnectionError(
            "[WinError 10061] No connection could be made because the target machine actively refused it"
        )

    monkeypatch.setattr(retrieval_agent, "search_with_reranking", _boom)

    with pytest.raises(runner.EvaluationRetrievalError, match="retrieval failed"):
        runner.run_evaluation(_FakeDb(), _FakeEvalQuery(), k=5)


def test_postgresql_unavailable_raises_clear_evaluation_failure(monkeypatch):
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    _mock_generation(monkeypatch)

    class _BrokenDb(_FakeDb):
        def query(self, *a, **k):
            raise OSError("connection to server failed: Connection refused")

    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([_hit()], True))

    with pytest.raises(runner.EvaluationRetrievalError, match="retrieval failed"):
        runner.run_evaluation(_BrokenDb(), _FakeEvalQuery(), k=5)


def test_claude_configured_real_anthropic_call_generation_metrics_available(monkeypatch):
    """The one test in this module that makes a real network call: uses the
    actual configured ANTHROPIC_API_KEY through the real run_agent()/
    claude_gateway, proving generation is genuinely reachable rather than
    assumed — the model-availability investigation independently verified
    this key/model combination live (models.list() + messages.create() both
    succeeded) before this assertion was written; if it ever regresses, this
    is the test that will catch it. Retrieval is isolated from Qdrant the
    same way as every other test here — this test is specifically about the
    generation stage."""
    if not settings.anthropic_api_key:
        pytest.skip("no ANTHROPIC_API_KEY configured in this environment")
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([], True))

    run = runner.run_evaluation(_FakeDb(), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["generation_available"] is True
    assert not run.generated_answer.startswith("[generation failed:")
    assert experiments.generation_availability([run]) == "Generation evaluation available."


def test_claude_auth_failure_generation_metrics_reported_honestly(monkeypatch):
    """Deterministic counterpart to the real-call test above: forces the
    exact failure a revoked/invalid ANTHROPIC_API_KEY would produce (a real
    anthropic.AuthenticationError raised from the LangChain model's
    .invoke(), the same object services/agents/planner.py::run_agent()
    actually calls) without depending on the environment's key being broken,
    and proves the resulting GenerationError is classified auth_failed and
    reported as an authentication failure — not silently assumed, and not
    the generic "no AI model configured" text a pre-fix version of this
    code would have shown regardless of cause."""
    settings.parent_child_retrieval_enabled = False
    settings.query_rewriting_enabled = False
    monkeypatch.setattr(retrieval_agent, "search_with_reranking", lambda db, **k: ([], True))

    class _FakeBoundModel:
        def invoke(self, messages):
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            resp = httpx.Response(401, request=req, json=body)
            # Match the Anthropic SDK's own str() format for a real 401
            # ("Error code: 401 - {...}") — verified live against the real
            # API with a deliberately invalid key during the model-
            # availability investigation this test documents — rather than
            # a plain custom message, so this test exercises the same
            # substring experiments.py::_looks_like_auth_failure() actually
            # matches against in production, not a hand-picked string.
            raise anthropic.AuthenticationError(f"Error code: 401 - {body}", response=resp, body=body)

    class _FakeLangchainModel:
        def bind_tools(self, tools):
            return _FakeBoundModel()

    monkeypatch.setattr(planner.claude_gateway, "get_langchain_model", lambda **k: _FakeLangchainModel())

    run = runner.run_evaluation(_FakeDb(), _FakeEvalQuery(), k=5)

    assert run.retrieval_trace["generation_available"] is False
    assert run.generated_answer.startswith("[generation failed:")
    assert experiments.generation_availability([run]) == "Generation evaluation unavailable: Anthropic authentication failed."
