import streamlit as st

import api_client
from api_client import APIError
from components import card, debug_json, explorable_table, metric_cards, page_header, show_api_error

page_header("Admin", "settings", "Collections, metrics, cost, and guardrail operations.", color="red-70")
metric_cards()

try:
    _availability = api_client.get_model_availability()
except APIError as exc:
    _availability = {"disabled": False}
    show_api_error(exc)

_forced_off = st.toggle(
    "🔌 Force Claude model unavailable (testing)", value=_availability["disabled"],
    help=(
        "Simulates the Claude API being down, without touching the real ANTHROPIC_API_KEY — every "
        "chat message will get a degraded, non-AI raw-search reply, and the chat page's 'try a "
        "different model' retry button. Resets on backend restart."
    ),
)
if _forced_off != _availability["disabled"]:
    try:
        api_client.set_model_availability(_forced_off)
        st.rerun()
    except APIError as exc:
        show_api_error(exc)

st.divider()

tab_collections, tab_metrics, tab_gateway, tab_guardrails = st.tabs(
    ["Qdrant collections", "Metrics", "Gateway & Cost", "Guardrails"]
)

with tab_collections:
    try:
        collections = api_client.list_collections()
    except APIError as exc:
        show_api_error(exc)
        collections = []

    for coll in collections:
        with card(f"coll_card_{coll['name']}"):
            c1, c2 = st.columns([4, 1])
            primary = " (active)" if coll["is_primary"] else ""
            c1.markdown(f"**{coll['name']}**{primary} — {coll['points_count']} points, {coll['status']}")
            if not coll["is_primary"] and c2.button("Delete", key=f"del_coll_{coll['name']}"):
                try:
                    api_client.delete_collection(coll["name"])
                    st.success(f"Deleted {coll['name']}.")
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)

    st.divider()
    with st.form("create_collection", clear_on_submit=True):
        st.caption("Create collection")
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Name")
        vector_size = c2.number_input("Vector size", min_value=1, value=1024)
        distance = c3.selectbox("Distance", ["Cosine", "Euclid", "Dot"])
        if st.form_submit_button("Create"):
            try:
                api_client.create_collection(name, int(vector_size), distance)
                st.success(f"Created {name}.")
                st.rerun()
            except APIError as exc:
                show_api_error(exc)

with tab_metrics:
    try:
        metrics = api_client.get_metrics()
    except APIError as exc:
        show_api_error(exc)
        st.stop()

    st.subheader("Latency by endpoint")
    if metrics["latency_summary"]:
        explorable_table(metrics["latency_summary"])
    else:
        st.caption("No latency samples yet.")

    st.subheader("Token usage by model")
    if metrics["token_usage_summary"]:
        explorable_table(metrics["token_usage_summary"])
    else:
        st.caption("No token usage recorded yet.")

    debug_json(metrics, "Raw /admin/metrics response")

with tab_gateway:
    st.caption(
        "Every Claude Gateway call (services/agents/planner.py, evaluation/generation_judge.py, "
        "memory/store.py) — which agent called which model/tier, and its estimated cost. "
        "Approximate pricing, configured in backend/config/models.yaml."
    )
    try:
        usage = api_client.get_gateway_usage()
    except APIError as exc:
        show_api_error(exc)
        st.stop()

    st.metric("Total estimated cost (all time)", f"${usage['total_cost_usd']:.4f}")

    st.subheader("By agent / model / tier")
    if usage["summary"]:
        explorable_table(usage["summary"])
    else:
        st.caption("No gateway calls recorded yet — send a chat message or run an eval to populate this.")

    st.subheader(f"Recent calls (last {len(usage['samples'])})")
    if usage["samples"]:
        explorable_table(usage["samples"])
    else:
        st.caption("No gateway calls recorded yet.")

    debug_json(usage, "Raw /admin/gateway-usage response")

with tab_guardrails:
    st.caption(
        "Pass/redact/block counts per guardrail check, across both input rails (before the planner "
        "runs) and output rails (before a reply is returned). See docs/GUARDRAILS_ARCHITECTURE.md."
    )
    try:
        analytics = api_client.get_guardrail_analytics()
    except APIError as exc:
        show_api_error(exc)
        st.stop()

    summary = analytics["summary"]
    if summary:
        total_pass = sum(r["pass_count"] for r in summary)
        total_redact = sum(r["redact_count"] for r in summary)
        total_block = sum(r["block_count"] for r in summary)
        m1, m2, m3 = st.columns(3)
        m1.metric("Passed", total_pass)
        m2.metric("Redacted", total_redact)
        m3.metric("Blocked", total_block)

        st.subheader("By check")
        explorable_table(summary)
    else:
        st.caption("No guardrail checks recorded yet — send a chat message to populate this.")

    st.subheader(f"Recent events (last {len(analytics['events'])})")
    if analytics["events"]:
        explorable_table(analytics["events"])
    else:
        st.caption("No guardrail events recorded yet.")

    debug_json(analytics, "Raw /admin/guardrail-analytics response")
