"""
ui/messaging_widget.py
=======================
Floating, always-on-screen internal messaging widget — styled like the
"support chat" bubble common on many websites (bottom-right corner button
with an unread badge that opens a panel), but asynchronous: the other
person does not need to be online, and messages are end-to-end encrypted
(see auth/crypto_messaging.py + auth/messaging.py) so message content is
unreadable to anyone inspecting the underlying JSON file without the
sender's/recipient's own password.

Call render_floating_messenger(auth_user) once per page render, anywhere
after the auth gate — it renders itself fixed to the bottom-right corner
via a small CSS hook on a keyed st.container().
"""
from typing import Any, Dict

import streamlit as st

from auth import messaging, presence
from i18n import t
from utils.teams_link import teams_chat_link

_WIDGET_KEY = "ilt_floating_messenger"


def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
        div.st-key-{_WIDGET_KEY} {{
            position: fixed;
            bottom: 22px;
            right: 24px;
            z-index: 9999;
            width: auto;
        }}
        div.st-key-{_WIDGET_KEY} button {{
            border-radius: 50px !important;
            box-shadow: 0 4px 14px rgba(0,0,0,0.22);
            font-weight: 600;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_floating_messenger(auth_user: Dict[str, Any]) -> None:
    my_cws = auth_user.get("cws")
    my_name = auth_user.get("name", "Unknown")
    private_key_pem = auth_user.get("_private_key_pem")
    if not my_cws:
        return

    _inject_css()
    unread_n = messaging.get_unread_count(my_cws)
    label = f"✉️ {t('msg.title')}" + (f" 🔴 {unread_n}" if unread_n else "")

    with st.container(key=_WIDGET_KEY):
        with st.popover(label, width="content"):
            if not private_key_pem:
                st.warning(t("msg.no_private_key"), icon="⚠️")

            st.caption(t("msg.encryption_note"))

            msg_tab_inbox, msg_tab_sent, msg_tab_compose = st.tabs([
                t("msg.inbox"), t("msg.sent"), t("msg.compose"),
            ])

            with msg_tab_inbox:
                inbox = messaging.get_inbox(my_cws, private_key_pem=private_key_pem)
                if not inbox:
                    st.caption(t("msg.no_inbox"))
                else:
                    for m in inbox:
                        unread_flag = "" if m.get("read") else f" {t('msg.unread_badge')}"
                        with st.expander(
                            f"{m['subject']}{unread_flag} — {m['created_at'][:16]}",
                            expanded=not m.get("read"),
                        ):
                            st.caption(f"{t('msg.from')}: {m['from_name']} ({m['from_cws']})")
                            if m.get("related_pattern"):
                                st.caption(f"{t('msg.related_fix')}: {m['related_pattern']}")
                            st.markdown(m["body"])
                            bcol1, bcol2 = st.columns(2)
                            if not m.get("read"):
                                if bcol1.button(t("msg.mark_read"), key=f"fmsg_read_{m['id']}", width="stretch"):
                                    messaging.mark_read(m["id"], my_cws)
                                    st.rerun()
                            if bcol2.button(t("msg.delete"), key=f"fmsg_del_in_{m['id']}", width="stretch"):
                                messaging.delete_message(m["id"], my_cws)
                                st.rerun()

            with msg_tab_sent:
                sent = messaging.get_sent(my_cws, private_key_pem=private_key_pem)
                if not sent:
                    st.caption(t("msg.no_sent"))
                else:
                    for m in sent:
                        with st.expander(f"{m['subject']} — {m['created_at'][:16]}", expanded=False):
                            st.caption(f"{t('msg.to')}: {m['to_name']} ({m['to_cws']})")
                            if m.get("related_pattern"):
                                st.caption(f"{t('msg.related_fix')}: {m['related_pattern']}")
                            st.markdown(m["body"])
                            if st.button(t("msg.delete"), key=f"fmsg_del_out_{m['id']}", width="stretch"):
                                messaging.delete_message(m["id"], my_cws)
                                st.rerun()

            with msg_tab_compose:
                search_recipient = st.text_input(t("msg.search_recipient"), key="fmsg_search_recipient")
                candidates = messaging.search_users(search_recipient, exclude_cws=my_cws)
                if not candidates:
                    st.warning(t("msg.no_recipient_found"), icon="⚠️")
                else:
                    recipient_options = {
                        f"{'🟢' if presence.is_online(c['cws']) else '⚫'} {c['name']} ({c['cws']})": c
                        for c in candidates
                    }
                    recipient_label = st.selectbox(
                        t("msg.recipient"), options=list(recipient_options.keys()), key="fmsg_recipient_select"
                    )
                    recipient_pick = recipient_options[recipient_label]
                    st.caption(f"{t('online.last_seen')}: {presence.humanize_last_seen(recipient_pick['cws'])}")
                    if not messaging.recipient_ready(recipient_pick["cws"]):
                        st.info(t("msg.recipient_not_ready"), icon="⏳")
                    recipient_email = recipient_pick.get("email_teams")
                    if recipient_email:
                        st.link_button(
                            t("teams.message_button"),
                            teams_chat_link(recipient_email),
                            help=t("teams.message_help"),
                            width="stretch",
                        )
                    with st.form(key="fmsg_compose_form"):
                        msg_subject = st.text_input(t("msg.subject"), key="fmsg_subject")
                        msg_body = st.text_area(t("msg.body"), key="fmsg_body")
                        msg_submitted = st.form_submit_button(t("msg.send"), type="primary", width="stretch")
                        if msg_submitted:
                            recipient = recipient_options[recipient_label]
                            sent_msg = messaging.send_message(
                                from_cws=my_cws,
                                from_name=my_name,
                                to_cws=recipient["cws"],
                                to_name=recipient["name"],
                                subject=msg_subject,
                                body=msg_body,
                            )
                            if sent_msg is not None:
                                st.success(t("msg.sent_success", name=recipient["name"], cws=recipient["cws"]))
                            else:
                                st.error(t("msg.recipient_not_ready_error", name=recipient["name"]))
