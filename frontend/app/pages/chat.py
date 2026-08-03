import streamlit as st

from api_client import (
    BACKEND_URL,
    create_user,
    get_conversation,
    get_user_preferences,
    list_conversations,
    list_users,
    put_user_preferences,
    send_chat_message,
)
from theme import hero, inject_theme

st.set_page_config(page_title="Chat - rag-chat", page_icon="💬", layout="wide")
inject_theme()
hero("💬 Chat", "Ask questions grounded in your documents, run analytics, or generate reports.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None


def _new_conversation() -> None:
    st.session_state.messages = []
    st.session_state.conversation_id = None


def _load_conversation(conversation_id: str) -> None:
    try:
        convo = get_conversation(conversation_id)
    except Exception as exc:
        st.sidebar.error(f"Could not load conversation: {exc}")
        return
    st.session_state.conversation_id = convo["id"]
    st.session_state.messages = [
        {
            "role": m["role"],
            "text": m["content"],
            "sources": m.get("sources") or [],
            "report": m.get("report"),
        }
        for m in convo["messages"]
    ]


with st.sidebar:
    st.subheader("You")
    email_input = st.text_input("Email", value=st.session_state.user_email or "")
    if st.button("Set user"):
        if not email_input.strip():
            st.error("Enter an email.")
        else:
            try:
                existing = next((u for u in list_users() if u["email"] == email_input.strip().lower()), None)
                user = existing or create_user(email_input.strip())
            except Exception as exc:
                st.error(f"Could not set user: {exc}")
            else:
                st.session_state.user_id = user["id"]
                st.session_state.user_email = user["email"]
                _new_conversation()
                st.rerun()

    if st.session_state.user_id:
        st.caption(f"Signed in as {st.session_state.user_email}")

        st.divider()
        st.subheader("Conversations")
        if st.button("➕ New conversation"):
            _new_conversation()
            st.rerun()
        try:
            convos = list_conversations(user_id=st.session_state.user_id)["items"]
        except Exception as exc:
            st.error(f"Could not load conversations: {exc}")
            convos = []
        for c in convos:
            label = (c["title"] or "Untitled")[:40]
            active = c["id"] == st.session_state.conversation_id
            if st.button(("● " if active else "") + label, key=f"convo-{c['id']}"):
                _load_conversation(c["id"])
                st.rerun()

        st.divider()
        st.subheader("Preferences")
        try:
            prefs = get_user_preferences(st.session_state.user_id)
        except Exception:
            prefs = {}
        with st.form("preferences-form"):
            tone = st.selectbox(
                "Answer tone", options=["default", "concise", "detailed"],
                index=["default", "concise", "detailed"].index(prefs.get("tone", "default")),
            )
            timezone = st.text_input("Timezone", value=prefs.get("timezone", ""))
            if st.form_submit_button("Save preferences"):
                try:
                    put_user_preferences(st.session_state.user_id, {"tone": tone, "timezone": timezone})
                except Exception as exc:
                    st.error(f"Could not save preferences: {exc}")
                else:
                    st.success("Saved.")
    else:
        if st.button("➕ New conversation"):
            _new_conversation()
            st.rerun()
        st.caption("Set a user above to save conversation history and preferences.")


def _render_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        for s in sources:
            filename = s.get("document_filename") or s["document_id"]
            st.markdown(f"**[{s['index']}] {filename}** (chunk {s['chunk_index']})")
            st.caption(s["text"][:500])


def _render_report(report: dict | None) -> None:
    if not report:
        return
    url = f"{BACKEND_URL}{report['download_url']}"
    st.markdown(f"📄 **{report['title']}** ({report['format'].upper()}, {report['row_count']} rows) — [Download]({url})")


_AGENT_ICONS = {
    "Planner Agent": "🧭",
    "Retrieval Agent": "🔎",
    "SQL Agent": "🗄️",
    "Report Agent": "🧾",
    "Response Synthesizer": "🧩",
}


def _render_trace(trace: list[dict]) -> None:
    if not trace:
        return
    agents_used = " → ".join(dict.fromkeys(t["agent"] for t in trace))
    with st.expander(f"🧭 Agent trace · {agents_used}"):
        for i, step in enumerate(trace, start=1):
            icon = _AGENT_ICONS.get(step["agent"], "⚙️")
            st.markdown(f"**{i}. {icon} {step['agent']}** — `{step['tool']}`")
            if step.get("input"):
                st.caption(f"in: {step['input'][:200]}")
            st.caption(f"→ {step['summary']}")
            if i < len(trace):
                st.markdown("&nbsp;&nbsp;&nbsp;↓", unsafe_allow_html=True)
        st.caption("See the **🧭 Pipeline** page for how this maps to the multi-agent architecture.")


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["text"])
        _render_sources(msg.get("sources", []))
        _render_report(msg.get("report"))
        _render_trace(msg.get("trace", []))

prompt = st.chat_input("Type a message...")
if prompt:
    st.session_state.messages.append({"role": "user", "text": prompt, "sources": [], "report": None, "trace": []})
    with st.chat_message("user"):
        st.write(prompt)

    sources: list[dict] = []
    report: dict | None = None
    trace: list[dict] = []
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                result = send_chat_message(
                    prompt,
                    conversation_id=st.session_state.conversation_id,
                    user_id=st.session_state.user_id,
                )
                reply = result["reply"]
                sources = result.get("sources", [])
                report = result.get("report")
                trace = result.get("trace", [])
                st.session_state.conversation_id = result.get("conversation_id", st.session_state.conversation_id)
            except Exception as exc:
                reply = f"Error contacting backend: {exc}"
        st.write(reply)
        _render_sources(sources)
        _render_report(report)
        _render_trace(trace)

    st.session_state.messages.append(
        {"role": "assistant", "text": reply, "sources": sources, "report": report, "trace": trace}
    )
