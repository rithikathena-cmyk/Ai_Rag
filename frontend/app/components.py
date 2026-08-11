"""Small UI helpers shared across pages, so each page focuses on its own
layout instead of re-implementing error display / debug JSON / status badges.

Also owns the app's design tokens and global CSS (inject_global_styles) —
kept here rather than duplicated per-page so every view picks up the same
look automatically through page_header()/card()/status_badge()."""

import pandas as pd
import streamlit as st
from streamlit_extras.annotated_text import annotated_text
from streamlit_extras.dataframe_explorer import dataframe_explorer
from streamlit_extras.metric_cards import style_metric_cards
from streamlit_extras.stylable_container import stylable_container

from api_client import APIError

# ---------------------------------------------------------- design tokens ---
# Warm, minimal "Claude-inspired" palette — see the design brief this
# implements. PRIMARY is the one accent color used sparingly (buttons,
# focus rings, active nav) rather than per-section branding.

BG = "#F7F5F2"
BG_SECONDARY = "#EFEDE8"
SURFACE = "#FFFFFF"
INK = "#242322"
MUTED = "#6F6B66"
BORDER = "#DEDAD4"
PRIMARY = "#D97757"
PRIMARY_HOVER = "#C96849"
USER_MSG_BG = "#EFEDE8"
ASSISTANT_MSG_BG = "#FFFFFF"

# Section accents used by page_header()'s icon chip — kept muted/desaturated
# rather than the brighter streamlit-extras palette this replaces, so each
# section still has a wayfinding color without looking rainbow-ish.
ACCENTS = {
    "blue": "#2563EB",
    "violet": "#7C3AED",
    "green": "#059669",
    "orange": "#D97706",
    "red": "#DC2626",
    "teal": "#0891B2",
    "cyan": "#0EA5E9",
}

# Every existing page_header(..., color="blue-70") call site keeps working
# unchanged — old streamlit-extras color names just map onto the new accents.
_LEGACY_COLOR_MAP = {
    "blue-70": "blue", "violet-70": "violet", "green-70": "green",
    "orange-70": "orange", "red-70": "red", "blue-green-70": "teal",
    "light-blue-70": "cyan",
}

# Role display names/colors for role_tag() — only the five roles the
# enterprise permission model actually distinguishes between (llm_rbac.yaml)
# get a specific color; anything else (the inert manufacturing Role values —
# see backend/app/core/roles.py) falls back to a neutral tag rather than
# growing this list to match.
_ROLE_LABELS = {"admin": "Admin", "hr": "HR", "project_manager": "Project Manager", "user": "Employee", "ceo": "CEO"}
_ROLE_COLORS = {
    "admin": ACCENTS["red"], "hr": ACCENTS["violet"], "project_manager": ACCENTS["teal"], "user": ACCENTS["blue"],
    "ceo": ACCENTS["orange"],
}

# Canonical cost-ascending order/labels/captions for the 3 Claude model
# tiers LLM-RBAC ever resolves an end-user role to (backend/config/
# llm_rbac.yaml's per-role tiers_allowed — the role-based model-access
# policy; backend/config/models.yaml is the source of truth for what each
# tier actually maps to, this is display-only). The ONE place this mapping
# is defined frontend-side — views/chat.py's model selector and
# render_capabilities() below both import from here rather than each
# keeping their own copy.
MODEL_TIER_ORDER = ["haiku", "sonnet", "opus"]
MODEL_TIER_LABELS = {"haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus"}
MODEL_TIER_CAPTIONS = {"haiku": "Fast and efficient", "sonnet": "Balanced performance", "opus": "Advanced reasoning"}


def sorted_model_tiers(tiers) -> list[str]:
    """The backend returns model_tiers_allowed alphabetically (sorted() in
    routers/users.py) — re-sort by cost so a selector/label always reads
    Haiku -> Sonnet -> Opus, not an alphabetical accident (e.g. Project
    Manager's {opus, sonnet} would otherwise show "Opus + Sonnet")."""
    return [t for t in MODEL_TIER_ORDER if t in tiers] + [t for t in tiers if t not in MODEL_TIER_ORDER]


