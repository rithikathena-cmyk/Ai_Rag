import streamlit as st

from api_client import (
    create_collection,
    create_user,
    delete_collection,
    delete_document,
    get_metrics,
    list_collections,
    list_documents,
    list_upload_logs,
    list_users,
    reindex_document,
    update_user,
)
from theme import badge, hero, inject_theme, section_title

st.set_page_config(page_title="Admin - rag-chat", page_icon="🛠️", layout="wide")
inject_theme()
hero("🛠️ Admin Dashboard", "User management, document/collection administration, and system monitoring.")

tab_users, tab_docs, tab_stats = st.tabs(["👤 Users", "📄 Documents & Collections", "📈 Statistics"])

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
with tab_users:
    section_title("➕ Create user")
    with st.form("admin-create-user"):
        col1, col2 = st.columns(2)
        with col1:
            new_email = st.text_input("Email")
        with col2:
            new_name = st.text_input("Display name (optional)")
        if st.form_submit_button("Create", type="primary"):
            if not new_email.strip():
                st.error("Enter an email.")
            else:
                try:
                    create_user(new_email.strip(), new_name.strip() or None)
                except Exception as exc:
                    st.error(f"Could not create user: {exc}")
                else:
                    st.success(f"Created {new_email}.")
                    st.rerun()

    st.divider()
    section_title("👥 Users")
    try:
        users = list_users(limit=500)
    except Exception as exc:
        st.error(f"Could not load users: {exc}")
        users = []

    if not users:
        st.write("No users yet.")
    else:
        st.dataframe(
            [
                {
                    "email": u["email"], "display_name": u["display_name"] or "—",
                    "role": u["role"], "active": u["is_active"], "created_at": u["created_at"],
                }
                for u in users
            ],
            use_container_width=True,
        )

        st.markdown("**Edit a user**")
        labels = [f"{u['email']} ({str(u['id'])[:8]})" for u in users]
        chosen = st.selectbox("User", options=labels, key="admin-user-select")
        target = users[labels.index(chosen)]
        st.markdown(
            f"Currently: {badge(target['role'])} {badge('active' if target['is_active'] else 'inactive')}",
            unsafe_allow_html=True,
        )

        col_role, col_active, col_save = st.columns([2, 2, 1])
        with col_role:
            new_role = st.selectbox(
                "Role", options=["user", "admin"],
                index=["user", "admin"].index(target["role"]), key=f"role-{target['id']}",
            )
        with col_active:
            new_active = st.checkbox("Active", value=target["is_active"], key=f"active-{target['id']}")
        with col_save:
            st.write("")
            st.write("")
            if st.button("Save", key=f"save-{target['id']}"):
                try:
                    update_user(str(target["id"]), role=new_role, is_active=new_active)
                except Exception as exc:
                    st.error(f"Could not update user: {exc}")
                else:
                    st.success("Updated.")
                    st.rerun()

