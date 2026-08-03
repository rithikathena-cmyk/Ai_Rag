import streamlit as st

from api_client import (
    delete_document,
    get_document_chunks,
    get_document_entities,
    get_document_versions,
    grant_permission,
    list_document_permissions,
    list_documents,
    list_users,
    revoke_permission,
    upload_document,
)
from theme import badge, hero, inject_theme, section_title

st.set_page_config(page_title="Documents - rag-chat", page_icon="📄", layout="wide")
inject_theme()
hero("📄 Documents", "Upload files, browse ingested content, and manage chunks, entities, versions, and access.")

SUPPORTED_TYPES = [
    "pdf", "docx", "pptx", "html", "htm", "png", "jpg", "jpeg", "tif", "tiff", "bmp",
    "md", "markdown", "txt", "xlsx", "xls", "csv", "json", "xml", "sql",
]

try:
    _existing = list_documents(limit=200)["items"]
except Exception:
    _existing = []

section_title("⬆️ Upload")
col_upload, col_version = st.columns([2, 1])
with col_upload:
    uploaded_file = st.file_uploader("Upload a document", type=SUPPORTED_TYPES)
with col_version:
    version_choice = st.selectbox(
        "Upload as a new version of (optional)",
        options=["(new document)"] + [f"{d['filename']} ({str(d['id'])[:8]})" for d in _existing],
    )
    previous_version_of = None
    if version_choice != "(new document)":
        idx = ([f"{d['filename']} ({str(d['id'])[:8]})" for d in _existing]).index(version_choice)
        previous_version_of = str(_existing[idx]["id"])

if uploaded_file is not None and st.button("Upload", type="primary"):
    with st.spinner(f"Parsing {uploaded_file.name}..."):
        try:
            result = upload_document(uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type, previous_version_of)
        except Exception as exc:
            st.error(f"Upload failed: {exc}")
        else:
            st.success(f"Ingested {uploaded_file.name} as {result['document_type']} (v{result['version_number']})")
            st.json(result["metadata"])
            st.rerun()

st.divider()
section_title("📚 Ingested documents")

col_refresh, col_filter = st.columns([1, 3])
with col_refresh:
    if st.button("Refresh"):
        st.rerun()
with col_filter:
    name_filter = st.text_input("Filter by filename", value="", placeholder="type to filter...")

try:
    data = list_documents(limit=200)
except Exception as exc:
    st.error(f"Could not load documents: {exc}")
    data = {"items": []}

items = data["items"]
if name_filter:
    items = [d for d in items if name_filter.lower() in d["filename"].lower()]

if not items:
    st.write("No documents match." if name_filter else "No documents ingested yet.")
else:
    st.dataframe(
        [
            {
                "filename": item["filename"],
                "type": item["document_type"],
                "classification": item["classification"] or "—",
                "status": item["status"],
                "version": item["version_number"],
                "latest": item["is_latest_version"],
                "title": item["metadata"]["title"],
                "pages": item["metadata"]["page_count"],
                "chunks": item["chunk_count"],
                "created_at": item["created_at"],
            }
            for item in items
        ],
        use_container_width=True,
    )

st.divider()
section_title("🔎 Document details")

if not items:
    st.write("Upload a document to see its details here.")
