import streamlit as st

import api_client
from api_client import APIError
from components import INK, MUTED, PRIMARY, show_api_error

st.markdown(
    f"""
    <div style="text-align:center; padding: 2rem 0 0.5rem;">
        <div style="width:52px; height:52px; margin:0 auto 0.75rem; border-radius:12px;
                    background:{PRIMARY}; display:flex;
                    align-items:center; justify-content:center; font-size:26px; color:#FFFFFF;">✦</div>
        <h1 style="margin:0; font-size:1.6rem; color:{INK};">RAG Platform</h1>
        <p style="color:{MUTED}; margin-top:0.35rem;">Sign in to chat, search, and manage your knowledge base.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _do_login(email: str, password: str) -> None:
    tokens = api_client.login(email, password)
    st.session_state["access_token"] = tokens["access_token"]
    st.session_state["refresh_token"] = tokens["refresh_token"]
    # Fetched right after login (not decoded from the token) so the sidebar
    # and this page always reflect the account's *current* role/active
    # status, same as the backend's own get_current_user dependency does.
    st.session_state["current_user"] = api_client.get_current_user_info()


_, center, _ = st.columns([1, 1.2, 1])

with center:
    if st.session_state.get("access_token"):
        user = st.session_state.get("current_user") or {}
        with st.container(border=True):
            st.success(f"Logged in as **{user.get('email', '?')}** — role: `{user.get('role', '?')}`")
            st.caption(f"User ID: `{user.get('id', '?')}`")
            col1, col2 = st.columns(2)
            if col1.button("Go to Dashboard →", type="primary", use_container_width=True):
                st.switch_page("views/dashboard.py")
            if col2.button("Log out", use_container_width=True):
                api_client.logout()
                st.rerun()
        st.info(
            "This ID is used automatically as the Chat page's User ID, which drives per-document "
            "permission filtering — see docs/AGENT_SECURITY_MODEL.md."
        )
    else:
        with st.container(border=True):
            # No self-service registration — account creation is Admin/CEO-only
            # (POST /users requires one of those roles on the backend now; see
            # routers/users.py::create_user). An Admin or CEO creates accounts
            # from the Users page after logging in, not from here.
            with st.form("login_form"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", type="primary", use_container_width=True):
                    if not email or not password:
                        st.warning("Enter both an email and a password.")
                    else:
                        try:
                            _do_login(email, password)
                            # Can't st.switch_page("views/chat.py") here directly:
                            # main.py builds its role-gated `pages` dict at the
                            # top of the script, before nav.run() reaches this
                            # code — so on the very run login succeeds, Chat
                            # isn't registered yet. Defer one rerun so main.py
                            # rebuilds `pages` with the now-current role first,
                            # then does the actual switch_page itself.
                            st.session_state["_redirect_after_login"] = True
                            st.rerun()
                        except APIError as exc:
                            show_api_error(exc)
            st.caption("Don't have an account? Ask an Admin or CEO to create one for you.")
