import streamlit as st

import api_client
from api_client import APIError
from components import MODEL_TIER_LABELS, page_header, show_api_error, sorted_model_tiers

page_header(
    "Roles & Permissions", "shield",
    "Read-only view of each role's permission, tool, and quota configuration.",
    color="violet-70",
)

st.caption(
    "Role definitions live in backend/config/llm_rbac.yaml — editing them is an "
    "infrastructure change, not something this page writes back. MANAGE_ROLES is "
    "reserved for a future admin UI that authors RBAC changes safely."
)

try:
    data = api_client.get_roles()
except APIError as exc:
    show_api_error(exc)
    st.stop()

for role in data["roles"]:
    with st.container(border=True):
        st.markdown(f"### {role['display_name']}")
        st.caption(f"`{role['role']}`" + (f" · default department: {role['department_default']}" if role['department_default'] else ""))

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Model tiers**")
            ordered_tiers = sorted_model_tiers(role["tiers_allowed"])
            st.caption(", ".join(MODEL_TIER_LABELS.get(t, t.capitalize()) for t in ordered_tiers) or "—")
            st.markdown("**Tools**")
            st.caption(", ".join(role["tools"]) or "—")
            st.markdown("**Knowledge departments**")
            st.caption(", ".join(role["knowledge_departments"]) or "— (unrestricted)")

        with col2:
            st.markdown("**Permissions**")
            if role["all_permissions"]:
                st.caption("Everything — unrestricted (Admin).")
            else:
                for perm in role["granted_permissions"]:
                    st.caption(f"• {perm}")

        quotas = role["quotas"]
        st.markdown("**Quotas**")
        q1, q2, q3 = st.columns(3)
        q1.metric("Requests/min", quotas.get("requests_per_minute") or "Unlimited")
        q2.metric("Daily requests", quotas.get("daily_requests") or "Unlimited")
        q3.metric("Max concurrent", quotas.get("max_concurrent_requests") or "Unlimited")
