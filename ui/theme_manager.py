"""
ui/theme_manager.py
======================
Theme system shared by all three apps (ILT Troubleshooter's app.py, the
standalone psld_app.py, and portal_app.py): a color-vision-deficiency
(CVD) accent-color picker layered safely on top of Streamlit's own
native Light/Dark theme engine.

Design notes (IMPORTANT — read before changing this file)
-----------------------------------------------------------
Earlier versions of this module tried to make our OWN CSS force the
page's background/surface/text colors for "light" vs "dark" vs the CVD
palettes. That approach is fundamentally broken in Streamlit: a large
number of built-in widgets (selectbox/multiselect popovers, checkboxes,
radios, sliders, code blocks, dataframes, st.info/warning/error/success
boxes, tooltips, the sidebar nav, etc.) are styled directly from
Streamlit's OWN theme context (baseweb/Emotion components reading
`theme.colors.*` in JS), not from arbitrary CSS custom properties we
inject. Streamlit's actual base theme is configured once, server-wide,
in `.streamlit/config.toml` (`[theme]` / `[theme.light]`). If our CSS
forces a light-looking background on top of a base theme that's
hardcoded to "dark" (or vice-versa), those internal widgets don't get
the memo — you end up with dark-on-dark or light-on-light text in
various corners of the app, i.e. exactly the "o branco quebra tudo, não
dá pra ler nada" bug this file was rewritten to fix.

The fix: `.streamlit/config.toml` now defines BOTH `[theme]` (dark,
default) and `[theme.light]` (light) — real, first-class Streamlit
themes. Every user can pick between them via the app's own native
"☰ menu → Settings → Theme" control (or "Use system setting" to follow
their OS), and Streamlit's real theme engine themes every single widget
consistently, because that's what it's designed to do. We no longer try
to fight or duplicate that.

What THIS module still owns: an accent/status-color picker for
color-vision-deficiency (CVD) support — Streamlit has no concept of more
than two named themes (light/dark), so a third "protanopia-safe" theme
etc. isn't something Streamlit itself can offer. Since we can't safely
touch backgrounds anymore, we only ever override a small, fully-owned
set of accent surfaces: the primary/success/warning/danger colors used
by our OWN buttons, badges, header underline, and tab highlight — using
the Okabe-Ito palette, which stays distinguishable across protanopia/
deuteranopia/tritanopia. Everything else (all native widget internals)
is left to Streamlit's real theme engine, matched via `st.context.theme.
type` so our accent overlay never mismatches the actual background.

Backward compatibility: user profiles may have "dark" or "light" saved
from before this rewrite (`auth/user_store.py::get_user_theme`). Those
two values still validate and now simply mean "standard accent colors,
no CVD override" — the actual background comes from Streamlit's own
native theme regardless of which of the two was saved.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from auth import user_store

VALID_THEMES = ("dark", "light", "protanopia", "deuteranopia", "tritanopia")
DEFAULT_THEME = "dark"

# Maps a saved theme choice to which accent set below applies. "dark"
# and "light" are both just "standard" now — the background difference
# between them is entirely handled by Streamlit's own native theme.
_ACCENT_KEY = {
    "dark": "standard",
    "light": "standard",
    "protanopia": "protanopia",
    "deuteranopia": "deuteranopia",
    "tritanopia": "tritanopia",
}

# Structural colors — NOT user-selectable; resolved from Streamlit's own
# active native theme (`st.context.theme.type`) so our CSS never
# disagrees with how Streamlit is actually rendering its own widgets.
# Values mirror .streamlit/config.toml's [theme] and [theme.light].
_STRUCTURE = {
    "dark":  {"bg": "#0a0a0a", "surface": "#1c1c1c", "border": "#333333", "ink": "#f5f5f5", "muted": "#a3a3a3"},
    "light": {"bg": "#f7f7f8", "surface": "#ffffff", "border": "#dcdcdc", "ink": "#161616", "muted": "#5f5f5f"},
}

# Accent/status colors — this IS user-selectable (the actual point of
# this module). Two full sets per accent key (one tuned for a light
# backdrop, one for dark) so contrast stays good either way; the
# CVD-safe sets use the Okabe-Ito palette's hues in both cases, just
# lightened/darkened as needed for contrast against the active backdrop.
_ACCENTS = {
    "light": {
        "standard": {
            "primary": "#caa000", "primary-dark": "#9c7d00", "primary-ink": "#111111",
            "success": "#15803d", "warning": "#b45309", "danger": "#b91c1c",
        },
        "protanopia": {
            "primary": "#0072B2", "primary-dark": "#00507e", "primary-ink": "#ffffff",
            "success": "#009E73", "warning": "#E69F00", "danger": "#D55E00",
        },
        "deuteranopia": {
            "primary": "#0072B2", "primary-dark": "#00507e", "primary-ink": "#ffffff",
            "success": "#56B4E9", "warning": "#E69F00", "danger": "#D55E00",
        },
        "tritanopia": {
            "primary": "#CC79A7", "primary-dark": "#a35784", "primary-ink": "#111111",
            "success": "#009E73", "warning": "#D55E00", "danger": "#9E2146",
        },
    },
    "dark": {
        "standard": {
            "primary": "#FFCD11", "primary-dark": "#E6B800", "primary-ink": "#111111",
            "success": "#4ade80", "warning": "#fbbf24", "danger": "#f87171",
        },
        # Same Okabe-Ito hues as the light-backdrop sets above, just
        # brightened for legibility against a near-black background —
        # dark text (not white) on the lightened accent chips/buttons.
        "protanopia": {
            "primary": "#5AB4E5", "primary-dark": "#0072B2", "primary-ink": "#0a0a0a",
            "success": "#4FCBA3", "warning": "#F3BB4A", "danger": "#F0895B",
        },
        "deuteranopia": {
            "primary": "#5AB4E5", "primary-dark": "#0072B2", "primary-ink": "#0a0a0a",
            "success": "#8CCDF2", "warning": "#F3BB4A", "danger": "#F0895B",
        },
        "tritanopia": {
            "primary": "#E29FC4", "primary-dark": "#CC79A7", "primary-ink": "#0a0a0a",
            "success": "#4FCBA3", "warning": "#F0895B", "danger": "#D9718F",
        },
    },
}

_THEME_LABELS = {
    "dark": "Standard",
    "light": "Standard",
    "protanopia": "Protanopia-friendly",
    "deuteranopia": "Deuteranopia-friendly",
    "tritanopia": "Tritanopia-friendly",
}
# What the picker actually shows/offers — collapses "dark"/"light" into
# one "Standard" choice, since background is no longer picked here.
_PICKER_OPTIONS = ("dark", "protanopia", "deuteranopia", "tritanopia")


def _current_cws() -> str:
    user = st.session_state.get("auth_user") or {}
    return (user.get("cws", "") or "").strip().upper()


def _native_theme_type() -> str:
    """Returns "light" or "dark" reflecting Streamlit's OWN active theme
    (set via .streamlit/config.toml + the user's own ☰ Settings -> Theme
    pick / OS preference) — never guessed or overridden by us. Falls
    back to "dark" (this app's configured default) if Streamlit can't
    report it yet (e.g. very first paint of a new session)."""
    try:
        t = st.context.theme.type
    except Exception:
        t = None
    return t if t in ("light", "dark") else DEFAULT_THEME


def get_active_theme() -> str:
    """Resolves the accent-color choice to use for the current run: the
    signed-in user's saved preference (persists across devices/logins)
    if set, else this browser tab's own session_state pick (so an
    anonymous pre-login screen can still be themed), else the default."""
    cws = _current_cws()
    if cws:
        saved = user_store.get_user_theme(cws)
        if saved in VALID_THEMES:
            return saved
    picked = st.session_state.get("_ui_theme")
    return picked if picked in VALID_THEMES else DEFAULT_THEME


def render_theme_selector(label: str = "Color-blind-safe accents") -> None:
    """Small selectbox (meant for the sidebar) letting the user switch
    the CVD-safe accent palette; persists immediately to their profile
    if logged in. Also renders a one-click "☀️ Light / 🌙 Dark" button
    pair right above it — Streamlit's own native background switch lives
    inside the ☰ hamburger menu, which turned out to not be discoverable
    enough (users reported "there's no light theme anymore" simply
    because they never opened that menu); these buttons drive the exact
    same native control programmatically so switching backgrounds is
    right here, in the same place as everything else theme-related."""
    st.caption("Appearance")
    c1, c2 = st.columns(2)
    if c1.button("☀️ Light", key="_theme_native_light_btn", width="stretch"):
        _click_native_theme_button("Light")
    if c2.button("🌙 Dark", key="_theme_native_dark_btn", width="stretch"):
        _click_native_theme_button("Dark")

    current = get_active_theme()
    picked = st.selectbox(
        label,
        options=list(_PICKER_OPTIONS),
        index=_PICKER_OPTIONS.index(current) if current in _PICKER_OPTIONS else 0,
        format_func=lambda k: _THEME_LABELS.get(k, k),
        key="_theme_selector_widget",
        help="Only swaps accent/status colors for color-vision-deficiency"
             "-safe options — the ☀️/🌙 buttons above control the actual "
             "light/dark background (via Streamlit's own native theme).",
    )
    if picked != current:
        st.session_state["_ui_theme"] = picked
        cws = _current_cws()
        if cws:
            user_store.set_user_theme(cws, picked)
        st.rerun()


def _click_native_theme_button(which: str) -> None:
    """Programmatically drives Streamlit's own native ☰ menu -> Light/Dark
    toggle (the one true source of truth for background theming — see
    this module's docstring) from a plain, always-visible sidebar button,
    instead of requiring users to find/open the hamburger menu themselves.
    Opens the menu, clicks the matching native `stMainMenuItem-theme-*`
    item, then closes the menu again — all via a tiny injected script,
    since Streamlit's own frontend doesn't expose a Python-side API for
    this."""
    components.html(
        f"""
        <script>
        (function() {{
            try {{
                var doc = window.parent.document;
                var menuBtn = doc.querySelector('[data-testid="stMainMenuButton"]');
                if (!menuBtn) return;
                menuBtn.click();
                setTimeout(function() {{
                    var item = doc.querySelector('[data-testid="stMainMenuItem-theme-{which}"]');
                    if (item) {{ item.click(); }}
                    setTimeout(function() {{
                        var evt = new KeyboardEvent('keydown', {{
                            key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true,
                        }});
                        doc.dispatchEvent(evt);
                    }}, 250);
                }}, 250);
            }} catch (e) {{ /* no-op if DOM structure ever changes */ }}
        }})();
        </script>
        """,
        height=0,
    )


def inject_theme_css(theme_name: str | None = None) -> None:
    """Injects the shared stylesheet's accent colors using the given
    theme's palette (falls back to the active/resolved theme if not
    given), matched to Streamlit's OWN currently-active native
    background (light/dark) so our CSS never disagrees with how
    Streamlit is already rendering its built-in widgets. Call this once,
    right after `st.set_page_config(...)`, in every entrypoint (app.py,
    psld_app.py, portal_app.py)."""
    _render_theme_css_fragment(theme_name)


@st.fragment(run_every=2)
def _render_theme_css_fragment(theme_name: str | None) -> None:
    """Streamlit's native Light/Dark toggle (☰ menu) restyles every
    built-in widget instantly on the client (Emotion/React re-render) —
    but it does NOT, by itself, trigger a Python script rerun. Our own
    CSS below (`_CSS_TEMPLATE`) is regular server-rendered HTML, so its
    `--ilt-*` variables (matched to `_native_theme_type()`) would go
    stale the instant the user flips native theme, until the next
    unrelated rerun happened to catch up — in the meantime our overlay
    colors disagreed with the (already-correct) native ones, reproducing
    the original "switching theme breaks readability" complaint even
    after the config.toml fix.

    An earlier version of this fix tried to detect the native theme
    change client-side (watching the `.stApp` element's Emotion class
    hash) and force a full `window.location.reload()` — but a hard
    reload re-negotiates the browser session from scratch and silently
    logged users out before the login cookie had a chance to restore,
    which is worse than the original bug. `st.fragment(run_every=...)`
    is Streamlit's own supported mechanism for periodic partial reruns:
    it re-executes just this small function every 2 seconds (cheap — a
    few dozen CSS variables) WITHOUT reloading the page or touching
    `st.session_state`/login, so within at most ~2s of the user flipping
    native theme, our accent/status colors resync automatically."""
    theme_name = theme_name if theme_name in VALID_THEMES else get_active_theme()
    native = _native_theme_type()
    struct = _STRUCTURE[native]
    accent_key = _ACCENT_KEY.get(theme_name, "standard")
    accent = _ACCENTS[native][accent_key]
    p = {**struct, **accent}
    st.markdown(_CSS_TEMPLATE.format(**p), unsafe_allow_html=True)


# A minimal, modern, low-clutter stylesheet — flatter surfaces, fewer
# heavy borders/shadows/badges than the original design, built entirely
# from the palette's CSS variables so every theme (light/dark/CVD) just
# works without any theme-specific overrides elsewhere in the app.
_CSS_TEMPLATE = """
<style>
    :root, .stApp {{
        --ilt-primary: {primary};
        --ilt-primary-dark: {primary-dark};
        --ilt-primary-ink: {primary-ink};
        --ilt-ink: {ink};
        --ilt-muted: {muted};
        --ilt-border: {border};
        --ilt-surface: {surface};
        --ilt-bg-soft: {bg};
        --ilt-danger: {danger};
        --ilt-success: {success};
        --ilt-warning: {warning};

        --background-color: {bg};
        --secondary-background-color: {surface};
        --text-color: {ink};
        --primary-color: {primary};
    }}

    html, body, .stApp,
    [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] {{
        background-color: var(--ilt-bg-soft) !important;
        color: var(--ilt-ink) !important;
    }}
    .stMarkdown, .stMarkdown p, .stCaption, small {{ color: var(--ilt-ink) !important; }}

    /* Header — flat, no gradient/shadow clutter */
    .main-header {{
        border-bottom: 2px solid var(--ilt-primary);
        padding: 0.9rem 0.2rem 0.8rem;
        margin-bottom: 1.3rem;
    }}
    .main-header h1 {{
        margin: 0;
        font-size: 1.3rem;
        font-weight: 600;
        letter-spacing: 0.1px;
        color: var(--ilt-ink) !important;
    }}

    .status-connected    {{ color: var(--ilt-success) !important; font-weight: 600; }}
    .status-disconnected {{ color: var(--ilt-danger) !important; font-weight: 600; }}

    /* Cards — flat single border, no shadow/gradient */
    .error-card, .info-card {{
        border: 1px solid var(--ilt-border);
        border-left: 3px solid var(--ilt-danger);
        background: var(--ilt-surface) !important;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin-bottom: 0.6rem;
        color: var(--ilt-ink) !important;
    }}
    .info-card {{ border-left-color: var(--ilt-primary); }}
    .error-card-title {{ font-weight: 600; color: var(--ilt-ink) !important; font-size: 0.92rem; }}

    .tariff-badge {{
        background: var(--ilt-warning);
        color: #ffffff !important;
        padding: 1px 8px;
        border-radius: 999px;
        font-size: 0.7rem;
        font-weight: 600;
    }}

    .section-title {{
        font-size: 0.98rem;
        font-weight: 700;
        color: var(--ilt-ink) !important;
        border-bottom: 1px solid var(--ilt-border);
        padding-bottom: 0.35rem;
        margin: 0.9rem 0 0.75rem;
    }}

    div[data-testid="metric-container"] {{
        background: var(--ilt-surface) !important;
        border: 1px solid var(--ilt-border);
        border-radius: 8px;
        padding: 0.6rem;
    }}
    div[data-testid="stMetricValue"], div[data-testid="stMetricLabel"] {{
        color: var(--ilt-ink) !important;
    }}

    .stButton > button, .stFormSubmitButton > button {{
        border-radius: 6px;
        font-weight: 500;
        background-color: var(--ilt-surface);
        color: var(--ilt-ink) !important;
        border: 1px solid var(--ilt-border);
    }}
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{
        background: var(--ilt-primary) !important;
        border-color: var(--ilt-primary) !important;
        color: var(--ilt-primary-ink) !important;
        font-weight: 600;
    }}
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{
        background: var(--ilt-primary-dark) !important;
        border-color: var(--ilt-primary-dark) !important;
    }}

    /* Nav tabs — plain understated pills, no heavy pill-tray background */
    div[data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 1px solid var(--ilt-border);
        flex-wrap: wrap;
    }}
    button[data-baseweb="tab"] {{
        border-radius: 6px 6px 0 0 !important;
        padding: 0.3rem 0.8rem !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        color: var(--ilt-muted) !important;
        background: transparent !important;
        border: none !important;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: var(--ilt-primary) !important;
        border-bottom: 2px solid var(--ilt-primary) !important;
    }}
    div[data-baseweb="tab-highlight"] {{ display: none !important; }}
    div[data-baseweb="tab-border"]    {{ display: none !important; }}

    div[data-testid="stPills"] button {{ border-radius: 999px !important; font-size: 0.8rem !important; }}

    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    div[data-baseweb="select"] * {{ color: var(--ilt-ink) !important; }}
    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    div[data-baseweb="select"] > div, div[data-baseweb="base-input"] {{
        background-color: var(--ilt-surface) !important;
        border: 1px solid var(--ilt-border) !important;
    }}

    section[data-testid="stSidebar"] {{
        background: var(--ilt-surface) !important;
        border-right: 1px solid var(--ilt-border);
    }}

    div[data-testid="stExpander"] {{
        border-radius: 8px;
        border: 1px solid var(--ilt-border);
        background: var(--ilt-surface) !important;
    }}
    div[data-testid="stDataFrame"], div[data-testid="stTable"] {{ background: var(--ilt-surface) !important; }}

    footer {{ visibility: hidden; }}
    [data-testid="stDecoration"] {{ display: none !important; }}

    /* Global announcement banner (see auth/announcements.py) */
    .global-banner {{
        border: 1px solid var(--ilt-primary);
        background: var(--ilt-surface);
        color: var(--ilt-ink) !important;
        border-radius: 8px;
        padding: 0.55rem 0.9rem;
        margin-bottom: 1rem;
        font-size: 0.88rem;
    }}
</style>
"""