def inject_global_styles() -> None:
    """Global CSS: brand font, hides Streamlit's default chrome (menu/footer/
    rainbow decoration bar), and restyles the sidebar nav, buttons, inputs,
    tabs, alerts, and dataframes to a consistent enterprise look. Call once
    from main.py before st.navigation() builds the page — it applies to
    every view from there since main.py always runs first."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}

        #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none; }}
        header[data-testid="stHeader"] {{ background: transparent; }}

        .stApp {{ background: {BG}; }}
        .block-container {{ padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px; }}

        /* Sidebar — warm secondary surface, quiet by default. The page-link
        nav styling lives further down (search "stPageLink") — main.py runs
        st.navigation(..., position="hidden") on every page and renders its
        own st.page_link() list instead of Streamlit's auto nav, so there's
        no [data-testid="stSidebarNav*"] element left to style here. */
        [data-testid="stSidebar"] {{ background: {BG_SECONDARY}; border-right: 1px solid {BORDER}; }}

        /* Buttons / inputs — no shadows/gradients, a quiet border that warms
        to the accent color on hover instead of filling with color. */
        .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
            border-radius: 10px; font-weight: 500; border: 1px solid {BORDER}; transition: 0.15s ease;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
            border-color: {PRIMARY}; color: {PRIMARY};
        }}
        button[kind="primary"], button[kind="primaryFormSubmit"] {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
        button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {{
            background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; color: {SURFACE};
        }}

        /* Inputs render as a plain background fill with no border by default —
        give them an explicit border/surface rather than relying on the
        sidebar/page background contrast. */
        .stTextInput input, .stTextArea textarea, .stNumberInput input,
        [data-baseweb="select"] > div, [data-testid="stChatInput"] {{
            border-radius: 10px !important;
            border: 1px solid {BORDER} !important;
            background: {SURFACE} !important;
        }}
        .stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus,
        [data-baseweb="select"]:focus-within > div, [data-testid="stChatInput"]:focus-within {{
            border-color: {PRIMARY} !important;
            box-shadow: 0 0 0 1px {PRIMARY} !important;
        }}

        /* The sticky bar that hosts st.chat_input at the bottom of the page
        (Streamlit's own [data-testid="stBottom"] wrapper, not something this
        app renders) defaults to plain white and was never themed — it reads
        as a stark white strip against the page's cream background below the
        last message. Match it to the page background so it blends in. */
        [data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] {{
            background: {BG} !important;
        }}

        /* Tabs */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {BORDER}; }}
        [data-testid="stTabs"] [data-baseweb="tab"] {{ font-weight: 500; color: {MUTED}; }}
        [data-testid="stTabs"] [aria-selected="true"] {{ color: {PRIMARY}; font-weight: 600; }}

        /* Alerts, dataframes, expanders */
        [data-testid="stAlert"] {{ border-radius: 10px; }}
        [data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; overflow: hidden; }}
        [data-testid="stExpander"] {{ border-radius: 10px; border-color: {BORDER}; background: {SURFACE}; }}

        /* Chat messages — whitespace + a hairline divider instead of colored
        bubbles, per the design brief ("do not use large colored message
        bubbles"). Assistant stays on the page background; user gets a
        faint warm tint just enough to separate it visually. */
        [data-testid="stChatMessage"] {{
            border-radius: 12px; border: none; box-shadow: none; padding: 0.6rem 0.1rem;
            background: transparent; border-bottom: 1px solid {BORDER};
        }}
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {{ background: {USER_MSG_BG}44; border-radius: 12px; }}
        [data-testid="stChatMessageAvatarCustom"], [data-testid="stChatMessageAvatarUser"] {{
            background: {PRIMARY} !important; color: {SURFACE} !important;
        }}

        /* Sidebar brand block — see sidebar_brand() */
        .ep-brand {{ display: flex; align-items: center; gap: 10px; padding: 0.25rem 0 1rem; }}
        .ep-brand-mark {{
            width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
            background: {PRIMARY};
            display: flex; align-items: center; justify-content: center; font-size: 17px; color: {SURFACE};
        }}
        .ep-brand-text {{ line-height: 1.2; }}
        .ep-brand-name {{ font-weight: 700; font-size: 0.95rem; color: {INK}; }}
        .ep-brand-tag {{ font-size: 0.72rem; color: {MUTED}; }}

        /* Page header — see page_header() */
        .ep-header {{ margin-bottom: 1.5rem; }}
        .ep-header-row {{ display: flex; align-items: center; gap: 10px; }}
        .ep-header-icon {{
            width: 32px; height: 32px; border-radius: 10px; flex-shrink: 0; font-size: 16px;
            display: flex; align-items: center; justify-content: center;
        }}
        .ep-header-title {{ font-size: 1.5rem; font-weight: 700; color: {INK}; margin: 0; }}
        .ep-header-sub {{ color: {MUTED}; font-size: 0.9rem; margin: 0.35rem 0 0 42px; }}
        .ep-header-rule {{ border: none; border-top: 1px solid {BORDER}; margin-top: 1rem; }}

        /* Chat-page-specific — see views/chat.py */
        .ep-empty-state {{ text-align: center; padding: 3rem 1rem 1.5rem; }}
        .ep-empty-mark {{ font-size: 2rem; color: {PRIMARY}; margin-bottom: 0.5rem; }}
        .ep-empty-title {{ font-size: 1.4rem; font-weight: 600; color: {INK}; margin: 0 0 0.4rem; }}
        .ep-empty-sub {{ color: {MUTED}; font-size: 0.95rem; }}
        .ep-disclaimer {{ color: {MUTED}; font-size: 0.75rem; text-align: center; margin-top: 0.4rem; }}

        /* Sidebar workspace — see sidebar_section_label()/conversation_row()/
        sidebar_profile() in this file, wired together by main.py. A flex
        column on the sidebar's own content wrapper + a zero-content spacer
        div is the standard, non-hacky way to push the last block (the user
        profile) to the bottom without fighting Streamlit's internals — it
        only takes effect when the sidebar has room to spare; a long
        conversation list still scrolls normally instead of forcing the
        profile off-screen. */
        [data-testid="stSidebarUserContent"] {{ display: flex; flex-direction: column; min-height: calc(100vh - 3rem); }}
        .ep-spacer {{ flex: 1 1 auto; }}

        .ep-section-label {{
            font-size: 0.68rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
            color: {MUTED}; margin: 1rem 0 0.3rem; padding: 0 0.2rem;
        }}

        [data-testid="stSidebar"] [data-testid="stPageLink"] {{
            border-radius: 8px; margin: 1px 0; padding: 0.1rem 0.3rem;
        }}
        [data-testid="stSidebar"] [data-testid="stPageLink"]:hover {{ background: {SURFACE}; }}
        [data-testid="stSidebar"] [data-testid="stPageLink"][aria-current="page"] p {{ color: {PRIMARY}; font-weight: 600; }}

        /* Popover dropdown bodies (e.g. conversation_row()'s "⋯" menu) render
        through a BaseWeb portal appended outside the sidebar entirely, so a
        stylable_container scoped to one row can never reach in here — styled
        globally instead. Menu-style: no border, left-aligned, subtle hover
        fill instead of the default button's border-color-flip hover, and a
        tight width instead of Streamlit's default fixed min-width (which is
        what made a one-item "Delete" menu look like an oversized empty box). */
        [data-testid="stPopoverBody"] {{ padding: 0.35rem !important; min-width: 0 !important; }}
        [data-testid="stPopoverBody"] [data-testid="stVerticalBlock"] {{ width: auto !important; gap: 0.15rem !important; }}
        [data-testid="stPopoverBody"] .stButton > button {{
            width: 100%; border: none !important; box-shadow: none !important;
            justify-content: flex-start !important; text-align: left !important;
            padding: 0.35rem 0.6rem !important; font-weight: 400;
        }}
        [data-testid="stPopoverBody"] .stButton > button:hover {{ background: {BG_SECONDARY}; color: {PRIMARY}; }}

        /* Conversation row styling itself is injected per-row via
        stylable_container in conversation_row() below (needs the row's
        active/inactive state baked in per call, same as the pre-existing
        per-row pattern this replaces) — nothing global needed here.

        Bottom-anchored user profile — a plain understated row, not a
        colored role badge (see role_label()). */
        .ep-profile {{ display: flex; align-items: center; gap: 10px; padding: 0.6rem 0.2rem 0.3rem; }}
        .ep-profile-avatar {{
            width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
            background: {BG_SECONDARY}; border: 1px solid {BORDER}; color: {MUTED};
            display: flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 600;
        }}
        .ep-profile-text {{ line-height: 1.25; overflow: hidden; }}
        .ep-profile-name {{ font-size: 0.83rem; font-weight: 500; color: {INK}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .ep-profile-role {{ font-size: 0.72rem; color: {MUTED}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def sidebar_brand(name: str = "ATHENA", tagline: str = "AI Assistant", mark: str = "✦") -> None:
    """Logo + product name block pinned to the top of the sidebar."""
    st.markdown(
        f"""
        <div class="ep-brand">
            <div class="ep-brand-mark">{mark}</div>
            <div class="ep-brand-text">
                <div class="ep-brand-name">{name}</div>
                <div class="ep-brand-tag">{tagline}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar_section_label(text: str) -> None:
    """A small muted uppercase section heading in the sidebar (e.g. "Recent",
    "Assistant", "Administration") — used both for the conversation list and
    for the permission-driven page groups, so the two visually match."""
    st.markdown(f'<div class="ep-section-label">{text}</div>', unsafe_allow_html=True)


