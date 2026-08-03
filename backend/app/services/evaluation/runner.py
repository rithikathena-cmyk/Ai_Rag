import time

from sqlalchemy.orm import Session

from app.models.eval_query import EvalQueryModel
from app.models.eval_run import EvalRunModel
from app.services.agents.planner import run_agent
from app.services.evaluation.generation_judge import JudgeError, judge_answer
from app.services.evaluation.retrieval_metrics import mean_reciprocal_rank, ndcg_at_k, precision_at_k, recall_at_k
from app.services.monitoring.metrics import record_latency
from app.services.reranking.pipeline import search_with_reranking


def run_evaluation(db: Session, eval_query: EvalQueryModel, *, k: int = 10) -> EvalRunModel:
    relevant = set(eval_query.expected_chunk_ids or [])

    retrieval_start = time.perf_counter()
    hits, _reranked = search_with_reranking(db, query=eval_query.query, mode="hybrid", top_k=k)
    retrieval_latency_ms = (time.perf_counter() - retrieval_start) * 1000
    record_latency("eval:retrieval", retrieval_latency_ms)
    retrieved_ids = [str(h.chunk_id) for h in hits]

    if relevant:
        recall = recall_at_k(retrieved_ids, relevant, k)
        precision = precision_at_k(retrieved_ids, relevant, k)
        mrr = mean_reciprocal_rank(retrieved_ids, relevant)
        ndcg = ndcg_at_k(retrieved_ids, relevant, k)
    else:
        recall = precision = mrr = ndcg = None

    generation_start = time.perf_counter()
    try:
        agent_result = run_agent(eval_query.query)
        generated_answer = agent_result.reply
        source_texts = [s["text"] for s in agent_result.sources]
    except Exception as exc:
        generated_answer = f"[generation failed: {exc}]"
        source_texts = []
    generation_latency_ms = (time.perf_counter() - generation_start) * 1000
    record_latency("eval:generation", generation_latency_ms)

    groundedness = faithfulness = hallucination_rate = None
    judge_notes = None
    try:
        verdict = judge_answer(eval_query.query, generated_answer, source_texts)
        groundedness = verdict["groundedness"]
        faithfulness = verdict["faithfulness"]
        hallucination_rate = verdict["hallucination_rate"]
        judge_notes = verdict["notes"]
    except JudgeError as exc:
        judge_notes = f"judge failed: {exc}"

    run = EvalRunModel(
        eval_query_id=eval_query.id,
        k=k,
        retrieved_chunk_ids=retrieved_ids,
        recall_at_k=recall,
        precision_at_k=precision,
        mrr=mrr,
        ndcg_at_k=ndcg,
        retrieval_latency_ms=retrieval_latency_ms,
        generated_answer=generated_answer,
        groundedness=groundedness,
        faithfulness=faithfulness,
        hallucination_rate=hallucination_rate,
        judge_notes=judge_notes,
        generation_latency_ms=generation_latency_ms,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
