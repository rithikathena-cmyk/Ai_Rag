import streamlit as st
from streamlit_extras.stylable_container import stylable_container

import api_client
from api_client import APIError
from components import (
    BG_SECONDARY, INK, MODEL_TIER_CAPTIONS, MODEL_TIER_LABELS, MODEL_TIER_ORDER, PRIMARY, debug_json, show_api_error,
    sorted_model_tiers,
)
from permissions import UPLOAD_DOCUMENTS, has_permission

# /chat is LLM-RBAC-governed (docs/LLM_RBAC_ARCHITECTURE.md) and requires a
# verified identity — there is no anonymous mode anymore, since the caller's
# role/department drives which documents, tools, and model tier apply.
if not st.session_state.get("current_user"):
    st.warning("Log in to use the chat assistant — see the Login page.")
    st.stop()


def render_trace(trace):
    st.markdown("**Guardrail & agent steps**")
    for i, step in enumerate(trace, start=1):
        icon = "🛡️" if step["agent"] == "Guardrails" else "🧭"
        st.markdown(f"{i}. {icon} **{step['agent']} → `{step['tool']}`** — {step['summary']}")
        if step.get("input"):
            st.caption(f"input: {step['input']}")


# routers/chat.py's _guardrail_trace() formats every GuardrailStep's summary
# as "{action}: {detail}" (services/guardrails/types.py) — action is always
# one of these three literal prefixes, so a plain string-prefix check is
# exact, not a heuristic.
_GUARDRAIL_ICONS = {"pass": "✅", "block": "🚫", "redact": "✂️"}


def _guardrail_action(step: dict) -> str:
    return step["summary"].split(":", 1)[0]


def render_guardrail_checks(trace: list[dict]) -> None:
    """Every guardrail check that ran on this turn — input AND output side —
    with its pass/block/redact outcome, always visible (not gated behind the
    sidebar's "show reasoning" toggle like the full agent trace below,
    since the backend already computes and returns this on every single
    response regardless of that setting; hiding it by default just made it
    hard to find). routers/chat.py appends one trace entry per guardrail
    check that actually ran, blocked or not, so a passing check is exactly
    as visible here as a blocking one."""
    checks = [step for step in trace if step["agent"] == "Guardrails"]
    if not checks:
        return
    blocked = sum(1 for c in checks if _guardrail_action(c) != "pass")
    label = (
        f"🛡️ Guardrail checks — all {len(checks)} passed" if blocked == 0
        else f"🛡️ Guardrail checks — {len(checks) - blocked} passed, {blocked} flagged"
    )
    with st.expander(label, expanded=False):
        for check in checks:
            action = _guardrail_action(check)
            icon = _GUARDRAIL_ICONS.get(action, "•")
            st.markdown(f"{icon} **`{check['tool']}`** — {check['summary']}")


# "The next model" (the degraded-response retry button) walks forward
# through components.py's shared MODEL_TIER_ORDER, filtered to whichever of
# the three tiers this role actually has — see that module for why the
# order/labels/captions live there and not duplicated here.
_TIER_ORDER = MODEL_TIER_ORDER
_TIER_LABELS = MODEL_TIER_LABELS
_TIER_CAPTIONS = MODEL_TIER_CAPTIONS


def _next_tier(current: str, allowed: list[str]) -> str | None:
    candidates = [t for t in _TIER_ORDER if t in allowed and t != current]
    return candidates[0] if candidates else None


# Mirrors backend/app/gateway/schemas.py::GenerationErrorReason — the backend
# only ever sends us the enum value (never raw exception text/status codes),
# so this is purely presentation: pick the specific, accurate sentence for
# why this reply is degraded instead of one generic "model unavailable"
# message regardless of cause. Falls back to the "internal" phrasing for any
# reason string this build doesn't recognize (e.g. a backend deployed ahead
# of this frontend build added a new one) — never silently blank.
_DEGRADED_REASON_MESSAGES = {
    "no_api_key": "⚠️ No AI model is configured for this deployment — showing raw search results instead of a generated answer.",
    "model_disabled": "⚠️ The AI model has been temporarily disabled by an administrator — showing raw search results instead of a generated answer.",
    "auth_failed": "⚠️ The AI provider rejected our credentials — showing raw search results instead of a generated answer.",
    "provider_unavailable": "⚠️ The AI provider is temporarily unavailable — showing raw search results instead of a generated answer.",
    "provider_error": "⚠️ The AI provider could not process this request — showing raw search results instead of a generated answer.",
    "capacity": "⚠️ The AI model is at capacity right now — showing raw search results instead of a generated answer.",
    "internal": "⚠️ The AI model was unavailable for this reply — showing raw search results instead of a generated answer.",
}
_DEFAULT_DEGRADED_MESSAGE = _DEGRADED_REASON_MESSAGES["internal"]


