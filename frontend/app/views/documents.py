import threading
import time
import uuid

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

import api_client
from api_client import APIError
from components import card, debug_json, explorable_table, metric_cards, page_header, show_api_error, status_badge
from permissions import DELETE_DOCUMENTS, MANAGE_DOCUMENTS, UPLOAD_DOCUMENTS, has_permission

# Permission-driven (not a hardcoded role set) — matches whatever
# llm_rbac.yaml's rbac_permissions actually grants the caller's role, so
# this stays correct automatically as roles/permissions change (e.g. HR
# gained Upload Documents in the enterprise permission model without this
# file needing an update). The backend's require_permission() on each
# endpoint is the real, unbypassable gate — hiding controls here is only
# for not showing a button that would just 403.
try:
    _capabilities = api_client.get_my_capabilities()
except APIError:
    _capabilities = None
_can_upload = has_permission(_capabilities, UPLOAD_DOCUMENTS)
_can_delete = has_permission(_capabilities, DELETE_DOCUMENTS)
_can_manage = has_permission(_capabilities, MANAGE_DOCUMENTS)

page_header(
    "Documents", "document",
    "Browse and inspect ingestion pipeline results." if not _can_upload
    else "Upload, browse, and inspect ingestion pipeline results.",
    color="blue-green-70",
)

if "selected_document_id" not in st.session_state:
    st.session_state.selected_document_id = None

# Order matches app/services/monitoring/progress.py's STAGES list server-side.
LIVE_STAGES = [
    ("parse", "Parsing"),
    ("summarize", "Summarizing"),
    ("entity", "Extracting entities"),
    ("chunk", "Chunking"),
    ("embed", "Embedding"),
    ("sparse", "Computing sparse (BM25) vectors"),
    ("sparse_index", "Indexing sparse terms"),
    ("qdrant_upsert", "Upserting into Qdrant"),
]
LIVE_STAGE_LABELS = dict(LIVE_STAGES)


def _stage_lines(stages: dict) -> str:
    lines = []
    for key, label in LIVE_STAGES:
        s = stages.get(key, {"status": "pending", "elapsed_ms": None})
        if s["status"] == "done":
            lines.append(f"✅ {label} — {s['elapsed_ms']:.0f} ms")
        elif s["status"] == "running":
            lines.append(f"🔄 {label} — running…")
        else:
            lines.append(f"⬜ {label} — pending")
    return "\n\n".join(lines)


def _run_upload_with_progress(file, previous_version_of: str | None) -> dict:
    """Uploads `file`, live-polling /documents/{id}/progress while the
    (synchronous, potentially minutes-long) upload request is still in
    flight. The upload itself runs on a background thread since `requests`
    blocks; this thread just polls and repaints a progress bar until it
    joins."""
    client_document_id = str(uuid.uuid4())
    result_box: dict = {}

    def _do_upload():
        try:
            result_box["doc"] = api_client.upload_document(
                file.name, file.getvalue(), file.type, previous_version_of, client_document_id
            )
        except APIError as exc:
            result_box["error"] = exc

    thread = threading.Thread(target=_do_upload, daemon=True)
    # A plain threading.Thread has no Streamlit ScriptRunContext, so
    # st.session_state is invisible to it — _do_upload()'s call into
    # api_client.upload_document() would see an empty session_state and send
    # the request with no Authorization header at all (a real 401 "missing
    # bearer token", not an expired-token case), no matter how recently the
    # user logged in. add_script_run_ctx() propagates this run's context to
    # the thread so st.session_state resolves correctly inside it.
    add_script_run_ctx(thread)
    thread.start()

    progress_bar = st.progress(0.0, text=f"Uploading {file.name}…")
    step_placeholder = st.empty()
    start_time = time.time()

    while thread.is_alive():
        elapsed = time.time() - start_time
        try:
            prog = api_client.get_ingestion_progress(client_document_id)
        except APIError:
            prog = None
        if prog:
            stages = prog["stages"]
            frac = sum(1 for s in stages.values() if s["status"] == "done") / len(stages)
            current_label = LIVE_STAGE_LABELS.get(prog["current_stage"], "starting")
            progress_bar.progress(frac, text=f"{current_label} — {elapsed:.0f}s elapsed")
            step_placeholder.markdown(_stage_lines(stages))
        else:
            progress_bar.progress(0.0, text=f"Starting… {elapsed:.0f}s elapsed")
        time.sleep(0.5)

    thread.join()

    if "error" in result_box:
        progress_bar.empty()
        step_placeholder.empty()
        raise result_box["error"]

    try:
        final_prog = api_client.get_ingestion_progress(client_document_id)
    except APIError:
        final_prog = None
    progress_bar.progress(1.0, text=f"Done — {time.time() - start_time:.0f}s total")
    if final_prog:
        step_placeholder.markdown(_stage_lines(final_prog["stages"]))

    return result_box["doc"]


