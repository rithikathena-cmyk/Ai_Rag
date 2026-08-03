import streamlit as st

PRIMARY = "#4F46E5"
PRIMARY_DARK = "#3730A3"
PRIMARY_LIGHT = "#EEF0FF"
ACCENT = "#0EA5A4"
SUCCESS = "#16A34A"
SUCCESS_BG = "#ECFDF3"
WARNING = "#D97706"
WARNING_BG = "#FFFBEB"
DANGER = "#DC2626"
DANGER_BG = "#FEF2F2"
MUTED = "#667085"
BORDER = "#E4E7EF"
SURFACE_ALT = "#F8F9FC"
TEXT = "#1B1F2A"

_STATUS_COLORS = {
    # generic
    "success": (SUCCESS, SUCCESS_BG), "completed": (SUCCESS, SUCCESS_BG), "active": (SUCCESS, SUCCESS_BG),
    "connected": (SUCCESS, SUCCESS_BG), "ok": (SUCCESS, SUCCESS_BG), "admin": (SUCCESS, SUCCESS_BG),
    "degraded": (WARNING, WARNING_BG), "pending": (WARNING, WARNING_BG), "user": (WARNING, WARNING_BG),
    "failed": (DANGER, DANGER_BG), "rejected": (DANGER, DANGER_BG), "error": (DANGER, DANGER_BG),
    "inactive": (DANGER, DANGER_BG),
}


def inject_theme() -> None:
    """Injects the shared visual theme (cards, buttons, tabs, badges, etc). Call once near the
    top of every page — CSS targets Streamlit's documented data-testid attributes, so it's stable
    across reruns and pages without needing per-page markup changes."""
    st.markdown(
        f"""
        <style>
        /* Force our light theme regardless of OS/browser dark-mode preference or whether
        .streamlit/config.toml was found (it's only discovered from the process's cwd, and
        this app is run from a few different working directories) — the injected CSS below
        is the single source of truth for how the app looks, not config.toml. */
        html {{ color-scheme: light; }}
        html, body, [class*="css"] {{
            font-family: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        [data-testid="stAppViewContainer"] {{
            background: #FFFFFF;
            color: {TEXT};
        }}
        [data-testid="stHeader"] {{
            background: #FFFFFF;
        }}
        [data-testid="stSidebar"] {{
            background: {SURFACE_ALT};
            border-right: 1px solid {BORDER};
        }}
        [data-testid="stSidebar"] * {{
            color: {TEXT} !important;
        }}
        [data-testid="stSidebarNavLink"]:hover {{
            background: {PRIMARY_LIGHT} !important;
        }}

        /* ---- Cards (st.container(border=True)) ---- */
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 14px !important;
            border: 1px solid {BORDER} !important;
            transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
            background: white;
            color: {TEXT};
        }}
        div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
            box-shadow: 0 8px 24px rgba(79, 70, 229, 0.12);
            transform: translateY(-2px);
            border-color: {PRIMARY}55 !important;
        }}
        div[data-testid="stVerticalBlockBorderWrapper"] h1,
        div[data-testid="stVerticalBlockBorderWrapper"] h2,
        div[data-testid="stVerticalBlockBorderWrapper"] h3,
        div[data-testid="stVerticalBlockBorderWrapper"] h4,
        div[data-testid="stVerticalBlockBorderWrapper"] h5,
        div[data-testid="stVerticalBlockBorderWrapper"] p,
        div[data-testid="stVerticalBlockBorderWrapper"] span,
        div[data-testid="stVerticalBlockBorderWrapper"] li,
        div[data-testid="stVerticalBlockBorderWrapper"] label {{
            color: {TEXT} !important;
        }}

        /* ---- Buttons ---- */
        div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
            border-radius: 8px;
            font-weight: 600;
            transition: transform 0.12s ease, box-shadow 0.12s ease;
        }}
        div[data-testid="stButton"] button:hover, div[data-testid="stFormSubmitButton"] button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(79, 70, 229, 0.18);
        }}
        div[data-testid="stButton"] button[kind="primary"], div[data-testid="stFormSubmitButton"] button[kind="primary"] {{
            background: linear-gradient(135deg, {PRIMARY} 0%, {PRIMARY_DARK} 100%);
            border: none;
        }}

        /* ---- Page links styled as nav cards ---- */
        a.stPageLink {{
            border-radius: 10px !important;
            border: 1px solid {BORDER} !important;
            padding: 0.5rem 0.9rem !important;
            transition: background 0.15s ease, border-color 0.15s ease;
        }}
        a.stPageLink, a.stPageLink span, a.stPageLink p {{
            color: {PRIMARY_DARK} !important;
        }}
        a.stPageLink:hover {{
            background: {PRIMARY_LIGHT} !important;
            border-color: {PRIMARY} !important;
        }}

        /* ---- Metrics ---- */
        div[data-testid="stMetric"] {{
            background: {SURFACE_ALT};
            border: 1px solid {BORDER};
            border-left: 4px solid {PRIMARY};
            border-radius: 10px;
            padding: 0.9rem 1.1rem;
        }}
        div[data-testid="stMetricLabel"] {{ color: {MUTED}; font-weight: 600; }}
        div[data-testid="stMetricValue"] {{ color: #111827; }}

        /* ---- Tabs ---- */
        div[data-testid="stTabs"] button[role="tab"] {{
            font-weight: 600;
            border-radius: 8px 8px 0 0;
        }}
        div[data-testid="stTabs"] button[aria-selected="true"] {{
            color: {PRIMARY};
        }}

        /* ---- Expanders ---- */
        div[data-testid="stExpander"] {{
            border-radius: 10px !important;
            border: 1px solid {BORDER} !important;
        }}

        /* ---- Alerts ---- */
        div[data-testid="stAlert"] {{
            border-radius: 10px;
        }}

        /* ---- DataFrames ---- */
        div[data-testid="stDataFrame"] {{
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid {BORDER};
        }}

        /* ---- Hero banner ---- */
        .rc-hero {{
            background: linear-gradient(135deg, {PRIMARY} 0%, {PRIMARY_DARK} 100%);
            border-radius: 18px;
            padding: 2rem 2.2rem;
            color: white;
            margin-bottom: 1.4rem;
            box-shadow: 0 12px 32px rgba(55, 48, 163, 0.25);
        }}
        .rc-hero h1 {{ margin: 0 0 0.3rem 0; font-size: 2rem; color: white; }}
        .rc-hero p {{ margin: 0; opacity: 0.92; font-size: 1.02rem; }}

        /* ---- Section headers ---- */
        .rc-section-title {{
            font-size: 1.05rem;
            font-weight: 700;
            color: #111827;
            margin: 0.2rem 0 0.6rem 0;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }}

        /* ---- Badges ---- */
        .rc-badge {{
            display: inline-block;
            padding: 0.16rem 0.65rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.01em;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str) -> None:
    st.markdown(f"""<div class="rc-hero"><h1>{title}</h1><p>{subtitle}</p></div>""", unsafe_allow_html=True)


def section_title(text: str) -> None:
    st.markdown(f'<div class="rc-section-title">{text}</div>', unsafe_allow_html=True)


def badge(text: str) -> str:
    color, bg = _STATUS_COLORS.get(str(text).lower(), (MUTED, SURFACE_ALT))
    return f'<span class="rc-badge" style="color:{color}; background:{bg};">{text}</span>'
