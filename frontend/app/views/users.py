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
# is the real enforcement regardless of what this shows. Setting a per-user
# token limit uses this same Admin+CEO grant, see below.
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

    # Search-filtered, not a row-per-user list: with ~200 seeded demo
    # accounts, rendering 3 widgets (role select + active toggle + save
    # button) per row meant 400+ widgets on every rerun — slow to open and
    # slow on every interaction (same problem the token-limit editor below
    # already solved with a picker). Nothing renders until there's a query,
    # and matches are capped so a broad search can't reintroduce the same
    # widget-count problem.
    _MAX_MATCHES = 20
    _query = st.text_input(
        "Search by email or display name", key="user_search_query", placeholder="Type to search…",
    ).strip().lower()
    if not _query:
        st.caption(f"{len(_users)} accounts — type above to search by email or display name.")
    else:
        _matches = [u for u in _users if _query in u["email"].lower() or _query in (u.get("display_name") or "").lower()]
        if not _matches:
            st.caption("No accounts match your search.")
        elif len(_matches) > _MAX_MATCHES:
            st.caption(f"{len(_matches)} accounts match — showing the first {_MAX_MATCHES}. Refine your search to narrow it down.")
        else:
            st.caption(f"{len(_matches)} account(s) match.")
    for u in (_matches[:_MAX_MATCHES] if _query else []):
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

# Per-user token-limit override is Admin+CEO — a broader grant than
# MANAGE_USERS (checked by role, same as account creation above), since CEO
# doesn't have MANAGE_USERS but should still be able to cap a user's token
# budget. PUT /users/{id}/token-limit is the real enforcement; this check is
# UI-only.
#
# One user editor, not a row of number_input per user like the role/status
# editor above: with ~200 seeded demo accounts, rendering two number_inputs
# per row (400+ widgets) made the expander take minutes to open in practice
# (confirmed live) — a picker + single editor keeps this page responsive
# regardless of how many accounts exist.
if _can_create:
    with st.expander("Set a user's token limit", expanded=False, icon=":material/speed:"):
        st.caption(
            "Caps an individual user's daily/monthly token budget below their role's default "
            "(llm_rbac.yaml). Clear a field to fall back to the role default."
        )
        _overridden = [u for u in _users if u.get("daily_token_limit_override") or u.get("monthly_token_limit_override")]
        if _overridden:
            st.caption(f"{len(_overridden)} account(s) currently have an override set:")
            explorable_table(
                [
                    {
                        "Email": u["email"], "Role": u["role"],
                        "Daily limit": u.get("daily_token_limit_override") or "Role default",
                        "Monthly limit": u.get("monthly_token_limit_override") or "Role default",
                    }
                    for u in _overridden
                ]
            )

        _by_email = {u["email"]: u for u in _users}
        _picked_email = st.selectbox("Account", sorted(_by_email), key="token_limit_target_email")
        _target = _by_email[_picked_email]

        # Current usage for the picked account — shown so an admin can see
        # *why* a limit looks exhausted (already-accrued usage this period,
        # unaffected by changing the limit — see reset_usage()'s docstring
        # on the backend) before reaching for either control below.
        try:
            _target_usage = api_client.get_user_usage(_target["id"])
            st.caption(
                f"Current usage — today: {_target_usage['daily_tokens_used']:,} tokens · "
                f"this month: {_target_usage['monthly_tokens_used']:,} tokens."
            )
        except APIError as exc:
            _target_usage = None
            show_api_error(exc)

        c1, c2, c3 = st.columns([2, 2, 1.5])
        new_daily = c1.number_input(
            "Daily tokens", min_value=1, step=1000, value=_target.get("daily_token_limit_override"),
            key=f"daily_limit_{_target['id']}", placeholder="Role default",
        )
        new_monthly = c2.number_input(
            "Monthly tokens", min_value=1, step=10000, value=_target.get("monthly_token_limit_override"),
            key=f"monthly_limit_{_target['id']}", placeholder="Role default",
        )
        if c3.button("Save", key=f"save_limit_{_target['id']}", use_container_width=True):
            if new_daily == _target.get("daily_token_limit_override") and new_monthly == _target.get("monthly_token_limit_override"):
                st.toast("No changes to save.")
            else:
                try:
                    api_client.set_user_token_limit(_target["id"], new_daily, new_monthly)
                    st.success(f"Updated token limit for {_target['email']}.")
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)

        # Deliberately a separate action from Save above, not a side effect
        # of it — see reset_usage()'s docstring in services/llm_rbac/
        # quotas.py. Saving a new limit alone never touches accrued usage;
        # this is the explicit "give this user a clean slate" control for
        # when an admin actually wants that (e.g. unblocking someone whose
        # usage already exceeds a limit that was just raised).
        st.caption("Resetting usage is separate from the limit above — it zeroes today's and this month's accrued usage without changing the limit.")
        if st.button(
            f"Reset {_target['email']}'s usage to 0", key=f"reset_usage_{_target['id']}",
            disabled=_target_usage is not None and _target_usage["daily_tokens_used"] == 0 and _target_usage["monthly_tokens_used"] == 0,
        ):
            try:
                api_client.reset_user_usage(_target["id"])
                st.success(f"Reset usage for {_target['email']}.")
                st.rerun()
            except APIError as exc:
                show_api_error(exc)
