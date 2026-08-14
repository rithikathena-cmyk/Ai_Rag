"""Small UI helpers shared across pages, so each page focuses on its own
layout instead of re-implementing error display / debug JSON / status badges.

Also owns the app's design tokens and global CSS (inject_global_styles) —
kept here rather than duplicated per-page so every view picks up the same
look automatically through page_header()/card()/status_badge()."""

import html

import pandas as pd
import streamlit as st
from streamlit_extras.dataframe_explorer import dataframe_explorer
from streamlit_extras.stylable_container import stylable_container

from api_client import APIError

# ---------------------------------------------------------- design tokens ---
# Warm, minimal "Claude-inspired" palette — see the design brief this
# implements. PRIMARY is the one accent color used sparingly (buttons,
# focus rings, active nav) rather than per-section branding.

BG = "#FFFFFF"
BG_SECONDARY = "#FAFAF9"
SURFACE = "#FFFFFF"
INK = "#242322"
MUTED = "#6F6B66"
BORDER = "#E7E5E2"
PRIMARY = "#D97757"
PRIMARY_HOVER = "#C96849"
USER_MSG_BG = "#F4F4F3"
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

# Minimal monoline icons for page_header()'s chip — stroke="currentColor" so
# each one picks up the chip's own accent color for free. Replaces the old
# per-page emoji (🏠📄🔎 etc.), which read as colorful/playful rather than
# the quiet, single-color icon language claude.ai actually uses. Call sites
# pass a key from this dict; page_header() falls back to treating the string
# as raw HTML (so a stray emoji still renders) if the key isn't found here.
_PAGE_ICONS = {
    "home": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11 L12 4 L21 11"/><path d="M5.5 9.5 V20 H18.5 V9.5"/><path d="M10 20 V14 H14 V20"/></svg>',
    "chat": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5 H20 V16 H9 L5 19.5 V16 H4 Z"/></svg>',
    "document": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3 H14 L18.5 7.5 V21 H6.5 Z"/><path d="M14 3 V7.5 H18.5"/><path d="M9 12.5 H16 M9 16 H16"/></svg>',
    "search": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 L21 21"/></svg>',
    "bar_chart": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 20 V13 M12 20 V6 M19 20 V10"/></svg>',
    "monitoring": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 17 L9 10.5 L13 14 L20.5 5.5"/><path d="M15 5.5 H20.5 V11"/></svg>',
    "group": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3 20 C3 15.5 5.5 13.5 9 13.5 C12.5 13.5 15 15.5 15 20"/><circle cx="17" cy="9" r="2.3"/><path d="M15.5 13.6 C18.6 13.9 21 15.8 21 20"/></svg>',
    "shield": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 L19.5 6 V11.5 C19.5 16.5 16.3 19.8 12 21.5 C7.7 19.8 4.5 16.5 4.5 11.5 V6 Z"/><path d="M9 12 L11 14 L15.5 9.5"/></svg>',
    "receipt": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3 H18 V21 L15.5 19 L13 21 L10.5 19 L8 21 L5.5 19 V3 Z" transform="translate(0.5 0)"/><path d="M9 8 H15 M9 12 H15 M9 16 H13"/></svg>',
    "settings": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7 H14 M17 7 H20 M4 12 H7 M10 12 H20 M4 17 H14 M17 17 H20"/><circle cx="16" cy="7" r="2"/><circle cx="8.5" cy="12" r="2"/><circle cx="16" cy="17" r="2"/></svg>',
    "science": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3 H14 M10.5 3 V9.5 L5.5 18.5 C5 19.6 5.8 21 7 21 H17 C18.2 21 19 19.6 18.5 18.5 L13.5 9.5 V3"/><path d="M8 15.5 H16"/></svg>',
}

