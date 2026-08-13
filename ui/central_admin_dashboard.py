"""
ui/central_admin_dashboard.py
================================
The Central Admin Dashboard shown to administrators in portal_app.py —
a single, consolidated "super panel" covering both ILT Troubleshooter and
PSLD - Parts:

  - Overview: users online right now, approvals pending, KB size, AI
    snapshot, per-portal access counts.
  - Administration: the FULL existing Administration surface
    (ui/admin_tab.py) embedded as-is — pending approvals, full user
    management (approve/reject/remove/reset password/promote-demote
    admin, PSLD/ILT/Reviewer flags, per-screen tab lock-down), broadcast
    messaging, the AI Control Center, and app settings. Nothing
    reinvented here — this reuses the same proven code ILT
    Troubleshooter's own Administration tab uses, so every admin action
    that works there also works from this shared portal.
  - AI & Console: deeper read-only monitoring — every AI/self-learning
    subsystem's live status (troubleshooter/ai_core.py's unified status),
    active session count, online users, KB/DB profile counts — "is
    everything OK" at a glance.
  - Portal & Announcements: the global announcement banner + the
    ilt_app_url/psld_app_url settings that drive the "Enter..." buttons.
  - Audit log: filterable table of recent login/permission/routing
    events (see auth/audit_log.py).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from auth import audit_log, email_utils, presence, session_store, user_store
from config import app_settings
from database import connection_profiles
from integrations import servicenow_azure_ad
from troubleshooter import ai_core, servicenow_resolution_kb
from ui.admin_tab import render_admin_tab


def _status_badge(label: str, ok: bool, detail: str = "") -> str:
    """One-line HTML badge: green dot + label if `ok`, red dot otherwise."""
    color = "#1a7f37" if ok else "#cf222e"
    bg = "rgba(26,127,55,0.12)" if ok else "rgba(207,34,46,0.12)"
    dot = "●"
    text = f"{label}: {'OK' if ok else 'Down'}" if not detail else f"{label}: {detail}"
    return (
        f'<div style="display:inline-flex;align-items:center;gap:6px;'
        f'padding:4px 10px;border-radius:14px;background:{bg};color:{color};'
        f'font-size:0.85rem;font-weight:600;margin:2px 6px 2px 0;">'
        f'<span style="font-size:0.7rem;">{dot}</span>{text}</div>'
    )


def _render_overview() -> None:
    st.markdown("#### Overview")
    users = user_store.list_users()
    approved = [u for u in users if user_store.is_approved(u)]
    online = presence.list_online_users()
    try:
        kb_total = servicenow_resolution_kb.kb_stats().get("total", len(servicenow_resolution_kb.list_entries()))
    except Exception:
        kb_total = len(servicenow_resolution_kb.list_entries())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Online now", len(online))
    c2.metric("Approved users", len(approved))
    c3.metric("Pending approval", len(users) - len(approved))
    c4.metric("Resolution KB entries", kb_total)

    c5, c6, c7 = st.columns(3)
    c5.metric("Parts - Brasil access", len(user_store.list_psld_parts_users()))
    c6.metric("ILT - Transportation access", len(user_store.list_ilt_transportation_users()))
    c7.metric("Parts Reviewers", len(user_store.list_parts_reviewer_users()))

    st.markdown("##### AI snapshot")
    try:
        ai_status = ai_core.get_unified_ai_status()
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("ILT local intelligence", "Trained" if ai_status["ilt_local_intelligence"].get("trained") else "Not trained")
        a2.metric("PSLD semantic engine", "Available" if ai_status["psld_semantic_engine"].get("available") else "Unavailable")
        a3.metric("PSLD neural matcher", "Trained" if ai_status["psld_neural"].get("trained") else ("Ready" if ai_status["psld_neural"].get("available") else "Unavailable"))
        a4.metric("PSLD feedback samples", ai_status.get("psld_feedback_count", 0))
    except Exception as e:
        st.caption(f"AI status unavailable right now ({e}).")


def _render_ai_console() -> None:
    st.markdown("#### AI & self-learning — live status")
    st.caption("Read-only monitoring of every AI/self-learning subsystem in the app. Use Administration → AI Control Center to force a retrain/deep-learn.")

    try:
        status = ai_core.get_unified_ai_status()
    except Exception as e:
        st.error(f"Could not read AI status: {e}")
        status = None

    # ── Integrations status strip ────────────────────────────────────────
    st.markdown("##### Integrations")
    try:
        sn_ok = servicenow_azure_ad.is_configured()
    except Exception:
        sn_ok = False
    try:
        email_ok = email_utils.is_configured()
    except Exception:
        email_ok = False
    try:
        db_profiles_n = len(connection_profiles.list_profiles())
    except Exception:
        db_profiles_n = 0
    ilt_ok = bool(status and status["ilt_local_intelligence"].get("trained"))
    psld_sem_ok = bool(status and status["psld_semantic_engine"].get("available"))

    badges = "".join([
        _status_badge("ServiceNow (Azure AD)", sn_ok),
        _status_badge("Oracle DB profiles", db_profiles_n > 0, f"{db_profiles_n} configured" if db_profiles_n else ""),
        _status_badge("E-mail / SMTP", email_ok),
        _status_badge("ILT local intelligence", ilt_ok, "Trained" if ilt_ok else "Not trained"),
        _status_badge("PSLD semantic engine", psld_sem_ok, "Available" if psld_sem_ok else "Unavailable"),
    ])
    st.markdown(f'<div>{badges}</div>', unsafe_allow_html=True)
    st.write("")

    # ── Activity chart — transactions over time from the audit log ──────
    st.markdown("##### Activity — last 100 recorded transactions")
    recent_events = audit_log.list_events(limit=100)
    if recent_events:
        df_events = pd.DataFrame(recent_events)
        df_events["app"] = df_events["app"].replace("", "unknown").fillna("unknown")
        try:
            df_events["timestamp"] = pd.to_datetime(df_events["timestamp"])
            df_events["hour"] = df_events["timestamp"].dt.floor("h")
            by_hour = df_events.groupby(["hour", "app"]).size().unstack(fill_value=0)
            by_hour.columns = [str(c) for c in by_hour.columns]
            st.bar_chart(by_hour, height=220)
        except Exception as e:
            st.caption(f"Could not chart activity ({e}).")
        by_type = df_events["event_type"].value_counts()
        c_chart, c_apps = st.columns(2)
        with c_chart:
            st.caption("By event type")
            st.bar_chart(by_type, height=180)
        with c_apps:
            st.caption("By app")
            st.bar_chart(df_events["app"].value_counts(), height=180)
    else:
        st.caption("No transactions recorded yet.")

    if status:
        with st.container(border=True):
            st.markdown("**ILT Troubleshooter — local intelligence**")
            ilt = status["ilt_local_intelligence"]
            cols = st.columns(4)
            cols[0].metric("Trained", "Yes" if ilt.get("trained") else "No")
            cols[1].metric("KB docs", ilt.get("kb_docs", 0))
            cols[2].metric("DB docs scanned", ilt.get("db_docs_scanned", 0))
            match_rate = ilt.get("db_match_rate")
            cols[3].metric("DB match rate", f"{round(match_rate * 100)}%" if match_rate is not None else "—")
            if ilt.get("stale"):
                st.warning("Index is stale (KB has grown since last training).", icon="🔄")
            st.caption(f"Last trained: {ilt.get('last_trained_at', '—')}")

        with st.container(border=True):
            st.markdown("**PSLD - Parts — semantic + neural + cluster engines**")
            sem = status["psld_semantic_engine"]
            neu = status["psld_neural"]
            clu = status["psld_cluster"]
            cols = st.columns(3)
            cols[0].metric("Semantic engine", "Available" if sem.get("available") else "Unavailable")
            if sem.get("available"):
                cols[0].caption(f"Model: {sem.get('model', '—')}")
            else:
                cols[0].caption(f"Reason: {sem.get('reason', '—')}")
            cols[1].metric("Neural matcher", "Trained" if neu.get("trained") else ("Ready" if neu.get("available") else "Unavailable"))
            cols[2].metric("Cluster model", "Trained" if clu.get("trained") else ("Ready" if clu.get("available") else "Unavailable"))
            st.caption(f"Self-learning feedback samples: {status.get('psld_feedback_count', 0)}")

        with st.container(border=True):
            st.markdown("**PSLD - Parts — KB / ABEND / Double-Check**")
            kb_stats = status.get("psld_kb_stats") or {}
            review = status.get("psld_review_queue") or {}
            cols = st.columns(4)
            cols[0].metric("KB entries", kb_stats.get("total", 0))
            cols[1].metric("ABEND registered", status.get("psld_abend_total", 0))
            cols[2].metric("ABEND pending program", status.get("psld_abend_pending", 0))
            cols[3].metric("Double-Check pending", review.get("pending", 0))

    st.markdown("#### Backend / system console")

    # ── Live architecture diagram — colored by current status ────────────
    online_n = len(presence.list_online_users())
    try:
        active_n = len(session_store.list_active_sessions())
    except Exception:
        active_n = 0
    ok_color, down_color = "#1a7f37", "#cf222e"
    diagram = f'''
    digraph G {{
        rankdir=LR;
        bgcolor="transparent";
        node [shape=box style="rounded,filled" fontname="Segoe UI" fontsize=11 color="#888888"];
        edge [fontname="Segoe UI" fontsize=9 color="#888888"];

        Portal [label="Portal\\n({online_n} online / {active_n} sessions)" fillcolor="#e8f0fe"];
        ILT [label="ILT Troubleshooter" fillcolor="{'#e6f4ea' if ilt_ok else '#fce8e6'}"];
        PSLD [label="PSLD - Parts" fillcolor="{'#e6f4ea' if psld_sem_ok else '#fce8e6'}"];
        AI [label="AI engines\\n(local intelligence)" fillcolor="{'#e6f4ea' if (ilt_ok or psld_sem_ok) else '#fce8e6'}"];
        DB [label="Oracle DB\\n({db_profiles_n} profiles)" fillcolor="{'#e6f4ea' if db_profiles_n else '#fce8e6'}"];
        SN [label="ServiceNow (Azure AD)" fillcolor="{'#e6f4ea' if sn_ok else '#fce8e6'}"];
        Mail [label="E-mail / SMTP" fillcolor="{'#e6f4ea' if email_ok else '#fce8e6'}"];

        Portal -> ILT; Portal -> PSLD;
        ILT -> AI; PSLD -> AI;
        ILT -> DB; PSLD -> DB;
        ILT -> SN; PSLD -> SN;
        Portal -> Mail;
    }}
    '''
    with st.container(border=True):
        st.markdown("**Live architecture**")
        st.caption("Green = healthy/configured, red = unavailable/not configured. Reflects the status strip above in real time.")
        st.graphviz_chart(diagram, use_container_width=True)

    with st.container(border=True):
        st.markdown("**Sessions & presence**")
        try:
            active_sessions = session_store.list_active_sessions()
        except Exception:
            active_sessions = []
        online = presence.list_online_users()
        cols = st.columns(3)
        cols[0].metric("Active sessions (all apps)", len(active_sessions))
        cols[1].metric("Online users", len(online))
        cols[2].metric("Python PID (this process)", os.getpid())
        if active_sessions:
            with st.expander("Active session detail", expanded=False):
                st.dataframe(pd.DataFrame(active_sessions), hide_index=True, width="stretch")

    with st.container(border=True):
        st.markdown("**Database connection profiles**")
        try:
            profiles = connection_profiles.list_profiles()
        except Exception:
            profiles = []
        st.metric("Configured Oracle profiles", len(profiles))
        if profiles:
            st.caption(", ".join(p.get("name", "?") for p in profiles))
        st.caption("Profiles are stored encrypted at rest (db_connections.json). This panel doesn't test live connectivity to avoid opening unexpected DB sessions from the admin dashboard.")

    with st.container(border=True):
        st.markdown("**Recent transactions (audit log)**")
        recent = audit_log.list_events(limit=15)
        if not recent:
            st.caption("No events recorded yet.")
        else:
            st.dataframe(pd.DataFrame(recent), hide_index=True, width="stretch")


def _render_announcement_settings() -> None:
    st.markdown("#### Global announcement")
    st.caption("Shown as a dismissible top banner in every app (ILT Troubleshooter, PSLD - Parts, and this portal).")
    settings = app_settings.get_settings()
    enabled = st.checkbox("Active", value=bool(settings.get("global_announcement_enabled")), key="_ann_enabled")
    message = st.text_area("Message", value=settings.get("global_announcement_message", ""), key="_ann_message", height=80)
    severity = st.selectbox(
        "Severity", options=["info", "warning", "error"],
        index=["info", "warning", "error"].index(settings.get("global_announcement_severity", "info")),
        key="_ann_severity",
    )
    if st.button("Save announcement", type="primary", key="_ann_save"):
        app_settings.update_settings({
            "global_announcement_enabled": enabled,
            "global_announcement_message": message,
            "global_announcement_severity": severity,
        })
        st.success("Announcement settings saved.")
        st.rerun()

    st.divider()
    st.markdown("#### Portal URLs")
    st.caption("Where the \"Enter ILT Troubleshooter\" / \"Enter PSLD - Parts\" buttons point.")
    col1, col2 = st.columns(2)
    ilt_url = col1.text_input("ILT Troubleshooter URL", value=settings.get("ilt_app_url", ""), key="_ilt_url")
    psld_url = col2.text_input("PSLD - Parts URL", value=settings.get("psld_app_url", ""), key="_psld_url")
    if st.button("Save portal URLs", key="_portal_urls_save"):
        app_settings.update_settings({"ilt_app_url": ilt_url, "psld_app_url": psld_url})
        st.success("Portal URLs saved.")
        st.rerun()


def _render_audit_log() -> None:
    st.markdown("#### Audit / console log")
    st.caption(
        "Full backend audit trail: logins, admin actions, AI training runs, DB connections "
        "(including Oracle username-mismatch flags), autonomous-fix approvals, and integration/"
        "background-job errors. Filter by severity to quickly spot failures."
    )
    col1, col2, col3, col4 = st.columns([2, 1.4, 1.2, 1])
    cws_filter = col1.text_input("Filter by CWS (optional)", key="_audit_cws_filter")
    category_filter = col2.selectbox(
        "Category",
        options=["(all)"] + audit_log.KNOWN_CATEGORIES,
        key="_audit_category_filter",
    )
    severity_filter = col3.selectbox("Severity", options=["(all)", "error", "warning", "info"], key="_audit_severity_filter")
    limit = col4.number_input("Show last N", min_value=10, max_value=2000, value=200, step=10, key="_audit_limit")

    events = audit_log.list_events(
        limit=int(limit),
        cws=cws_filter.strip() or None,
        category=None if category_filter == "(all)" else category_filter,
        severity=None if severity_filter == "(all)" else severity_filter,
    )
    if not events:
        st.caption("No audit events recorded yet (matching current filters).")
        return

    all_events_unfiltered = audit_log.list_events(limit=2000)
    error_count = sum(1 for e in all_events_unfiltered if e.get("severity") == "error")
    warning_count = sum(1 for e in all_events_unfiltered if e.get("severity") == "warning")
    m1, m2, m3 = st.columns(3)
    m1.metric("Total events (last 2000)", len(all_events_unfiltered))
    m2.metric("⚠️ Warnings", warning_count)
    m3.metric("🚫 Errors", error_count)

    st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")


def render_central_admin_dashboard() -> None:
    st.markdown('<div class="section-title">Central Admin Dashboard</div>', unsafe_allow_html=True)
    tab_overview, tab_admin, tab_ai, tab_portal, tab_audit = st.tabs(
        ["Overview", "Administration", "AI & Console", "Portal & Announcements", "Audit log"]
    )
    with tab_overview:
        _render_overview()
    with tab_admin:
        render_admin_tab()
    with tab_ai:
        _render_ai_console()
    with tab_portal:
        _render_announcement_settings()
    with tab_audit:
        _render_audit_log()