def render_model_picker(allowed_tiers: list[str]) -> None:
    """Composer-footer model picker — a compact popover (Streamlit's own
    st.popover, the same pattern already used for the sidebar's conversation
    "⋯" menu and Settings) rather than a page-top radio, so it reads as part
    of the chat composer.

    allowed_tiers is already the caller's role-filtered list from
    GET /users/me/capabilities — server-derived from llm_rbac.yaml's
    tiers_allowed for this role, never computed here. This function only
    decides how to *display* an already-authorized set; it is not the
    security boundary. A request naming a tier outside this list still gets
    a 403 from authorize_llm_request()/_resolve_tier() on the backend
    (services/llm_rbac/engine.py) regardless of what this popover shows —
    verified live for every role during the role-based model access pass."""
    if not allowed_tiers:
        return
    selected = st.session_state.chat_model_tier
    selected_label = _TIER_LABELS.get(selected, selected.capitalize())

    if len(allowed_tiers) == 1:
        # Nothing to pick from — a plain label, not a picker with a single
        # inert option (the employee-only-model case).
        st.caption(f"Claude {selected_label}")
        return

    with st.popover(f"Claude {selected_label}  ▾"):
        for tier in allowed_tiers:
            label = _TIER_LABELS.get(tier, tier.capitalize())
            is_selected = tier == selected
            clicked = st.button(
                f"**Claude {label}**  ✓" if is_selected else f"Claude {label}",
                key=f"model_pick_{tier}", use_container_width=True,
            )
            st.caption(_TIER_CAPTIONS.get(tier, ""))
            if clicked and not is_selected:
                st.session_state.chat_model_tier = tier
                st.rerun()


def _retry_with_tier(idx: int, tier: str) -> None:
    """Re-sends the user message preceding chat_messages[idx] with an
    explicit model_tier override, replacing that degraded assistant entry in
    place. idx-1 is always the triggering user turn — messages strictly
    alternate user/assistant, appended in pairs, never reordered."""
    user_prompt = st.session_state.chat_messages[idx - 1]["content"]
    try:
        result = api_client.send_chat_message(
            user_prompt, conversation_id=st.session_state.conversation_id, top_k=st.session_state.chat_top_k,
            model_tier=tier,
        )
    except APIError as exc:
        show_api_error(exc)
        return
    st.session_state.chat_messages[idx] = {
        "role": "assistant", "content": result["reply"], "sources": result["sources"],
        "report": result["report"], "trace": result["trace"],
        "model_tier": result["model_tier"], "degraded": result["degraded"],
        "degraded_reason": result.get("degraded_reason"),
    }
    st.rerun()


def render_assistant_message(idx: int, msg: dict, allowed_tiers: list[str]) -> None:
    st.markdown(msg["content"])
    if msg.get("sources"):
        with st.expander(f"📎 {len(msg['sources'])} source(s)"):
            for s in msg["sources"]:
                st.markdown(f"**[{s['index']}] {s.get('document_filename') or s['document_id']}** (chunk {s['chunk_index']})")
                st.caption(s["text"])
    if msg.get("report"):
        r = msg["report"]
        st.info(f"📊 Report generated: **{r['title']}** ({r['format']}, {r['row_count']} rows) — see the Reports page to download it.")

    if msg.get("degraded"):
        st.warning(_DEGRADED_REASON_MESSAGES.get(msg.get("degraded_reason"), _DEFAULT_DEGRADED_MESSAGE))
        tier = _next_tier(msg.get("model_tier", ""), allowed_tiers)
        if tier:
            if st.button(f"🔁 Try again with {tier.capitalize()}", key=f"retry_{idx}"):
                with st.spinner(f"Retrying with {tier.capitalize()}…"):
                    _retry_with_tier(idx, tier)
        else:
            st.caption("No alternate model tier available for your role.")

    # Always visible — every guardrail check the backend ran on this turn,
    # not gated behind the reasoning toggle below (see
    # render_guardrail_checks()'s docstring for why).
    if msg.get("trace"):
        render_guardrail_checks(msg["trace"])

    # Off by default — the brief's "show reasoning summary" toggle
    # (sidebar Options) controls this rather than always rendering it, since
    # most of the time the answer alone is what a user wants to scan. This
    # is the full agent/tool trace (retrieval, SQL, report generation, ...);
    # guardrail checks specifically are always shown above regardless of
    # this toggle.
    if msg.get("trace") and st.session_state.get("chat_show_reasoning", False):
        render_trace(msg["trace"])


