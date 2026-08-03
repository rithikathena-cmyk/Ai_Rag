import streamlit as st

from api_client import (
    create_eval_query,
    delete_eval_query,
    get_eval_summary,
    list_eval_queries,
    list_eval_runs,
    run_eval_query,
    search_documents,
)
from theme import hero, inject_theme, section_title

st.set_page_config(page_title="Evaluation - rag-chat", page_icon="🧪", layout="wide")
inject_theme()
hero("🧪 Evaluation", "Score retrieval quality against curated ground truth, and generation quality with an LLM-as-judge.")

tab_set, tab_run, tab_history = st.tabs(["📋 Eval Set", "▶ Run", "📊 History & Dashboard"])

# ---------------------------------------------------------------------------
# Eval Set
# ---------------------------------------------------------------------------
with tab_set:
    section_title("➕ Add an eval query")
    st.write(
        "Give a question and (optionally) mark which chunks are actually relevant to it. "
        "Retrieval metrics (Recall@K, Precision@K, MRR, nDCG) need that ground truth — without it, "
        "only generation metrics (groundedness, faithfulness, hallucination rate) can be scored."
    )

    if "eval_candidate_chunks" not in st.session_state:
        st.session_state.eval_candidate_chunks = []
    if "eval_selected_chunk_ids" not in st.session_state:
        st.session_state.eval_selected_chunk_ids = set()

    eval_question = st.text_input("Question", key="eval-new-question")
    eval_description = st.text_input("Description (optional)", key="eval-new-description")

    st.markdown("**Find relevant chunks (optional)**")
    find_col, _ = st.columns([2, 3])
    with find_col:
        find_query = st.text_input("Search the corpus to find relevant chunks", value=eval_question or "")
    if st.button("Search") and find_query.strip():
        try:
            result = search_documents(find_query, mode="hybrid", top_k=15, rerank=True)
            st.session_state.eval_candidate_chunks = result["results"]
        except Exception as exc:
            st.error(f"Search failed: {exc}")

    for hit in st.session_state.eval_candidate_chunks:
        chunk_id = hit["chunk_id"]
        filename = hit.get("document_filename") or hit["document_id"]
        checked = st.checkbox(
            f"[{filename} · chunk {hit['chunk_index']} · score {hit['score']:.3f}] "
            f"{hit['text'][:150]}{'…' if len(hit['text']) > 150 else ''}",
            value=chunk_id in st.session_state.eval_selected_chunk_ids,
            key=f"eval-chunk-{chunk_id}",
        )
        if checked:
            st.session_state.eval_selected_chunk_ids.add(chunk_id)
        else:
            st.session_state.eval_selected_chunk_ids.discard(chunk_id)

    st.caption(f"{len(st.session_state.eval_selected_chunk_ids)} chunk(s) marked relevant.")

    if st.button("💾 Save eval query", type="primary"):
        if not eval_question.strip():
            st.error("Enter a question.")
        else:
            try:
                create_eval_query(
                    eval_question.strip(), eval_description.strip() or None,
                    list(st.session_state.eval_selected_chunk_ids),
                )
            except Exception as exc:
                st.error(f"Could not save: {exc}")
            else:
                st.success("Saved.")
                st.session_state.eval_candidate_chunks = []
                st.session_state.eval_selected_chunk_ids = set()
                st.rerun()

    st.divider()
    section_title("📋 Existing eval queries")
    try:
        eval_queries = list_eval_queries()
    except Exception as exc:
        st.error(f"Could not load eval queries: {exc}")
        eval_queries = []

    if not eval_queries:
        st.write("No eval queries yet — add one above.")
    else:
        st.dataframe(
            [
                {
                    "query": q["query"], "description": q["description"] or "—",
                    "ground_truth_chunks": len(q["expected_chunk_ids"]), "created_at": q["created_at"],
                }
                for q in eval_queries
            ],
            use_container_width=True,
        )
        del_labels = [f"{q['query'][:60]} ({str(q['id'])[:8]})" for q in eval_queries]
        del_choice = st.selectbox("Delete a query", options=["(none)"] + del_labels)
        if del_choice != "(none)" and st.button("🗑️ Delete"):
            target = eval_queries[del_labels.index(del_choice)]
            try:
                delete_eval_query(str(target["id"]))
            except Exception as exc:
                st.error(f"Could not delete: {exc}")
            else:
                st.success("Deleted.")
                st.rerun()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
