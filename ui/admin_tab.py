"""
ui/admin_tab.py
=================
Administration tab — visible only to admin users (the root admin CWS
"DEMOADMIN" plus anyone granted admin privileges from here).

Sections:
  1. Pending Approvals — new registrations waiting for a decision.
  2. All Users         — full roster: view, remove, promote/demote admin.
  3. Broadcast List     — manage who receives admin broadcast messages,
                          and send a broadcast (via the existing encrypted
                          internal messaging system).
"""
import pandas as pd
import streamlit as st

from auth import user_store, broadcast_list, messaging, presence, email_utils, audit_log
from auth.user_store import ROOT_ADMIN_CWS
from config import app_settings
from i18n import SUPPORTED_LANGUAGES, t
from utils.teams_link import teams_chat_link
from troubleshooter import ai_core, ilt_ai_core, local_intelligence, psld_semantic_engine, servicenow_resolution_kb
from troubleshooter.loader import load_troubleshoot_db


def _admin_identity() -> tuple[str, str]:
    user = st.session_state.get("auth_user") or {}
    return user.get("cws", ""), user.get("name", "")


def _render_audit_log_subtab() -> None:
    """Full backend audit trail, reachable directly from inside this app's
    own Administration tab (not just the Portal's Central Admin Dashboard
    super-panel) — covers logins, admin actions, AI training runs (deep-
    learn/mega-deep-learn/continuous learning), DB connections (including
    Oracle username-mismatch flags), autonomous-fix approve/reject/teach,
    and integration errors (e.g. ServiceNow sync failures)."""
    st.markdown(f"##### {t('admin.sub_audit')}")
    st.caption(t("admin.audit_caption"))
    col1, col2, col3, col4 = st.columns([2, 1.4, 1.2, 1])
    cws_filter = col1.text_input(t("admin.audit_filter_cws"), key="_ilt_audit_cws_filter")
    category_filter = col2.selectbox(
        t("admin.audit_filter_category"),
        options=["(all)"] + audit_log.KNOWN_CATEGORIES,
        key="_ilt_audit_category_filter",
    )
    severity_filter = col3.selectbox(t("admin.audit_filter_severity"), options=["(all)", "error", "warning", "info"], key="_ilt_audit_severity_filter")
    limit = col4.number_input(t("admin.audit_filter_limit"), min_value=10, max_value=2000, value=200, step=10, key="_ilt_audit_limit")

    events = audit_log.list_events(
        limit=int(limit),
        cws=cws_filter.strip() or None,
        category=None if category_filter == "(all)" else category_filter,
        severity=None if severity_filter == "(all)" else severity_filter,
    )
    if not events:
        st.caption(t("admin.audit_empty"))
        return

    all_events_unfiltered = audit_log.list_events(limit=2000)
    error_count = sum(1 for e in all_events_unfiltered if e.get("severity") == "error")
    warning_count = sum(1 for e in all_events_unfiltered if e.get("severity") == "warning")
    m1, m2, m3 = st.columns(3)
    m1.metric(t("admin.audit_total_events"), len(all_events_unfiltered))
    m2.metric("⚠️ " + t("admin.audit_warnings"), warning_count)
    m3.metric("🚫 " + t("admin.audit_errors"), error_count)

    st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")


