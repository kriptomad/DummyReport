"""
ui/sql_glossary_tab.py
=======================
A simple reference/glossary of SQL keywords and functions, so anyone who
feels lost while using the Query Builder (or writing raw SQL in the SQL
tab) can look up what a keyword does, its syntax, and a worked example.
"""
import streamlit as st

from ai.sql_functions import SQL_FUNCTIONS, get_categories, search_functions
from i18n import t


def render_sql_glossary_tab() -> None:
    st.markdown(f'<div class="section-title">{t("glossary.title")}</div>', unsafe_allow_html=True)
    st.caption(t("glossary.subtitle"))

    col_search, col_filter = st.columns([3, 1])
    query = col_search.text_input(t("glossary.search_label"), key="glossary_search", placeholder=t("glossary.search_placeholder"))
    categories = ["All"] + get_categories()
    category = col_filter.selectbox(t("glossary.category_label"), categories, key="glossary_category")

    results = search_functions(query=query, category=category)

    st.caption(t("glossary.result_count", count=len(results), total=len(SQL_FUNCTIONS)))

    if not results:
        st.info(t("glossary.no_results"))
        return

    # Group by category for readability, preserving the catalog's own order.
    grouped: dict = {}
    for f in results:
        grouped.setdefault(f["category"], []).append(f)

    for cat, items in grouped.items():
        st.markdown(f"#### {cat}")
        for f in items:
            with st.expander(f"**{f['name']}**"):
                st.markdown(f"**{t('glossary.syntax_label')}:** `{f['syntax']}`")
                st.markdown(f"**{t('glossary.description_label')}:** {f['description']}")
                st.markdown(f"**{t('glossary.example_label')}:**")
                st.code(f["example"], language="sql")
