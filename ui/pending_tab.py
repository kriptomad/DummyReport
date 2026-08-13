"""
ui/pending_tab.py
===================
"📌 Pendências" — worklist of ERR_MSG values seen during Troubleshooter
analysis (or the admin's "Process & Feed Internal AI" DB scan) that don't
match anything in the Knowledge Base yet. Lets the technical
team/analysts review each one, optionally ask the internal AI for a
draft fix (meaning / how to validate / action), edit it, and explicitly
approve it before it's posted to the KB — the AI never writes to the KB
on its own; see troubleshooter/pending_errors.py.
"""
import streamlit as st

from troubleshooter import pending_errors
from i18n import t


def _identity() -> tuple[str, str]:
    user = st.session_state.get("auth_user") or {}
    return user.get("cws", ""), user.get("name", "")


def render_pending_tab() -> None:
    current_cws, _ = _identity()

    st.markdown(f'<div class="section-title">{t("pending.title")}</div>', unsafe_allow_html=True)
    st.caption(t("pending.caption"))

    items = pending_errors.list_pending()

    if not items:
        st.success(t("pending.empty"), icon="✅")
        return

    st.caption(t("pending.count", count=len(items)))

    for item in items:
        key = item["key"]
        with st.container(border=True):
            header_l, header_r = st.columns([4, 1])
            with header_l:
                st.markdown(f"**`{item['err_msg']}`**")
                st.caption(
                    f"{t('pending.first_seen')}: {str(item.get('first_seen_at', ''))[:19]} · "
                    f"{t('pending.last_seen')}: {str(item.get('last_seen_at', ''))[:19]}"
                )
            with header_r:
                st.metric(t("pending.occurrences"), item.get("occurrences", 1))

            suggestion = item.get("suggestion") or {}
            similar = item.get("similar")
            source = item.get("suggestion_source")
            # Whether a generation was ever attempted for this item — persisted
            # (not just "does the suggestion dict have any non-empty value"),
            # so a legitimate "no AI provider configured, no similar KB entry"
            # result (all fields blank) still reveals the editable form instead
            # of leaving the "Generate" button appearing to do nothing forever.
            already_generated = bool(item.get("suggestion_generated_at"))

            if not already_generated:
                if st.button(t("pending.generate_suggestion"), key=f"pend_gen_{key}", type="primary"):
                    with st.spinner(t("pending.generating")):
                        result = pending_errors.generate_suggestion(key)
                    if result.get("ok"):
                        st.session_state[f"pend_last_source_{key}"] = result.get("source")
                        st.rerun()
                    else:
                        st.error(t("pending.generate_failed", reason=result.get("reason", "")))
            else:
                source_labels = {
                    "similar_kb": t("pending.source_similar_kb"),
                    "none": t("pending.source_none"),
                }
                source_label = source_labels.get(source, t("pending.source_ai", provider=source))

                # Clear, explicit feedback about what the last generation
                # actually produced — no more silent no-op button clicks.
                if source == "none":
                    st.warning(t("pending.source_none_hint"), icon="⚠️")
                elif source == "similar_kb":
                    st.info(f"🤖 {source_label}", icon="🤖")
                else:
                    st.success(f"🤖 {source_label}", icon="🤖")

                if similar and similar.get("pattern"):
                    score_pct = int(similar.get("score", 0) * 100)
                    if suggestion.get("meaning") == similar.get("meaning") and score_pct >= 50:
                        st.caption(t("pending.similar_match_used", pattern=similar["pattern"], score=score_pct))
                    else:
                        # Below the auto-fill threshold — still a useful hint,
                        # shown for reference only (fields were NOT auto-filled
                        # from it, avoiding a wrong/misleading draft).
                        with st.expander(t("pending.similar_match_hint", pattern=similar["pattern"], score=score_pct), expanded=False):
                            st.caption(t("kbtab.field_meaning") + ":")
                            st.write(similar.get("meaning", "—"))
                            st.caption(t("kbtab.field_action") + ":")
                            st.write(similar.get("action", "—"))

                with st.form(key=f"pend_form_{key}"):
                    st.caption(t("pending.form_hint"))
                    f_meaning = st.text_area(t("kbtab.field_meaning"), value=suggestion.get("meaning", ""))
                    f_how = st.text_area(t("kbtab.field_how_to_check"), value=suggestion.get("how_to_check", ""))
                    f_action = st.text_area(t("kbtab.field_action"), value=suggestion.get("action", ""))
                    fcol1, fcol2 = st.columns(2)
                    with fcol1:
                        f_responsible = st.text_input(t("kbtab.field_responsible"), value=suggestion.get("responsible", ""))
                    with fcol2:
                        f_category = st.text_input(t("kbtab.field_category"), value=suggestion.get("category", ""))

                    bcol1, bcol2, bcol3 = st.columns(3)
                    approve_clicked = bcol1.form_submit_button(t("pending.approve"), type="primary", width="stretch")
                    regen_clicked = bcol2.form_submit_button(t("pending.regenerate"), width="stretch")
                    dismiss_clicked = bcol3.form_submit_button(t("pending.dismiss"), width="stretch")

                    if approve_clicked:
                        if not (f_meaning.strip() and f_action.strip()):
                            st.error(t("pending.approve_missing_fields"))
                        else:
                            result = pending_errors.approve(
                                key, current_cws or "SYSTEM",
                                meaning=f_meaning, how_to_check=f_how, action=f_action,
                                responsible=f_responsible, category=f_category,
                            )
                            if result.get("ok"):
                                st.success(t("pending.approve_success"))
                                st.rerun()
                            else:
                                st.error(t("pending.approve_failed", reason=result.get("reason", "")))

                    if regen_clicked:
                        with st.spinner(t("pending.generating")):
                            pending_errors.generate_suggestion(key)
                        st.rerun()

                    if dismiss_clicked:
                        result = pending_errors.dismiss(key, current_cws or "SYSTEM")
                        if result.get("ok"):
                            st.info(t("pending.dismiss_success"))
                            st.rerun()
                        else:
                            st.error(t("pending.dismiss_failed", reason=result.get("reason", "")))
