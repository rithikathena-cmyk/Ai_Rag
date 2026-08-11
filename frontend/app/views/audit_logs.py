import streamlit as st

import api_client
from api_client import APIError
from components import explorable_table, page_header, show_api_error

# VIEW_AUDIT_LOGS-gated in main.py's nav (CEO/Admin per llm_rbac.yaml) — same
# convention as views/roles.py: no separate frontend permission check, GET
# /upload-logs' own require_permission(VIEW_AUDIT_LOGS) is the real gate.
page_header("Audit Logs", "🧾", "Document ingestion history — every upload attempt, success or failure.", color="red")

outcome_filter = st.selectbox("Outcome", ["All", "success", "rejected", "failed"], label_visibility="collapsed")

try:
    logs = api_client.list_upload_logs(outcome=None if outcome_filter == "All" else outcome_filter, limit=200)
except APIError as exc:
    show_api_error(exc)
    st.stop()

st.caption(f"{logs['total']} total")

if not logs["items"]:
    st.caption("No upload activity recorded yet.")
else:
    explorable_table(
        [
            {
                "When": item["created_at"], "Filename": item["filename"] or "—", "Outcome": item["outcome"],
                "Size": f"{item['file_size_bytes'] / 1024:.1f} KB" if item["file_size_bytes"] else "—",
                "Error": item["error_message"] or "—",
            }
            for item in logs["items"]
        ]
    )
