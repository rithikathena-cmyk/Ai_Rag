import streamlit as st

import api_client
from api_client import APIError, get_health
from components import activity_log_panel, conversation_row, inject_global_styles, sidebar_brand, sidebar_profile
from permissions import (
    SYSTEM_SETTINGS, VIEW_ANALYTICS, VIEW_AUDIT_LOGS, VIEW_DOCUMENTS, VIEW_ROLES, VIEW_USERS,
    has_permission,
)

st.set_page_config(page_title="RAG Platform", page_icon=":material/auto_awesome:", layout="wide")
inject_global_styles()

_user = st.session_state.get("current_user")
_role = _user["role"] if _user else None

# Fetched once here (not per-page) so every page's nav-visibility check below
# uses the same snapshot. A fetch failure fails closed (None -> has_permission
# always False) — worse case is a permitted page briefly missing from nav,
# never a denied one appearing, and every page still enforces its own real
# check against the backend regardless of what nav shows.
_capabilities = None
if _user:
    try:
        _capabilities = api_client.get_my_capabilities()
    except APIError:
        _capabilities = None

# Nav visibility only — never authorization. Every one of these pages still
# calls the same backend endpoints, which enforce the real permission/role/
# department/ownership rules (see docs/LLM_RBAC_ARCHITECTURE.md and the
# enterprise permission model) regardless of what's shown here. Built via
# has_permission() checks against /users/me/capabilities' granted_permissions
# — NOT role string branching — so a role automatically gets the right nav
# just from what llm_rbac.yaml's rbac_permissions grants it (brief §10).
pages = {
    "Account": [
        st.Page("views/login.py", title="Login", icon=":material/lock:"),
    ],
}

if _user:
    # Chat is available to every logged-in role, including Employee — an
    # Employee's whole surface is the chatbot (Dashboard for their own usage/
    # quota summary, Chat to actually use it). Employee does NOT get
    # VIEW_DOCUMENTS (llm_rbac.yaml — only HR/PM/CEO/Admin do), so no
    # Documents/Search/Reports/Analytics/Administration nav for them at all;
    # the backend enforces the same line, not just this nav check (every
    # GET /documents* route now requires VIEW_DOCUMENTS too).
    pages["Overview"] = [st.Page("views/dashboard.py", title="Dashboard", icon=":material/home:")]
    pages["Assistant"] = [st.Page("views/chat.py", title="Chat", icon=":material/chat_bubble_outline:")]

    if has_permission(_capabilities, VIEW_DOCUMENTS):
        pages["Knowledge base"] = [st.Page("views/documents.py", title="Documents", icon=":material/description:")]

    # Search/Reports aren't in the 12-permission catalog either — mapped to
    # VIEW_ANALYTICS (HR/PM/CEO/Admin per the matrix, not Employee), matching
    # today's exact "not Employee" behavior while now being permission-driven.
    if has_permission(_capabilities, VIEW_ANALYTICS):
        pages.setdefault("Knowledge base", []).append(st.Page("views/search.py", title="Search", icon=":material/search:"))
        pages["Outputs"] = [st.Page("views/reports.py", title="Reports", icon=":material/bar_chart:")]
        pages["Analytics"] = [st.Page("views/metrics.py", title="Query Metrics", icon=":material/monitoring:")]

    # Users/Audit Logs — VIEW_USERS and VIEW_AUDIT_LOGS have been part of the
    # permission catalog since the RBAC pass (granted to HR/PM/CEO/Admin and
    # CEO/Admin respectively — see llm_rbac.yaml), and api_client already had
    # list_users()/list_upload_logs() wired for them, but no page consumed
    # either yet. Added here (read-only, plus an edit control on Users gated
    # on the separate MANAGE_USERS permission — see views/users.py) so this
    # sidebar redesign's per-role nav isn't linking to pages that don't exist.
    if has_permission(_capabilities, VIEW_USERS):
        pages.setdefault("Administration", []).append(st.Page("views/users.py", title="Users", icon=":material/group:"))

    if has_permission(_capabilities, VIEW_ROLES):
        pages.setdefault("Administration", []).append(
            st.Page("views/roles.py", title="Roles & Permissions", icon=":material/shield_person:")
        )

    if has_permission(_capabilities, VIEW_AUDIT_LOGS):
        pages.setdefault("Administration", []).append(st.Page("views/audit_logs.py", title="Audit Logs", icon=":material/receipt_long:"))

    if has_permission(_capabilities, SYSTEM_SETTINGS):
        # views/admin.py mixes real system-config UI (Qdrant collections,
        # model-availability toggle) with operational-analytics tabs in one
        # page — splitting those into permission-scoped sub-pages is a
        # further frontend refactor, out of scope here, so this page stays
        # System-Settings-gated (Admin-only) rather than also opening it to
        # CEO for just its analytics tabs (CEO's analytics access is the
        # Query Metrics page above, same as HR/PM).
        pages.setdefault("Administration", []).append(st.Page("views/admin.py", title="System Settings", icon=":material/tune:"))

    # Evaluation is cost-sensitive (real LLM calls) and genuinely role-based
    # on the backend (require_role(ADMIN, CEO), not a coarse permission) —
    # mirrored here as a role check rather than forcing it into the
    # permission catalog it doesn't belong in.
    if _role in ("admin", "ceo"):
        pages.setdefault("Administration", []).append(st.Page("views/evaluation.py", title="Evaluation", icon=":material/science:"))

