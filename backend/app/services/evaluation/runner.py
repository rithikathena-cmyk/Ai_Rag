import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.eval_query import EvalQueryModel
from app.models.eval_run import EvalRunModel
from app.models.gateway_usage_log import GatewayUsageLogModel
from app.services.agents.planner import _maybe_rewrite_query, run_agent
from app.services.agents.retrieval_agent import search_documents
from app.services.evaluation.generation_judge import JudgeError, judge_answer
from app.services.evaluation.retrieval_metrics import mean_reciprocal_rank, ndcg_at_k, precision_at_k, recall_at_k
from app.services.guardrails.pii import redact_pii
from app.services.monitoring.metrics import record_latency


class EvaluationRetrievalError(Exception):
    """Raised when the production retrieval path itself fails to run at all
    (Qdrant unreachable, PostgreSQL unreachable, etc.) — deliberately kept
    distinct from a legitimate zero-hit retrieval, which still produces
    measurable (zero-valued) recall/precision/mrr/ndcg rather than an error.
    Callers (services/evaluation/experiments.py, routers/evaluation.py) let
    this propagate as a clear failure instead of silently recording a
    misleading run."""


def run_evaluation(db: Session, eval_query: EvalQueryModel, *, k: int = 10) -> EvalRunModel:
    relevant = set(eval_query.expected_chunk_ids or [])
    # Shared by every Claude Gateway call this evaluation makes (query
    # rewriting, the planner's tool-loop turns, the judge) so token usage/
    # cost/model can be read back from the existing gateway_usage_logs audit
    # trail afterward — see _maybe_rewrite_query()'s/run_agent()'s/
    # judge_answer()'s request_id param — instead of adding a second
    # usage-tracking mechanism.
    eval_request_id = str(uuid.uuid4())

    # Retrieval metrics now come from the same production retrieval boundary
    # services/agents/planner.py's search_documents tool calls — never a
    # direct search_with_reranking()/hybrid_search() call — so Recall/
    # Precision/MRR/NDCG actually reflect Phase 3A (fetch_parent_context(),
    # reached inside search_documents() when parent_child_retrieval_enabled)
    # and Phase 3B (_maybe_rewrite_query(), reached here directly — the same
    # function the tool calls, not a reimplementation) whenever those flags
    # are on. See docs/RAG_RETRIEVAL.md "Evaluation Architecture Correction".
    retrieval_start = time.perf_counter()
    try:
        effective_query, rewrite_trace = _maybe_rewrite_query(
            eval_query.query, conversation_summary=None, request_id=eval_request_id
        )
        # No role/user_id here by design (see comment above) — eval measures
        # raw retrieval quality against the eval set, not RBAC-narrowed
        # results, so this is the one legitimate allow_unfiltered=True caller
        # (see resolve_document_ids's docstring).
        raw_results = search_documents(db, query=effective_query, top_k=k, allow_unfiltered=True)
    except Exception as exc:
        raise EvaluationRetrievalError(
            f"retrieval failed for eval query {eval_query.id}: {exc}"
        ) from exc
    retrieval_latency_ms = (time.perf_counter() - retrieval_start) * 1000
    record_latency("eval:retrieval", retrieval_latency_ms)
    retrieved_ids = [r["chunk_id"] for r in raw_results]
    # Proof Phase 3A actually ran and enriched results — a chunk only gets a
    # "parent_context" key when fetch_parent_context() found and attached one.
    parent_context_chunk_ids = [r["chunk_id"] for r in raw_results if "parent_context" in r]

    if relevant:
        recall = recall_at_k(retrieved_ids, relevant, k)
        precision = precision_at_k(retrieved_ids, relevant, k)
        mrr = mean_reciprocal_rank(retrieved_ids, relevant)
        ndcg = ndcg_at_k(retrieved_ids, relevant, k)
    else:
        recall = precision = mrr = ndcg = None

    generation_start = time.perf_counter()
    try:
        agent_result = run_agent(eval_query.query, request_id=eval_request_id)
        generated_answer = agent_result.reply
        source_texts = [s["text"] for s in agent_result.sources]
    except Exception as exc:
        generated_answer = f"[generation failed: {exc}]"
        source_texts = []
    generation_latency_ms = (time.perf_counter() - generation_start) * 1000
    record_latency("eval:generation", generation_latency_ms)

    groundedness = faithfulness = hallucination_rate = None
    citation_accuracy = answer_relevance = None
    judge_notes = None
    judge_latency_ms = 0.0
    try:
        judge_start = time.perf_counter()
        verdict = judge_answer(eval_query.query, generated_answer, source_texts, request_id=eval_request_id)
        judge_latency_ms = (time.perf_counter() - judge_start) * 1000
        groundedness = verdict["groundedness"]
        faithfulness = verdict["faithfulness"]
        hallucination_rate = verdict["hallucination_rate"]
        citation_accuracy = verdict["citation_accuracy"]
        answer_relevance = verdict["answer_relevance"]
        judge_notes = verdict["notes"]
    except JudgeError as exc:
        judge_notes = f"judge failed: {exc}"

    total_latency_ms = retrieval_latency_ms + generation_latency_ms + judge_latency_ms

    # Every Claude call this evaluation made (planner tool-loop turns, the
    # judge) already wrote its own row to gateway_usage_logs, tagged with
    # eval_request_id above, via claude_gateway.generate()/the planner's own
    # record_usage() call — read it back rather than re-deriving tokens/cost.
    usage_rows = db.query(GatewayUsageLogModel).filter(GatewayUsageLogModel.request_id == eval_request_id).all()
    tokens_input = sum(r.tokens_input for r in usage_rows) if usage_rows else None
    tokens_output = sum(r.tokens_output for r in usage_rows) if usage_rows else None
    cost_usd = sum(r.cost_usd for r in usage_rows) if usage_rows else None
    model = usage_rows[0].model if usage_rows else None

    # Answers "did this evaluation actually execute Phase 3A/3B?" from real
    # values produced by the real calls above — never reconstructed after the
    # fact. rewrite_trace is exactly the dict _maybe_rewrite_query() itself
    # builds (None when query rewriting is off); parent_context_chunk_ids is
    # non-empty only when fetch_parent_context() (inside search_documents())
    # actually attached parent text to a hit.
    retrieval_trace = {
        "original_query": eval_query.query,
        "effective_query": effective_query,
        "query_rewriting_enabled": settings.query_rewriting_enabled,
        "rewrite_trace": rewrite_trace,
        "parent_child_retrieval_enabled": settings.parent_child_retrieval_enabled,
        "parent_context_chunk_ids": parent_context_chunk_ids,
        "retrieved_chunk_ids": retrieved_ids,
        "retrieval_latency_ms": retrieval_latency_ms,
        "generation_available": not (generated_answer or "").startswith("[generation failed:"),
    }

    # run_agent() here is called directly (see comment above), not through
    # routers/chat.py's HTTP pipeline — so unlike a real chat turn,
    # generated_answer never passed through run_output_guardrails() and
    # could still contain literal PII the model wrote into its own reply.
    # judge_answer() above scored the real, unredacted text (an eval-quality
    # concern, not a security one — the judge needs to see what was actually
    # generated); this is a separate, later redaction pass purely for what
    # gets persisted, mirroring chat.py's own redact-before-store behavior.
    # Reuses redact_pii() directly rather than the full guardrails pipeline
    # (which also runs the system-prompt-leak check) since that's a second,
    # unrelated concern this isolated fix isn't meant to pull in.
    persisted_answer = redact_pii(generated_answer)[0]

    run = EvalRunModel(
        eval_query_id=eval_query.id,
        k=k,
        retrieved_chunk_ids=retrieved_ids,
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=mrr,
        ndcg_at_k=ndcg,
        retrieval_latency_ms=retrieval_latency_ms,
        generated_answer=persisted_answer,
        groundedness=groundedness,
        faithfulness=faithfulness,
        hallucination_rate=hallucination_rate,
        citation_accuracy=citation_accuracy,
        answer_relevance=answer_relevance,
        judge_notes=judge_notes,
        generation_latency_ms=generation_latency_ms,
        total_latency_ms=total_latency_ms,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        model=model,
        retrieval_trace=retrieval_trace,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
