import streamlit as st
from streamlit_extras.add_vertical_space import add_vertical_space

import api_client
from api_client import APIError
from components import card, metric_cards, page_header, render_capabilities, role_label, show_api_error, status_badge
from permissions import MANAGE_EMPLOYEE_PII, has_permission

if not st.session_state.get("current_user"):
    st.warning("Log in to view your dashboard — see the Login page.")
    st.stop()

_user = st.session_state["current_user"]
_display_name = (_user.get("display_name") or _user["email"].split("@")[0]).title()
# role="user" is the LLM-RBAC "Employee" role — Employee has neither
# VIEW_DOCUMENTS nor VIEW_ANALYTICS (llm_rbac.yaml), so Documents/Search/
# Reports aren't in their nav at all; this dashboard can't link out to those
# pages (st.switch_page() would raise a page-not-found error) or call their
# list endpoints (GET /documents now 403s an Employee token too).
_is_employee = _user["role"] == "user"

page_header(
    "Dashboard", "home",
    f"Welcome back, {_display_name} — here's what's happening in your knowledge base.",
    color="blue",
)


def _safe(fn, *args, **kwargs):
    """Runs an api_client call and returns (result, error) instead of raising —
    lets the dashboard degrade one card/section at a time instead of one
    failed call (e.g. an unreachable backend) blanking the whole page."""
    try:
        return fn(*args, **kwargs), None
    except APIError as exc:
        return None, exc


doc_result, doc_err = (None, None)
if not _is_employee:
    doc_result, doc_err = _safe(api_client.list_documents, limit=5, offset=0)
convo_result, convo_err = _safe(api_client.list_conversations, user_id=_user["id"], limit=1)
report_result, report_err = (None, None)
if not _is_employee:
    report_result, report_err = _safe(api_client.list_reports, limit=5, offset=0)

metric_cols = st.columns(1 if _is_employee else 3)
if _is_employee:
    metric_cols[0].metric("Your conversations", convo_result["total"] if convo_result else "—")
else:
    metric_cols[0].metric("Documents", doc_result["total"] if doc_result else "—")
    metric_cols[1].metric("Your conversations", convo_result["total"] if convo_result else "—")
    metric_cols[2].metric("Reports generated", report_result["total"] if report_result else "—")
metric_cards()

# Usage tiles render right under the row above (not after "Quick actions"/
# "What you can do") so every tile on the page groups together at the top,
# instead of the numeric summary being split by a wall of text in between.
st.caption("Chat/search quotas reset daily and monthly.")

usage_result, usage_err = _safe(api_client.get_my_usage)
if usage_err:
    show_api_error(usage_err)
elif usage_result:

    def _abbreviate(n: float) -> str:
        # st.metric truncates long values in a multi-column row rather than
        # wrapping — "512,000 / 2,000,000" clips to "512,000 / 2,0..." at
        # this widget's width, so token counts need a compact form.
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return f"{n:,.0f}"

    def _fmt(used, limit) -> str:
        return f"{_abbreviate(used)} (unlimited)" if limit is None else f"{_abbreviate(used)} / {_abbreviate(limit)}"

    u1, u2 = st.columns(2)
    u1.metric("Requests today", _fmt(usage_result["daily_requests_used"], usage_result["daily_requests_limit"]))
    u2.metric("Tokens today", _fmt(usage_result["daily_tokens_used"], usage_result["daily_tokens_limit"]))
    u3, u4 = st.columns(2)
    u3.metric("Tokens this month", _fmt(usage_result["monthly_tokens_used"], usage_result["monthly_tokens_limit"]))
    cost_limit = usage_result["monthly_cost_usd_limit"]
    cost_used = usage_result["monthly_cost_usd_used"]
    u4.metric(
        "Cost this month",
        f"${cost_used:,.2f} (unlimited)" if cost_limit is None else f"${cost_used:,.2f} / ${cost_limit:,.2f}",
    )
    metric_cards()

    rpm = usage_result["requests_per_minute_limit"]
    concurrent = usage_result["max_concurrent_requests_limit"]
    rpm_label = f"{rpm} req/min" if rpm is not None else "unlimited"
    concurrent_label = str(concurrent) if concurrent is not None else "unlimited"
    st.caption(f"Rate limit: {rpm_label} · Max concurrent requests: {concurrent_label}")

add_vertical_space(1)
st.markdown("##### Quick actions")
action_cols = st.columns(1 if _is_employee else 4)
if action_cols[0].button("Start a chat", type="primary", use_container_width=True):
    st.switch_page("views/chat.py")
if not _is_employee:
    if action_cols[1].button("Browse documents", use_container_width=True):
        st.switch_page("views/documents.py")
    if action_cols[2].button("Search knowledge base", use_container_width=True):
        st.switch_page("views/search.py")
    if action_cols[3].button("View reports", use_container_width=True):
        st.switch_page("views/reports.py")

add_vertical_space(1)
st.markdown("##### What you can do")
st.markdown(f"**Role:** {role_label(_user['role'])}")

caps_result, caps_err = _safe(api_client.get_my_capabilities)
if caps_err:
    show_api_error(caps_err)
elif caps_result:
    render_capabilities(caps_result)

add_vertical_space(1)