if st.session_state.pop("_redirect_after_login", False) and "Assistant" in pages:
    # Pass the actual StreamlitPage object just built into `pages`, not a
    # bare path string — switch_page() needs to resolve against the exact
    # page instance registered with st.navigation() in *this* run (see
    # views/login.py for why this can't happen on the same run as login).
    # Chat, not Dashboard — every role's actual work starts in chat, and
    # Chat is available to every logged-in role including Employee (see
    # this dict's own comment above), so "Assistant" is always present
    # whenever "Overview" would have been.
    st.switch_page(pages["Assistant"][0])

# position="hidden" turns off Streamlit's own auto-rendered page list, which
# always draws in its own fixed slot above anything a page/main.py puts in
# st.sidebar — there's no supported way to interleave Brand / New chat /
# Recent conversations / role nav / Settings / profile around it, which the
# design brief's sidebar hierarchy requires. We render the equivalent links
# ourselves via st.page_link() below instead, in the exact order we want;
# st.page_link()'s own active-page highlighting still works normally.
nav = st.navigation(pages, position="hidden")

with st.sidebar:
    sidebar_brand()

    if _user:
        # New chat / search / recent conversations — a persistent workspace
        # element like Claude's sidebar, not scoped to just the Chat page
        # (previously this lived only inside views/chat.py's own sidebar
        # block, invisible everywhere else). Every logged-in role gets
        # VIEW_CONVERSATIONS (llm_rbac.yaml), so no extra permission check
        # is needed here beyond being logged in.
        if st.button("New chat", use_container_width=True):
            st.session_state.conversation_id = None
            st.session_state.chat_messages = []
            st.switch_page("views/chat.py")

        search = st.text_input(
            "Search conversations", placeholder="Search conversations…",
            label_visibility="collapsed", key="sidebar_conv_search",
        )

        try:
            _recent = api_client.list_conversations(user_id=_user["id"], limit=50)["items"]
            _recent_err = None
        except APIError as exc:
            _recent = []
            _recent_err = exc

        if search:
            _recent = [c for c in _recent if search.lower() in (c["title"] or "").lower()]

        if _recent_err:
            st.caption(f"Couldn't load conversations — {_recent_err.message}")
        elif _recent:
            # Collapsed by default — a dropdown-style disclosure instead of
            # always rendering every conversation flatly in the sidebar,
            # which ate a lot of vertical space once there were more than a
            # handful. conversation_row()'s own click/delete handling is
            # unchanged; only the collapse wrapper around the loop is new.
            with st.expander(f"Recent ({len(_recent)})"):
                for c in _recent:
                    label = c["title"] or f"Conversation {c['id'][:8]}"
                    active = c["id"] == st.session_state.get("conversation_id")
                    title_clicked, delete_clicked = conversation_row(c["id"], label, active)
                    if title_clicked and not active:
                        try:
                            detail = api_client.get_conversation(c["id"])
                            st.session_state.conversation_id = c["id"]
                            st.session_state.chat_messages = [
                                {
                                    "role": m["role"], "content": m["content"], "sources": m["sources"] or [],
                                    "report": m["report"], "trace": [], "model_tier": None, "degraded": False,
                                }
                                for m in detail["messages"]
                            ]
                            st.switch_page("views/chat.py")
                        except APIError as exc:
                            st.error(exc.message)
                    if delete_clicked:
                        try:
                            api_client.delete_conversation(c["id"])
                            if active:
                                st.session_state.conversation_id = None
                                st.session_state.chat_messages = []
                            st.rerun()
                        except APIError as exc:
                            st.error(exc.message)

        # Role-aware navigation — a flat st.page_link() list, straight from
        # the permission-driven `pages` dict built above ("Account" is the
        # pre-login group, skipped here). No section-label headers ("Overview",
        # "Assistant", ...) between groups — just the links themselves.
        for section, section_pages in pages.items():
            if section == "Account":
                continue
            for p in section_pages:
                # st.page_link() does NOT inherit the icon from the
                # st.Page object automatically (verified against this
                # Streamlit version's source — icon stays None unless
                # passed explicitly here), even though that same icon
                # does drive Streamlit's own auto-generated nav (which
                # this app hides via position="hidden" above).
                st.page_link(p, icon=p.icon)

        # Settings — the brief's mockup places a single "⚙ Settings" entry
        # between nav and the profile row; these two controls (top-k,
        # reasoning trace) previously lived in views/chat.py's own sidebar
        # expander, only visible on the Chat page. Same session_state keys,
        # just relocated so they're reachable from anywhere, matching the
        # mockup's persistent Settings slot. A popover's body — like an
        # expander's — always runs every rerun regardless of open/closed
        # state, so these keys are set on every page load exactly as before.
        with st.popover("Settings", use_container_width=True):
            st.slider(
                "Top K sources", 1, 20, 5, key="chat_top_k",
                help="How many document chunks the chat assistant retrieves per search. Higher can improve recall on broad questions; lower keeps answers tighter and faster.",
            )
            st.checkbox(
                "Show reasoning summary", key="chat_show_reasoning",
                help="Show the guardrail & agent steps behind each chat reply.",
            )

        # Activity Log — guardrail checks + sources for every reply, live
        # inline in the sidebar (an st.expander, not a popover — see
        # activity_log_panel()'s own docstring for why that swap happened:
        # a popover's body renders through a portal with no size cap, and
        # once a session has enough turns/checks it grows tall/wide enough
        # to cover other page content). Reachable from any page, like
        # Settings above. Reads st.session_state.chat_messages directly
        # rather than a fresh API call — sources/trace are already returned
        # with every /chat response and stored there; nothing new to fetch.
        _chat_messages = st.session_state.get("chat_messages", [])
        _assistant_messages = [m for m in _chat_messages if m["role"] == "assistant" and m.get("trace")]
        activity_log_panel(_assistant_messages)

        # Spacer pushes everything below it to the bottom of the sidebar
        # when there's room (see inject_global_styles()'s .ep-spacer rule).
        st.markdown('<div class="ep-spacer"></div>', unsafe_allow_html=True)

        try:
            health = get_health()
            st.caption(f":green[●] Backend connected — {health['status']}")
        except APIError:
            st.caption(":red[●] Backend unreachable")

        _display_name = _user.get("display_name") or _user["email"].split("@")[0]
        sidebar_profile(_display_name, _role)
        if st.button("Sign out", use_container_width=True, key="sidebar_logout"):
            api_client.logout()
            st.switch_page("views/login.py")
    else:
        st.page_link(pages["Account"][0])
        st.markdown('<div class="ep-spacer"></div>', unsafe_allow_html=True)
        st.caption("Not logged in")

nav.run()
