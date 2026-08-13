from typing import Any, Dict, List, Tuple

import streamlit as st

from auth import messaging, user_store
from i18n import t

try:
    from config.app_settings import get_settings
except ImportError:
    get_settings = None


def _help_identity() -> Tuple[str, str]:
    user = st.session_state.get("auth_user") or {}
    return user.get("cws", ""), user.get("name", "")


def _resolve_support_contacts() -> List[Dict[str, Any]]:
    if get_settings is not None:
        settings = get_settings() or {}
        support_cws = (settings.get("support_contact_cws") or user_store.ROOT_ADMIN_CWS).strip()
        configured_user = user_store.get_user(support_cws)
        if configured_user:
            return [configured_user]

    admin_users = [u for u in user_store.list_users() if user_store.is_admin(u["cws"])]
    if admin_users:
        return admin_users

    root_admin = user_store.get_user(user_store.ROOT_ADMIN_CWS)
    return [root_admin] if root_admin else []


def render_help_tab() -> None:
    from_cws, from_name = _help_identity()

    st.markdown(f'<div class="section-title">{t("help.title")}</div>', unsafe_allow_html=True)

    st.markdown(f"### {t('help.faq_title')}")
    for idx in range(1, 5):
        with st.expander(t(f"help.q{idx}"), expanded=False):
            st.markdown(t(f"help.a{idx}"))

    st.info(t("help.support_hint"), icon="💬")
    st.divider()

    st.markdown(f"### {t('help.contact_title')}")
    st.caption(t("msg.encryption_note"))

    category_options = [
        t("help.contact_category_bug"),
        t("help.contact_category_question"),
        t("help.contact_category_feature"),
        t("help.contact_category_other"),
    ]

    with st.form("help_contact_admin_form"):
        subject = st.text_input(t("help.contact_subject"))
        category = st.selectbox(t("help.contact_category"), options=category_options)
        body = st.text_area(t("help.contact_body"), height=140)
        send = st.form_submit_button(t("help.contact_send"), type="primary", width="stretch")

    if send:
        if not subject.strip() or not body.strip():
            st.warning(t("help.contact_fill_required"), icon="⚠️")
            return

        recipients = _resolve_support_contacts()
        if not recipients:
            st.warning(t("help.contact_unavailable"), icon="⚠️")
            return

        sent, skipped = 0, []
        formatted_subject = f"[{category}] {subject.strip()}"
        sender_name = from_name or from_cws or "User"

        for recipient in recipients:
            result = messaging.send_message(
                from_cws=from_cws,
                from_name=sender_name,
                to_cws=recipient["cws"],
                to_name=recipient.get("name", recipient["cws"]),
                subject=formatted_subject,
                body=body.strip(),
            )
            if result:
                sent += 1
            else:
                skipped.append(recipient["cws"])

        if sent:
            st.success(t("help.contact_sent", count=sent))
            if skipped:
                st.caption(t("help.contact_skipped", who=", ".join(skipped)))
        else:
            st.warning(t("help.contact_not_delivered"), icon="⚠️")
            if skipped:
                st.caption(t("help.contact_skipped", who=", ".join(skipped)))
