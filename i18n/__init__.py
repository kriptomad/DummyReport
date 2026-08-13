"""
i18n — lightweight internationalization helper for the Streamlit app.

Usage:
    from i18n import t, language_selector, get_language, set_language

    t("tabs.report")                     -> "Report" (en) / "Relatório" (pt)
    t("report.records", count=5)         -> supports str.format() kwargs

Language is stored in st.session_state["language"] and defaults to "en".
Add a selector anywhere with `language_selector()` (usually in the sidebar).
"""
from i18n.translations import TRANSLATIONS

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "pt": "Português",
}


def get_default_language() -> str:
    try:
        from config.app_settings import get_setting
        lang = get_setting("default_language", DEFAULT_LANGUAGE)
    except Exception:
        lang = DEFAULT_LANGUAGE
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def get_language() -> str:
    """Return the currently selected language code ('en' or 'pt')."""
    try:
        import streamlit as st
        return st.session_state.get("language", get_default_language())
    except Exception:
        return get_default_language()


def set_language(lang: str) -> None:
    """Set the active language for the current Streamlit session."""
    import streamlit as st
    if lang not in SUPPORTED_LANGUAGES:
        lang = get_default_language()
    st.session_state["language"] = lang


def t(key: str, **kwargs) -> str:
    """
    Translate `key` into the current session language.

    Falls back to English if the key is missing in the active language,
    and falls back to the raw key (so the UI never crashes) if it's
    missing everywhere.
    """
    lang = get_language()
    entry = TRANSLATIONS.get(key)

    if entry is None:
        text = key
    else:
        text = entry.get(lang) or entry.get(get_default_language()) or entry.get(DEFAULT_LANGUAGE) or key

    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def language_selector(location="sidebar", key: str = "language_selector") -> str:
    """
    Renders a small language selector widget and returns the selected code.

    Args:
        location: "sidebar" renders inside st.sidebar, anything else renders
            inline in the current container.
        key: Streamlit widget key (change if you need more than one selector).
    """
    import streamlit as st

    if "language" not in st.session_state:
        st.session_state["language"] = get_default_language()

    codes = list(SUPPORTED_LANGUAGES.keys())
    labels = [SUPPORTED_LANGUAGES[c] for c in codes]
    current_index = codes.index(st.session_state["language"]) if st.session_state["language"] in codes else 0

    container = st.sidebar if location == "sidebar" else st

    selected_label = container.selectbox(
        "🌐 Language / Idioma",
        labels,
        index=current_index,
        key=key,
    )
    selected_code = codes[labels.index(selected_label)]
    st.session_state["language"] = selected_code
    return selected_code