# ---------------------------------------------------------------------------
# Documents & Collections
# ---------------------------------------------------------------------------
with tab_docs:
    section_title("📄 Documents")
    try:
        docs = list_documents(limit=500)["items"]
    except Exception as exc:
        st.error(f"Could not load documents: {exc}")
        docs = []

    if not docs:
        st.write("No documents ingested yet. Use the **Documents** page to upload one.")
    else:
        st.dataframe(
            [
                {
                    "filename": d["filename"], "status": d["status"], "classification": d["classification"] or "—",
                    "version": d["version_number"], "latest": d["is_latest_version"],
                    "chunks": d["chunk_count"], "created_at": d["created_at"],
                }
                for d in docs
            ],
            use_container_width=True,
        )

        doc_labels = [f"{d['filename']} ({str(d['id'])[:8]})" for d in docs]
        doc_choice = st.selectbox("Manage a document", options=doc_labels, key="admin-doc-select")
        doc_target = docs[doc_labels.index(doc_choice)]
        doc_id = str(doc_target["id"])

        col_reindex, col_delete = st.columns(2)
        with col_reindex:
            if st.button("🔁 Re-index (recompute embeddings)", key=f"reindex-{doc_id}"):
                with st.spinner("Re-embedding and re-indexing..."):
                    try:
                        reindex_document(doc_id)
                    except Exception as exc:
                        st.error(f"Re-index failed: {exc}")
                    else:
                        st.success("Re-indexed.")
                        st.rerun()
        with col_delete:
            confirm = st.checkbox("Confirm permanent delete", key=f"admin-confirm-del-{doc_id}")
            if st.button("🗑️ Delete", disabled=not confirm, key=f"admin-del-{doc_id}"):
                try:
                    delete_document(doc_id)
                except Exception as exc:
                    st.error(f"Could not delete: {exc}")
                else:
                    st.success("Deleted.")
                    st.rerun()

    st.divider()
    section_title("🗂️ Qdrant collections")
    try:
        collections = list_collections()
    except Exception as exc:
        st.error(f"Could not load collections: {exc}")
        collections = []

    if collections:
        st.dataframe(
            [
                {"name": c["name"], "points": c["points_count"], "status": c["status"],
                 "primary (in use)": c["is_primary"]}
                for c in collections
            ],
            use_container_width=True,
        )

    with st.expander("➕ Create collection"):
        with st.form("admin-create-collection"):
            col_name, col_size, col_dist = st.columns(3)
            with col_name:
                coll_name = st.text_input("Name")
            with col_size:
                coll_size = st.number_input("Vector size", min_value=1, value=1024)
            with col_dist:
                coll_dist = st.selectbox("Distance", options=["Cosine", "Euclid", "Dot"])
            if st.form_submit_button("Create"):
                if not coll_name.strip():
                    st.error("Enter a collection name.")
                else:
                    try:
                        create_collection(coll_name.strip(), int(coll_size), coll_dist)
                    except Exception as exc:
                        st.error(f"Could not create collection: {exc}")
                    else:
                        st.success(f"Created '{coll_name}'.")
                        st.rerun()

    non_primary = [c["name"] for c in collections if not c["is_primary"]]
    if non_primary:
        del_choice = st.selectbox("Delete a collection", options=["(none)"] + non_primary, key="admin-del-collection")
        if del_choice != "(none)" and st.button("🗑️ Delete collection", key="admin-del-collection-btn"):
            try:
                delete_collection(del_choice)
            except Exception as exc:
                st.error(f"Could not delete collection: {exc}")
            else:
                st.success(f"Deleted '{del_choice}'.")
                st.rerun()
    else:
        st.caption("The active document collection can't be deleted here; create another to test deletion.")

# ---------------------------------------------------------------------------
# Statistics / Monitoring
# ---------------------------------------------------------------------------
with tab_stats:
    section_title("📥 Upload history")
    outcome_filter = st.selectbox("Filter by outcome", options=["all", "success", "degraded", "rejected"])
    try:
        logs = list_upload_logs(outcome=None if outcome_filter == "all" else outcome_filter, limit=200)["items"]
    except Exception as exc:
        st.error(f"Could not load upload logs: {exc}")
        logs = []

    failed = [entry for entry in logs if entry["outcome"] != "success"]
    col_total, col_failed = st.columns(2)
    col_total.metric("Uploads shown", len(logs))
    col_failed.metric("Failed / degraded", len(failed))

    if logs:
        st.dataframe(
            [
                {
                    "filename": entry["filename"] or "—", "outcome": entry["outcome"],
                    "error_code": entry["error_code"] or "—", "created_at": entry["created_at"],
                }
                for entry in logs
            ],
            use_container_width=True,
        )
    else:
        st.write("No upload activity yet.")

    st.divider()
    section_title("⏱️ Retrieval & chat latency")
    try:
        metrics = get_metrics()
    except Exception as exc:
        st.error(f"Could not load metrics: {exc}")
        metrics = {"latency_summary": [], "latency_samples": [], "token_usage_summary": [], "token_usage_samples": []}

    latency_summary = metrics["latency_summary"]
    if latency_summary:
        st.dataframe(
            [
                {"endpoint": s["endpoint"], "requests": s["count"],
                 "avg_ms": round(s["avg_ms"], 1), "p95_ms": round(s["p95_ms"], 1)}
                for s in latency_summary
            ],
            use_container_width=True,
        )
        st.bar_chart({s["endpoint"]: s["avg_ms"] for s in latency_summary})
    else:
        st.write("No requests recorded yet this session — use Chat or Search, then come back.")

    st.divider()
    section_title("🔤 Token usage")
    token_summary = metrics["token_usage_summary"]
    if token_summary:
        st.dataframe(
            [
                {"source": t["source"], "model": t["model"], "calls": t["call_count"],
                 "input_tokens": t["total_input_tokens"], "output_tokens": t["total_output_tokens"]}
                for t in token_summary
            ],
            use_container_width=True,
        )
        st.bar_chart(
            {
                f"{t['source']}": t["total_input_tokens"] + t["total_output_tokens"]
                for t in token_summary
            }
        )
    else:
        st.write("No model calls recorded yet this session.")

    st.caption(
        "Latency and token usage are tracked in-process for this backend session (not persisted across restarts)."
    )
