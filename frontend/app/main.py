import streamlit as st

from api_client import get_eval_summary, get_health, list_conversations, list_documents, list_reports
from theme import badge, hero, inject_theme, section_title

st.set_page_config(page_title="rag-chat", page_icon="📚", layout="wide")
inject_theme()

try:
    get_health()
    status_html = badge("connected")
    status_text = "All systems operational"
except Exception:
    status_html = badge("error")
    status_text = "Backend unreachable"

hero("📚 rag-chat", "Hybrid retrieval, multi-agent chat, and document intelligence over your own files.")
st.markdown(f"{status_html}&nbsp;&nbsp;<span style='color:#667085;'>{status_text}</span>", unsafe_allow_html=True)
st.write("")


def _safe_count(fn, *args, key="total", **kwargs) -> str:
    try:
        result = fn(*args, **kwargs)
        return str(result[key] if isinstance(result, dict) else len(result))
    except Exception:
        return "—"


def _safe_run_count() -> str:
    try:
        return str(get_eval_summary()["run_count"])
    except Exception:
        return "—"


section_title("📊 At a glance")
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("📄 Documents", _safe_count(list_documents, limit=1))
kpi2.metric("🧾 Reports generated", _safe_count(list_reports, limit=1))
kpi3.metric("💬 Conversations", _safe_count(list_conversations, limit=1))
kpi4.metric("🧪 Eval runs", _safe_run_count())

st.write("")
section_title("🧭 Explore")

FEATURES = [
    ("💬", "Chat", "Ask questions grounded in your documents, run analytics, or generate reports.", "pages/chat.py"),
    ("📄", "Documents", "Upload files, browse ingested content, and manage chunks, entities, versions, and access.", "pages/documents.py"),
    ("🔍", "Search", "Run hybrid, semantic, or keyword search directly against the retrieval pipeline.", "pages/search.py"),
    ("🧾", "Reports", "Browse and download reports generated from chat.", "pages/reports.py"),
    ("🧭", "Pipeline", "See how a message flows through the multi-agent architecture, and try it live.", "pages/pipeline.py"),
    ("🛠️", "Admin", "Manage users and roles, re-index or remove documents, and monitor latency and token usage.", "pages/admin.py"),
    ("🧪", "Evaluation", "Score retrieval (Recall@K, MRR, nDCG) and generation quality with curated eval sets.", "pages/evaluation.py"),
]

cols = st.columns(3)
for i, (icon, title, description, target) in enumerate(FEATURES):
    with cols[i % 3]:
        with st.container(border=True):
            st.markdown(f"#### {icon} {title}")
            st.write(description)
            st.page_link(target, label="Open →")

st.write("")
st.caption("Tip: the sidebar always lists every page — this view is just a faster way in.")
