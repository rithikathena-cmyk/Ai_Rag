import streamlit as st

import api_client
from api_client import APIError
from components import explorable_table, page_header, show_api_error
from permissions import MANAGE_USERS, has_permission

# VIEW_USERS-gated in main.py's nav (HR/PM/CEO/Admin per llm_rbac.yaml) — not
# re-checked here, same convention as views/roles.py: a user who reaches this
# page without the permission just gets a clean 403 box from the GET /users
# call below, since require_permission(VIEW_USERS) is the real backend gate.
try:
    _capabilities = api_client.get_my_capabilities()
except APIError:
    _capabilities = None
_can_manage = has_permission(_capabilities, MANAGE_USERS)

page_header(
    "Users", "group",
    "Directory of every account on this platform." if not _can_manage
    else "Directory of every account — role and active-status changes are audited on the backend.",
    color="violet",
)

# Account creation is Admin/CEO-only on the backend (POST /users requires
# require_role(ADMIN, CEO) — deliberately not the same MANAGE_USERS gate as
# the role/active-status editing below, which is Admin-only per the
# permission matrix). Checked here purely for UI display; the backend call
# is the real enforcement regardless of what this shows.
_current_role = (st.session_state.get("current_user") or {}).get("role")
_can_create = _current_role in ("admin", "ceo")

if _can_create:
    with st.expander("Create a new account", expanded=False, icon=":material/add:"):
        with st.form("create_user_form", clear_on_submit=True):
            new_email = st.text_input("Email", key="new_user_email")
            new_display_name = st.text_input("Display name (optional)", key="new_user_display_name")
            new_password = st.text_input(
                "Temporary password", type="password", key="new_user_password", help="At least 8 characters."
            )
            _ROLE_CREATE_OPTIONS = ["user", "hr", "project_manager", "ceo", "admin"]
            new_role = st.selectbox("Role", _ROLE_CREATE_OPTIONS, key="new_user_role")
            if st.form_submit_button("Create account", type="primary"):
                if not new_email or not new_password:
                    st.warning("Enter both an email and a password.")
                else:
                    try:
                        api_client.create_user(new_email, new_password, new_display_name or None, role=new_role)
                        st.success(f"Created {new_email}.")
                        st.rerun()
                    except APIError as exc:
                        show_api_error(exc)

try:
    _users = api_client.list_users(limit=200)
except APIError as exc:
    show_api_error(exc)
    st.stop()

if not _users:
    st.caption("No users yet.")
    st.stop()

if not _can_manage:
    explorable_table(
        [{"Email": u["email"], "Display name": u.get("display_name") or "—", "Role": u["role"], "Active": u["is_active"]} for u in _users]
    )
else:
    # MANAGE_USERS (Admin only, per the matrix) — role/active-status editing.
    # PATCH /users/{id} already existed in api_client, unused until now.
    _ROLE_OPTIONS = ["user", "hr", "project_manager", "ceo", "admin"]
    st.caption("Change a user's role or active status. Every change goes straight through PATCH /users/{id}.")
    for u in _users:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1.5])
            c1.markdown(f"**{u['email']}**  \n{u.get('display_name') or '—'}")
            new_role = c2.selectbox(
                "Role", _ROLE_OPTIONS, index=_ROLE_OPTIONS.index(u["role"]) if u["role"] in _ROLE_OPTIONS else 0,
                key=f"role_{u['id']}", label_visibility="collapsed",
            )
            new_active = c3.toggle("Active", value=u["is_active"], key=f"active_{u['id']}")
            if c4.button("Save", key=f"save_{u['id']}", use_container_width=True):
                if new_role == u["role"] and new_active == u["is_active"]:
                    st.toast("No changes to save.")
                else:
                    try:
                        api_client.update_user(
                            u["id"], role=new_role if new_role != u["role"] else None,
                            is_active=new_active if new_active != u["is_active"] else None,
                        )
                        st.success(f"Updated {u['email']}.")
                        st.rerun()
                    except APIError as exc:
                        show_api_error(exc)
