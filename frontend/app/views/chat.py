import re

import streamlit as st
from streamlit_extras.stylable_container import stylable_container

import api_client
from api_client import APIError
from components import (
    BG_SECONDARY, BORDER, BRAND_MARK_SVG, INK, MODEL_TIER_CAPTIONS, MODEL_TIER_LABELS, MODEL_TIER_ORDER, PRIMARY,
    debug_json, render_capabilities, role_label, show_api_error, sorted_model_tiers,
)
from permissions import UPLOAD_DOCUMENTS, has_permission

_APPROVAL_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _pending_employee_pii_approval(trace: list[dict]) -> dict | None:
    """None unless this turn's trace shows the employee-PII approval gate
    fired (routers/chat.py's pre-flight branch, docs/GUARDRAILS_ARCHITECTURE.md
    §14) — every other reply shape (a normal answer, an ordinary guardrail
    block, a degraded response) returns None here, so the dialog below only
    ever pops for this one specific outcome."""
    intent_step = next((s for s in trace if s.get("tool") == "employee_pii_intent"), None)
    requested_step = next((s for s in trace if s.get("tool") == "employee_pii_approval_requested"), None)
    if intent_step is None or requested_step is None:
        return None
    match = _APPROVAL_ID_RE.search(requested_step.get("summary", ""))
    if match is None:
        return None
    return {"approval_id": match.group(0), "detail": intent_step.get("summary", "")}