def conversation_row(conv_id: str, label: str, active: bool) -> tuple[bool, bool]:
    """Renders one sidebar conversation row: the title as a flat list-item
    button, plus a small "⋯" popover with the actions the current user is
    actually authorized to take. Returns (title_clicked, delete_clicked) —
    the caller owns the actual navigation/delete API calls, this is display
    only.

    Only "Delete" is offered — DELETE /conversations/{id} is the only
    conversation-mutation endpoint the backend exposes (no rename/archive
    field on ConversationModel); a Rename/Archive menu entry would be a UI
    control with nothing real behind it.

    Uses stylable_container (like card()/the old chat.py row button) rather
    than a global CSS class, since the active/inactive color has to be baked
    into this specific row's styles per call.

    Only the row itself and the "⋯" trigger button live inside this
    container's own DOM subtree — stylable_container's :has() scoping only
    reaches those. The popover's actual dropdown body (the Delete button)
    renders through a BaseWeb portal appended elsewhere in the document, not
    as a descendant of this row at all, so it can never be reached from
    here; it's styled globally instead, see inject_global_styles()'s
    [data-testid="stPopoverBody"] rule. Also: everything below has to stay
    inside ONE top-level {{ }} block using nested selectors (relying on the
    browser's native CSS nesting) — stylable_container only prefixes its
    :has() scope onto the first top-level rule in css_styles; separate
    sibling rule blocks after it are emitted completely unscoped."""
    bg = SURFACE if active else "transparent"
    weight = 600 if active else 400
    color = PRIMARY if active else INK
    with stylable_container(
        key=f"conv-row-{conv_id}",
        css_styles=f"""
        {{
            display: flex; align-items: center;

            div[data-testid="stPopover"] button {{
                border: none !important; background: transparent !important; box-shadow: none !important;
                color: {MUTED} !important; padding: 0.35rem 0.3rem !important; min-height: 1.6rem !important;
            }}
            div[data-testid="stPopover"] button:hover {{ color: {PRIMARY} !important; }}
            div[data-testid="stPopover"] svg {{ display: none; }}

            div[data-testid="column"]:first-child button {{
                border: none !important; background: {bg} !important; box-shadow: none !important;
                text-align: left !important; justify-content: flex-start !important;
                font-weight: {weight} !important; color: {color} !important;
                padding: 0.35rem 0.5rem !important; border-radius: 8px !important; font-size: 0.86rem !important;
                display: block !important;
            }}
            div[data-testid="column"]:first-child button p {{
                white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
            }}
            div[data-testid="column"]:first-child button:hover {{ background: {SURFACE} !important; color: {PRIMARY} !important; }}
        }}
        """,
    ):
        col_title, col_menu = st.columns([0.86, 0.14])
        with col_title:
            title_clicked = st.button(label, key=f"conv_btn_{conv_id}", use_container_width=True)
        with col_menu:
            with st.popover("⋯", use_container_width=True):
                delete_clicked = st.button("🗑️ Delete", key=f"conv_del_{conv_id}", use_container_width=True)
    return title_clicked, delete_clicked