# session_state survives Streamlit's rerun-on-every-interaction model (see
# ARCHITECTURE.md / the write-up in this conversation for why that's needed).
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []  # [{"role": "user"|"assistant", "content": str, "sources": [...], "report": {...}|None, "trace": [...], "model_tier": str, "degraded": bool, "degraded_reason": str|None}]
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None

try:
    _capabilities = api_client.get_my_capabilities()
    _capabilities_error = None
except APIError as exc:
    _capabilities = None
    _capabilities_error = exc
_allowed_tiers = sorted_model_tiers(_capabilities["model_tiers_allowed"]) if _capabilities else []

if _allowed_tiers and st.session_state.get("chat_model_tier") not in _allowed_tiers:
    st.session_state.chat_model_tier = _allowed_tiers[0]

_logged_in_user = st.session_state["current_user"]

# New chat / search / recent conversations / Settings (Top K, reasoning
# trace) all moved to main.py's sidebar — persistent across every page now,
# not just this one. "What you can do" (capabilities) lives on the
# Dashboard page already (render_capabilities() there); not duplicated here.

# ---------------------------------------------------------------- main area

if _capabilities_error:
    show_api_error(_capabilities_error)

# Role-specific empty-state copy (design brief §14) — keyed by role, with a
# generic fallback for any role not explicitly named there.
_WELCOME_COPY = {
    "user": "Ask questions about the information available to you.",
    "hr": "Access HR information, documents, and authorized analytics.",
    "project_manager": "Work with your authorized project information and documents.",
    "ceo": "View enterprise insights and authorized organizational information.",
    "admin": "Manage the AI platform, users, permissions, and system configuration.",
}

pending_prompt = None