if _can_upload:
    tab_upload, tab_library, tab_detail = st.tabs(["Upload", "Library", "Detail"])
else:
    tab_upload = None
    tab_library, tab_detail = st.tabs(["Library", "Detail"])

if tab_upload is not None:
    with tab_upload:
        with st.form("upload_form", clear_on_submit=True):
            file = st.file_uploader("Choose a file", type=None)
            previous_version_of = st.text_input(
                "Previous version ID (optional)",
                help="Paste an existing document's UUID to upload this as a new version of it.",
            )
            submitted = st.form_submit_button("Upload", type="primary")

        if submitted:
            if not file:
                st.warning("Choose a file first.")
            else:
                try:
                    doc = _run_upload_with_progress(file, previous_version_of or None)
                except APIError as exc:
                    show_api_error(exc)
                else:
                    if doc["status"] == "completed":
                        st.success(f"Uploaded **{doc['filename']}** — {doc['chunk_count']} chunks.")
                    else:
                        st.warning(f"Uploaded but {status_badge(doc['status'])}: {doc['error_message']}")
                    debug_json(doc, "Raw /documents/upload response")

with tab_library:
    col1, col2 = st.columns([1, 1])
    limit = col1.number_input("Page size", min_value=5, max_value=200, value=25, step=5)
    offset = col2.number_input("Offset", min_value=0, value=0, step=int(limit))

    try:
        result = api_client.list_documents(limit=limit, offset=offset)
    except APIError as exc:
        show_api_error(exc)
    else:
        st.caption(f"{result['total']} document(s) total")
        for doc in result["items"]:
            with card(f"doc_card_{doc['id']}"):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{doc['filename']}** — {status_badge(doc['status'])}")
                c1.caption(
                    f"{doc['document_type']} · {doc['classification'] or 'unclassified'} · "
                    f"v{doc['version_number']} · {doc['chunk_count']} chunks"
                )
                if c2.button("Open", key=f"open_{doc['id']}"):
                    st.session_state.selected_document_id = doc["id"]
                    st.rerun()
        debug_json(result, "Raw /documents response")

