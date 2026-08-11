import altair as alt
import pandas as pd
import streamlit as st

import api_client
from api_client import APIError
from components import explorable_table, metric_cards, page_header, show_api_error

page_header(
    "Query Metrics", "📈",
    "Per-query stage timing for retrieval (search/chat) and ingestion (uploads) — not just endpoint averages.",
    color="light-blue-70",
)
metric_cards()

try:
    data = api_client.get_query_metrics()
except APIError as exc:
    show_api_error(exc)
    st.stop()

retrieval_samples = data["retrieval_samples"]
ingestion_samples = data["ingestion_samples"]

# Fixed hue order (Okabe-Ito, colorblind-safe) — one color per pipeline stage,
# assigned by identity and never cycled/reused across an unrelated series.
RETRIEVAL_STAGES = ["filter_ms", "embed_ms", "sparse_ms", "qdrant_ms", "rerank_ms"]
RETRIEVAL_COLORS = ["#56B4E9", "#E69F00", "#009E73", "#0072B2", "#D55E00"]

INGESTION_STAGES = ["parse_ms", "summarize_ms", "entity_ms", "chunk_ms", "embed_ms", "sparse_ms", "sparse_index_ms"]
INGESTION_COLORS = ["#E69F00", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999"]


def _stage_frame(samples: list[dict], stage_keys: list[str], label_key: str, n: int = 20) -> pd.DataFrame:
    """Long-form frame (one row per query x stage) for a stacked bar — only
    stages that are true, additive parts of total_ms belong in `stage_keys`."""
    recent = samples[-n:]
    rows = []
    for i, s in enumerate(recent):
        stages = s.get("stages_ms", {})
        label = s.get(label_key, "")
        short_label = f"{i+1}. {(label[:40] + '…') if len(label) > 40 else label}"
        for stage in stage_keys:
            if stage in stages:
                rows.append({"query": short_label, "stage": stage, "ms": stages[stage], "order": i})
    return pd.DataFrame(rows)


def _stacked_chart(frame: pd.DataFrame, stage_keys: list[str], colors: list[str]):
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            y=alt.Y("query:N", sort=alt.SortField("order"), title=None),
            x=alt.X("ms:Q", title="milliseconds", stack="zero"),
            color=alt.Color("stage:N", scale=alt.Scale(domain=stage_keys, range=colors), legend=alt.Legend(title="Stage")),
            order=alt.Order("order:N"),
            tooltip=["query", "stage", alt.Tooltip("ms:Q", format=".1f")],
        )
        .properties(height=max(200, 24 * min(len(frame["query"].unique()), 20)))
    )


tab_retrieval, tab_ingestion, tab_suggestions = st.tabs(["Retrieval", "Ingestion", "Suggestions"])

with tab_retrieval:
    if not retrieval_samples:
        st.info("No searches recorded yet — run a query from Chat or Search to populate this.")
    else:
        df = pd.DataFrame([
            {
                "query": s["query"],
                **{k: s["stages_ms"].get(k) for k in RETRIEVAL_STAGES},
                "total_ms": s["stages_ms"].get("total_ms"),
                "candidates": s["candidate_count"],
                "results": s["result_count"],
            }
            for s in retrieval_samples
        ])

        m = st.columns(len(RETRIEVAL_STAGES) + 1)
        for col, stage in zip(m, RETRIEVAL_STAGES):
            avg = df[stage].mean()
            col.metric(stage.replace("_ms", ""), f"{avg:.0f} ms" if pd.notna(avg) else "—")
        m[-1].metric("total (avg)", f"{df['total_ms'].mean():.0f} ms")

        st.subheader(f"Last {min(20, len(retrieval_samples))} queries — stage breakdown")
        chart_df = _stage_frame(retrieval_samples, RETRIEVAL_STAGES, "query")
        if not chart_df.empty:
            st.altair_chart(_stacked_chart(chart_df, RETRIEVAL_STAGES, RETRIEVAL_COLORS), use_container_width=True)

        st.subheader("Raw samples")
        explorable_table(df.sort_index(ascending=False))