# Role display names — only the five roles the enterprise permission model
# actually distinguishes between (llm_rbac.yaml) get a specific label;
# anything else (the inert manufacturing Role values — see
# backend/app/core/roles.py) falls back to a title-cased version of the raw
# role string rather than growing this list to match.
_ROLE_LABELS = {"admin": "Admin", "hr": "HR", "project_manager": "Project Manager", "user": "Employee", "ceo": "CEO"}

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

        /* This Streamlit version's CSS-in-JS classes are named
        "st-emotion-cache-*" — the old streamlit-extras-era selector
        "[class*='css']" no longer matches any of them, so this rule was
        silently a no-op everywhere except the couple of elements that
        happen to inherit from <body> directly. Every element still fell
        back to Streamlit's own default ("Source Sans Pro"). Targeting
        every descendant of the app root with !important is what actually
        wins against Streamlit's built-in, more-specific font rules.
        st.expander(icon=...)'s ligature span carries data-testid=
        "stExpanderIcon", NOT "stIconMaterial" (confirmed via the live DOM —
        st.Page(icon=...) nav icons are the ones that use stIconMaterial) —
        the original exclusion here never matched an expander's icon at all,
        so Inter always force-applied to it and the Material Symbols
        ligature ("speed", "add", ...) rendered as literal overlapping text
        instead of substituting to the icon glyph. Both testids need
        excluding for icons anywhere in the app to render correctly. */
        html, body, .stApp,
        .stApp *:not([data-testid="stIconMaterial"]):not([data-testid="stExpanderIcon"]) {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
        }}
        .stApp code, .stApp pre, .stApp kbd, .stApp samp {{
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace !important;
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
        .ep-empty-mark {{ color: {PRIMARY}; margin-bottom: 0.5rem; }}
        .ep-empty-mark svg {{ width: 34px; height: 34px; }}
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

        /* activity_log_panel()'s sidebar expander — structural [data-testid]
        rules for ANY expander have to live here globally rather than inside
        that function's own stylable_container: empirically, only the FIRST
        top-level rule in a stylable_container's css_styles reliably scopes
        (same class of quirk as the popover comment above documents for
        portal-rendered content, just for a different underlying reason —
        here the element isn't portal-rendered, css_styles just doesn't
        propagate its scope past the first rule for these testids). Applies
        to every st.expander on the page (also debug_json()'s), not just
        this one — harmless there too, same box/scroll treatment is a
        reasonable default for any expander, not a regression.
        The max-height+scroll is the actual size fix: without it, a long
        session (many turns x 11 input + 5 output checks each) pushes
        Settings/profile/sign-out far down the sidebar instead of scrolling
        within its own bounded box. */
        [data-testid="stExpander"] {{
            border: 1px solid {BORDER} !important; border-radius: 10px !important;
            background: {SURFACE} !important; box-shadow: none !important;
        }}
        [data-testid="stExpander"] summary {{ font-size: 0.86rem !important; padding: 0.4rem 0.6rem !important; }}
        [data-testid="stExpander"] summary:hover {{ color: {PRIMARY} !important; }}
        [data-testid="stExpanderDetails"] {{
            max-height: 46vh !important; overflow-y: auto !important; overflow-x: hidden !important;
            padding: 0.1rem 0.5rem 0.4rem !important;
        }}

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


BRAND_MARK_SVG = """
<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
    <path d="M12 0 L13.8 9.4 L22 4.5 L14.9 11.4 L24 12 L14.9 12.6 L22 19.5
             L13.8 14.6 L12 24 L10.2 14.6 L2 19.5 L9.1 12.6 L0 12 L9.1 11.4
             L2 4.5 L10.2 9.4 Z"/>
</svg>
"""


def sidebar_brand(name: str = "ATHENA", tagline: str = "AI Assistant", mark: str | None = None) -> None:
    """Logo + product name block pinned to the top of the sidebar. `mark`
    defaults to an inline SVG sunburst (an evocative mark in the same spirit
    as this app's existing Claude-inspired palette, not a reproduction of
    Anthropic's actual trademarked logo) — pass a plain string to override
    with something else (e.g. an emoji) instead."""
    mark_html = mark if mark is not None else BRAND_MARK_SVG
    st.markdown(
        f"""
        <div class="ep-brand">
            <div class="ep-brand-mark">{mark_html}</div>
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
    sibling rule blocks after it are emitted completely unscoped.

    Deliberately NOT st.columns() for the title/menu side-by-side layout —
    live-reproduced bug: st.columns() converts its fractional widths into a
    pixel width via a one-shot JS/ResizeObserver measurement that gets baked
    into an inline style on first render. Switching conversations triggers
    st.switch_page(), which remounts the sidebar; a row's columns can end up
    measured mid-transition against a near-zero-width sidebar and then never
    re-measure (no further resize event fires), permanently freezing that
    row at a few px wide with its title clipped to nothing. Plain CSS flex
    on the row's own stVerticalBlock has no such cache — the browser
    recomputes it on every paint — so it can't get stuck this way."""
    bg = SURFACE if active else "transparent"
    weight = 600 if active else 400
    color = PRIMARY if active else INK
    with stylable_container(
        key=f"conv-row-{conv_id}",
        css_styles=f"""
        {{
            display: flex !important; flex-direction: row !important; align-items: center; gap: 2px;

            /* stylable_container's own first child is the hidden, empty
            marker element it uses for this rule's own :has() targeting
            (see the auto-generated margin-bottom rule for > div:first-child
            below, in the same injected stylesheet — it treats :first-child
            as this same marker). Invisible or not, it's still
            a real block-level element-container, and Streamlit's own base
            CSS gives every element-container width:100% — as a flex-basis
            that's the row's full width, so it claimed the entire row and
            pushed the real title/menu buttons off past the right edge.
            Pin it to zero size explicitly rather than relying on flex-
            shrink to sort it out against two differently-sized siblings. */
            > div[data-testid="element-container"]:first-child {{ flex: 0 0 0 !important; min-width: 0 !important; }}

            /* Flex properties only take effect on the flex container's
            DIRECT children — that's these element-container wrappers, not
            the .stButton/stPopover divs nested a level inside each.
            flex-basis 0 (not auto) is required here, not stylistic: with
            auto, the button's own width:100% doesn't count toward its
            intrinsic content size, so the flex algorithm falls back to
            sizing this item off the *unclipped* title text's natural
            width — wider than the space actually available once the fixed
            20px "⋯" sibling is accounted for — and the title item then
            visually+interactively overlapped the menu button, silently
            eating its clicks. A 0 basis makes this item start from nothing
            and grow purely off leftover space, which is exactly the space
            this sibling doesn't use. overflow:hidden is a second, harder
            guarantee against the same class of bug regardless of cause. */
            > div[data-testid="element-container"]:has(.stButton) {{ flex: 1 1 0%; min-width: 0; overflow: hidden; }}
            > div[data-testid="element-container"]:has(div[data-testid="stPopover"]) {{ flex: 0 0 auto; }}

            div[data-testid="stPopover"] button {{
                border: none !important; background: transparent !important; box-shadow: none !important;
                color: {MUTED} !important; padding: 0.35rem 0.3rem !important; min-height: 1.6rem !important;
            }}
            div[data-testid="stPopover"] button:hover {{ color: {PRIMARY} !important; }}
            div[data-testid="stPopover"] svg {{ display: none; }}

            .stButton button {{
                border: none !important; background: {bg} !important; box-shadow: none !important;
                text-align: left !important; justify-content: flex-start !important;
                font-weight: {weight} !important; color: {color} !important;
                padding: 0.35rem 0.5rem !important; border-radius: 8px !important; font-size: 0.86rem !important;
                display: block !important; width: 100% !important;
            }}
            .stButton button p {{
                white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
            }}
            .stButton button:hover {{ background: {SURFACE} !important; color: {PRIMARY} !important; }}
        }}
        """,
    ):
        title_clicked = st.button(label, key=f"conv_btn_{conv_id}", use_container_width=True)
        with st.popover("⋯", use_container_width=True):
            delete_clicked = st.button("Delete", key=f"conv_del_{conv_id}", use_container_width=True)
    return title_clicked, delete_clicked


def role_label(role: str) -> str:
    """Plain-text role label — deliberately not a colored pill/badge; every
    role indicator in this app (bottom profile row, dashboard's "What you
    can do") is a plain bold label, with color reserved for buttons/
    active-state/focus rather than decorating every badge."""
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


# routers/chat.py's _guardrail_trace() formats every guardrail
# ChatTraceStep's summary as "{action}: {detail}" — action is always one of
# these three literal prefixes (services/guardrails/types.py's
# GuardrailAction), so a plain string-prefix check is exact, not a
# heuristic. Shared here (not defined in views/chat.py) so main.py's sidebar
# guardrail log can use the same icon/parsing convention without importing
# a page module — Streamlit page files run top-level st.stop() guards on
# import, which makes them unsafe to import from anywhere but their own
# st.navigation() entry.
GUARDRAIL_ICONS = {"pass": "✅", "block": "🚫", "redact": "✂️"}


def guardrail_action(step: dict) -> str:
    return step["summary"].split(":", 1)[0]


def activity_log_panel(assistant_messages: list[dict]) -> None:
    """Sidebar-resident (not popover) guardrail/source log — every reply's
    checks and citations, newest first. Replaces the earlier st.popover
    version: a popover's body renders through a BaseWeb portal with no
    awareness of the surrounding page, so once a session accumulates enough
    turns/checks the box grows tall/wide enough to cover other page content.
    st.expander's body is a normal descendant in the DOM (not portal-
    rendered), so its size is capped with an ordinary CSS max-height+scroll
    instead — see inject_global_styles()'s [data-testid="stExpander"]/
    stExpanderDetails rules for why that lives there and not in this
    function's own stylable_container. The panel now lives inline in the
    sidebar's document flow and never overlaps anything else on the page.

    assistant_messages: st.session_state.chat_messages entries with
    role == "assistant" and a non-empty trace (the caller filters this,
    same as the popover version did) — this function only renders."""
    turns = list(enumerate(assistant_messages, start=1))
    all_checks = [step for _, m in turns for step in m["trace"] if step["agent"] == "Guardrails"]
    flagged = sum(1 for c in all_checks if guardrail_action(c) != "pass")
    total_sources = sum(len(m.get("sources") or []) for _, m in turns)
    # :red[...] is Streamlit's own colored-markdown directive (same one
    # status_badge() already uses below) — st.expander's label renders
    # markdown, so this needs no custom CSS/scoping workaround, unlike the
    # structural [data-testid=...] rules above.
    label = f":red[🛡️ Activity Log ({flagged} flagged)]" if flagged else "🛡️ Activity Log"

    with stylable_container(
        key="activity-log-panel",
        css_styles=f"""
        {{
            .al-summary {{
                font-size: 0.7rem; color: {MUTED}; padding: 0.1rem 0.1rem 0.5rem; border-bottom: 1px solid {BORDER};
                margin-bottom: 0.4rem;
            }}
            /* Per-row dynamic color (turn accent, check dot, tool-name color)
            is driven entirely by CSS CLASSES set in the generated markup
            below, not inline style="--x: y" custom properties — verified
            live that Streamlit's unsafe_allow_html markdown renderer strips
            style="..." attributes from this HTML entirely, so any custom
            property set that way silently never reaches the DOM and every
            var(--x, fallback) always resolves to its fallback. Classes have
            no such problem. */
            .al-turn {{ border: 1px solid {BORDER}; border-radius: 8px; background: {BG}; padding: 0.45rem 0.55rem; margin-bottom: 0.45rem; }}
            .al-turn.al-clean {{ border-left: 3px solid {ACCENTS["green"]}; }}
            .al-turn.al-flagged {{ border-left: 3px solid {ACCENTS["red"]}; }}
            .al-turn-head {{
                font-size: 0.76rem; font-weight: 600; color: {INK}; margin-bottom: 0.3rem;
            }}
            .al-turn-head.al-flagged {{ color: {ACCENTS["red"]}; }}
            .al-preview {{ font-weight: 400; color: {MUTED}; }}
            .al-section-label {{
                font-size: 0.62rem; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
                color: {MUTED}; margin: 0.35rem 0 0.15rem;
            }}
            .al-check {{ display: flex; align-items: flex-start; gap: 0.4rem; padding: 0.12rem 0; font-size: 0.72rem; }}
            .al-dot {{ width: 6px; height: 6px; border-radius: 50%; margin-top: 0.32rem; flex: 0 0 auto; }}
            .al-dot.al-pass {{ background: {ACCENTS["green"]}; }}
            .al-dot.al-block {{ background: {ACCENTS["red"]}; }}
            .al-dot.al-redact {{ background: {ACCENTS["orange"]}; }}
            /* A <span>, not <code> — Streamlit's own markdown CSS carries an
            equal-specificity !important color rule for inline <code> that
            wins on source order regardless of !important on this side, so
            outscoring it isn't reliable. A plain span has no such built-in
            styling to fight. */
            .al-tool {{
                font-size: 0.66rem; background: {BG_SECONDARY}; padding: 0.05rem 0.3rem; border-radius: 4px;
                white-space: nowrap; font-weight: 600;
            }}
            .al-tool.al-pass {{ color: {ACCENTS["green"]}; }}
            .al-tool.al-block {{ color: {ACCENTS["red"]}; }}
            .al-tool.al-redact {{ color: {ACCENTS["orange"]}; }}
            .al-detail {{ color: {MUTED}; overflow-wrap: anywhere; }}
            .al-source {{ font-size: 0.72rem; padding: 0.25rem 0 0; border-top: 1px dashed {BORDER}; margin-top: 0.3rem; }}
            .al-source strong {{ font-weight: 600; overflow-wrap: anywhere; }}
        }}
        """,
    ):
        with st.expander(label, expanded=False):
            if not turns:
                st.caption("No activity yet — start a chat.")
                return

            reply_word = "reply" if len(turns) == 1 else "replies"
            st.markdown(
                f'<div class="al-summary">{len(all_checks)} checks &middot; {total_sources} sources across '
                f'{len(turns)} {reply_word} &mdash; {len(all_checks) - flagged} passed, {flagged} flagged</div>',
                unsafe_allow_html=True,
            )

            rows: list[str] = []
            for turn_no, m in reversed(turns):
                checks = [s for s in m["trace"] if s["agent"] == "Guardrails"]
                sources = m.get("sources") or []
                turn_flagged = sum(1 for c in checks if guardrail_action(c) != "pass")
                head_icon = "🚫" if turn_flagged else "✅"
                preview = html.escape((m["content"] or "")[:70])

                turn_class = "al-turn al-flagged" if turn_flagged else "al-turn al-clean"
                head_class = "al-turn-head al-flagged" if turn_flagged else "al-turn-head"
                rows.append(f'<div class="{turn_class}">')
                rows.append(f'<div class="{head_class}">{head_icon} Reply {turn_no} <span class="al-preview">— "{preview}"</span></div>')
                if checks:
                    rows.append('<div class="al-section-label">Guardrail checks</div>')
                    for check in checks:
                        # action is always "pass"/"block"/"redact" (services/guardrails/types.py's
                        # GuardrailAction) — matches the al-pass/al-block/al-redact CSS classes exactly.
                        action = guardrail_action(check)
                        detail = check["summary"].split(":", 1)[1].strip() if ":" in check["summary"] else ""
                        rows.append(
                            f'<div class="al-check"><span class="al-dot al-{action}"></span>'
                            f'<span><span class="al-tool al-{action}">{html.escape(check["tool"])}</span> '
                            f'<span class="al-detail">{html.escape(detail)}</span></span></div>'
                        )
                if sources:
                    rows.append(f'<div class="al-section-label">Sources ({len(sources)})</div>')
                    for s in sources:
                        fname = html.escape(s.get("document_filename") or s["document_id"])
                        rows.append(
                            f'<div class="al-source"><strong>[{s["index"]}] {fname}</strong> '
                            f'(chunk {s["chunk_index"]})</div>'
                        )
                rows.append("</div>")
            st.markdown("".join(rows), unsafe_allow_html=True)


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
    gradient bar read more "dev tool" than enterprise). `icon` is a key into
    _PAGE_ICONS (e.g. "home", "document"); an unrecognized string still
    renders as-is (raw HTML/emoji), so old call sites can't silently break."""
    accent = ACCENTS.get(_LEGACY_COLOR_MAP.get(color, color), PRIMARY)
    icon_html = _PAGE_ICONS.get(icon, icon)
    subtitle_html = f'<p class="ep-header-sub">{subtitle}</p>' if subtitle else ""
    st.markdown(
        f"""
        <div class="ep-header">
            <div class="ep-header-row">
                <div class="ep-header-icon" style="background:{accent}1A; color:{accent};">{icon_html}</div>
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
    """Call once after a block of st.metric()s to restyle them as cards —
    a plain white tile with a uniform thin neutral border on every edge and
    no shadow, matching claude.ai's own stat-tile treatment (color is
    reserved for buttons/active-state/focus, not decorating every card).

    Deliberately custom CSS instead of streamlit_extras' style_metric_cards():
    that helper hardcodes a 0.5rem left border plus matching lopsided
    padding (its "accent bar" look) that its own border_left_color param
    can't even out — passing it the same color as the other three sides
    still leaves a visibly thicker left edge. Writing the rule directly
    also lets the label/value pick up this app's own type tokens (MUTED/
    INK) instead of Streamlit's default metric colors."""
    st.markdown(
        f"""
        <style>
        div[data-testid="stMetric"] {{
            background-color: {SURFACE};
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 1.1rem 1.25rem;
            box-shadow: none;
        }}
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] p {{
            font-size: 0.82rem; font-weight: 500; color: {MUTED};
        }}
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
            font-size: 1.7rem; font-weight: 600; color: {INK};
        }}
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{ font-size: 0.82rem; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
