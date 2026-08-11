import streamlit as st

import api_client
from api_client import APIError
from components import card, debug_json, explorable_table, metric_cards, page_header, show_api_error

page_header("Evaluation", "🧪", "Retrieval and generation quality, tracked over time.", color="green-70")
metric_cards()

tab_queries, tab_runs, tab_summary, tab_experiments = st.tabs(["Eval queries", "Runs", "Summary", "Experiments"])

with tab_queries:
    with st.form("new_eval_query", clear_on_submit=True):
        query = st.text_input("Query")
        description = st.text_input("Description (optional)")
        expected = st.text_input("Expected chunk IDs (comma-separated, optional)")
        if st.form_submit_button("Add"):
            if not query.strip():
                st.warning("Enter a query.")
            else:
                ids = [c.strip() for c in expected.split(",") if c.strip()]
                try:
                    api_client.create_eval_query(query, description or None, ids)
                    st.success("Added.")
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)

    try:
        queries = api_client.list_eval_queries()
    except APIError as exc:
        show_api_error(exc)
        queries = []

    for eq in queries:
        with card(f"eval_query_card_{eq['id']}"):
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.markdown(f"**{eq['query']}**")
            if eq["description"]:
                c1.caption(eq["description"])
            if c2.button("Run", key=f"run_{eq['id']}"):
                with st.spinner("Retrieving + judging…"):
                    try:
                        run = api_client.run_eval_query(eq["id"], k=10)
                    except APIError as exc:
                        show_api_error(exc)
                    else:
                        st.success(
                            f"recall@10={run['recall_at_k']}, mrr={run['mrr']}, "
                            f"groundedness={run['groundedness']}, faithfulness={run['faithfulness']}, "
                            f"citation_accuracy={run['citation_accuracy']}, answer_relevance={run['answer_relevance']}"
                        )
            if c3.button("Delete", key=f"del_eq_{eq['id']}"):
                try:
                    api_client.delete_eval_query(eq["id"])
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)

with tab_runs:
    try:
        runs = api_client.list_eval_runs()
    except APIError as exc:
        show_api_error(exc)
        runs = []
    if runs:
        explorable_table(runs)
    else:
        st.caption("No runs yet — run an eval query from the first tab.")

with tab_summary:
    try:
        summary = api_client.eval_summary()
    except APIError as exc:
        show_api_error(exc)
        st.stop()

    st.caption(f"Across {summary['run_count']} run(s)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Avg recall@k", f"{summary['avg_recall_at_k']:.3f}" if summary["avg_recall_at_k"] is not None else "—")
    m2.metric("Avg precision@k", f"{summary['avg_precision_at_k']:.3f}" if summary["avg_precision_at_k"] is not None else "—")
    m3.metric("Avg MRR", f"{summary['avg_mrr']:.3f}" if summary["avg_mrr"] is not None else "—")
    m4.metric("Avg nDCG@k", f"{summary['avg_ndcg_at_k']:.3f}" if summary["avg_ndcg_at_k"] is not None else "—")

    m5, m6, m7, m8 = st.columns(4)
    m5.metric("Avg groundedness", f"{summary['avg_groundedness']:.3f}" if summary["avg_groundedness"] is not None else "—")
    m6.metric("Avg faithfulness", f"{summary['avg_faithfulness']:.3f}" if summary["avg_faithfulness"] is not None else "—")
    m7.metric("Avg hallucination rate", f"{summary['avg_hallucination_rate']:.3f}" if summary["avg_hallucination_rate"] is not None else "—")
    m8.metric("Avg citation accuracy", f"{summary['avg_citation_accuracy']:.3f}" if summary["avg_citation_accuracy"] is not None else "—")

    m9, m10, m11, m12 = st.columns(4)
    m9.metric("Avg answer relevance", f"{summary['avg_answer_relevance']:.3f}" if summary["avg_answer_relevance"] is not None else "—")
    m10.metric("Avg retrieval latency", f"{summary['avg_retrieval_latency_ms']:.0f} ms" if summary["avg_retrieval_latency_ms"] is not None else "—")
    m11.metric("Avg generation latency", f"{summary['avg_generation_latency_ms']:.0f} ms" if summary["avg_generation_latency_ms"] is not None else "—")
    m12.metric("Avg total latency", f"{summary['avg_total_latency_ms']:.0f} ms" if summary["avg_total_latency_ms"] is not None else "—")

    m13, m14, m15, m16 = st.columns(4)
    m13.metric("Avg input tokens", f"{summary['avg_tokens_input']:.0f}" if summary["avg_tokens_input"] is not None else "—")
    m14.metric("Avg output tokens", f"{summary['avg_tokens_output']:.0f}" if summary["avg_tokens_output"] is not None else "—")
    m15.metric("Avg cost / run", f"${summary['avg_cost_usd']:.4f}" if summary["avg_cost_usd"] is not None else "—")
    m16.metric("Total cost (all runs)", f"${summary['total_cost_usd']:.4f}" if summary["total_cost_usd"] is not None else "—")
    metric_cards()

    debug_json(summary, "Raw /eval/summary response")

