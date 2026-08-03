import streamlit as st

from api_client import search_documents
from theme import inject_theme, hero

st.set_page_config(page_title="Search - rag-chat", page_icon="🔍", layout="wide")
inject_theme()
hero("🔍 Search", "Run the hybrid retrieval pipeline directly — semantic, keyword, or both, with metadata filters.")

with st.form("search-form"):
    query = st.text_input("Query", placeholder="e.g. onboarding mentor process")
    col1, col2, col3 = st.columns(3)
    with col1:
        mode = st.radio("Mode", options=["hybrid", "semantic", "keyword"], horizontal=True)
    with col2:
        top_k = st.slider("Top K", min_value=1, max_value=50, value=10)
    with col3:
        rerank = st.checkbox("Rerank", value=True)

    col4, col5 = st.columns(2)
    with col4:
        document_type = st.text_input("Filter: document type", placeholder="pdf, txt, sql...")
    with col5:
        classification = st.text_input("Filter: classification", placeholder="SOP, Legal, Financial...")

    submitted = st.form_submit_button("Search", type="primary")

if submitted:
    if not query.strip():
        st.error("Enter a query.")
    else:
        try:
            with st.spinner("Searching..."):
                result = search_documents(
                    query, mode=mode, top_k=top_k, rerank=rerank,
                    document_type=document_type or None, classification=classification or None,
                )
        except Exception as exc:
            st.error(f"Search failed: {exc}")
        else:
            st.caption(
                f"{result['total']} result(s) · mode={result['mode']} · reranked={result['reranked']}"
            )
            if not result["results"]:
                st.write("No matches.")
            for hit in result["results"]:
                with st.container(border=True):
                    filename = hit.get("document_filename") or hit["document_id"]
                    st.markdown(f"**{filename}** · chunk {hit['chunk_index']} · score `{hit['score']:.4f}`")
                    preview = hit["text"][:300] + ("…" if len(hit["text"]) > 300 else "")
                    st.write(preview)
                    if len(hit["text"]) > 300:
                        with st.expander("Full text"):
                            st.write(hit["text"])