with tab_detail:
    doc_id = st.session_state.selected_document_id or st.text_input("Document ID")
    if not doc_id:
        st.info("Pick a document from the Library tab, or paste an ID above.")
    else:
        try:
            doc = api_client.get_document(doc_id)
        except APIError as exc:
            show_api_error(exc)
        else:
            st.subheader(doc["filename"])
            st.caption(f"ID: `{doc['id']}` · lineage: `{doc['lineage_id']}` · version {doc['version_number']}")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Status", doc["status"])
            m2.metric("Chunks", doc["chunk_count"])
            m3.metric("Classification", doc["classification"] or "—")
            m4.metric("Confidence", f"{doc['classification_confidence']:.2f}" if doc["classification_confidence"] else "—")
            metric_cards()

            if doc["summary"]:
                st.markdown("**Summary**")
                st.write(doc["summary"])

            btn_col1, btn_col2 = st.columns(2)
            # Reindex was previously ungated in the UI (and on the backend —
            # see routers/documents.py's now-added MANAGE_DOCUMENTS check).
            if _can_manage and btn_col1.button("Reindex (re-embed chunks)"):
                try:
                    with st.spinner("Re-embedding…"):
                        api_client.reindex_document(doc_id)
                    st.success("Reindexed.")
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)
            if _can_delete and btn_col2.button("Delete document", type="secondary"):
                try:
                    api_client.delete_document(doc_id)
                    st.session_state.selected_document_id = None
                    st.success("Deleted.")
                    st.rerun()
                except APIError as exc:
                    show_api_error(exc)

            sub_pipeline, sub_chunks, sub_entities, sub_versions, sub_perms = st.tabs(
                ["Pipeline", "Chunks", "Entities", "Versions", "Permissions"]
            )

            with sub_pipeline:
                try:
                    pipeline_chunks = api_client.get_document_chunks(doc_id, limit=200)
                except APIError as exc:
                    show_api_error(exc)
                    pipeline_chunks = []

                st.markdown("### 1️⃣ Parse")
                with st.container(border=True):
                    st.caption(
                        f"{doc['metadata']['page_count'] or '—'} pages · "
                        f"{doc['metadata']['language'] or 'unknown language'} · "
                        f"{doc['metadata']['table_count']} tables · {doc['metadata']['image_count']} images"
                    )
                    if doc["metadata"]["headings"]:
                        st.caption("Headings: " + ", ".join(doc["metadata"]["headings"][:10]))
                    try:
                        parsed_text = api_client.get_document_text(doc_id)["text"]
                    except APIError as exc:
                        st.warning(f"Parsed text unavailable: {exc.message}")
                    else:
                        preview = parsed_text[:1000]
                        st.text_area("Extracted text (preview)", preview, height=200, disabled=True)
                        with st.expander(f"Full parsed text ({len(parsed_text):,} chars)"):
                            st.text(parsed_text)

                st.markdown("### 2️⃣ Chunk")
                with st.container(border=True):
                    if not pipeline_chunks:
                        st.caption("No chunks produced.")
                    else:
                        strategy = pipeline_chunks[0]["strategy"]
                        st.caption(f"{len(pipeline_chunks)} chunks · strategy: {strategy}")
                        for c in pipeline_chunks[:20]:
                            with st.expander(f"Chunk {c['chunk_index']} — {c['token_count']} tokens"):
                                st.write(c["text"])
                        if len(pipeline_chunks) > 20:
                            st.caption(f"…and {len(pipeline_chunks) - 20} more (see Chunks tab).")

                st.markdown("### 3️⃣ Embed")
                with st.container(border=True):
                    if not pipeline_chunks:
                        st.caption("No chunks to embed.")
                    else:
                        embedded_count = sum(1 for c in pipeline_chunks if c.get("qdrant_point_id"))
                        model_names = {c.get("embedding_model") for c in pipeline_chunks if c.get("embedding_model")}
                        model_label = ", ".join(m for m in model_names if m) or "—"
                        e1, e2 = st.columns(2)
                        e1.metric("Embedded chunks", f"{embedded_count}/{len(pipeline_chunks)}")
                        e2.metric("Model", model_label)
                        st.caption("Vectors are stored in Qdrant, not returned to the API — this confirms each chunk was embedded and indexed.")

            with sub_chunks:
                try:
                    chunks = api_client.get_document_chunks(doc_id, limit=50)
                except APIError as exc:
                    show_api_error(exc)
                else:
                    for c in chunks:
                        size_label = f"target {c['chunk_size_tokens']}" if c["chunk_size_tokens"] is not None else "target n/a"
                        overlap_label = f"overlap {c['overlap_tokens']}" if c["overlap_tokens"] is not None else "overlap n/a"
                        title = (
                            f"Chunk {c['chunk_index']} — {c['token_count']} tokens "
                            f"({size_label}, {overlap_label}) · {c['strategy']}"
                        )
                        with st.expander(title):
                            st.write(c["text"])

            with sub_entities:
                try:
                    entities = api_client.get_document_entities(doc_id)
                except APIError as exc:
                    show_api_error(exc)
                else:
                    if entities:
                        explorable_table(entities)
                    else:
                        st.caption("No entities extracted.")

            with sub_versions:
                try:
                    versions = api_client.get_document_versions(doc_id)
                except APIError as exc:
                    show_api_error(exc)
                else:
                    if versions["versions"]:
                        explorable_table(versions["versions"])
                    else:
                        st.caption("No other versions.")

            with sub_perms:
                # Grant/list/revoke were previously ungated in the UI (and on
                # the backend — see routers/documents.py's now-added
                # MANAGE_DOCUMENTS check) — granting access is itself a
                # privileged action, so a role without it doesn't even get
                # the list call (would just 403).
                if not _can_manage:
                    st.caption("You don't have permission to view or manage this document's access grants.")
                else:
                    try:
                        perms = api_client.list_permissions(doc_id)
                    except APIError as exc:
                        show_api_error(exc)
                        perms = []
                    if perms:
                        explorable_table(perms)
                    else:
                        st.caption("No explicit permission grants.")

                    with st.form(f"grant_perm_{doc_id}", clear_on_submit=True):
                        g1, g2, g3 = st.columns([2, 1, 1])
                        grant_user_id = g1.text_input("User ID")
                        level = g2.selectbox("Level", ["read", "write", "admin"])
                        if g3.form_submit_button("Grant"):
                            try:
                                api_client.grant_permission(doc_id, grant_user_id, level)
                                st.success("Granted.")
                                st.rerun()
                            except APIError as exc:
                                show_api_error(exc)