def _recent_documents() -> None:
    st.markdown("##### Recent documents")
    if doc_err:
        show_api_error(doc_err)
    elif not doc_result["items"]:
        st.caption("No documents yet — upload one from Knowledge base → Documents.")
    else:
        for doc in doc_result["items"]:
            with card(f"dash_doc_{doc['id']}"):
                st.markdown(f"**{doc['filename']}** — {status_badge(doc['status'])}")
                st.caption(f"{doc['document_type']} · v{doc['version_number']} · {doc['chunk_count']} chunks")


if not _is_employee:
    col_docs, col_reports = st.columns(2)
    with col_docs:
        _recent_documents()
    with col_reports:
        st.markdown("##### Recent reports")
        if report_err:
            show_api_error(report_err)
        elif not report_result["items"]:
            st.caption("No reports yet — ask the chat assistant to generate one.")
        else:
            for report in report_result["items"]:
                with card(f"dash_report_{report['id']}"):
                    st.markdown(f"**{report['title']}** ({report['format']}, {report['row_count']} rows)")
                    st.caption(report["created_at"])

# Pending approvals — the fallback discovery surface now that the dedicated
# Approvals page is gone (views/chat.py's in-chat dialog is the convenient
# immediate-action path for the requester's own just-created employee_pii
# request, but any authorized reviewer — not just whoever triggered it —
# still needs a way to find and decide a request later; without this
# section, dismissing the chat dialog would leave that request unreachable).
# Same visibility rule main.py used for the old page's nav entry:
# MANAGE_EMPLOYEE_PII is granted to hr/ceo/admin (llm_rbac.yaml), matching
# GET/POST /approvals' own require_role(ADMIN, CEO, HR) — HR's results are
# already scoped server-side to their own department, same as before.
if has_permission(caps_result, MANAGE_EMPLOYEE_PII):
    st.divider()
    st.markdown("##### Pending approvals")
    pending_result, pending_err = _safe(api_client.list_approvals, status="pending", limit=20)
    if pending_err:
        show_api_error(pending_err)
    elif not pending_result["items"]:
        st.caption("Nothing pending.")
    else:
        for item in pending_result["items"]:
            try:
                detail = api_client.get_approval(item["id"])
            except APIError as exc:
                with card(f"dash_approval_{item['id']}"):
                    st.caption(f"Request {item['id']} — could not load details: {exc.message}")
                continue
            payload = detail.get("payload") or {}
            with card(f"dash_approval_{item['id']}"):
                header = f"{detail['action']} · {detail['target_type']}"
                if payload.get("employee_id"):
                    header += f" · {payload['employee_id']}"
                st.markdown(f"**{header}**")
                requester = detail.get("requested_by_email") or f"role `{detail['role']}`"
                st.caption(f"Requested by {requester} · {detail['created_at']}")
                if payload.get("raw_message"):
                    st.caption(f"“{payload['raw_message']}”")
                # employee_pii add/modify/store needs an explicit confirmed
                # value before approving — same rule as the chat dialog and
                # the original Approvals page: approving with an empty
                # values dict would flip status to "active" without ever
                # writing anything, silently no-op'ing the actual change.
                dash_values: dict[str, str] = {}
                if detail["target_type"] == "employee_pii" and detail["action"] in ("add", "modify", "store"):
                    for field in ("full_name", "email", "phone", "address", "government_id"):
                        entered = st.text_input(
                            field.replace("_", " ").title(), key=f"dash_val_{item['id']}_{field}",
                        )
                        if entered:
                            dash_values[field] = entered
                approve_col, reject_col = st.columns(2)
                if approve_col.button("Approve", key=f"dash_approve_{item['id']}", use_container_width=True, type="primary"):
                    try:
                        api_client.decide_approval(item["id"], "approved", values=dash_values or None)
                        st.rerun()
                    except APIError as exc:
                        show_api_error(exc)
                if reject_col.button("Reject", key=f"dash_reject_{item['id']}", use_container_width=True):
                    try:
                        api_client.decide_approval(item["id"], "rejected")
                        st.rerun()
                    except APIError as exc:
                        show_api_error(exc)

if _user["role"] == "admin":
    st.divider()
    st.markdown("##### Operations snapshot")
    st.caption("Admin-only — see Operations → Admin / Evaluation for full detail.")

    gateway_result, gateway_err = _safe(api_client.get_gateway_usage, limit=1)
    collections_result, collections_err = _safe(api_client.list_collections)
    guardrail_result, guardrail_err = _safe(api_client.get_guardrail_analytics)
    eval_result, eval_err = _safe(api_client.eval_summary)

    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Gateway cost (all time)", f"${gateway_result['total_cost_usd']:.4f}" if gateway_result else "—")
    o2.metric("Qdrant collections", len(collections_result) if collections_result is not None else "—")

    blocked = "—"
    if guardrail_result is not None:
        blocked = sum(r["block_count"] for r in guardrail_result["summary"]) if guardrail_result["summary"] else 0
    o3.metric("Guardrail blocks", blocked)

    avg_groundedness = "—"
    if eval_result is not None and eval_result["avg_groundedness"] is not None:
        avg_groundedness = f"{eval_result['avg_groundedness']:.2f}"
    o4.metric("Avg groundedness", avg_groundedness)
    metric_cards()

    # These four calls almost always fail together (backend unreachable) — show
    # one error rather than stacking up to four identical red boxes.
    first_err = next((e for e in (gateway_err, collections_err, guardrail_err, eval_err) if e), None)
    if first_err:
        show_api_error(first_err)
