import time

import streamlit as st

from api_client import BACKEND_URL, send_chat_message
from theme import hero, inject_theme

st.set_page_config(page_title="Pipeline - rag-chat", page_icon="🧭", layout="wide")
inject_theme()
hero("🧭 Pipeline", "How a chat message actually turns into a grounded answer — click any stage to see what it does.")

tab_gen, tab_agents, tab_live = st.tabs(
    ["① Response Generation", "② Multi-Agent Routing", "③ Try it live"]
)

# ---------------------------------------------------------------------------
# Tab 1 — Response Generation
# ---------------------------------------------------------------------------
GEN_STAGES = [
    ("📥", "Retrieved Context", "Chunks pulled from Qdrant (hybrid semantic + keyword) and/or rows "
     "pulled from PostgreSQL — already filtered, and reranked when relevant."),
    ("🧱", "Prompt Builder", "The system prompt, conversation summary, saved user preferences, and "
     "any tool results are assembled into the context Claude actually sees."),
    ("🤖", "Claude", "The model (adaptive thinking enabled) reasons over that context and either "
     "writes a reply or calls another tool to gather more before answering."),
    ("✅", "Grounded Response", "A reply built only from what the tools returned — never outside "
     "knowledge the model happens to know."),
    ("🔖", "Source Citation", "Bracketed numbers like [1], [2] tie each claim back to the exact "
     "retrieved chunk it came from."),
]

with tab_gen:
    st.subheader("Use Claude for reasoning")
    st.write("Every reply — whether or not it needed a tool — flows through this pipeline.")

    if "gen_selected" not in st.session_state:
        st.session_state.gen_selected = GEN_STAGES[0][1]

    cols = st.columns(len(GEN_STAGES) * 2 - 1)
    for i, (icon, name, _) in enumerate(GEN_STAGES):
        col = cols[i * 2]
        with col:
            selected = st.session_state.gen_selected == name
            if st.button(f"{icon}\n\n{name}", key=f"gen-{name}", use_container_width=True,
                         type="primary" if selected else "secondary"):
                st.session_state.gen_selected = name
        if i * 2 + 1 < len(cols):
            with cols[i * 2 + 1]:
                st.markdown("<div style='text-align:center; padding-top:1.6em;'>➜</div>", unsafe_allow_html=True)

    detail = next(d for _, n, d in GEN_STAGES if n == st.session_state.gen_selected)
    with st.container(border=True):
        st.markdown(f"**{st.session_state.gen_selected}**")
        st.write(detail)

# ---------------------------------------------------------------------------
# Tab 2 — Multi-Agent RAG
# ---------------------------------------------------------------------------
AGENTS = {
    "🧭 Planner Agent": [
        "Understand intent",
        "Decide required tools",
        "Route queries",
        "Parallel execution",
    ],
    "🔎 Retrieval Agent": [
        "Search Qdrant",
        "Hybrid retrieval",
        "Metadata filtering",
    ],
    "🗄️ SQL Agent": [
        "Query PostgreSQL",
        "Analytics",
        "Dashboard metrics",
    ],
    "🧾 Report Agent": [
        "Generate CSV",
        "Generate Excel",
        "Company templates",
    ],
    "🧩 Response Synthesizer": [
        "Merge tool outputs",
        "Resolve citations",
        "Write the final, grounded reply",
    ],
}

with tab_agents:
    st.subheader("Build specialized agents")
    st.write("The Planner routes each request to one or more specialist agents — running independent "
             "parts in parallel — then the Synthesizer merges everything into one answer.")

    if "agent_selected" not in st.session_state:
        st.session_state.agent_selected = "🧭 Planner Agent"

    def _node_button(label: str) -> None:
        selected = st.session_state.agent_selected == label
        if st.button(label, key=f"node-{label}", use_container_width=True,
                     type="primary" if selected else "secondary"):
            st.session_state.agent_selected = label

    _, mid, _ = st.columns([2, 1, 2])
    with mid:
        st.markdown("<div style='text-align:center;'>🧑 User</div>", unsafe_allow_html=True)
        st.markdown("<div style='text-align:center;'>↓</div>", unsafe_allow_html=True)
        _node_button("🧭 Planner Agent")
        st.markdown("<div style='text-align:center;'>↓ fans out to ↓</div>", unsafe_allow_html=True)

    a, b, c = st.columns(3)
    with a:
        _node_button("🔎 Retrieval Agent")
    with b:
        _node_button("🗄️ SQL Agent")
    with c:
        _node_button("🧾 Report Agent")

    _, mid2, _ = st.columns([2, 1, 2])
    with mid2:
        st.markdown("<div style='text-align:center;'>↓ merges into ↓</div>", unsafe_allow_html=True)
        _node_button("🧩 Response Synthesizer")

    with st.container(border=True):
        st.markdown(f"**{st.session_state.agent_selected} — responsibilities**")
        for item in AGENTS[st.session_state.agent_selected]:
            st.markdown(f"- {item}")

    st.divider()
    st.markdown("**Example flow (from the design spec)**")
    st.code(
        "User\n"
        "  → \"Generate employee attendance report\"\n"
        "  → Retrieve Data\n"
        "  → Create CSV\n"
        "  → Download",
        language=None,
    )

# ---------------------------------------------------------------------------
# Tab 3 — Try it live
# ---------------------------------------------------------------------------
EXAMPLE_PROMPT = "Generate a report of document counts by classification"

with tab_live:
    st.subheader("Run the real pipeline")
    st.write(
        "This system doesn't have HR data, so instead of the attendance-report example, this sends a "
        "prompt the SQL Agent and Report Agent can actually satisfy against your uploaded documents:"
    )
    st.code(EXAMPLE_PROMPT, language=None)

    custom_prompt = st.text_input("Or try your own prompt", value="")
    run_prompt = custom_prompt.strip() or EXAMPLE_PROMPT

    if st.button("▶ Run this live", type="primary"):
        status = st.status("🧭 Planner Agent is deciding how to route this...", expanded=True)
        try:
            result = send_chat_message(run_prompt)
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Error contacting backend: {exc}")
        else:
            for step in result.get("trace", []):
                icon = {"Planner Agent": "🧭", "Retrieval Agent": "🔎", "SQL Agent": "🗄️",
                        "Report Agent": "🧾", "Response Synthesizer": "🧩"}.get(step["agent"], "⚙️")
                status.write(f"{icon} **{step['agent']}** (`{step['tool']}`) — {step['summary']}")
                time.sleep(0.35)
            status.update(label="Done", state="complete", expanded=True)

            st.markdown("**Reply**")
            st.write(result["reply"])

            report = result.get("report")
            if report:
                url = f"{BACKEND_URL}{report['download_url']}"
                st.markdown(
                    f"📄 **{report['title']}** ({report['format'].upper()}, {report['row_count']} rows) "
                    f"— [Download]({url})"
                )

            sources = result.get("sources", [])
            if sources:
                with st.expander(f"Sources ({len(sources)})"):
                    for s in sources:
                        filename = s.get("document_filename") or s["document_id"]
                        st.markdown(f"**[{s['index']}] {filename}** (chunk {s['chunk_index']})")
                        st.caption(s["text"][:500])
