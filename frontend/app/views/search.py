import streamlit as st

import api_client
from api_client import APIError
from components import card, debug_json, page_header, show_api_error

page_header("Search", "🔎", "Hybrid dense + BM25 retrieval over your knowledge base.", color="violet-70")

# /search is LLM-RBAC-governed the same way /chat is (docs/LLM_RBAC_ARCHITECTURE.md).
if not st.session_state.get("current_user"):
    st.warning("Log in to search the knowledge base — see the Login page.")
    st.stop()

with st.form("search_form"):
    query = st.text_input("Query")
    c1, c2, c3 = st.columns(3)
    mode = c1.selectbox("Mode", ["hybrid", "semantic", "keyword"])
    top_k = c2.slider("Top K", 1, 50, 10)
    rerank = c3.checkbox("Rerank (cross-encoder)", value=True)

    with st.expander("Filters"):
        f1, f2, f3 = st.columns(3)
        document_type = f1.text_input("Document type") or None
        classification = f2.text_input("Classification") or None
        language = f3.text_input("Language") or None
        latest_only = st.checkbox("Latest version only", value=True)

    submitted = st.form_submit_button("Search", type="primary")

if submitted:
    if not query.strip():
        st.warning("Enter a query.")
    else:
        filters = {
            "document_type": document_type,
            "classification": classification,
            "language": language,
            "latest_version_only": latest_only,
        }
        with st.spinner("Searching…"):
            try:
                # POST {BACKEND_URL}/search — same hybrid dense+BM25 → RRF → rerank
                # pipeline the chat agent's search_documents tool uses internally.
                result = api_client.search(query, mode=mode, top_k=top_k, rerank=rerank, filters=filters)
            except APIError as exc:
                show_api_error(exc)
                st.stop()

        st.caption(f"{result['total']} result(s) · mode={result['mode']} · reranked={result['reranked']}")
        for i, hit in enumerate(result["results"]):
            with card(f"search_hit_{i}"):
                st.markdown(f"**{hit.get('document_filename') or hit['document_id']}** — score `{hit['score']:.4f}`")
                st.caption(f"chunk {hit['chunk_index']} · strategy: {hit['strategy']}")
                st.write(hit["text"])

        debug_json(result, "Raw /search response")