def render_admin_tab(show_psld: bool = True) -> None:
    """
    Renders the Administration tab.

    `show_psld` controls whether PSLD - Parts-specific management/monitoring
    (the Parts - Brasil / Double-Check reviewer access flags, PSLD's
    per-screen checkboxes, and the whole PSLD AI section in AI Control
    Center) is shown here. PSLD - Parts is a fully standalone app now (see
    psld_app.py) — that content belongs in the Portal's Central Admin
    Dashboard "super-panel" (which calls this same function with the
    default `show_psld=True`), not duplicated inside ILT Troubleshooter's
    own local Administration tab, which passes `show_psld=False`.
    """
    admin_cws, admin_name = _admin_identity()

    # Defense-in-depth: app.py only shows this tab in the UI for admins, but
    # that's just a visibility toggle — nothing stops this function from
    # being called directly (a future refactor, a stray import, etc.). Re-
    # check admin status here so a non-admin can never reach the actual
    # admin actions below even if the tab-gating logic in app.py is ever
    # bypassed or changes.
    if not admin_cws or not user_store.is_admin(admin_cws):
        st.error(t("admin.access_denied"), icon="🚫")
        return

    settings = app_settings.get_settings()
    show_broadcast = settings.get("enable_messaging", True) and settings.get("enable_broadcast", True)

    st.markdown(f'<div class="section-title">{t("admin.title")}</div>', unsafe_allow_html=True)
    st.caption(t("admin.subtitle"))

    subtab_defs = [
        ("pending", t("admin.sub_pending")),
        ("users", t("admin.sub_users")),
    ]
    if show_broadcast:
        subtab_defs.append(("broadcast", t("admin.sub_broadcast")))
    is_root_admin = (admin_cws or "").strip().upper() == ROOT_ADMIN_CWS
    # AI Control Center: consolidates every AI/self-learning subsystem in
    # the app (main Troubleshooter local intelligence, PSLD - Parts
    # semantic engine + ResolutionDocs deep-learn) in one place, with
    # monitoring, cross-reference ("cruzamentos") visibility, and manual
    # force-sync/force-deep-learn actions. Available to ALL admins, not
    # just the root admin — the underlying actions are read-only/local or
    # bounded read-only DB aggregates, nothing destructive.
    subtab_defs.append(("ai", t("admin.sub_ai")))
    subtab_defs.append(("audit", t("admin.sub_audit")))
    subtab_defs.append(("settings", t("admin.sub_settings")))
    subtabs = dict(zip([key for key, _ in subtab_defs], st.tabs([label for _, label in subtab_defs])))

    # ── Pending approvals ────────────────────────────────────
    with subtabs["pending"]:
        pending = user_store.list_pending_users()
        if not pending:
            st.success(t("admin.no_pending"), icon="✅")
        else:
            st.caption(t("admin.pending_count", count=len(pending)))
            for u in pending:
                cws = u["cws"]
                with st.container(border=True):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"**{u.get('name', '')}** ({cws})")
                        st.caption(f"✉️ {u.get('email_teams', '—')} · 💼 {u.get('cargo', '—')}")
                        st.caption(f"🕓 {t('admin.requested_at')}: {str(u.get('created_at', ''))[:19]}")
                    with c2:
                        if st.button(t("admin.approve"), key=f"approve_{cws}", type="primary", width="stretch"):
                            ok, msg = user_store.approve_user(cws, admin_cws)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    with st.expander(t("admin.reject_expander"), expanded=False):
                        reason = st.text_area(t("admin.reject_reason"), key=f"reject_reason_{cws}")
                        if st.button(t("admin.reject"), key=f"reject_{cws}", width="stretch"):
                            ok, msg = user_store.reject_user(cws, admin_cws, reason)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()

    # ── All users ─────────────────────────────────────────────
    with subtabs["users"]:
        all_users = user_store.list_users()
        online_count = len(presence.list_online_users())
        st.caption(t("admin.total_users", count=len(all_users)) + f" · 🟢 {online_count} online")

        status_icons = {"approved": "🟢", "pending": "🟡", "rejected": "🔴", None: "🟢"}

        options = [
            f"{status_icons.get(u.get('status'), '⚪')} "
            f"{'🟢' if presence.is_online(u['cws']) else '⚫'} "
            f"{u['name']} ({u['cws']})"
            for u in all_users
        ]
        if not options:
            st.info(t("admin.no_users"), icon="ℹ️")
        else:
            selected_label = st.selectbox(t("admin.select_user"), options=options, key="admin_user_select")
            selected_idx = options.index(selected_label)
            selected = all_users[selected_idx]
            sel_cws = selected["cws"]

            with st.container(border=True):
                st.markdown(f"### {selected.get('name', '')} ({sel_cws})")
                info_cols = st.columns(3)
                info_cols[0].metric(t("admin.status_label"), (selected.get("status") or "approved").capitalize())
                info_cols[1].metric(t("admin.admin_label"), "Yes" if user_store.is_admin(sel_cws) else "No")
                info_cols[2].metric(t("admin.created_label"), str(selected.get("created_at", ""))[:10])
                online = presence.is_online(sel_cws)
                st.caption(
                    f"{t('online.status_online') if online else t('online.status_offline')} · "
                    f"{t('online.last_seen')}: {presence.humanize_last_seen(sel_cws)}"
                )
                st.caption(f"✉️ {selected.get('email_teams', '—')} · 💼 {selected.get('cargo', '—')}")
                if selected.get("email_teams"):
                    st.link_button(
                        t("teams.message_button"),
                        teams_chat_link(selected["email_teams"]),
                        help=t("teams.message_help"),
                    )
                if selected.get("status") == "rejected":
                    st.warning(f"{t('admin.rejected_reason_label')}: {selected.get('rejected_reason', '—')}")

                # Persistent temp-password banner: survives reruns (unlike a
                # one-off st.success/st.code inside the button's own click
                # handler, which disappears the moment ANYTHING else on the
                # page is clicked) until the admin explicitly dismisses it —
                # fixes the real complaint that a reset password could be
                # shown once and then never be recoverable from the UI again.
                temp_pw_key = f"admin_temp_password_{sel_cws}"
                pending_pw = st.session_state.get(temp_pw_key)
                if pending_pw:
                    st.warning(t("admin.reset_password_shown_banner", cws=sel_cws), icon="🔑")
                    st.code(pending_pw, language=None)
                    if st.button(t("admin.reset_password_dismiss"), key=f"dismiss_temp_pw_{sel_cws}"):
                        del st.session_state[temp_pw_key]
                        st.rerun()

                is_root = sel_cws.strip().upper() == user_store.ROOT_ADMIN_CWS

                b1, b2, b3, b4 = st.columns(4)
                with b1:
                    if user_store.is_admin(sel_cws):
                        if not is_root and st.button(t("admin.revoke_admin"), key=f"revoke_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_admin(sel_cws, False)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    else:
                        if st.button(t("admin.grant_admin"), key=f"grant_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_admin(sel_cws, True)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                with b2:
                    if selected.get("status") == "rejected":
                        if st.button(t("admin.approve"), key=f"reapprove_{sel_cws}", width="stretch"):
                            ok, msg = user_store.approve_user(sel_cws, admin_cws)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                with b3:
                    if not is_root:
                        confirm_key = f"confirm_remove_{sel_cws}"
                        if st.session_state.get(confirm_key):
                            if st.button(t("admin.confirm_remove"), key=f"do_remove_{sel_cws}", type="primary", width="stretch"):
                                ok, msg = user_store.remove_user(sel_cws)
                                (st.success if ok else st.error)(msg)
                                st.session_state[confirm_key] = False
                                if ok:
                                    st.rerun()
                        else:
                            if st.button(t("admin.remove_user"), key=f"remove_{sel_cws}", width="stretch"):
                                st.session_state[confirm_key] = True
                                st.rerun()
                    else:
                        st.caption(t("admin.root_protected"))
                with b4:
                    reset_confirm_key = f"confirm_reset_pw_{sel_cws}"
                    if st.session_state.get(reset_confirm_key):
                        if st.button(t("admin.confirm_reset_password"), key=f"do_reset_pw_{sel_cws}", type="primary", width="stretch"):
                            ok, msg, temp_password = user_store.admin_reset_password(sel_cws, admin_cws)
                            st.session_state[reset_confirm_key] = False
                            if ok:
                                # Always persist the temp password for on-screen
                                # display (see the banner above) — never rely
                                # solely on e-mail delivery succeeding, and
                                # never let it disappear after a single render.
                                st.session_state[f"admin_temp_password_{sel_cws}"] = temp_password
                                audit_log.record_event(
                                    "password_reset", cws=sel_cws, detail=f"Reset by admin {admin_cws}",
                                    app="ilt", category="admin", severity="warning",
                                )
                                target_email = selected.get("email_teams", "")
                                subject = t("admin.reset_password_email_subject")
                                body = t(
                                    "admin.reset_password_email_body",
                                    cws=sel_cws,
                                    temp_password=temp_password,
                                )
                                sent, send_msg = email_utils.send_email(target_email, subject, body)
                                if sent:
                                    st.success(t("admin.reset_password_success_emailed", email=target_email))
                                else:
                                    st.warning(t("admin.reset_password_email_failed", msg=send_msg))
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        if st.button(t("admin.reset_password"), key=f"reset_pw_{sel_cws}", width="stretch"):
                            st.session_state[reset_confirm_key] = True
                            st.rerun()

                if show_psld:
                    st.divider()
                    psld_col1, psld_col2 = st.columns([3, 1])
                    with psld_col1:
                        st.caption(
                            t("admin.psld_flag_caption", status=(
                                t("admin.psld_flag_on") if user_store.is_psld_parts(sel_cws) else t("admin.psld_flag_off")
                            ))
                        )
                    with psld_col2:
                        if user_store.is_psld_parts(sel_cws):
                            if st.button(t("admin.psld_flag_revoke"), key=f"psld_revoke_{sel_cws}", width="stretch"):
                                ok, msg = user_store.set_psld_parts_flag(sel_cws, False)
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()
                        else:
                            if st.button(t("admin.psld_flag_grant"), key=f"psld_grant_{sel_cws}", width="stretch"):
                                ok, msg = user_store.set_psld_parts_flag(sel_cws, True)
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()

                    reviewer_col1, reviewer_col2 = st.columns([3, 1])
                    with reviewer_col1:
                        st.caption(
                            t("admin.reviewer_flag_caption", status=(
                                t("admin.psld_flag_on") if user_store.is_parts_reviewer(sel_cws) else t("admin.psld_flag_off")
                            ))
                        )
                    with reviewer_col2:
                        if user_store.is_parts_reviewer(sel_cws):
                            if st.button(t("admin.reviewer_flag_revoke"), key=f"reviewer_revoke_{sel_cws}", width="stretch"):
                                ok, msg = user_store.set_parts_reviewer_flag(sel_cws, False)
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()
                        else:
                            if st.button(t("admin.reviewer_flag_grant"), key=f"reviewer_grant_{sel_cws}", width="stretch"):
                                ok, msg = user_store.set_parts_reviewer_flag(sel_cws, True)
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.rerun()

                ilt_col1, ilt_col2 = st.columns([3, 1])
                with ilt_col1:
                    st.caption(
                        "\"ILT - Transportation\" portal access: "
                        + ("granted" if user_store.is_ilt_transportation(sel_cws) else "not granted")
                    )
                with ilt_col2:
                    if user_store.is_ilt_transportation(sel_cws):
                        if st.button("Revoke ILT access", key=f"ilt_revoke_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_ilt_transportation_flag(sel_cws, False)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    else:
                        if st.button("Grant ILT access", key=f"ilt_grant_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_ilt_transportation_flag(sel_cws, True)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()

                # "ILT Support" flag — controls access to the "Autonomous
                # Fix" tab (approve/reject AI-drafted fix proposals from
                # the mega deep-learn). Not gated by show_psld — ILT-only.
                support_col1, support_col2 = st.columns([3, 1])
                with support_col1:
                    st.caption(
                        "\"ILT Support\" (Autonomous Fix approval access): "
                        + ("granted" if user_store.is_ilt_support(sel_cws) else "not granted")
                    )
                with support_col2:
                    if user_store.is_ilt_support(sel_cws):
                        if st.button("Revoke Support access", key=f"ilt_support_revoke_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_ilt_support_flag(sel_cws, False)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    else:
                        if st.button("Grant Support access", key=f"ilt_support_grant_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_ilt_support_flag(sel_cws, True)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()


                # "Business" flag — controls whether the one-click "Connect
                # with application account" button appears for this user in
                # ILT Troubleshooter's Oracle connection dialog (see
                # app.py's connection_dialog() / config/db_config.py). Not
                # gated by show_psld — this is an ILT-only concern and
                # should be visible/manageable in both ILT's own local
                # Administration tab and the Portal's super-panel.
                business_col1, business_col2 = st.columns([3, 1])
                with business_col1:
                    st.caption(
                        "\"Business\" application-account access (one-click Oracle "
                        "connect, no personal DB credentials needed): "
                        + ("granted" if user_store.is_business_user(sel_cws) else "not granted")
                    )
                with business_col2:
                    if user_store.is_business_user(sel_cws):
                        if st.button("Revoke Business access", key=f"business_revoke_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_business_flag(sel_cws, False)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()
                    else:
                        if st.button("Grant Business access", key=f"business_grant_{sel_cws}", width="stretch"):
                            ok, msg = user_store.set_business_flag(sel_cws, True)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()

                # Registered personal Oracle DB username — lets the
                # connection dialog warn this user if they're about to
                # connect with a *different* Oracle account than the one
                # on file for them (the exact "shared login" pattern DBA
                # account-sharing monitoring flags). Purely a username on
                # record for bookkeeping/mismatch-checking — never a
                # password, and never used to auto-connect on its own.
                oracle_user_col1, oracle_user_col2 = st.columns([3, 1])
                with oracle_user_col1:
                    new_oracle_username = st.text_input(
                        "Registered Oracle DB username (their own personal account)",
                        value=user_store.get_oracle_username(sel_cws),
                        key=f"oracle_username_{sel_cws}",
                        placeholder="e.g. demo_user_db",
                        help="Used only to warn this user if they try to connect with someone "
                             "else's Oracle username — never stores a password.",
                    )
                with oracle_user_col2:
                    st.markdown("<div style='height:1.8rem'></div>", unsafe_allow_html=True)
                    if st.button("Save", key=f"oracle_username_save_{sel_cws}", width="stretch"):
                        ok, msg = user_store.set_oracle_username(sel_cws, new_oracle_username)
                        (st.success if ok else st.error)(msg)

                # ── Per-screen (tab-level) access lock-down ──────────
                st.divider()
                with st.expander(f"🔒 Screen access for {selected.get('name', sel_cws)}", expanded=False):
                    st.caption(
                        "Untick a screen to hide that specific tab for this user, without "
                        "removing their overall portal access. Admins always see everything."
                    )
                    if user_store.is_admin(sel_cws):
                        st.info("This user is an administrator — all screens are always visible.", icon="ℹ️")
                    else:
                        current_access = user_store.get_screen_access(sel_cws)
                        portals_to_show = []
                        if user_store.is_ilt_transportation(sel_cws):
                            portals_to_show.append(("ilt", "ILT Troubleshooter"))
                        if show_psld and user_store.is_psld_parts(sel_cws):
                            portals_to_show.append(("psld", "PSLD - Parts"))
                        if not portals_to_show:
                            st.caption("This user has no portal access yet — grant Parts - Brasil and/or ILT - Transportation above first.")
                        for portal_key, portal_label in portals_to_show:
                            st.markdown(f"**{portal_label}**")
                            screen_items = list(user_store.SCREEN_REGISTRY.get(portal_key, {}).items())
                            n_cols = 3
                            cols = st.columns(n_cols)
                            new_values = {}
                            for idx, (screen_key, screen_label) in enumerate(screen_items):
                                col = cols[idx % n_cols]
                                allowed_now = current_access.get(screen_key, True)
                                new_values[screen_key] = col.checkbox(
                                    screen_label, value=allowed_now,
                                    key=f"screen_{sel_cws}_{screen_key}",
                                )
                            if st.button(f"Save {portal_label} screen access", key=f"save_screens_{portal_key}_{sel_cws}"):
                                changed = 0
                                for screen_key, enabled in new_values.items():
                                    if current_access.get(screen_key, True) != enabled:
                                        user_store.set_screen_access(sel_cws, screen_key, enabled)
                                        audit_log_event = f"screen_access:{screen_key}={enabled}"
                                        try:
                                            from auth import audit_log as _audit_log
                                            _audit_log.record_event("screen_access_changed", cws=admin_cws, detail=f"{audit_log_event} for {sel_cws}", app="admin")
                                        except Exception:
                                            pass
                                        changed += 1
                                if changed:
                                    st.success(f"{changed} screen(s) updated for {sel_cws}.")
                                    st.rerun()
                                else:
                                    st.info("No changes to save.")


    # ── Broadcast list & sending ──────────────────────────────
    if show_broadcast:
        with subtabs["broadcast"]:
            st.markdown(f"#### {t('admin.broadcast_recipients_title')}")
            st.caption(t("admin.broadcast_recipients_caption"))

            recipients = broadcast_list.get_recipients()
            add_col, list_col = st.columns([2, 3])
            with add_col:
                candidates = [u["cws"] for u in user_store.list_users() if u["cws"] not in recipients]
                new_recipient = st.selectbox(t("admin.add_recipient"), options=[""] + candidates, key="admin_new_recipient")
                if st.button(t("admin.add_button"), key="admin_add_recipient_btn", width="stretch") and new_recipient:
                    ok, msg = broadcast_list.add_recipient(new_recipient)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            with list_col:
                if not recipients:
                    st.info(t("admin.no_recipients"), icon="ℹ️")
                else:
                    for r in recipients:
                        rc1, rc2 = st.columns([4, 1])
                        rc1.markdown(f"📧 **{r}**")
                        if rc2.button("🗑️", key=f"rm_recipient_{r}"):
                            ok, msg = broadcast_list.remove_recipient(r)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                st.rerun()

            st.divider()
            st.markdown(f"#### {t('admin.send_broadcast_title')}")
            if not recipients:
                st.caption(t("admin.no_recipients_to_send"))
            else:
                with st.form("admin_broadcast_form"):
                    subject = st.text_input(t("admin.broadcast_subject"), key="admin_broadcast_subject")
                    body = st.text_area(t("admin.broadcast_body"), key="admin_broadcast_body", height=120)
                    send = st.form_submit_button(t("admin.send_broadcast_button"), type="primary", width="stretch")

                if send:
                    if not subject.strip() or not body.strip():
                        st.warning(t("admin.broadcast_fill_required"), icon="⚠️")
                    else:
                        sent, skipped = 0, []
                        for cws in recipients:
                            target = user_store.get_user(cws)
                            if not target:
                                skipped.append(cws)
                                continue
                            result = messaging.send_message(
                                admin_cws, admin_name or "Administrator",
                                cws, target.get("name", cws),
                                subject, body,
                            )
                            if result:
                                sent += 1
                            else:
                                skipped.append(cws)
                        st.success(t("admin.broadcast_sent", count=sent))
                        if skipped:
                            st.caption(t("admin.broadcast_skipped", who=", ".join(skipped)))

    with subtabs["ai"]:
        st.markdown(f"### {t('admin.ai_center_title')}")
        st.caption(t("admin.ai_center_caption"))

        with st.container(border=True):
            unified = ai_core.get_unified_ai_status()
            if show_psld:
                o1, o2, o3, o4 = st.columns(4)
                o1.metric(
                    t("admin.ai_overview_ilt"),
                    "✅" if unified["ilt_local_intelligence"].get("trained") else "—",
                )
                o2.metric(
                    t("admin.ai_overview_psld_semantic"),
                    "✅" if unified["psld_semantic_engine"].get("available") else "🚫",
                )
                o3.metric(
                    t("admin.ai_overview_psld_neural"),
                    "✅" if unified["psld_neural"].get("trained") else ("⏳" if unified["psld_neural"].get("available") else "🚫"),
                )
                o4.metric(t("admin.ai_overview_psld_kb"), unified["psld_kb_stats"].get("total", 0))
            else:
                # ILT Troubleshooter's own local Administration tab — PSLD
                # is a fully standalone app now, so only show ILT's own AI
                # status here (see render_admin_tab's show_psld docstring).
                o1, = st.columns(1)
                o1.metric(
                    t("admin.ai_overview_ilt"),
                    "✅" if unified["ilt_local_intelligence"].get("trained") else "—",
                )
            st.caption(t("admin.ai_overview_caption"))

        st.markdown(f"#### {t('admin.ai_title')}")
        st.caption(t("admin.ai_caption"))

        try:
            _current_kb_size = len(load_troubleshoot_db())
        except Exception:
            _current_kb_size = None
        status = local_intelligence.get_status(current_kb_size=_current_kb_size)

        if not status.get("sklearn_available"):
            st.error(t("admin.ai_sklearn_missing"), icon="🚫")
        else:
            if status.get("trained"):
                if status.get("stale"):
                    st.warning(t("admin.ai_stale"), icon="🔄")

                m1, m2, m3 = st.columns(3)
                m1.metric(t("admin.ai_kb_docs"), status.get("kb_docs", 0))
                m2.metric(t("admin.ai_db_docs_scanned"), status.get("db_docs_scanned", 0))
                match_rate = status.get("db_match_rate")
                m3.metric(
                    t("admin.ai_db_match_rate"),
                    f"{round(match_rate * 100)}%" if match_rate is not None else "—",
                )
                st.caption(t("admin.ai_last_trained", when=str(status.get("last_trained_at", "—"))))

                if status.get("db_feed_requested") and status.get("db_error"):
                    st.error(t("admin.ai_db_error", reason=status["db_error"]), icon="🚫")
                elif status.get("db_feed_requested") and not status.get("db_docs_scanned"):
                    st.warning(t("admin.ai_db_empty"), icon="⚠️")
                elif not status.get("db_feed_requested"):
                    st.info(t("admin.ai_kb_only"), icon="ℹ️")

                gaps = status.get("gap_candidates") or []
                real_gaps = [g for g in gaps if "err_msg" in g]
                if real_gaps:
                    with st.expander(t("admin.ai_gaps_expander", count=len(real_gaps)), expanded=False):
                        st.caption(t("admin.ai_gaps_caption"))
                        for g in real_gaps:
                            st.markdown(
                                f"**{g['count']}x** (score {int(g['best_score'] * 100)}%) — "
                                f"`{str(g['err_msg'])[:200]}`"
                            )
                elif status.get("db_docs_scanned"):
                    st.success(t("admin.ai_no_gaps"), icon="✅")
            else:
                st.info(t("admin.ai_not_trained"), icon="ℹ️")

            st.divider()
            use_db = st.toggle(
                t("admin.ai_use_db"),
                value=True,
                key="admin_ai_use_db",
                help=t("admin.ai_use_db_help"),
                disabled=not st.session_state.get("conn"),
            )
            if not st.session_state.get("conn"):
                st.caption(t("admin.ai_no_conn"))

            if st.button(t("admin.ai_train_button"), type="primary", key="admin_ai_train_btn"):
                with st.spinner(t("admin.ai_training_spinner")):
                    try:
                        df_ts = load_troubleshoot_db()
                        conn = st.session_state.get("conn") if use_db else None
                        result = local_intelligence.retrain(df_ts, conn=conn)
                        if result.get("trained"):
                            if conn is not None and result.get("db_error"):
                                # The index was rebuilt from the KB, but the
                                # real-data feed from the DB failed — this
                                # must NOT be reported as a full success, or
                                # the admin has no way to know the AI isn't
                                # actually learning from real production data.
                                st.error(t("admin.ai_train_partial", reason=result["db_error"]), icon="⚠️")
                            elif conn is not None and not result.get("db_docs_scanned"):
                                st.warning(t("admin.ai_train_no_data"), icon="⚠️")
                            else:
                                st.success(t("admin.ai_train_success"), icon="✅")
                            st.rerun()
                        else:
                            st.error(t("admin.ai_train_failed", reason=result.get("error", "")))
                    except Exception as e:
                        st.error(t("admin.ai_train_failed", reason=str(e)))

            st.divider()
            st.caption(t("admin.kb_translate_caption"))
            if st.button(t("admin.kb_translate_button"), key="admin_kb_translate_btn"):
                with st.spinner(t("admin.kb_translate_spinner")):
                    from troubleshooter.feedback_store import backfill_kb_translations
                    result = backfill_kb_translations()
                if result.get("ok"):
                    st.success(
                        t("admin.kb_translate_success", updated=result.get("rows_updated", 0), total=result.get("rows_total", 0)),
                        icon="✅",
                    )
                else:
                    st.error(t("admin.kb_translate_failed", reason=result.get("reason", "")))

        st.divider()
        _render_ilt_ai_section()

        if show_psld:
            st.divider()
            _render_psld_ai_section()

    with subtabs["audit"]:
        _render_audit_log_subtab()

    with subtabs["settings"]:
        st.markdown(f"#### {t('admin.settings_title')}")
        st.caption(t("admin.settings_caption"))

        default_language = settings.get("default_language", "en")
        language_codes = list(SUPPORTED_LANGUAGES.keys())
        if default_language not in SUPPORTED_LANGUAGES:
            default_language = "en"

        with st.container(border=True):
            st.subheader(t("admin.settings_general"))
            app_name = st.text_input(t("admin.settings_app_name"), value=settings.get("app_name", ""), key="admin_settings_app_name")
            support_contact_cws = st.text_input(
                t("admin.settings_support_contact_cws"),
                value=settings.get("support_contact_cws", ""),
                key="admin_settings_support_contact_cws",
                help=t("admin.settings_support_contact_help"),
            )
            default_language = st.selectbox(
                t("admin.settings_default_language"),
                options=language_codes,
                index=language_codes.index(default_language),
                format_func=lambda code: SUPPORTED_LANGUAGES.get(code, code),
                key="admin_settings_default_language",
                help=t("admin.settings_default_language_help"),
            )

        with st.container(border=True):
            st.subheader(t("admin.settings_feature_toggles"))
            enable_ai_query_builder = st.toggle(
                t("admin.settings_enable_ai_query_builder"),
                value=bool(settings.get("enable_ai_query_builder", True)),
                key="admin_settings_enable_ai_query_builder",
            )
            enable_messaging = st.toggle(
                t("admin.settings_enable_messaging"),
                value=bool(settings.get("enable_messaging", True)),
                key="admin_settings_enable_messaging",
            )
            enable_broadcast = st.toggle(
                t("admin.settings_enable_broadcast"),
                value=bool(settings.get("enable_broadcast", True)),
                key="admin_settings_enable_broadcast",
            )
            enable_copilot_chat = st.toggle(
                t("admin.settings_enable_copilot_chat"),
                value=bool(settings.get("enable_copilot_chat", True)),
                key="admin_settings_enable_copilot_chat",
            )
            enable_schema_manager = st.toggle(
                t("admin.settings_enable_schema_manager"),
                value=bool(settings.get("enable_schema_manager", True)),
                key="admin_settings_enable_schema_manager",
            )

        with st.container(border=True):
            st.subheader(t("admin.settings_maintenance"))
            maintenance_mode_enabled = st.toggle(
                t("admin.settings_maintenance_mode_enabled"),
                value=bool(settings.get("maintenance_mode_enabled", False)),
                key="admin_settings_maintenance_mode_enabled",
                help=t("admin.settings_maintenance_mode_help"),
            )
            maintenance_mode_message = st.text_area(
                t("admin.settings_maintenance_mode_message"),
                value=settings.get("maintenance_mode_message", ""),
                key="admin_settings_maintenance_mode_message",
                placeholder=t("admin.settings_maintenance_mode_message_placeholder"),
                height=100,
            )

        kb_col, history_col = st.columns(2)
        with kb_col:
            with st.container(border=True):
                st.subheader(t("admin.settings_knowledge_base"))
                kb_freshness_yellow_days = st.number_input(
                    t("admin.settings_kb_freshness_yellow_days"),
                    min_value=1,
                    step=1,
                    value=int(settings.get("kb_freshness_yellow_days", 90)),
                    key="admin_settings_kb_freshness_yellow_days",
                )
                kb_freshness_red_days = st.number_input(
                    t("admin.settings_kb_freshness_red_days"),
                    min_value=1,
                    step=1,
                    value=int(settings.get("kb_freshness_red_days", 365)),
                    key="admin_settings_kb_freshness_red_days",
                )

        with history_col:
            with st.container(border=True):
                st.subheader(t("admin.settings_history_limits"))
                max_shipment_history = st.number_input(
                    t("admin.settings_max_shipment_history"),
                    min_value=1,
                    step=1,
                    value=int(settings.get("max_shipment_history", 50)),
                    key="admin_settings_max_shipment_history",
                )
                max_query_history = st.number_input(
                    t("admin.settings_max_query_history"),
                    min_value=1,
                    step=1,
                    value=int(settings.get("max_query_history", 50)),
                    key="admin_settings_max_query_history",
                )

        with st.container(border=True):
            st.subheader(t("admin.settings_security"))
            session_timeout_minutes = st.number_input(
                t("admin.settings_session_timeout_minutes"),
                min_value=1,
                step=1,
                value=int(settings.get("session_timeout_minutes", 480)),
                key="admin_settings_session_timeout_minutes",
            )
            require_admin_approval_for_new_users = st.toggle(
                t("admin.settings_require_admin_approval_for_new_users"),
                value=bool(settings.get("require_admin_approval_for_new_users", True)),
                key="admin_settings_require_admin_approval_for_new_users",
            )

        with st.container(border=True):
            st.subheader(t("admin.settings_smtp"))
            st.caption(t("admin.settings_smtp_caption"))
            smtp_col1, smtp_col2 = st.columns(2)
            with smtp_col1:
                smtp_host = st.text_input(
                    t("admin.settings_smtp_host"),
                    value=settings.get("smtp_host", ""),
                    key="admin_settings_smtp_host",
                    placeholder="smtp.example.com",
                )
                smtp_username = st.text_input(
                    t("admin.settings_smtp_username"),
                    value=settings.get("smtp_username", ""),
                    key="admin_settings_smtp_username",
                )
                smtp_from_address = st.text_input(
                    t("admin.settings_smtp_from_address"),
                    value=settings.get("smtp_from_address", ""),
                    key="admin_settings_smtp_from_address",
                    placeholder="ilt-troubleshooter@example.com",
                )
            with smtp_col2:
                smtp_port = st.number_input(
                    t("admin.settings_smtp_port"),
                    min_value=1,
                    max_value=65535,
                    step=1,
                    value=int(settings.get("smtp_port", 587)),
                    key="admin_settings_smtp_port",
                )
                smtp_password = st.text_input(
                    t("admin.settings_smtp_password"),
                    value=settings.get("smtp_password", ""),
                    type="password",
                    key="admin_settings_smtp_password",
                )
                smtp_from_name = st.text_input(
                    t("admin.settings_smtp_from_name"),
                    value=settings.get("smtp_from_name", ""),
                    key="admin_settings_smtp_from_name",
                )
            smtp_use_tls = st.toggle(
                t("admin.settings_smtp_use_tls"),
                value=bool(settings.get("smtp_use_tls", True)),
                key="admin_settings_smtp_use_tls",
            )
            if email_utils.is_configured():
                st.success(t("admin.settings_smtp_configured"), icon="✅")
            else:
                st.warning(t("admin.settings_smtp_not_configured"), icon="⚠️")

        save_col, reset_col, _ = st.columns([1, 1, 3])
        if save_col.button(t("admin.settings_save"), type="primary", width="stretch"):
            yellow_days = int(kb_freshness_yellow_days)
            red_days = int(max(kb_freshness_red_days, yellow_days + 1))
            app_settings.update_settings({
                "app_name": app_name.strip() or app_settings.DEFAULT_SETTINGS["app_name"],
                "support_contact_cws": support_contact_cws.strip() or app_settings.DEFAULT_SETTINGS["support_contact_cws"],
                "default_language": default_language,
                "enable_ai_query_builder": bool(enable_ai_query_builder),
                "enable_messaging": bool(enable_messaging),
                "enable_broadcast": bool(enable_broadcast),
                "enable_copilot_chat": bool(enable_copilot_chat),
                "enable_schema_manager": bool(enable_schema_manager),
                "maintenance_mode_enabled": bool(maintenance_mode_enabled),
                "maintenance_mode_message": maintenance_mode_message.strip(),
                "kb_freshness_yellow_days": yellow_days,
                "kb_freshness_red_days": red_days,
                "max_shipment_history": int(max_shipment_history),
                "max_query_history": int(max_query_history),
                "session_timeout_minutes": int(session_timeout_minutes),
                "require_admin_approval_for_new_users": bool(require_admin_approval_for_new_users),
                "smtp_host": smtp_host.strip(),
                "smtp_port": int(smtp_port),
                "smtp_use_tls": bool(smtp_use_tls),
                "smtp_username": smtp_username.strip(),
                "smtp_password": smtp_password,
                "smtp_from_address": smtp_from_address.strip(),
                "smtp_from_name": smtp_from_name.strip(),
            })
            st.success(t("admin.settings_saved"))
            st.rerun()

        confirm_key = "confirm_reset_app_settings"
        with reset_col:
            if st.session_state.get(confirm_key):
                if st.button(t("admin.settings_confirm_reset"), key="admin_settings_do_reset", type="primary", width="stretch"):
                    app_settings.reset_to_defaults()
                    st.session_state[confirm_key] = False
                    st.success(t("admin.settings_reset_success"))
                    st.rerun()
            else:
                if st.button(t("admin.settings_reset"), key="admin_settings_reset", width="stretch"):
                    st.session_state[confirm_key] = True
                    st.rerun()


def admin_cws_for_ai() -> str:
    """Small helper so _render_psld_ai_section doesn't need the full
    (cws, name) tuple threaded through as parameters."""
    user = st.session_state.get("auth_user") or {}
    return user.get("cws", "") or "SYSTEM"


def _render_ilt_ai_section() -> None:
    """ILT Troubleshooter's own Level 1 (unsupervised clustering) + Level 2
    (supervised MLP+RandomForest ensemble) neural pipeline
    (troubleshooter/ilt_ai_core.py) — shown right below the main ILT
    local-intelligence (TF-IDF/k-NN) section, mirroring
    _render_psld_ai_section()'s layout so both apps' AI sections have the
    same level of depth/precision. Entirely separate data/model from
    PSLD's — see ilt_ai_core.py's module docstring."""
    cluster = ilt_ai_core.cluster_status()
    neural = ilt_ai_core.neural_status()
    try:
        kb_size = len(load_troubleshoot_db())
    except Exception:
        kb_size = 0
    from troubleshooter import feedback_store
    try:
        fb_count = len(feedback_store.all_correction_feedback())
    except Exception:
        fb_count = 0

    st.markdown(f"##### {t('admin.ai_ilt_cluster_title')}")
    st.caption(t("admin.ai_ilt_cluster_caption"))
    if not cluster.get("available"):
        st.error(t("admin.ai_ilt_cluster_unavailable", reason=cluster.get("reason", "")), icon="🚫")
    elif cluster.get("trained"):
        c1, c2, c3 = st.columns(3)
        c1.metric(t("admin.ai_ilt_cluster_entries"), cluster.get("entries", 0))
        c2.metric(t("admin.ai_ilt_cluster_clusters"), cluster.get("clusters", 0))
        c3.metric(t("admin.ai_psld_neural_arch"), cluster.get("architecture", "—"))
        st.caption(t("admin.ai_last_trained", when=str(cluster.get("last_trained_at", "—"))))
    else:
        st.info(t("admin.ai_ilt_cluster_not_trained", needed=ilt_ai_core.MIN_ENTRIES_FOR_CLUSTERING, have=kb_size), icon="ℹ️")

    st.divider()
    st.markdown(f"##### {t('admin.ai_ilt_neural_title')}")
    st.caption(t("admin.ai_ilt_neural_caption"))
    if not neural.get("available"):
        st.error(t("admin.ai_ilt_neural_unavailable", reason=neural.get("reason", "")), icon="🚫")
    elif neural.get("trained"):
        n1, n2, n3 = st.columns(3)
        n1.metric(t("admin.ai_ilt_neural_samples"), neural.get("samples", 0))
        n2.metric(t("admin.ai_ilt_neural_classes"), neural.get("classes", 0))
        n3.metric(t("admin.ai_ilt_neural_arch"), neural.get("architecture", "—"))
        st.caption(t("admin.ai_last_trained", when=str(neural.get("last_trained_at", "—"))))
    else:
        st.info(
            t("admin.ai_ilt_neural_not_trained", needed=ilt_ai_core.MIN_TOTAL_FEEDBACK_SAMPLES, classes=ilt_ai_core.MIN_DISTINCT_CLASSES, have=fb_count),
            icon="ℹ️",
        )

    st.divider()
    st.markdown(f"##### {t('admin.ai_ilt_deep_title')}")
    if st.button(t("admin.ai_ilt_deep_button"), key="admin_ilt_deep_btn", disabled=kb_size == 0):
        with st.status(t("admin.ai_ilt_deep_spinner"), expanded=True) as status_box:
            def _on_step(entry):
                secs = entry.get("seconds")
                secs_txt = f" ({secs}s)" if secs is not None else ""
                status_box.write(f"✅ **{entry['step']}**{secs_txt} — {entry['detail']}")
            combined = ilt_ai_core.force_full_deep_learn(on_step=_on_step)
            status_box.update(label=t("admin.ai_ilt_deep_spinner"), state="complete")
        cluster_result = combined["cluster"]
        neural_result = combined["neural"]
        audit_log.record_event(
            "ai_deep_learn", cws=admin_cws_for_ai(),
            detail=(
                f"Force full deep-learn: cluster trained={cluster_result.get('trained')} "
                f"({cluster_result.get('train_duration_seconds', 0)}s), "
                f"neural trained={neural_result.get('trained')} ({neural_result.get('train_duration_seconds', 0)}s)"
            ),
            app="ilt", category="ai_training", severity="info",
        )
        if cluster_result.get("trained"):
            st.success(
                f"{t('admin.ai_ilt_cluster_entries')}: {cluster_result.get('entries', 0)} · "
                f"{t('admin.ai_ilt_cluster_clusters')}: {cluster_result.get('clusters', 0)} · "
                f"{cluster_result.get('train_duration_seconds', 0)}s",
                icon="🧬",
            )
        if neural_result.get("trained"):
            st.success(
                t("admin.ai_ilt_neural_train_success", samples=neural_result.get("samples", 0), classes=neural_result.get("classes", 0))
                + f" · {neural_result.get('train_duration_seconds', 0)}s",
                icon="🧠",
            )
        elif neural_result.get("reason") == "insufficient_data":
            st.info(
                t("admin.ai_ilt_neural_not_trained", needed=neural_result.get("needed_samples", 0), classes=neural_result.get("needed_classes", 0), have=neural_result.get("samples", 0)),
                icon="ℹ️",
            )
        st.rerun()
    if kb_size == 0:
        st.caption(t("admin.ai_ilt_deep_no_data"))

    with st.expander(t("admin.ai_ilt_training_log_title")):
        st.caption(t("admin.ai_ilt_training_log_caption"))
        cluster_log = cluster.get("training_log") or []
        neural_log = neural.get("training_log") or []
        if not cluster_log and not neural_log:
            st.caption(t("admin.ai_ilt_training_log_empty"))
        else:
            if cluster_log:
                st.markdown(f"**Level 1 (cluster)** — {cluster.get('train_duration_seconds', '—')}s total")
                for entry in cluster_log:
                    st.text(f"  {entry['step']}: {entry['detail']} ({entry['seconds']}s)")
            if neural_log:
                st.markdown(f"**Level 2 (neural)** — {neural.get('train_duration_seconds', '—')}s total")
                for entry in neural_log:
                    st.text(f"  {entry['step']}: {entry['detail']} ({entry['seconds']}s)")

    st.divider()
    st.markdown(f"##### {t('admin.ai_ilt_mega_title')}")
    st.caption(t("admin.ai_ilt_mega_caption"))
    conn = st.session_state.get("conn")
    from troubleshooter import autonomous_fix
    pending_fixes = autonomous_fix.count_pending()
    mm1, mm2 = st.columns(2)
    mm1.metric(t("admin.ai_ilt_mega_pending_fixes"), pending_fixes)
    mm2.metric(t("admin.ai_ilt_mega_last_scan"), str(cluster.get("db_docs_scanned", 0) or 0))
    if not conn:
        st.warning(t("admin.ai_ilt_mega_no_conn"), icon="⚠️")
    if st.button(t("admin.ai_ilt_mega_button"), key="admin_ilt_mega_btn", type="primary", disabled=not conn):
        with st.status(t("admin.ai_ilt_mega_spinner"), expanded=True) as status_box:
            def _on_step(entry):
                secs = entry.get("seconds")
                secs_txt = f" ({secs}s)" if secs is not None else "…"
                status_box.write(f"✅ **{entry['step']}**{secs_txt} — {entry['detail']}")
            mega = ilt_ai_core.mega_deep_learn(conn=conn, created_by=admin_cws_for_ai(), on_step=_on_step)
            status_box.update(label=t("admin.ai_ilt_mega_spinner") + f" — {mega.get('total_duration_seconds', 0)}s", state="complete")
        mega_cluster = mega["cluster"]
        mega_neural = mega["neural"]
        mega_fixes = mega["autonomous_fixes"]
        audit_log.record_event(
            "ai_mega_deep_learn", cws=admin_cws_for_ai(),
            detail=(
                f"Mega deep-learn: db_docs_scanned={mega_cluster.get('db_docs_scanned', 0)}, "
                f"db_groups_added={mega_cluster.get('db_groups_added', 0)}, "
                f"fixes_drafted={mega_fixes.get('drafted', 0)}, total={mega.get('total_duration_seconds', 0)}s"
                + (f" — DB ERROR: {mega_cluster.get('db_error')}" if mega_cluster.get("db_error") else "")
            ),
            app="ilt", category="ai_training", severity="error" if mega_cluster.get("db_error") else "info",
        )
        if mega_cluster.get("trained"):
            st.success(
                t(
                    "admin.ai_ilt_mega_cluster_result",
                    entries=mega_cluster.get("entries", 0),
                    db_docs=mega_cluster.get("db_docs_scanned", 0),
                    db_groups=mega_cluster.get("db_groups_added", 0),
                ),
                icon="🧬",
            )
            if mega_cluster.get("db_error"):
                st.error(t("admin.ai_ilt_mega_db_error", reason=mega_cluster["db_error"]), icon="🚫")
        else:
            st.error(t("admin.ai_ilt_cluster_unavailable", reason=mega_cluster.get("reason", "")), icon="🚫")
        if mega_neural.get("trained"):
            st.success(t("admin.ai_ilt_neural_train_success", samples=mega_neural.get("samples", 0), classes=mega_neural.get("classes", 0)), icon="🧠")
        if mega_fixes.get("reason"):
            st.info(t("admin.ai_ilt_mega_fixes_reason", reason=mega_fixes["reason"]), icon="ℹ️")
        else:
            st.success(
                t(
                    "admin.ai_ilt_mega_fixes_result",
                    drafted=mega_fixes.get("drafted", 0),
                    skipped=mega_fixes.get("skipped_low_confidence", 0),
                ),
                icon="🤖",
            )
        st.rerun()

    st.divider()
    st.markdown(f"##### {t('admin.ai_ilt_continuous_title')}")
    st.caption(t("admin.ai_ilt_continuous_caption"))
    from troubleshooter import continuous_learning
    cl_status = continuous_learning.status()
    if cl_status.get("running"):
        cl1, cl2, cl3 = st.columns(3)
        cl1.metric(t("admin.ai_ilt_continuous_runs"), cl_status.get("run_count", 0))
        cl2.metric(t("admin.ai_ilt_continuous_interval"), f"{cl_status.get('interval_minutes', '—')} min")
        last_result = cl_status.get("last_result") or {}
        cl3.metric(t("admin.ai_ilt_continuous_fixes_drafted"), last_result.get("fixes_drafted", 0))
        st.caption(t("admin.ai_ilt_continuous_status_running", started_by=cl_status.get("started_by", "—"), when=cl_status.get("last_run_finished_at", "—")))
        if st.button(t("admin.ai_ilt_continuous_stop"), key="admin_ilt_continuous_stop"):
            continuous_learning.stop()
            audit_log.record_event(
                "ai_continuous_learning_stop", cws=admin_cws_for_ai(),
                detail=f"Stopped after {cl_status.get('run_count', 0)} run(s)",
                app="ilt", category="ai_training", severity="info",
            )
            st.rerun()
    else:
        if cl_status.get("last_error"):
            st.error(t("admin.ai_ilt_continuous_last_error", reason=cl_status["last_error"]), icon="🚫")
        conn = st.session_state.get("conn")
        if not conn:
            st.warning(t("admin.ai_ilt_mega_no_conn"), icon="⚠️")
        interval = st.slider(
            t("admin.ai_ilt_continuous_interval_label"),
            min_value=continuous_learning.MIN_INTERVAL_MINUTES,
            max_value=180,
            value=continuous_learning.DEFAULT_INTERVAL_MINUTES,
            step=5,
            key="admin_ilt_continuous_interval",
            disabled=not conn,
        )
        if st.button(t("admin.ai_ilt_continuous_start"), key="admin_ilt_continuous_start", disabled=not conn):
            result = continuous_learning.start(conn=conn, interval_minutes=interval, created_by=admin_cws_for_ai())
            if result.get("ok"):
                st.success(t("admin.ai_ilt_continuous_started", interval=interval))
                audit_log.record_event(
                    "ai_continuous_learning_start", cws=admin_cws_for_ai(),
                    detail=f"Started, interval={interval}min", app="ilt", category="ai_training", severity="info",
                )
            else:
                st.error(t("admin.ai_ilt_continuous_start_failed", reason=result.get("reason", "")))
                audit_log.record_event(
                    "ai_continuous_learning_start_failed", cws=admin_cws_for_ai(),
                    detail=str(result.get("reason", "")), app="ilt", category="ai_training", severity="error",
                )
            st.rerun()


def _render_psld_ai_section() -> None:
    """PSLD - Parts semantic engine + ResolutionDocs deep-learn, shown
    inside the admin AI Control Center (subtabs["ai"]) below the main
    ILT Troubleshooter local-intelligence section. Read-only monitoring
    (status, KB breakdown, self-learning "cruzamentos") plus two manual
    background actions: force-sync the ResolutionDocs inbox folder, and
    force a full deep-learn re-processing of every existing KB entry's
    attachment (slower, intentionally allowed to take a while for a
    deeper/more complete re-analysis)."""
    st.markdown(f"#### {t('admin.ai_psld_title')}")
    st.caption(t("admin.ai_psld_caption"))

    sem_status = psld_semantic_engine.semantic_status()
    stats = servicenow_resolution_kb.kb_stats()
    fb_count = psld_semantic_engine.feedback_count()
    neural = ai_core.neural_status()

    if not sem_status.get("available"):
        st.error(t("admin.ai_psld_unavailable", reason=sem_status.get("reason", "")), icon="🚫")
    else:
        st.caption(t("admin.ai_psld_model", model=sem_status.get("model", "")))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("admin.ai_psld_kb_total"), stats["total"])
    m2.metric(t("admin.ai_psld_kb_manual"), stats["manual"])
    m3.metric(t("admin.ai_psld_kb_folder"), stats["folder_import"])
    m4.metric(t("admin.ai_psld_feedback_count"), fb_count)
    st.caption(t("admin.ai_psld_last_activity", when=stats.get("last_activity") or "—"))

    st.divider()
    st.markdown(f"##### {t('admin.ai_psld_cluster_title')}")
    st.caption(t("admin.ai_psld_cluster_caption"))
    cluster = ai_core.cluster_status()
    if not cluster.get("available"):
        st.error(t("admin.ai_psld_cluster_unavailable", reason=cluster.get("reason", "")), icon="🚫")
    elif cluster.get("trained"):
        c1, c2, c3 = st.columns(3)
        c1.metric(t("admin.ai_psld_cluster_entries"), cluster.get("entries", 0))
        c2.metric(t("admin.ai_psld_cluster_clusters"), cluster.get("clusters", 0))
        c3.metric(t("admin.ai_psld_neural_arch"), cluster.get("architecture", "—"))
        st.caption(t("admin.ai_last_trained", when=str(cluster.get("last_trained_at", "—"))))
    else:
        st.info(t("admin.ai_psld_cluster_not_trained", needed=ai_core.MIN_ENTRIES_FOR_CLUSTERING, have=stats["total"]), icon="ℹ️")

    st.divider()
    st.markdown(f"##### {t('admin.ai_psld_neural_title')}")
    st.caption(t("admin.ai_psld_neural_caption"))
    if not neural.get("available"):
        st.error(t("admin.ai_psld_neural_unavailable", reason=neural.get("reason", "")), icon="🚫")
    elif neural.get("trained"):
        n1, n2, n3 = st.columns(3)
        n1.metric(t("admin.ai_psld_neural_samples"), neural.get("samples", 0))
        n2.metric(t("admin.ai_psld_neural_classes"), neural.get("classes", 0))
        n3.metric(t("admin.ai_psld_neural_arch"), neural.get("architecture", "—"))
        st.caption(t("admin.ai_last_trained", when=str(neural.get("last_trained_at", "—"))))
    else:
        st.info(
            t("admin.ai_psld_neural_not_trained", needed=ai_core.MIN_TOTAL_FEEDBACK_SAMPLES, classes=ai_core.MIN_DISTINCT_CLASSES, have=fb_count),
            icon="ℹ️",
        )

    st.divider()
    st.markdown(f"##### {t('admin.ai_psld_sync_title')}")
    pending = servicenow_resolution_kb.pending_inbox_files()
    total_files = servicenow_resolution_kb.scan_resolution_docs_inbox()
    st.caption(t("admin.ai_psld_sync_caption", folder=str(servicenow_resolution_kb.RESOLUTION_DOCS_INBOX)))
    if total_files:
        st.info(t("psld.resdocs_scan_found", total=len(total_files), new=len(pending)), icon="📂")
    else:
        st.caption(t("psld.resdocs_scan_empty"))

    sync_col, deep_col = st.columns(2)
    with sync_col:
        if st.button(t("admin.ai_psld_sync_button"), key="admin_psld_sync_btn", disabled=not pending, type="primary"):
            with st.spinner(t("admin.ai_psld_sync_spinner")):
                result = servicenow_resolution_kb.bulk_import_from_folder(created_by=admin_cws_for_ai())
            st.success(
                t("psld.resdocs_scan_result", created=result["created"], skipped_existing=result["skipped_existing"], failed=len(result["failed"])),
                icon="✅",
            )
            st.rerun()
    with deep_col:
        if st.button(t("admin.ai_psld_deep_button"), key="admin_psld_deep_btn", disabled=stats["with_attachment"] == 0 and fb_count == 0):
            with st.spinner(t("admin.ai_psld_deep_spinner")):
                combined = ai_core.force_full_deep_learn(created_by=admin_cws_for_ai())
            result = combined["reprocess"]
            neural_result = combined["neural"]
            st.success(
                t("admin.ai_psld_deep_result", processed=result["processed"], updated=result["updated"], failed=len(result["failed"])),
                icon="✅",
            )
            if result["failed"]:
                with st.expander(t("admin.ai_psld_deep_failed_expander", count=len(result["failed"])), expanded=False):
                    for f in result["failed"]:
                        st.caption(f"⚠️ **{f['title']}** — {f['reason']}")
            if neural_result.get("trained"):
                st.success(t("admin.ai_psld_neural_train_success", samples=neural_result.get("samples", 0), classes=neural_result.get("classes", 0)), icon="🧠")
            elif neural_result.get("reason") == "insufficient_data":
                st.info(
                    t("admin.ai_psld_neural_not_trained", needed=neural_result.get("needed_samples", 0), classes=neural_result.get("needed_classes", 0), have=neural_result.get("samples", 0)),
                    icon="ℹ️",
                )
            st.rerun()
    if stats["with_attachment"] == 0 and fb_count == 0:
        st.caption(t("admin.ai_psld_deep_no_attachments"))

    st.divider()
    st.markdown(f"##### {t('admin.ai_psld_docsimport_title')}")
    st.caption(t("admin.ai_psld_docsimport_caption", folder=str(servicenow_resolution_kb.DOCS_KB_ROOT)))
    docs_scan = servicenow_resolution_kb.scan_docs_kb_root()
    docs_already = servicenow_resolution_kb._already_imported_docs_relpaths(servicenow_resolution_kb.DOCS_KB_ROOT)
    docs_new_count = sum(
        1 for p in docs_scan
        if str(p.relative_to(servicenow_resolution_kb.DOCS_KB_ROOT)).strip().lower() not in docs_already
    )
    if docs_scan:
        st.info(t("admin.ai_psld_docsimport_found", total=len(docs_scan), new=docs_new_count), icon="📚")
        with st.expander(t("admin.ai_psld_docsimport_categories_expander"), expanded=False):
            cats = servicenow_resolution_kb.list_categories()
            st.write(", ".join(cats) if cats else "—")
    else:
        st.caption(t("admin.ai_psld_docsimport_empty"))
    docs_limit = st.number_input(t("admin.ai_psld_docsimport_limit_label"), min_value=0, value=0, step=50, help=t("admin.ai_psld_docsimport_limit_help"), key="admin_psld_docsimport_limit")
    if st.button(t("admin.ai_psld_docsimport_button"), key="admin_psld_docsimport_btn", disabled=docs_new_count == 0, type="primary"):
        with st.spinner(t("admin.ai_psld_docsimport_spinner", count=docs_new_count)):
            docs_result = servicenow_resolution_kb.bulk_import_from_docs_root(
                created_by=admin_cws_for_ai(),
                limit=int(docs_limit) if docs_limit else None,
            )
        st.success(
            t("admin.ai_psld_docsimport_result", created=docs_result["created"], skipped_existing=docs_result["skipped_existing"], failed=len(docs_result["failed"])),
            icon="✅",
        )
        if docs_result["failed"]:
            with st.expander(t("admin.ai_psld_docsimport_failed_expander", count=len(docs_result["failed"])), expanded=False):
                for relpath, reason in docs_result["failed"]:
                    st.caption(f"⚠️ **{relpath}** — {reason}")
        st.rerun()

    st.divider()
    st.markdown(f"##### {t('admin.ai_psld_crossrefs_title')}")
    st.caption(t("admin.ai_psld_crossrefs_caption"))
    recent_feedback = psld_semantic_engine.list_feedback(limit=25)
    if not recent_feedback:
        st.info(t("admin.ai_psld_crossrefs_empty"), icon="ℹ️")
    else:
        for row in recent_feedback:
            snippet = (row.get("ticket_text") or "")[:160]
            with st.container(border=True):
                st.markdown(f"🔗 **{row.get('entry_title', '?')}**")
                st.caption(f"“{snippet}{'…' if len(row.get('ticket_text') or '') > 160 else ''}”")
                st.caption(f"{row.get('confirmed_by', '?')} · {row.get('confirmed_at', '')}")