if not st.session_state.chat_messages:
    welcome = _WELCOME_COPY.get(_logged_in_user["role"], "Ask questions, search your documents, or request a report.")
    st.markdown(
        f"""
        <div class="ep-empty-state">
            <div class="ep-empty-mark">✦</div>
            <div class="ep-empty-title">How can I help?</div>
            <div class="ep-empty-sub">{welcome}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _suggestions = [
        "Summarize what's in my knowledge base",
        "Search for a specific policy or document",
        "Generate a report from the data",
    ]
    _cols = st.columns(len(_suggestions))
    for _col, _s in zip(_cols, _suggestions):
        with _col:
            if st.button(_s, use_container_width=True, key=f"suggest_{_s}"):
                pending_prompt = _s

for idx, msg in enumerate(st.session_state.chat_messages):
    with st.chat_message(msg["role"], avatar="✨" if msg["role"] == "assistant" else None):
        if msg["role"] == "assistant":
            render_assistant_message(idx, msg, _allowed_tiers)
        else:
            st.markdown(msg["content"])

if st.session_state.chat_messages:
    st.markdown('<p class="ep-disclaimer">AI responses may be inaccurate — verify important information.</p>', unsafe_allow_html=True)

# Composer toolbar — attach + model picker, directly above st.chat_input in
# the script so it renders directly above the input bar in the browser too
# (a plain call-order effect, not a positioning hack; st.chat_input always
# docks to its own fixed bottom bar regardless of where it's called). Kept
# in one stylable_container so both triggers share the same borderless,
# hover-fill treatment and visually read as the input's own footer/toolbar
# rather than two separate floating buttons.
#
# Attachment — this app has no inline multimodal chat attachment; a file
# here goes through the same real ingestion pipeline as the Documents page
# (parse -> chunk -> embed -> index), so it becomes searchable in subsequent
# turns rather than being read directly by this one message.
#
# Permission-aware composer (brief §16/non-negotiable rule): no 📎 at all for
# a role without UPLOAD_DOCUMENTS (Employee) — not just disabled, entirely
# absent, matching the brief's Employee-composer mockup. This is UI
# convenience only; POST /documents/upload itself 403s an Employee token
# regardless of what the frontend shows (routers/documents.py's
# require_permission(UPLOAD_DOCUMENTS) is the real, unbypassable gate).
_can_upload = has_permission(_capabilities, UPLOAD_DOCUMENTS)

if _can_upload or _allowed_tiers:
    with stylable_container(
        key="composer-toolbar",
        css_styles=f"""
        {{
            margin-bottom: -0.6rem;

            div[data-testid="stPopover"] button {{
                border: none !important; background: transparent !important; box-shadow: none !important;
                color: {INK} !important; font-weight: 500; padding: 0.3rem 0.7rem !important;
                border-radius: 8px !important; min-height: 0 !important;
            }}
            div[data-testid="stPopover"] button:hover {{ background: {BG_SECONDARY} !important; color: {PRIMARY} !important; }}
            div[data-testid="stPopover"] svg {{ display: none; }}
        }}
        """,
    ):
        # Exactly 2 columns, both always used: a narrow one for the 📎
        # trigger (only when present) and a wider one for the model picker —
        # a 3rd, unused column here was a real bug (its 0.76 share of the
        # row sat empty while the model picker was squeezed into 0.18,
        # wrapping its label letter-by-letter).
        if _can_upload:
            _col_attach, _col_model = st.columns([0.08, 0.3])
            with _col_attach:
                with st.popover("📎"):
                    st.caption("Uploads to your knowledge base — indexed documents become searchable in chat right away.")
                    uploaded = st.file_uploader(
                        "Attach a file", type=["pdf", "txt", "docx", "csv", "xlsx", "json"],
                        label_visibility="collapsed", key="chat_upload",
                    )
                    if uploaded is not None:
                        size_mb = uploaded.size / (1024 * 1024)
                        st.markdown(f"📄 **{uploaded.name}**  \n{size_mb:.1f} MB")
                        if st.button("Upload to knowledge base", type="primary", key="chat_upload_confirm"):
                            try:
                                with st.spinner(f"Indexing {uploaded.name}… this can take a minute for large files."):
                                    api_client.upload_document(uploaded.name, uploaded.getvalue(), uploaded.type)
                                st.success(f"{uploaded.name} indexed — you can ask about it now.")
                            except APIError as exc:
                                show_api_error(exc)
            with _col_model:
                render_model_picker(_allowed_tiers)
        else:
            _col_model, _ = st.columns([0.3, 0.7])
            with _col_model:
                render_model_picker(_allowed_tiers)

prompt = st.chat_input("Ask anything…") or pending_prompt
if prompt:
    st.session_state.chat_messages.append(
        {"role": "user", "content": prompt, "sources": [], "report": None, "trace": [], "model_tier": None, "degraded": False}
    )
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="✨"):
        with st.spinner("Thinking…"):
            try:
                # This one call is POST {BACKEND_URL}/chat — see api_client.send_chat_message.
                # model_tier is only passed when the user actually has a choice (the composer
                # model picker is a plain label, not a popover, for single-tier roles) —
                # omitting it otherwise keeps the backend's own default-tier resolution unchanged.
                model_tier_override = st.session_state.chat_model_tier if len(_allowed_tiers) > 1 else None
                result = api_client.send_chat_message(
                    prompt, conversation_id=st.session_state.conversation_id, top_k=st.session_state.chat_top_k,
                    model_tier=model_tier_override,
                )
            except APIError as exc:
                show_api_error(exc)
                st.session_state.chat_messages.pop()  # drop the optimistic user turn, nothing was persisted
                st.stop()

        st.session_state.conversation_id = result["conversation_id"]
        st.session_state.chat_messages.append(
            {
                "role": "assistant", "content": result["reply"], "sources": result["sources"],
                "report": result["report"], "trace": result["trace"],
                "model_tier": result["model_tier"], "degraded": result["degraded"],
                "degraded_reason": result.get("degraded_reason"),
            }
        )
        render_assistant_message(len(st.session_state.chat_messages) - 1, st.session_state.chat_messages[-1], _allowed_tiers)
        debug_json(result, "Raw /chat response")
