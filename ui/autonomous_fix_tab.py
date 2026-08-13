"""
ui/autonomous_fix_tab.py
==========================
"🤖 Autonomous Fix" — pending queue of AI-drafted fix proposals from
troubleshooter/autonomous_fix.py: real, recurring production errors the
mega deep-learn (Administration -> AI Control Center -> "Real Deep-Learn")
found that aren't an exact match for any Knowledge Base pattern, but
that the AI is confident enough (cluster + supervised neural ensemble
blend) are really just an existing KB fix applied to a new shipment/
date/location variant.

Restricted to root/app admins and users flagged "ILT Support" (see
auth.user_store.can_approve_autonomous_fixes) — approving one of these
feeds troubleshooter.ilt_ai_core's Level 2 supervised ensemble as a
confirmed training example, so it deserves the same care as any other
self-learning feedback. Nothing here is EVER written to the Knowledge
Base or treated as confirmed without an explicit human approval click.

Includes a lightweight "Learn Center" routing feature: an approver can
assign a specific pending item to a specific Support/Admin user (e.g.
someone being onboarded) so that person reviews and confirms it
themselves — the AI keeps learning WITH the team, not just from a
single reviewer.
"""
import streamlit as st

from auth import user_store, audit_log
from i18n import t
from troubleshooter import autonomous_fix


def _identity() -> tuple[str, str]:
    user = st.session_state.get("auth_user") or {}
    return user.get("cws", ""), user.get("name", "")


def render_autonomous_fix_tab() -> None:
    current_cws, _ = _identity()

    st.markdown(f'<div class="section-title">{t("autofix.title")}</div>', unsafe_allow_html=True)
    st.caption(t("autofix.caption"))

    view_mode = st.radio(
        t("autofix.view_mode_label"),
        options=["all", "mine"],
        format_func=lambda v: t("autofix.view_mode_all") if v == "all" else t("autofix.view_mode_mine"),
        horizontal=True,
        key="autofix_view_mode",
    )
    assigned_filter = current_cws if view_mode == "mine" else None
    items = autonomous_fix.list_pending_fixes(assigned_to=assigned_filter)

    support_users = [u["cws"] for u in user_store.list_ilt_support_users()]
    admin_users = [u["cws"] for u in user_store.list_users() if u.get("is_admin")]
    assignable = sorted(set(support_users) | set(admin_users))

    if not items:
        st.success(t("autofix.empty"), icon="✅")
        st.caption(t("autofix.empty_hint"))
    else:
        st.caption(t("autofix.count", count=len(items)))
        _render_pending_items(items, assignable, current_cws)

    st.divider()
    _render_learn_center(assigned_filter, assignable, current_cws)


def _render_pending_items(items, assignable, current_cws) -> None:
        key = item["key"]
        with st.container(border=True):
            header_l, header_r = st.columns([4, 1])
            with header_l:
                st.markdown(f"**`{item['representative_err_msg']}`**")
                st.caption(
                    f"{t('autofix.matched_pattern')}: `{item.get('matched_kb_pattern', '—')}` · "
                    f"{t('autofix.confidence')}: {int(item.get('confidence', 0) * 100)}%"
                )
            with header_r:
                st.metric(t("autofix.occurrences"), item.get("occurrences", 0))
                st.caption(t("autofix.distinct_variants", count=item.get("distinct_variants", 1)))

            variants = item.get("example_variants") or []
            if len(variants) > 1:
                with st.expander(t("autofix.variants_expander", count=len(variants)), expanded=False):
                    st.caption(t("autofix.variants_hint"))
                    for v in variants:
                        st.code(v, language=None)

            with st.expander(t("autofix.proposed_fix_expander"), expanded=False):
                st.caption(t("kbtab.field_meaning") + ":")
                st.write(item.get("proposed_meaning") or "—")
                st.caption(t("kbtab.field_how_to_check") + ":")
                st.write(item.get("proposed_how_to_check") or "—")
                st.caption(t("kbtab.field_action") + ":")
                st.write(item.get("proposed_action") or "—")

            assigned_to = item.get("assigned_to")
            if assigned_to:
                st.caption(t("autofix.assigned_to", cws=assigned_to))

            acol1, acol2, acol3 = st.columns([1, 1, 2])
            with acol1:
                if st.button(t("autofix.approve"), key=f"autofix_approve_{key}", type="primary"):
                    result = autonomous_fix.approve_fix(key, current_cws or "SYSTEM")
                    if result.get("ok"):
                        st.success(t("autofix.approve_success"))
                        audit_log.record_event(
                            "autonomous_fix_approved", cws=current_cws or "SYSTEM",
                            detail=f"Approved autonomous fix '{key}'", app="ilt",
                            category="autonomous_fix", severity="info",
                        )
                        st.rerun()
                    else:
                        st.error(t("autofix.approve_failed", reason=result.get("reason", "")))
            with acol2:
                if st.button(t("autofix.reject"), key=f"autofix_reject_{key}"):
                    result = autonomous_fix.reject_fix(key, current_cws or "SYSTEM")
                    if result.get("ok"):
                        st.info(t("autofix.reject_success"))
                        audit_log.record_event(
                            "autonomous_fix_rejected", cws=current_cws or "SYSTEM",
                            detail=f"Rejected autonomous fix '{key}'", app="ilt",
                            category="autonomous_fix", severity="info",
                        )
                        st.rerun()
                    else:
                        st.error(t("autofix.reject_failed", reason=result.get("reason", "")))
            with acol3:
                if assignable:
                    picked = st.selectbox(
                        t("autofix.assign_label"),
                        options=[""] + assignable,
                        index=0,
                        key=f"autofix_assign_pick_{key}",
                        label_visibility="collapsed",
                    )
                    if picked and st.button(t("autofix.assign_button"), key=f"autofix_assign_btn_{key}"):
                        result = autonomous_fix.assign_fix(key, picked, current_cws or "SYSTEM")
                        if result.get("ok"):
                            st.success(t("autofix.assign_success", cws=picked))
                            st.rerun()
                        else:
                            st.error(t("autofix.assign_failed", reason=result.get("reason", "")))


