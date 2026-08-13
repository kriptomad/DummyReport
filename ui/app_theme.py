"""
ui/app_theme.py
=================
Thin backwards-compatible wrapper — the real theme system (light/dark/
colorblind-friendly palettes) now lives in ui/theme_manager.py. Kept so
any older import of `inject_base_css()` keeps working (always applies
the "dark" theme, the previous default look).
"""
from ui.theme_manager import inject_theme_css


def inject_base_css() -> None:
    """Deprecated in favor of ui.theme_manager.inject_theme_css(), which
    supports light/dark/colorblind palettes and per-user persistence.
    Kept for backwards compatibility — always applies the "dark" theme."""
    inject_theme_css("dark")