else:
    labels = [f"{d['filename']} ({str(d['id'])[:8]})" for d in items]
    selected_label = st.selectbox("Select a document", options=labels)
    selected = items[labels.index(selected_label)]
    doc_id = str(selected["id"])

    st.markdown(
        f"### {selected['filename']} {badge(selected['status'])}"
        + (" " + badge('latest') if selected['is_latest_version'] else ""),
        unsafe_allow_html=True,
    )

    tab_overview, tab_chunks, tab_entities, tab_versions, tab_permissions = st.tabs(
        ["Overview", "Chunks", "Entities", "Versions", "Permissions"]
    )

    with tab_overview:
        meta = selected["metadata"]
        st.write(f"**Title:** {meta['title'] or '—'}")
        st.write(f"**Author:** {meta['author'] or '—'}")
        st.write(f"**Language:** {meta['language'] or '—'}")
        st.write(f"**Classification:** {selected['classification'] or '—'} "
                 f"({selected['classification_method'] or 'n/a'}, "
                 f"confidence {selected['classification_confidence'] or 0:.2f})")
        st.write(f"**Keywords:** {', '.join(meta['keywords']) or '—'}")
        if selected["summary"]:
            st.write("**Summary:**")
            st.caption(selected["summary"])

        st.divider()
        confirm_delete = st.checkbox("I understand this permanently deletes this document, its chunks, and vectors", key=f"confirm-del-{doc_id}")
        if st.button("🗑️ Delete this document", disabled=not confirm_delete, key=f"del-{doc_id}"):
            try:
                delete_document(doc_id)
            except Exception as exc:
                st.error(f"Could not delete: {exc}")
            else:
                st.success("Deleted.")
                st.rerun()

    with tab_chunks:
        try:
            chunks = get_document_chunks(doc_id)
        except Exception as exc:
            st.error(f"Could not load chunks: {exc}")
            chunks = []
        if not chunks:
            st.write("No chunks yet.")
        for c in chunks:
            with st.container(border=True):
                st.caption(f"#{c['chunk_index']} · {c['strategy']} · {c['token_count']} tokens")
                st.write(c["text"][:400] + ("…" if len(c["text"]) > 400 else ""))

    with tab_entities:
        try:
            entities = get_document_entities(doc_id)
        except Exception as exc:
            st.error(f"Could not load entities: {exc}")
            entities = []
        if not entities:
            st.write("No entities extracted yet.")
        else:
            st.dataframe(entities, use_container_width=True)

    with tab_versions:
        try:
            versions = get_document_versions(doc_id)["versions"]
        except Exception as exc:
            st.error(f"Could not load versions: {exc}")
            versions = []
        if not versions:
            st.write("No version history.")
        else:
            st.dataframe(
                [
                    {
                        "version": v["version_number"],
                        "filename": v["filename"],
                        "status": v["status"],
                        "is_latest": v["is_latest_version"],
                        "created_at": v["created_at"],
                    }
                    for v in versions
                ],
                use_container_width=True,
            )

    with tab_permissions:
        try:
            perms = list_document_permissions(doc_id)
        except Exception as exc:
            st.error(f"Could not load permissions: {exc}")
            perms = []
        if not perms:
            st.write("No permissions granted yet.")
        else:
            st.dataframe(
                [{"user_id": p["user_id"], "level": p["permission_level"], "created_at": p["created_at"]} for p in perms],
                use_container_width=True,
            )

        st.markdown("**Grant access**")
        try:
            users = list_users()
        except Exception:
            users = []
        if not users:
            st.caption("No users exist yet — create one from the Chat page first.")
        else:
            user_labels = [f"{u['email']} ({str(u['id'])[:8]})" for u in users]
            with st.form(key=f"grant-{doc_id}"):
                grant_user_label = st.selectbox("User", options=user_labels)
                grant_level = st.selectbox("Level", options=["read", "write", "admin"])
                if st.form_submit_button("Grant"):
                    grant_user = users[user_labels.index(grant_user_label)]
                    try:
                        grant_permission(doc_id, str(grant_user["id"]), grant_level)
                    except Exception as exc:
                        st.error(f"Could not grant permission: {exc}")
                    else:
                        st.success("Granted.")
                        st.rerun()

        if perms:
            revoke_labels = [f"{p['user_id']}" for p in perms]
            revoke_choice = st.selectbox("Revoke access for", options=["(none)"] + revoke_labels, key=f"revoke-{doc_id}")
            if revoke_choice != "(none)" and st.button("Revoke", key=f"revoke-btn-{doc_id}"):
                try:
                    revoke_permission(doc_id, revoke_choice)
                except Exception as exc:
                    st.error(f"Could not revoke permission: {exc}")
                else:
                    st.success("Revoked.")
                    st.rerun()