def _render_learn_center(assigned_filter, assignable, current_cws) -> None:
    """"Learn Center": real recurring errors the AI genuinely isn't sure
    about yet. Unlike the Autonomous Fix queue above (already-confident
    auto-drafts), these need an actual human teacher to pick the right
    KB pattern — a support/admin user can route a specific item to a
    specific person to walk through step by step, and each answer is
    logged as a real training example for the next mega deep-learn."""
    st.markdown(f'<div class="section-title">{t("autofix.learncenter_title")}</div>', unsafe_allow_html=True)
    st.caption(t("autofix.learncenter_caption"))

    candidates = autonomous_fix.list_teaching_candidates(assigned_to=assigned_filter)
    if not candidates:
        st.info(t("autofix.learncenter_empty"), icon="🎓")
        return

    st.caption(t("autofix.learncenter_count", count=len(candidates)))

    from troubleshooter import ilt_ai_core
    kb_patterns = (ilt_ai_core._load_cluster_state().get("kb_patterns") or [])

    for item in candidates:
        key = item["key"]
        with st.container(border=True):
            lcol, rcol = st.columns([4, 1])
            with lcol:
                st.markdown(f"**`{item['representative_err_msg']}`**")
                suggestions = item.get("suggested_patterns") or []
                if suggestions:
                    sugg_text = " · ".join(f"`{s['pattern']}` ({int(s['confidence'] * 100)}%)" for s in suggestions)
                    st.caption(f"{t('autofix.learncenter_ai_guess')}: {sugg_text}")
            with rcol:
                st.metric(t("autofix.occurrences"), item.get("occurrences", 0))

            variants = item.get("example_variants") or []
            if len(variants) > 1:
                with st.expander(t("autofix.variants_expander", count=len(variants)), expanded=False):
                    for v in variants:
                        st.code(v, language=None)

            assigned_to = item.get("assigned_to")
            if assigned_to:
                st.caption(t("autofix.assigned_to", cws=assigned_to))

            tcol1, tcol2, tcol3 = st.columns([2, 1, 2])
            with tcol1:
                suggested_options = [s["pattern"] for s in (item.get("suggested_patterns") or [])]
                other_options = [p for p in kb_patterns if p not in suggested_options]
                chosen = st.selectbox(
                    t("autofix.learncenter_pick_pattern"),
                    options=[""] + suggested_options + other_options,
                    index=0,
                    key=f"learn_pick_{key}",
                    label_visibility="collapsed",
                )
            with tcol2:
                if chosen and st.button(t("autofix.learncenter_teach_button"), key=f"learn_teach_{key}", type="primary"):
                    result = autonomous_fix.teach_fix(key, current_cws or "SYSTEM", chosen)
                    if result.get("ok"):
                        st.success(t("autofix.learncenter_teach_success"))
                        audit_log.record_event(
                            "autonomous_fix_taught", cws=current_cws or "SYSTEM",
                            detail=f"Taught '{key}' -> pattern '{chosen}'", app="ilt",
                            category="autonomous_fix", severity="info",
                        )
                        st.rerun()
                    else:
                        st.error(t("autofix.learncenter_teach_failed", reason=result.get("reason", "")))
            with tcol3:
                if assignable:
                    picked_person = st.selectbox(
                        t("autofix.assign_label"),
                        options=[""] + assignable,
                        index=0,
                        key=f"learn_assign_pick_{key}",
                        label_visibility="collapsed",
                    )
                    if picked_person and st.button(t("autofix.assign_button"), key=f"learn_assign_btn_{key}"):
                        result = autonomous_fix.assign_fix(key, picked_person, current_cws or "SYSTEM")
                        if result.get("ok"):
                            st.success(t("autofix.assign_success", cws=picked_person))
                            st.rerun()
                        else:
                            st.error(t("autofix.assign_failed", reason=result.get("reason", "")))