@st.dialog("Approval required")
def _employee_pii_approval_dialog(approval_id: str, detail: str) -> None:
    st.markdown("🔒 **This request needs sign-off before it takes effect.**")
    st.caption(detail)
    st.caption("Nothing has been sent to the AI model or written anywhere yet.")

    try:
        approval = api_client.get_approval(approval_id)
    except APIError as exc:
        show_api_error(exc)
        if st.button("Dismiss", use_container_width=True):
            st.rerun()
        return

    if approval["status"] != "pending":
        decider = approval.get("decided_by_email") or "another reviewer"
        st.caption(f"Already **{approval['status']}** by {decider}.")
        if st.button("Dismiss", use_container_width=True):
            st.rerun()
        return

    payload = approval.get("payload") or {}
    if payload.get("raw_message"):
        st.write("**Requested change** (visible only to you as an authorized reviewer):")
        st.code(payload["raw_message"])

    # Reviewer types the value themselves — never pre-filled from the
    # message above, matching the same explicit-confirmation guarantee
    # views/approvals.py's original form had. A false extraction from the
    # raw message is never trusted as the final written value.
    values: dict[str, str] = {}
    if approval["action"] in ("add", "modify", "store"):
        st.caption("Type the confirmed value(s) below — read them off the requested change above.")
        for field in ("full_name", "email", "phone", "address", "government_id"):
            entered = st.text_input(field.replace("_", " ").title(), key=f"dialog_val_{approval_id}_{field}")
            if entered:
                values[field] = entered

    col_approve, col_reject, col_dismiss = st.columns(3)
    if col_approve.button("Approve", type="primary", use_container_width=True):
        try:
            api_client.decide_approval(approval_id, "approved", values=values or None)
            st.success("Approved.")
            st.rerun()
        except APIError as exc:
            show_api_error(exc)
    if col_reject.button("Reject", use_container_width=True):
        try:
            api_client.decide_approval(approval_id, "rejected")
            st.success("Rejected.")
            st.rerun()
        except APIError as exc:
            show_api_error(exc)
    if col_dismiss.button("Decide later", use_container_width=True):
        st.rerun()

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
    verified live for every role during the role-based model access pass.

    No role in llm_rbac.yaml currently ships with an empty tiers_allowed
    (every role gets at least haiku), but a future role/config change could
    introduce one — a clear caption here beats silently rendering nothing,
    which would otherwise look like the composer forgot to load."""
    if not allowed_tiers:
        st.caption("No model is available for your role — contact an administrator.")
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
        "response_time_ms": result.get("response_time_ms"),
    }
    st.rerun()


def _format_response_time(ms: float | None) -> str | None:
    if ms is None:
        return None
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def render_assistant_message(idx: int, msg: dict, allowed_tiers: list[str]) -> None:
    st.markdown(msg["content"])
    _response_time = _format_response_time(msg.get("response_time_ms"))
    if _response_time:
        st.caption(f"⏱️ {_response_time}")
    # Sources and guardrail checks no longer render inline here — both moved
    # to persistent, per-reply-grouped sidebar popovers (main.py's "Sources"
    # and "Guardrail Log") so the chat transcript itself stays focused on the
    # conversation, reachable from any page rather than only next to the one
    # message that produced them.
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

    # Off by default — the brief's "show reasoning summary" toggle
    # (sidebar Options) controls this rather than always rendering it, since
    # most of the time the answer alone is what a user wants to scan. This
    # is the full agent/tool trace (retrieval, SQL, report generation, ...);
    # guardrail checks specifically live in the sidebar's Guardrail Log
    # instead (main.py), always available regardless of this toggle.
    if msg.get("trace") and st.session_state.get("chat_show_reasoning", False):
        render_trace(msg["trace"])


# session_state survives Streamlit's rerun-on-every-interaction model (see
# ARCHITECTURE.md / the write-up in this conversation for why that's needed).
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []  # [{"role": "user"|"assistant", "content": str, "sources": [...], "report": {...}|None, "trace": [...], "model_tier": str, "degraded": bool, "degraded_reason": str|None, "response_time_ms": float|None}]
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None

# Set (once) in the prompt-handling block below, right before its st.rerun()
# — st.dialog's decorated function has to actually be CALLED on the render
# pass where it should appear, and that pass only happens after the rerun,
# so the fact that a new reply needs the popup has to survive across it.
# Popped immediately below (not left in session_state) so the dialog shows
# exactly once for the turn that triggered it, never again on a later
# navigation/rerun that didn't just create a new pending approval.
if st.session_state.get("pending_pii_dialog"):
    _dialog_data = st.session_state.pop("pending_pii_dialog")
    _employee_pii_approval_dialog(_dialog_data["approval_id"], _dialog_data["detail"])

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
# not just this one. "What you can do" (capabilities) is reachable from the
# centered pill below on every chat, so the model tier in use is always
# visible while actually chatting instead of only on the Dashboard page.

# ---------------------------------------------------------------- main area

if _capabilities_error:
    show_api_error(_capabilities_error)

# Centered "Role · Model" pill — a dropdown (st.popover), not a persistent
# side rail, back to the page's normal single-column layout. The label
# itself already surfaces the model tier in use at a glance; opening it
# reveals the same Role/Tools/"You can" detail as Dashboard's "What you
# can do" via render_capabilities().
_current_tier_label = _TIER_LABELS.get(st.session_state.get("chat_model_tier", ""), "")
_trigger_label = f"{role_label(_logged_in_user['role'])} · Claude {_current_tier_label}" if _current_tier_label else role_label(_logged_in_user["role"])

with stylable_container(
    key="chat-permissions-trigger",
    css_styles=f"""
    {{
        /* stVerticalBlock's default flex-direction is column (it stacks
        elements vertically) — align-items is what centers along its
        CROSS axis (horizontal, for a column container); justify-content
        would center along its MAIN axis (vertical) instead, which is a
        no-op here since this block only ever holds the one row. */
        display: flex; align-items: center; margin-bottom: 1rem;

        /* st.popover renders as a direct stPopover child of this
        stVerticalBlock (no element-container wrapper the way st.markdown
        gets one), and align-items' default "stretch" — combined with
        Streamlit's own base CSS giving it width:100% — stretches it to
        the block's full width, leaving nothing for align-items:center to
        center against (the same Streamlit quirk worked around in
        conversation_row() below). An explicit width overrides stretch
        sizing, shrinking it to content so the centering above actually
        has visible effect. */
        > div[data-testid="stPopover"] {{ width: fit-content !important; min-width: 0 !important; }}

        div[data-testid="stPopover"] button {{
            border: 1px solid {BORDER} !important; border-radius: 999px !important;
            font-weight: 500; padding: 0.35rem 0.9rem !important; color: {INK} !important; box-shadow: none !important;
        }}
        div[data-testid="stPopover"] button:hover {{ border-color: {PRIMARY} !important; color: {PRIMARY} !important; }}
    }}
    """,
):
    with st.popover(f"{_trigger_label}  ▾"):
        st.markdown("**Your access**")
        if _capabilities_error:
            show_api_error(_capabilities_error)
        elif _capabilities:
            st.caption(f"Role: {role_label(_logged_in_user['role'])}")
            render_capabilities(_capabilities)

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
            <div class="ep-empty-mark">{BRAND_MARK_SVG}</div>
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
                "response_time_ms": result.get("response_time_ms"),
            }
        )
        render_assistant_message(len(st.session_state.chat_messages) - 1, st.session_state.chat_messages[-1], _allowed_tiers)
        debug_json(result, "Raw /chat response")

    _pii_approval = _pending_employee_pii_approval(result["trace"])
    if _pii_approval:
        st.session_state.pending_pii_dialog = _pii_approval

    # Without this, chat_messages gains the new turn only *after* main.py's
    # sidebar code already ran earlier in this same script execution (it's
    # positioned before nav.run(), which is what gets here) — so the
    # sidebar's Guardrail Log popover (and anything else there reading
    # chat_messages) would keep showing the state from before this message,
    # not because it doesn't read session_state correctly, but because it
    # already finished computing its content for this run. Matches
    # _regenerate_at()'s identical st.rerun() above for the same reason.
    st.rerun()

# Composer toolbar — attach + model picker. Deliberately rendered AFTER the
# chat_input/prompt-handling block above, not before it (an earlier version
# had this reversed). st.chat_input() always docks to its own fixed bottom
# bar regardless of where it's called in the script, but the toolbar has no
# such magic — it renders exactly where this code runs in top-to-bottom
# order. With the toolbar positioned BEFORE the prompt handler, sending a
# message meant the toolbar (rendered earlier in that same run) stayed put
# while the new user/assistant turn's direct st.chat_message() calls
# (positioned after it) appended below — visually sandwiching the toolbar
# mid-transcript, between the previous exchange and the new "Thinking…" one,
# for the entire duration of every single request (found live: reported as
# "the model picker/attach option shows up after every message" — it's not
# a popover misbehaving, it's this whole block rendering in the wrong
# place). Moving it here means the in-flight branch above hits st.rerun()
# and returns before this code ever executes during a pending request (no
# toolbar shows while "Thinking…" is up, which reads as intentional, not
# broken) — and once that rerun completes, the history loop (now including
# the finished turn) has already fully rendered by the time this reruns
# from the top, so the toolbar lands after ALL messages, every time.
#
# Kept in one stylable_container so both triggers share the same borderless,
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

# _capabilities (not just _allowed_tiers) in this condition is what lets the
# toolbar still render — and therefore render_model_picker()'s "no model
# available" caption still show — for a hypothetical role whose
# tiers_allowed comes back empty; without it the whole toolbar (and that
# message) would be skipped entirely whenever _can_upload is also False.
if _can_upload or _allowed_tiers or _capabilities:
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
        # 3 columns: two tightly-sized ones for the 📎 trigger and model
        # picker, plus a trailing spacer that absorbs the rest of the row.
        # st.columns() ratios split the FULL row width regardless of how
        # little content a column holds — a column doesn't shrink to fit its
        # button — so *only* 2 columns here (no spacer) left the model
        # picker's column far wider than its own button, and since the
        # button hugs the left edge of its column, that unused width showed
        # up as a visible gap floating *before* the button instead of
        # disappearing after it, reading as "attach and the model picker are
        # in the wrong position" rather than as a cohesive toolbar. (A prior
        # version of this went too far the other way — a 3rd spacer column
        # so large it squeezed the model picker's own column down to 0.18,
        # wrapping "Claude Sonnet ▾" letter-by-letter. The fix is a properly
        # *sized* pair of columns plus a spacer, not the presence/absence of
        # the spacer itself.)
        if _can_upload:
            _col_attach, _col_model, _col_spacer = st.columns([0.05, 0.2, 0.75])
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
            # Same 0.2 width as _col_model above (not the old 0.3) so the
            # model picker sits at the same horizontal position and size
            # regardless of whether this role also gets the 📎 column.
            _col_model, _ = st.columns([0.2, 0.8])
            with _col_model:
                render_model_picker(_allowed_tiers)