with tab_experiments:
    st.caption(
        "Phase 3 evaluation gate — runs the curated eval dataset under baseline, parent-child, and "
        "query-rewrite configurations back-to-back (config overrides only, nothing is enabled "
        "permanently) and compares results. See docs/RAG_RETRIEVAL.md §\"Phase 3 Evaluation "
        "Results\" for the full methodology and its known limitations."
    )
    c1, c2, c3, c4 = st.columns(4)
    k = c1.number_input("k", min_value=1, max_value=50, value=10)
    include_parent_child = c2.checkbox("Parent-child", value=True)
    include_query_rewrite = c3.checkbox("Query rewrite", value=True)
    include_combined = c4.checkbox("Combined (both on)", value=False)

    if st.button("Run experiment gate", type="primary"):
        with st.spinner("Running baseline + experiment configurations — this runs the full dataset multiple times…"):
            try:
                st.session_state["experiment_gate_result"] = api_client.run_experiment_gate(
                    k=int(k), include_parent_child=include_parent_child,
                    include_query_rewrite=include_query_rewrite, include_combined=include_combined,
                )
            except APIError as exc:
                show_api_error(exc)

    result = st.session_state.get("experiment_gate_result")
    if not result:
        st.caption("No experiment run yet — configure and click \"Run experiment gate\".")
    else:
        st.caption(
            f"Dataset size: {result['dataset_size']} eval quer{'y' if result['dataset_size'] == 1 else 'ies'} "
            f"· experiments run: {', '.join(result['experiments_run'])}"
        )
        if result["dataset_size"] < 5:
            st.warning(
                f"Only {result['dataset_size']} eval quer{'y' if result['dataset_size'] == 1 else 'ies'} in the "
                "dataset — too small to claim statistical significance. Results below are directional only."
            )

        def _fmt(v):
            if v is None:
                return "unavailable"
            return f"{v:,.4f}" if abs(v) < 100 else f"{v:,.1f}"

        def _render_feature(label: str, report: dict | None):
            if report is None:
                st.caption(f"{label}: not included in this run.")
                return
            st.markdown(f"#### {label}")
            badge = {
                "RECOMMEND ENABLE": "\U0001f7e2", "KEEP DISABLED": "\U0001f534", "INSUFFICIENT EVIDENCE": "\U0001f7e1",
            }.get(report["recommendation"], "")
            st.markdown(f"**{badge} {report['recommendation']}**")
            for reason in report["recommendation_reasons"]:
                st.caption(f"• {reason}")
            st.caption(f"Generation status: {report['generation_status']}")

            rows = [
                {
                    "Metric": c["metric"],
                    "Baseline": _fmt(c["baseline_avg"]),
                    label: _fmt(c["experiment_avg"]),
                    "Delta (exp − base)": _fmt(c["delta"]),
                    "Delta %": f"{c['delta_pct']:.1f}%" if c["delta_pct"] is not None else "—",
                    "Status": c["status"],
                }
                for c in report["comparisons"]
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)

            with st.expander("Per-question paired deltas"):
                paired_rows = [
                    {
                        "Metric": d["metric"], "Improved": d["improved"], "Degraded": d["degraded"],
                        "Unchanged": d["unchanged"], "Skipped (unavailable)": d["skipped_unavailable"],
                    }
                    for d in report["paired_deltas"]
                ]
                st.dataframe(paired_rows, use_container_width=True, hide_index=True)

        _render_feature("Parent-Child", result.get("parent_child"))
        _render_feature("Query Rewrite", result.get("query_rewrite"))

        if result.get("combined_runs"):
            st.markdown("#### Combined (both flags on)")
            st.caption(
                "No independent recommendation is computed for the combined condition — a combined "
                "result cannot attribute effect to either feature individually."
            )
            explorable_table(result["combined_runs"])

        debug_json(result, "Raw /eval/experiments/run response")