def role_label(role: str) -> str:
    """Plain-text role label for the bottom profile row — deliberately not a
    colored pill (see role_tag()) since the design brief calls for an
    understated role indicator there, not a badge."""
    return _ROLE_LABELS.get(role, role.replace("_", " ").title())


def sidebar_profile(display_name: str, role: str) -> None:
    """Bottom-anchored user identity row: initial-letter avatar circle, name,
    and a plain-text role — see role_label(). No click target of its own;
    the caller renders Log out (and anything else) right below this."""
    initial = (display_name or "?")[0].upper()
    st.markdown(
        f"""
        <div class="ep-profile">
            <div class="ep-profile-avatar">{initial}</div>
            <div class="ep-profile-text">
                <div class="ep-profile-name">{display_name}</div>
                <div class="ep-profile-role">{role_label(role)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_api_error(exc: APIError) -> None:
    label = f"`{exc.code}`" if exc.code else (f"HTTP {exc.status_code}" if exc.status_code else "Request failed")
    st.error(f"{label} — {exc.message}")


def debug_json(data, label: str = "Raw response") -> None:
    with st.expander(f"🔍 {label}"):
        st.json(data)


def status_badge(status: str) -> str:
    color = {"completed": "green", "degraded": "orange", "failed": "red", "pending": "gray"}.get(status, "gray")
    dot = {"completed": "🟢", "degraded": "🟡", "failed": "🔴", "pending": "⚪"}.get(status, "⚪")
    return f":{color}[{dot} {status}]"


_ACRONYMS = {"sop", "sops", "qa", "hr", "ceo", "sql", "it"}


def humanize_action(name: str) -> str:
    """llm_rbac.yaml action/tool names are snake_case identifiers
    ("search_manuals", "query_analytics", "explain_sops") — this is
    display-only, never used as a key back into that catalog."""
    return " ".join(word.upper() if word in _ACRONYMS else word.capitalize() for word in name.split("_"))


def render_capabilities(caps: dict) -> None:
    """Renders a /users/me/capabilities response — model tier(s), tools, and
    the named capability catalog. Used by views/dashboard.py's "What you can
    do" section — the sidebar redesign dropped the duplicate copy that used
    to live in views/chat.py's Options expander, since Dashboard already
    covers it and the brief's sidebar hierarchy has no room for it."""
    tiers = sorted_model_tiers(caps["model_tiers_allowed"])
    model_label = " + ".join(MODEL_TIER_LABELS.get(t, t.capitalize()) for t in tiers)
    st.markdown(f"**Model:** {model_label}")
    if caps["escalate_to_opus_for"]:
        st.caption("Escalates to Opus for: " + ", ".join(humanize_action(a) for a in caps["escalate_to_opus_for"]))

    st.markdown("**Tools:**")
    for tool in caps["tools"]:
        st.caption(f"• {humanize_action(tool)}")

    st.markdown("**You can:**")
    if caps["all_capabilities"]:
        st.caption("Everything — unrestricted access (CEO/Admin).")
    else:
        for capability in caps["capabilities"]:
            st.caption(f"• {humanize_action(capability)}")


def role_tag(role: str) -> tuple:
    """A (label, "", color) tuple for streamlit_extras.annotated_text — pass
    it (optionally alongside plain strings in the same call) to render a
    role as a colored pill instead of backtick-quoted raw role text."""
    return (_ROLE_LABELS.get(role, role), "", _ROLE_COLORS.get(role, MUTED))


def explorable_table(data) -> None:
    """st.dataframe with streamlit-extras' dataframe_explorer filter UI layered
    on top — for the list-of-dicts/DataFrame results the admin/evaluation/
    documents pages get back from the API. Caller is still responsible for
    the empty-state check (`if items: explorable_table(items) else: ...`),
    same as the plain st.dataframe calls this replaces, so each page keeps
    its own "no data yet" copy instead of a generic one here."""
    df = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    st.dataframe(dataframe_explorer(df, case=False), use_container_width=True, hide_index=True)


def page_header(title: str, icon: str = "", subtitle: str | None = None, color: str = "blue") -> None:
    """Page-top header: a colored icon chip, bold title, muted subtitle, and
    a hairline rule — replaces streamlit-extras' colored_header (whose bright
    gradient bar read more "dev tool" than enterprise)."""
    accent = ACCENTS.get(_LEGACY_COLOR_MAP.get(color, color), PRIMARY)
    subtitle_html = f'<p class="ep-header-sub">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"""
        <div class="ep-header">
            <div class="ep-header-row">
                <div class="ep-header-icon" style="background:{accent}1A; color:{accent};">{icon}</div>
                <h1 class="ep-header-title">{title}</h1>
            </div>
            {subtitle_html}
            <hr class="ep-header-rule" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def card(key: str):
    """A bordered container — streamlit-extras' stylable_container, used in
    place of st.container(border=True) for list rows. No shadow, per the
    design brief's "avoid excessive shadows" — a border alone is enough
    separation against the page's off-white background."""
    return stylable_container(
        key=key,
        css_styles=f"""
        {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 10px;
            padding: 1rem 1.25rem;
        }}
        """,
    )


def metric_cards() -> None:
    """Call once after a block of st.metric()s to restyle them as cards."""
    style_metric_cards(border_left_color=PRIMARY)