with tab_run:
    try:
        eval_queries = list_eval_queries()
    except Exception as exc:
        st.error(f"Could not load eval queries: {exc}")
        eval_queries = []

    if not eval_queries:
        st.write("Add an eval query in the **Eval Set** tab first.")
    else:
        labels = [f"{q['query'][:70]} ({str(q['id'])[:8]})" for q in eval_queries]
        choice = st.selectbox("Eval query", options=labels)
        target = eval_queries[labels.index(choice)]
        k = st.slider("K", min_value=1, max_value=20, value=10)

        if not target["expected_chunk_ids"]:
            st.info("No ground-truth chunks marked for this query — retrieval metrics will show as N/A.")

        if st.button("▶ Run evaluation", type="primary"):
            with st.spinner("Retrieving, generating, and judging..."):
                try:
                    run = run_eval_query(str(target["id"]), k=k)
                except Exception as exc:
                    st.error(f"Evaluation failed: {exc}")
                    run = None

            if run:
                st.markdown("**Retrieval**")
                r1, r2, r3, r4 = st.columns(4)
                r1.metric(f"Recall@{k}", f"{run['recall_at_k']:.2f}" if run["recall_at_k"] is not None else "N/A")
                r2.metric(f"Precision@{k}", f"{run['precision_at_k']:.2f}" if run["precision_at_k"] is not None else "N/A")
                r3.metric("MRR", f"{run['mrr']:.2f}" if run["mrr"] is not None else "N/A")
                r4.metric(f"nDCG@{k}", f"{run['ndcg_at_k']:.2f}" if run["ndcg_at_k"] is not None else "N/A")
                st.caption(f"Retrieval latency: {run['retrieval_latency_ms']:.0f} ms")

                st.markdown("**Generation**")
                g1, g2, g3, g4 = st.columns(4)
                g1.metric("Groundedness", f"{run['groundedness']:.2f}" if run["groundedness"] is not None else "N/A")
                g2.metric("Faithfulness", f"{run['faithfulness']:.2f}" if run["faithfulness"] is not None else "N/A")
                g3.metric(
                    "Hallucination rate",
                    f"{run['hallucination_rate']:.2f}" if run["hallucination_rate"] is not None else "N/A",
                )
                g4.metric("Latency", f"{run['generation_latency_ms']:.0f} ms")

                with st.container(border=True):
                    st.markdown("**Generated answer**")
                    st.write(run["generated_answer"])
                    if run["judge_notes"]:
                        st.caption(f"Judge notes: {run['judge_notes']}")

# ---------------------------------------------------------------------------
# History & Dashboard
# ---------------------------------------------------------------------------
with tab_history:
    try:
        summary = get_eval_summary()
    except Exception as exc:
        st.error(f"Could not load summary: {exc}")
        summary = None

    if summary:
        section_title(f"📊 Averages across {summary['run_count']} run(s)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg Recall@K", f"{summary['avg_recall_at_k']:.2f}" if summary["avg_recall_at_k"] is not None else "N/A")
        c2.metric("Avg Precision@K", f"{summary['avg_precision_at_k']:.2f}" if summary["avg_precision_at_k"] is not None else "N/A")
        c3.metric("Avg MRR", f"{summary['avg_mrr']:.2f}" if summary["avg_mrr"] is not None else "N/A")
        c4.metric("Avg nDCG@K", f"{summary['avg_ndcg_at_k']:.2f}" if summary["avg_ndcg_at_k"] is not None else "N/A")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Avg Groundedness", f"{summary['avg_groundedness']:.2f}" if summary["avg_groundedness"] is not None else "N/A")
        c6.metric("Avg Faithfulness", f"{summary['avg_faithfulness']:.2f}" if summary["avg_faithfulness"] is not None else "N/A")
        c7.metric("Avg Hallucination rate", f"{summary['avg_hallucination_rate']:.2f}" if summary["avg_hallucination_rate"] is not None else "N/A")
        c8.metric(
            "Avg latency (retrieval+gen)",
            f"{(summary['avg_retrieval_latency_ms'] or 0) + (summary['avg_generation_latency_ms'] or 0):.0f} ms",
        )

    st.divider()
    section_title("🕘 Run history")
    try:
        runs = list_eval_runs(limit=200)
    except Exception as exc:
        st.error(f"Could not load run history: {exc}")
        runs = []

    if not runs:
        st.write("No evaluation runs yet.")
    else:
        query_by_id = {}
        try:
            query_by_id = {str(q["id"]): q["query"] for q in list_eval_queries()}
        except Exception:
            pass

        rows = [
            {
                "query": query_by_id.get(str(r["eval_query_id"]), str(r["eval_query_id"])[:8]),
                "recall": r["recall_at_k"], "precision": r["precision_at_k"], "mrr": r["mrr"], "ndcg": r["ndcg_at_k"],
                "groundedness": r["groundedness"], "faithfulness": r["faithfulness"],
                "hallucination_rate": r["hallucination_rate"],
                "retrieval_ms": round(r["retrieval_latency_ms"] or 0, 0),
                "generation_ms": round(r["generation_latency_ms"] or 0, 0),
                "created_at": r["created_at"],
            }
            for r in runs
        ]
        st.dataframe(rows, use_container_width=True)

        chart_rows = [r for r in runs if r["groundedness"] is not None]
        if chart_rows:
            st.markdown("**Generation quality over time**")
            st.line_chart(
                {
                    "groundedness": [r["groundedness"] for r in reversed(chart_rows)],
                    "faithfulness": [r["faithfulness"] for r in reversed(chart_rows)],
                    "hallucination_rate": [r["hallucination_rate"] for r in reversed(chart_rows)],
                }
            )