with tab_ingestion:
    if not ingestion_samples:
        st.info("No uploads recorded yet — upload a document to populate this.")
    else:
        df = pd.DataFrame([
            {
                "filename": s["filename"],
                **{k: s["stages_ms"].get(k) for k in INGESTION_STAGES},
                "tokenize_ms": s["stages_ms"].get("tokenize_ms"),
                "total_ms": s["stages_ms"].get("total_ms"),
                "chunks": s["chunk_count"],
            }
            for s in ingestion_samples
        ])

        m = st.columns(4)
        m[0].metric("chunk (avg)", f"{df['chunk_ms'].mean():.0f} ms" if pd.notna(df["chunk_ms"].mean()) else "—")
        tokenize_avg = df["tokenize_ms"].mean()
        chunk_avg = df["chunk_ms"].mean()
        tokenize_share = f"{tokenize_avg / chunk_avg * 100:.0f}% of chunk_ms" if chunk_avg else "—"
        m[1].metric("tokenize (avg)", f"{tokenize_avg:.0f} ms" if pd.notna(tokenize_avg) else "—", tokenize_share)
        m[2].metric("embed (avg)", f"{df['embed_ms'].mean():.0f} ms" if pd.notna(df["embed_ms"].mean()) else "—")
        m[3].metric("total (avg)", f"{df['total_ms'].mean():.0f} ms" if pd.notna(df["total_ms"].mean()) else "—")
        st.caption(
            "tokenize_ms is the portion of chunk_ms spent purely in the HF tokenizer — "
            "shown separately rather than stacked, since it's a subset of chunk_ms, not an additional stage."
        )

        st.subheader(f"Last {min(20, len(ingestion_samples))} uploads — stage breakdown")
        chart_df = _stage_frame(ingestion_samples, INGESTION_STAGES, "filename")
        if not chart_df.empty:
            st.altair_chart(_stacked_chart(chart_df, INGESTION_STAGES, INGESTION_COLORS), use_container_width=True)

        st.subheader("Raw samples")
        explorable_table(df.sort_index(ascending=False))

with tab_suggestions:
    suggestions: list[tuple[str, str]] = []  # (level, message)

    if retrieval_samples:
        df = pd.DataFrame([s["stages_ms"] for s in retrieval_samples])
        total_avg = df.get("total_ms", pd.Series(dtype=float)).mean()
        rerank_avg = df.get("rerank_ms", pd.Series(dtype=float)).mean()
        qdrant_avg = df.get("qdrant_ms", pd.Series(dtype=float)).mean()

        if pd.notna(rerank_avg) and pd.notna(total_avg) and total_avg > 0 and rerank_avg / total_avg > 0.5:
            suggestions.append((
                "warning",
                f"Reranking is {rerank_avg / total_avg * 100:.0f}% of retrieval time ({rerank_avg:.0f} ms avg). "
                "Consider lowering `reranker_candidate_pool` further in `backend/app/core/config.py`, "
                "or moving the cross-encoder to GPU if one's available.",
            ))
        if pd.notna(qdrant_avg) and qdrant_avg > 500:
            suggestions.append((
                "error",
                f"Qdrant round-trips are averaging {qdrant_avg:.0f} ms — that's high for a local instance. "
                "If running natively on Windows, verify `QDRANT_HOST=127.0.0.1` in `.env`, not `localhost` "
                "(a ~2000ms IPv6-fallback tax applies to every call otherwise).",
            ))
        if len(retrieval_samples) < 5:
            suggestions.append(("info", "Fewer than 5 search samples recorded — run more queries for reliable averages."))

    if ingestion_samples:
        df = pd.DataFrame([s["stages_ms"] for s in ingestion_samples])
        chunk_avg = df.get("chunk_ms", pd.Series(dtype=float)).mean()
        tokenize_avg = df.get("tokenize_ms", pd.Series(dtype=float)).mean()
        embed_avg = df.get("embed_ms", pd.Series(dtype=float)).mean()
        total_avg = df.get("total_ms", pd.Series(dtype=float)).mean()

        if pd.notna(tokenize_avg) and pd.notna(chunk_avg) and chunk_avg > 0 and tokenize_avg / chunk_avg > 0.6:
            suggestions.append((
                "warning",
                f"Tokenization is {tokenize_avg / chunk_avg * 100:.0f}% of chunking time. "
                "text_utils.count_tokens re-tokenizes per recursive_split call — for very large documents, "
                "consider caching token counts per text span.",
            ))
        if pd.notna(embed_avg) and pd.notna(total_avg) and total_avg > 0 and embed_avg / total_avg > 0.5:
            suggestions.append((
                "warning",
                f"Embedding is {embed_avg / total_avg * 100:.0f}% of ingestion time ({embed_avg:.0f} ms avg). "
                "Consider a larger `embedding_batch_size` or GPU for BGE-M3.",
            ))
        if len(ingestion_samples) < 5:
            suggestions.append(("info", "Fewer than 5 uploads recorded — upload more documents for reliable averages."))

    if not suggestions:
        if not retrieval_samples and not ingestion_samples:
            st.info("No data yet — run some searches and uploads first.")
        else:
            st.success("No issues flagged against current thresholds.")
    else:
        for level, message in suggestions:
            getattr(st, level)(message)
