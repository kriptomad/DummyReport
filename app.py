import streamlit as st
import pandas as pd
from datetime import datetime
import html
import tempfile
import os

from config import app_settings
from config.db_config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SERVICE, DEFAULT_USER, get_app_account, is_app_account_configured
from database.connection import get_connection, test_connection
from database import connection_profiles
from database.queries import run_shipaudit_query, run_tariff_query
from database.support_queries import QUERY_CATALOGUE, run_location_status
from troubleshooter.loader import (
    COL_CATEGORY,
    COL_ERROR_PATTERN,
    COL_MEANING,
    COL_HOW_TO_CHECK,
    COL_ACTION,
    COL_RESPONSIBLE,
    COL_MEANING_EN,
    COL_HOW_TO_CHECK_EN,
    COL_ACTION_EN,
    is_success_message,
    load_troubleshoot_db,
)
from troubleshooter.engine import match_errors
from troubleshooter.feedback_store import submit_correction, get_corrections_history, delete_kb_entry
from troubleshooter import pending_errors
from reports.exporter import df_to_csv_bytes, df_to_excel_bytes
from reports.troubleshoot_report import build_troubleshoot_report_bytes
from reports.batch_processor_advanced import BatchProcessor, parse_shipment_list
from utils.history_store import (
    log_query,
    log_shipment_search,
    get_query_history,
    get_shipment_history,
    clear_query_history,
    clear_shipment_history,
    parse_ids,
)

# AI Query Builder and Schema Manager
from ui.ai_query_tab import render_ai_query_tab
from ui.schema_manager_tab import render_schema_manager_tab
from ui.knowledge_base_tab import render_knowledge_base_tab
from ui.pending_tab import render_pending_tab
from ui.autonomous_fix_tab import render_autonomous_fix_tab
from ui.lab_test_tab import render_lab_test_tab
from ui.copilot_chat_tab import render_copilot_chat_tab
from ui.admin_tab import render_admin_tab
from ui.help_tab import render_help_tab
from ui.query_builder_tab import render_query_builder_tab
from ui.sql_glossary_tab import render_sql_glossary_tab
from ui.theme_manager import inject_theme_css, render_theme_selector
from ui.announcement_banner import render_global_announcement_banner
from auth import user_store
from auth import audit_log
from auth import presence

# Internationalization (language selector + translation helper)
import i18n
from i18n import t, language_selector

# Authentication (login/registration gate)
from auth import ui as auth_ui
from auth import session_store
from streamlit_cookies_controller import CookieController
from ui.messaging_widget import render_floating_messenger
from troubleshooter import kb_ownership
from troubleshooter.fix_requests import (
    create_request,
    get_incoming,
    get_outgoing,
    get_pending_count,
    respond_to_request,
)


def collect_tariff_filters(prefix: str) -> dict:
    """Collect Rate Card Lookup editable filters from Streamlit state."""
    return {
        "master_rate_card_ids": st.session_state.get(f"{prefix}_master_rate_card_ids", "").strip(),
        "carrier_code": st.session_state.get(f"{prefix}_carrier_code", "").strip(),
        "origin_zone_code": st.session_state.get(f"{prefix}_origin_zone_code", "").strip(),
        "origin_country_code": st.session_state.get(f"{prefix}_origin_country_code", "").strip(),
        "destination_zone_code": st.session_state.get(f"{prefix}_destination_zone_code", "").strip(),
        "destination_country_code": st.session_state.get(f"{prefix}_destination_country_code", "").strip(),
        "service_code": st.session_state.get(f"{prefix}_service_code", "").strip(),
        "charge_code": st.session_state.get(f"{prefix}_charge_code", "").strip(),
        "equipment_type_code": st.session_state.get(f"{prefix}_equipment_type_code", "").strip(),
        "rate_code": st.session_state.get(f"{prefix}_rate_code", "").strip(),
    }


def highlight_err_msg_df(df: pd.DataFrame) -> pd.DataFrame:
    """Return a plain DataFrame with visual markers for ERR_MSG compatibility across pandas versions."""
    if "ERR_MSG" not in df.columns:
        return df

    df_copy = df.copy()
    # Use a text marker instead of Styler to avoid API differences across
    # pandas versions. Success status notes (e.g. "Load created
    # successfully") are never flagged — they aren't errors, so marking
    # them "[!]" would be actively misleading.
    df_copy["ERR_MSG"] = df_copy["ERR_MSG"].apply(
        lambda v: f"[!] {v}" if pd.notna(v) and str(v).strip() and not is_success_message(v) else v
    )
    return df_copy


def _count_real_errors(err_msg_series: pd.Series) -> int:
    """Unique ERR_MSG count, excluding success status notes (see
    is_success_message) — those aren't errors and shouldn't inflate the
    "unique errors" metric."""
    non_null = err_msg_series.dropna()
    real_errors = non_null[~non_null.apply(is_success_message)]
    return int(real_errors.nunique())


def render_query_details(df: pd.DataFrame, key_prefix: str, title: str = None) -> None:
    """Render record-level details for every query result."""
    if df.empty:
        return

    if title is None:
        title = t("common.query_details")

    st.markdown(f'<div class="section-title">📌 {title}</div>', unsafe_allow_html=True)

    def _label(i: int) -> str:
        parts = []
        if "SHIPMENT_ID" in df.columns:
            parts.append(f"Shipment {df.iloc[i]['SHIPMENT_ID']}")
        if "ORIGIN" in df.columns:
            parts.append(f"Origin {df.iloc[i]['ORIGIN']}")
        if "DESTINATION" in df.columns:
            parts.append(f"Destination {df.iloc[i]['DESTINATION']}")
        return " | ".join(parts) if parts else f"Record {i + 1}"

    selected_idx = st.selectbox(
        t("common.select_record"),
        options=list(range(len(df))),
        format_func=_label,
        key=f"{key_prefix}_selected_row",
    )

    selected_row = df.iloc[selected_idx]
    details_df = selected_row.to_frame(name=t("common.value"))
    details_df.index.name = t("common.field")
    st.dataframe(details_df, width="stretch", height=min(700, 42 * (len(details_df) + 1)))


def _kb_text_for_lang(row, pt_col: str, en_col: str, show_en: bool) -> str:
    """
    Returns the KB row's text for the requested display language. If an
    English translation is already stored, use it. Otherwise, translate
    the Portuguese text on the fly (cached per session so the same text
    is never translated twice) — this covers KB rows created before the
    bilingual auto-translate feature existed, so fix suggestions and
    step-by-step instructions always show in English when the UI
    language is English, instead of silently falling back to Portuguese.
    """
    pt_text = str(row.get(pt_col, "") or "").strip()
    if not show_en:
        return pt_text or "—"
    en_text = str(row.get(en_col, "") or "").strip()
    if en_text:
        return en_text
    if pt_text:
        translated = _translate_for_display(pt_text, "en")
        if translated:
            return translated
    return pt_text or "—"


@st.cache_data(show_spinner=False, ttl=3600)
def _translate_for_display(text: str, target_lang: str) -> str:
    """Cached wrapper so the same KB text isn't re-translated on every
    Streamlit rerun/page view — translation providers (LLM or the free
    Google Translate fallback) are only called once per unique text."""
    from troubleshooter.feedback_store import translate_text
    return translate_text(text, target_lang)


def render_feedback_widget(shipment_id: str, err_msg: str, key_suffix: str) -> None:
    """
    Renders the 'was this corrected?' feedback widget for a single error.
    Saving teaches the troubleshooting knowledge base (see
    troubleshooter/feedback_store.py) so future matches improve.
    """
    with st.expander(t("feedback.title"), expanded=False):
        fb_key = f"fb_{key_suffix}"
        corrected_flag = st.checkbox(t("feedback.was_corrected"), key=f"{fb_key}_chk")
        correction_input = st.text_area(
            t("feedback.correction_label"),
            key=f"{fb_key}_txt",
            placeholder=t("feedback.correction_placeholder"),
        )
        if st.button(t("feedback.save_btn"), key=f"{fb_key}_btn"):
            if corrected_flag and not correction_input.strip():
                st.warning(t("feedback.need_text"), icon="⚠️")
            else:
                auth_user = st.session_state.get("auth_user") or {}
                with st.spinner(t("feedback.saving")):
                    result = submit_correction(
                        shipment_id=shipment_id or "unknown",
                        err_msg=err_msg,
                        correction_text=correction_input,
                        corrected=corrected_flag,
                        cws=auth_user.get("cws"),
                        user_name=auth_user.get("name"),
                    )
                if result.get("action") == "logged_only":
                    st.success(t("feedback.logged"))
                elif result.get("action") == "pending_request":
                    st.info(t("requests.sent", owner=result.get("owner_cws", "?")))
                elif result.get("action") == "created":
                    st.success(t("feedback.learned_created", provider=result.get("ai_provider", "none")))
                else:
                    st.success(t("feedback.learned_updated", provider=result.get("ai_provider", "none")))


def render_troubleshoot_results(df_audit: pd.DataFrame, key_suffix: str = "single", shipment_label: str = "") -> None:
    """
    Renders the audit results table + full error analysis (meaning, how to
    validate, action, auto-queries, tariff, feedback/learning widget) for a
    single DataFrame scope. Called once for the whole result set when there's
    only one Shipment ID, or once per tab when multiple Shipment IDs are
    present (see tab_trouble below).
    """
    # ── Audit results table ──────────────────────────────
    st.markdown(f'<div class="section-title">{t("trouble.shipaudit_results")}</div>', unsafe_allow_html=True)

    ma1, ma2, ma3 = st.columns(3)
    ma1.metric(t("report.total_records"),   len(df_audit))
    ma2.metric(t("report.unique_shipments"), df_audit["SHIPMENT_ID"].nunique() if "SHIPMENT_ID" in df_audit.columns else "—")
    ma3.metric(t("report.unique_errors"),    _count_real_errors(df_audit["ERR_MSG"]) if "ERR_MSG" in df_audit.columns else "—")
    st.caption(t("trouble.success_excluded_note"))

    if "ERR_MSG" in df_audit.columns:
        st.dataframe(
            highlight_err_msg_df(df_audit),
            width="stretch",
            height=280,
        )
    else:
        st.dataframe(df_audit, width="stretch", height=280)

    render_query_details(df_audit, key_prefix=f"trouble_{key_suffix}", title=t("trouble.query_details_title"))

    # Export audit table
    exp_c1, exp_c2, _ = st.columns([1, 1, 4])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    exp_c1.download_button(
        t("trouble.export_csv"),
        data=df_to_csv_bytes(df_audit),
        file_name=f"shipaudit_trouble_{key_suffix}_{ts}.csv",
        mime="text/csv",
        width="stretch",
        key=f"exp_trouble_csv_{key_suffix}",
    )
    exp_c2.download_button(
        t("trouble.export_excel"),
        data=df_to_excel_bytes(df_audit),
        file_name=f"shipaudit_trouble_{key_suffix}_{ts}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
        key=f"exp_trouble_xlsx_{key_suffix}",
    )

    # ── Troubleshooter analysis ──────────────────────────
    if "ERR_MSG" not in df_audit.columns:
        st.warning(t("trouble.err_msg_not_found"), icon="⚠️")
        return

    st.markdown(f'<div class="section-title">{t("trouble.error_analysis")}</div>', unsafe_allow_html=True)

    # ── PT/EN language toggle for KB fix text (Meaning / Action) ──────────
    # KB entries are free-text written by whoever created the fix (usually
    # Portuguese). English is optional, manually filled in via "Manage My
    # Fixes". When missing, we fall back to Portuguese with a small note.
    # Defaults to whichever language the UI itself is currently set to
    # (instead of always defaulting to PT) so English-UI users aren't
    # shown Portuguese fix text/step-by-step guidance by default.
    lang_toggle_key = f"trouble_lang_{key_suffix}"
    default_lang = "EN" if i18n.get_language() == "en" else "PT"
    show_en = st.radio(
        t("trouble.language_toggle"),
        options=["PT", "EN"],
        index=0 if st.session_state.get(lang_toggle_key, default_lang) == "PT" else 1,
        horizontal=True,
        key=lang_toggle_key,
    ) == "EN"

    try:
        error_results = match_errors(df_audit["ERR_MSG"])
    except FileNotFoundError as e:
        st.error(str(e), icon="❌")
        error_results = []
    except ValueError as e:
        st.error(t("trouble.kb_format_error", error=e), icon="❌")
        error_results = []
    except Exception as e:
        st.error(t("trouble.unexpected_error", error=e), icon="❌")
        error_results = []

    if not error_results:
        st.info(t("trouble.no_errors_to_analyze"), icon="ℹ️")
        return

    # ── One-click consolidated Troubleshooting Report (Excel) ──────
    # Bundles the raw audit rows + KB meaning/validation/action/steps +
    # freshness/ownership + a worklist of unmatched errors into one
    # shareable workbook — no more screenshots to hand off an analysis.
    try:
        report_bytes = build_troubleshoot_report_bytes(
            df_audit, error_results, shipment_label=shipment_label or key_suffix
        )
        st.download_button(
            t("trouble.download_report"),
            data=report_bytes,
            file_name=f"troubleshooting_report_{key_suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"trouble_report_{key_suffix}",
            help=t("trouble.download_report_help"),
        )
    except Exception as e:
        st.caption(f"⚠️ {t('trouble.report_build_failed', error=e)}")

    no_match_errors = []

    for err_idx, item in enumerate(error_results):
        err_msg      = item["err_msg"]
        matches      = item["matches"]
        needs_tariff = item["needs_tariff"]
        category     = item.get("category", "")
        steps        = item.get("steps", [])
        error_type   = item.get("error_type", {})

        if not str(err_msg).strip() or str(err_msg).lower() == "nan":
            continue

        if not matches:
            no_match_errors.append(err_msg)
            continue

        with st.container(border=True):
            err_key = f"{key_suffix}_{err_idx}"

            # ── Error header card ──────────────────────
            tariff_badge = (
                f' <span class="tariff-badge">{t("trouble.tariff_required_badge")}</span>'
                if needs_tariff else ""
            )
            category_label = f" &nbsp;|&nbsp; 🏷️ {html.escape(str(category))}" if category else ""
            st.markdown(
                f'<div class="error-card">'
                f'<div class="error-card-title">❌ {html.escape(str(err_msg))}{tariff_badge}{category_label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── Best match: Meaning + How to validate + Action ──
            top = matches[0]
            score_pct = int(float(top.get("_match_score", 0.0)) * 100)
            match_method = str(top.get("_match_method", ""))
            score_color = "#155724" if score_pct >= 75 else ("#856404" if score_pct >= 45 else "#721c24")
            score_bg = "#d4edda" if score_pct >= 75 else ("#fff3cd" if score_pct >= 45 else "#f8d7da")

            header_l, header_r = st.columns([4, 1])
            with header_l:
                st.markdown(t("trouble.probable_meaning"))
            with header_r:
                st.markdown(
                    f'<div style="text-align:right;">'
                    f'<span style="background:{score_bg};color:{score_color};padding:0.2rem 0.6rem;'
                    f'border-radius:12px;font-weight:600;font-size:0.9rem;">{score_pct}%</span>'
                    f'<br><span style="font-size:0.7rem;color:#888;">{html.escape(match_method) or "—"}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.info(_kb_text_for_lang(top, COL_MEANING, COL_MEANING_EN, show_en), icon="ℹ️")

            tab_validate, tab_action = st.tabs([t("trouble.how_to_validate"), t("trouble.recommended_action")])
            with tab_validate:
                # KB "how to validate" text is user-editable (see
                # troubleshooter/feedback_store.py / ui/knowledge_base_tab.py),
                # so it must be HTML-escaped before going through
                # unsafe_allow_html — otherwise a malicious/careless KB edit
                # (e.g. "<img src=x onerror=...>") would execute as stored XSS
                # for every user who ever sees a match against that entry.
                how_to_check_text = _kb_text_for_lang(top, COL_HOW_TO_CHECK, COL_HOW_TO_CHECK_EN, show_en)
                st.markdown(
                    f'<div class="info-card">{html.escape(how_to_check_text)}</div>',
                    unsafe_allow_html=True,
                )
            with tab_action:
                st.success(_kb_text_for_lang(top, COL_ACTION, COL_ACTION_EN, show_en), icon="✅")

            # ── Suggested owner (highlight) ────────
            top_owner = html.escape(str(top.get(COL_RESPONSIBLE, "—")))
            st.markdown(
                f'<div style="background:#1e3a5f;color:white;padding:0.6rem 1rem;'
                f'border-radius:6px;margin:0.5rem 0;">'
                f'{t("trouble.suggested_owner", owner=top_owner)}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── KB ownership + freshness badge ─────────
            pattern = top.get(COL_ERROR_PATTERN, err_msg)
            kb_meta = kb_ownership.get_meta(pattern)
            fresh_color, _ = kb_ownership.freshness(kb_meta.get("updated_at", ""))
            badge_colors = {
                "green":  ("#d4edda", "#155724"),
                "yellow": ("#fff3cd", "#856404"),
                "red":    ("#f8d7da", "#721c24"),
            }
            bg, fg = badge_colors.get(fresh_color, ("#e2e3e5", "#383d41"))
            st.markdown(
                f'<div style="background:{bg};color:{fg};padding:0.5rem 0.8rem;'
                f'border-radius:6px;margin:0.4rem 0 0.8rem;font-size:0.85rem;">'
                f'{t(f"kb.freshness_{fresh_color}")} &nbsp;|&nbsp; '
                f'{t("kb.created_by", who=kb_meta.get("created_by", "SYSTEM"), when=str(kb_meta.get("created_at", ""))[:10])} &nbsp;|&nbsp; '
                f'{t("kb.updated_by", who=kb_meta.get("updated_by", "SYSTEM"), when=str(kb_meta.get("updated_at", ""))[:10])}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Ownership actions (history / request-to-edit / delete) all live in
            # ONE expander instead of being scattered across several separate
            # expanders + a loose button — one predictable place to look.
            owner_cws = kb_meta.get("updated_by") or kb_meta.get("created_by") or "SYSTEM"
            auth_user = st.session_state.get("auth_user") or {}
            current_cws = auth_user.get("cws", "")
            is_owner_or_unclaimed = owner_cws == "SYSTEM" or owner_cws.strip().lower() == (current_cws or "").strip().lower()

            with st.expander(t("kb.ownership_expander"), expanded=False):
                if kb_meta.get("history"):
                    st.markdown(f"**{t('kb.history_expander')}**")
                    for h in reversed(kb_meta["history"]):
                        st.caption(f"🕓 {h.get('at', '')[:10]} — {h.get('by', 'SYSTEM')}")
                        st.markdown(f"> {h.get('action_snapshot', '')}")
                    st.divider()

                if not is_owner_or_unclaimed:
                    st.markdown(f"**📨 {t('requests.new_request')}**")
                    st.caption(t("kb.not_owner_notice"))
                    req_type_label = st.selectbox(
                        t("requests.type"),
                        options=[t("requests.type_question"), t("requests.type_improvement")],
                        key=f"reqtype_{err_key}",
                    )
                    req_message = st.text_area(t("requests.message"), key=f"reqmsg_{err_key}")
                    req_proposed = st.text_area(t("requests.proposed_action"), key=f"reqprop_{err_key}")
                    if st.button(t("requests.submit"), key=f"reqsubmit_{err_key}"):
                        rtype = "question" if req_type_label == t("requests.type_question") else "improvement"
                        create_request(
                            requester_cws=current_cws or "anonymous",
                            requester_name=auth_user.get("name", "Unknown"),
                            owner_cws=owner_cws,
                            err_pattern=pattern,
                            request_type=rtype,
                            message=req_message,
                            proposed_action=req_proposed or None,
                        )
                        st.success(t("requests.sent", owner=owner_cws))
                elif current_cws:
                    # Owner (or entry still unclaimed by SYSTEM) can delete it directly.
                    st.markdown(f"**{t('kb.delete_my_fix')}**")
                    del_confirm_key = f"del_confirm_{err_key}"
                    if st.button(t("kb.delete_my_fix"), key=f"del_btn_{err_key}"):
                        st.session_state[del_confirm_key] = True
                    if st.session_state.get(del_confirm_key):
                        st.warning(t("kb.delete_confirm"))
                        if st.button(t("kb.delete_confirm_button"), key=f"del_confirm_btn_{err_key}", type="primary"):
                            result = delete_kb_entry(pattern, current_cws)
                            if result.get("deleted"):
                                st.success(t("kb.delete_success"))
                                st.session_state[del_confirm_key] = False
                                st.rerun()
                            else:
                                st.error(t("kb.delete_failed", reason=result.get("reason", "")))

            # ── Step-by-step ───────────────────────────
            # Steps-sheet content is hand-written (usually in Portuguese)
            # and mixes real numbered instructions with short section
            # labels (e.g. "Ação:") that _load_steps() marks with a
            # leading "### " so they render as bold sub-headings instead
            # of being folded into the same numbered list (previously
            # producing a confusing, "broken"-looking double-numbered
            # list). Each line is also machine-translated on the fly
            # (same PT/EN toggle + cache as the KB Meaning/Action fields)
            # so the guidance shown always matches the selected language.
            if steps:
                with st.expander(t("trouble.step_by_step"), expanded=True):
                    item_no = 0
                    for step in steps:
                        is_heading = step.startswith("### ")
                        text = step[4:].strip() if is_heading else step
                        if show_en and text:
                            translated = _translate_for_display(text, "en")
                            if translated:
                                text = translated
                        if is_heading:
                            st.markdown(f"**{text}**")
                            item_no = 0
                        else:
                            item_no += 1
                            st.markdown(f"{item_no}. {text}")

            # ── Extra details: error-type metadata + other matches, grouped
            # into a single collapsed expander so the card isn't cluttered
            # with several near-empty expanders for occasional-use info ──
            if error_type or len(matches) > 1:
                with st.expander(t("trouble.more_details"), expanded=False):
                    if error_type:
                        st.markdown(f"**{t('trouble.error_type_metadata')}**")
                        meta_cols = st.columns(3)
                        meta_cols[0].markdown(f"**Root Cause:**\n{error_type.get('ROOT_CAUSE','—')}")
                        meta_cols[1].markdown(f"**Rate Validation Required:**\n{error_type.get('RATE_VALIDATION_REQUIRED','—')}")
                        meta_cols[2].markdown(f"**Rate Exists in TM:**\n{error_type.get('RATE_EXISTS_IN_TM','—')}")
                        st.markdown(f"**Action Required:** {error_type.get('ACTION_REQUIRED','—')}")

                    if len(matches) > 1:
                        if error_type:
                            st.divider()
                        st.markdown(f"**{t('trouble.view_more_results', count=len(matches)-1)}**")
                        for row in matches[1:]:
                            s = int(float(row.get("_match_score", 0.0)) * 100)
                            st.markdown(f"**{s}% —** {_kb_text_for_lang(row, COL_MEANING, COL_MEANING_EN, show_en)}")
                            st.caption(t("trouble.action_label", action=_kb_text_for_lang(row, COL_ACTION, COL_ACTION_EN, show_en)))

            # ── Auto-run Support Queries ──────────────────
            auto_query_keys = item.get("auto_queries", [])
            if auto_query_keys:
                st.markdown(
                    f'<div class="section-title">{t("trouble.auto_queries")}</div>',
                    unsafe_allow_html=True,
                )
                for qkey in auto_query_keys:
                    qmeta = QUERY_CATALOGUE.get(qkey)
                    if not qmeta:
                        continue

                    param_key = qmeta["param_key"]

                    # ── origin_dest_validation: uses SHIPMENT_ID from audit ──
                    if param_key == "shipment_id":
                        shipment_ids = df_audit["SHIPMENT_ID"].dropna().unique().tolist() if "SHIPMENT_ID" in df_audit.columns else []
                        shipment_ids = [s.strip() for s in shipment_ids if s.strip()][:5]

                        with st.expander(f"{qmeta['label']} — {qmeta['description']}", expanded=True):
                            for sid in shipment_ids:
                                st.caption(f"🆔 SHIPMENT_ID: `{sid}`")
                                try:
                                    df_odv = qmeta["fn"](st.session_state["conn"], sid)
                                    if df_odv.empty:
                                        st.info(t("trouble.no_data_for", id=sid), icon="🔎")
                                    else:
                                        # Show status badges per row
                                        for _, orow in df_odv.iterrows():
                                            orig_status = str(orow.get("ORIGIN_STATUS", "")).upper()
                                            dest_status = str(orow.get("DESTINATION_STATUS", "")).upper()

                                            def _badge(status: str) -> str:
                                                if status == "ACTIVE":
                                                    return "🟢 ACTIVE"
                                                elif status == "INACTIVE":
                                                    return "🔴 INACTIVE"
                                                elif status == "NOT FOUND":
                                                    return "❌ NOT FOUND"
                                                return f"❓ {status}"

                                            col_o, col_d = st.columns(2)
                                            col_o.markdown(
                                                f"**ORIGIN** `{orow.get('AUDIT_ORIGIN','—')}`\n\n"
                                                f"Status: **{_badge(orig_status)}**"
                                            )
                                            col_d.markdown(
                                                f"**DESTINATION** `{orow.get('AUDIT_DESTINATION','—')}`\n\n"
                                                f"Status: **{_badge(dest_status)}**"
                                            )

                                        st.dataframe(df_odv, width="stretch", height=220)
                                        ts_odv = datetime.now().strftime("%Y%m%d_%H%M%S")
                                        st.download_button(
                                            t("trouble.export_origin_dest"),
                                            data=df_to_csv_bytes(df_odv),
                                            file_name=f"origin_dest_{sid}_{ts_odv}.csv",
                                            mime="text/csv",
                                            key=f"dl_odv_{err_key}_{sid[-6:]}",
                                        )
                                except Exception as ex:
                                    st.error(t("trouble.origin_dest_error", id=sid, error=ex), icon="❌")
                        continue

                    # ── location_status: MANUAL by design (see this KB catalogue
                    # entry's own label/description) — the team said matching a
                    # location ID to the right code/table depends on another
                    # query they'll provide separately, so this must NOT guess
                    # and auto-run against ORIGIN/DESTINATION values from the
                    # audit row (those aren't guaranteed to be real LOCATION_CODE
                    # codes). We only offer them as convenience suggestions the
                    # user can click to fill the box — nothing runs without an
                    # explicit "Run" click. ──
                    if param_key == "location_code":
                        loc_suggestions = []
                        if "ORIGIN" in df_audit.columns:
                            loc_suggestions += df_audit["ORIGIN"].dropna().unique().tolist()
                        if "DESTINATION" in df_audit.columns:
                            loc_suggestions += df_audit["DESTINATION"].dropna().unique().tolist()
                        loc_suggestions = sorted({c.strip() for c in loc_suggestions if c.strip()})

                        with st.expander(f"{qmeta['label']} — {qmeta['description']}", expanded=False):
                            st.caption(t("trouble.location_manual_hint"))
                            loc_input_key = f"loc_manual_{err_key}"
                            if loc_suggestions:
                                picked = st.selectbox(
                                    t("trouble.location_suggestions"),
                                    options=[""] + loc_suggestions,
                                    key=f"{loc_input_key}_pick",
                                    help=t("trouble.location_suggestions_help"),
                                )
                                if picked:
                                    st.session_state[loc_input_key] = picked
                            loc_col, btn_col = st.columns([3, 1])
                            loc_query = loc_col.text_input(
                                t("trouble.location_code_input"),
                                key=loc_input_key,
                                placeholder="e.g. HUB-ATL-01",
                            )
                            run_loc = btn_col.button(t("trouble.run_query_btn"), key=f"{loc_input_key}_run")
                            if run_loc:
                                if not loc_query.strip():
                                    st.warning(t("trouble.location_missing"), icon="⚠️")
                                else:
                                    try:
                                        df_loc = run_location_status(st.session_state["conn"], loc_query.strip())
                                        if df_loc.empty:
                                            st.warning(t("trouble.location_not_found", loc=loc_query.strip()), icon="⚠️")
                                        else:
                                            actv = df_loc.iloc[0].get("ACTIVE_FLAG", "?")
                                            badge = "🟢 Active" if str(actv).upper() in ("1", "Y", "YES", "ACTIVE", "A") else "🔴 Inactive"
                                            st.markdown(f"**Status:** {badge}  |  `ACTIVE_FLAG = {actv}`")
                                            st.dataframe(df_loc, width="stretch")
                                    except Exception as ex:
                                        st.error(t("trouble.location_query_error", loc=loc_query.strip(), error=ex), icon="❌")
                        continue


                    # ── All shipment_number queries ──
                    shipment_numbers = df_audit["SHIPMENT_ID"].dropna().unique().tolist() if "SHIPMENT_ID" in df_audit.columns else []
                    shipment_numbers = [s.strip() for s in shipment_numbers if s.strip()][:5]

                    with st.expander(f"{qmeta['label']} — {qmeta['description']}", expanded=(qkey in ("shipment_details", "shipment_history"))):
                        for shipment_number in shipment_numbers:
                            st.caption(f"🚢 SHIPMENT_NUMBER: `{shipment_number}`")
                            try:
                                df_sq = qmeta["fn"](st.session_state["conn"], shipment_number)
                                if df_sq.empty:
                                    st.info(t("trouble.no_data_found_generic", id=shipment_number), icon="🔎")
                                else:
                                    st.dataframe(df_sq, width="stretch", height=250)
                                    ts_sq = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    st.download_button(
                                        f"📥 Export {qmeta['label']}",
                                        data=df_to_csv_bytes(df_sq),
                                        file_name=f"{qkey}_{shipment_number}_{ts_sq}.csv",
                                        mime="text/csv",
                                        key=f"dl_{qkey}_{err_key}_{shipment_number[-6:]}",
                                    )
                            except Exception as ex:
                                st.error(t("trouble.query_run_error", query=qkey, id=shipment_number, error=ex), icon="❌")

            # ── Rate Card Lookup Query (conditional) ───────────
            if needs_tariff:
                st.markdown(
                    f'<div class="section-title">{t("trouble.tariff_pool_results")}</div>',
                    unsafe_allow_html=True,
                )
                tariff_filters = st.session_state.get("trouble_tariff_filters", {})
                master_rate_card_ids = str(tariff_filters.get("master_rate_card_ids", "")).strip()

                if not master_rate_card_ids:
                    st.warning(
                        t("trouble.requires_tariff_warning"),
                        icon="⚠️",
                    )
                else:
                    with st.spinner(t("trouble.running_tariff_pool", id=master_rate_card_ids)):
                        try:
                            df_tariff = run_tariff_query(
                                st.session_state["conn"],
                                **tariff_filters,
                            )
                            if df_tariff.empty:
                                st.info(
                                    t("trouble.no_tariff_data", id=master_rate_card_ids),
                                    icon="🔎",
                                )
                            else:
                                st.success(
                                    t("trouble.tariff_records_found", count=len(df_tariff), id=master_rate_card_ids),
                                )
                                st.dataframe(df_tariff, width="stretch", height=300)

                                t1, t2, _ = st.columns([1, 1, 4])
                                ts2 = datetime.now().strftime("%Y%m%d_%H%M%S")
                                t1.download_button(
                                    t("trouble.export_tariff_csv"),
                                    data=df_to_csv_bytes(df_tariff),
                                    file_name=f"tariff_{master_rate_card_ids}_{ts2}.csv",
                                    mime="text/csv",
                                    width="stretch",
                                    key=f"tariff_csv_{err_key}",
                                )
                                t2.download_button(
                                    t("trouble.export_tariff_excel"),
                                    data=df_to_excel_bytes(df_tariff),
                                    file_name=f"tariff_{master_rate_card_ids}_{ts2}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    width="stretch",
                                    key=f"tariff_xlsx_{err_key}",
                                )
                        except Exception as e:
                            st.error(t("trouble.tariff_query_error", error=e), icon="❌")

            # ── Feedback / self-learning widget ───────────
            render_feedback_widget(shipment_id=shipment_label or key_suffix, err_msg=err_msg, key_suffix=err_key)

        st.divider()

    # Unmatched errors — cataloged into the "Pendências" worklist (see
    # troubleshooter/pending_errors.py) so the technical team/analysts
    # have a persistent, prioritized list of KB gaps instead of these
    # vanishing the moment this analysis view is closed.
    if no_match_errors:
        pending_errors.register_unmatched_batch(no_match_errors)
        with st.expander(
            t("trouble.no_match_errors", count=len(no_match_errors)),
            expanded=False,
        ):
            st.caption(t("trouble.no_match_caption"))
            for e in no_match_errors:
                st.markdown(f"- `{e}`")


# ─────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title=app_settings.get_setting("app_name", "ILT Troubleshooter"),
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  CUSTOM CSS — modern, clean, minimal look
#  (shared with the standalone PSLD - Parts app — see ui/theme_manager.py)
# ─────────────────────────────────────────────
inject_theme_css()

# ─────────────────────────────────────────────
#  AUTH GATE — must sign in / register before anything else
# ─────────────────────────────────────────────
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None

# SECURITY: this used to store the session token in the URL (?s=...),
# which meant copying/sharing the page link handed over a fully logged-in
# session to whoever opened it (found by the user testing the app) — any
# leftover "s" param from an old bookmark/shared link is now dropped
# immediately and is NEVER honored for restoring a session anymore.
if "s" in st.query_params:
    del st.query_params["s"]

# Browser-cookie-based session persistence (NOT the URL) — a cookie
# never travels with a copied/shared page link, unlike the old query
# param approach. See auth/session_store.py for the server-side token
# store and SESSION_COOKIE_NAME in auth/ui.py.
#
# NOTE: on a brand-new browser tab, the cookie-controller's JS component
# hasn't reported the real cookies back yet on the very first script run
# (it only has the {} default), and — because the constructor caches
# that {} into st.session_state — it would otherwise NEVER re-check the
# browser on later reruns of this same tab, permanently "forgetting" a
# valid session and forcing a second, unnecessary login. So: if this is
# NOT the very first run (the "cookies" key already exists from a prior
# run), force `.refresh()` once to get the real, current browser cookies
# before deciding whether to restore a session. Doing this on the very
# first run instead would call the underlying component twice with the
# same key in one script pass and crash with StreamlitDuplicateElementKey.
_cookies_already_cached = "cookies" in st.session_state
cookie_controller = CookieController()
if st.session_state["auth_user"] is None and _cookies_already_cached:
    cookie_controller.refresh()

# Flush any cookie set/remove queued by a login/logout on the PRIOR run —
# see the `_PENDING_SET_KEY`/`_PENDING_REMOVE_KEY` comment in auth/ui.py
# for why this can't just happen inline before st.rerun(). Must run before
# the cookie-restore check below (harmless no-op when nothing is queued).
auth_ui.apply_pending_cookie_actions(cookie_controller)

# Restore a previously logged-in session after a page refresh (F5) — a
# fresh Streamlit run normally loses session_state, so without this the
# user would be bounced back to the login screen on every reload. The
# session token now lives in a browser cookie and maps to the saved
# auth_user (in RAM only — see auth/session_store.py) on the server.
if st.session_state["auth_user"] is None:
    _restore_token = cookie_controller.get(auth_ui.SESSION_COOKIE_NAME)
    if _restore_token:
        _restored_user = session_store.get_session(_restore_token)
        if _restored_user:
            st.session_state["auth_user"] = _restored_user
            st.session_state["_session_token"] = _restore_token
            session_store.touch_session(_restore_token, app_settings.get_setting("session_timeout_minutes", 480))
        else:
            # Token expired/invalid — drop the stale cookie so we don't
            # keep retrying it every run, and fall through to the login
            # screen. Queued (not called inline) for the same reason as
            # login/logout — see auth/ui.py.
            st.session_state[auth_ui._PENDING_REMOVE_KEY] = True


# Always create this placeholder at the exact same script position, on
# every run, whether logged in or not. This is a real, previously-latent
# bug fix: without a stable `st.empty()` slot, once a user successfully
# logs in and this whole `if auth_user is None:` branch stops being
# executed at all, Streamlit does not reliably garbage-collect the old
# login-form markup from the prior (aborted-via-`st.stop()`) run — it was
# observed lingering, fully visible, at the bottom of the page underneath
# the real app on every subsequent run/reload ("página de login embaixo
# do app do nada"). An `st.empty()` placeholder is explicitly tracked by
# position across runs, so leaving it untouched on a logged-in run makes
# Streamlit correctly render it as empty instead of stale.
_auth_gate_slot = st.empty()
if st.session_state["auth_user"] is None:
    with _auth_gate_slot.container():
        auth_ui.render_login_gate(cookie_controller)
    st.stop()

# An administrator can force a password reset (Administration tab); until
# the user picks a new password, block access to the rest of the app.
if st.session_state["auth_user"].get("must_change_password"):
    with _auth_gate_slot.container():
        auth_ui.render_force_password_change_gate(cookie_controller)
    st.stop()


current_user = st.session_state["auth_user"] or {}
current_cws = current_user.get("cws", "")
settings = app_settings.get_settings()
is_admin_user = user_store.is_admin(current_cws)
is_root_admin_user = (current_cws or "").strip().upper() == user_store.ROOT_ADMIN_CWS

# Presence heartbeat: stamps this user as "seen just now" on every rerun of
# the app (login, tab switch, button click, etc.) so the sidebar/Admin tab
# "online users" views stay accurate — see auth/presence.py.
presence.heartbeat(current_cws, current_user.get("name", ""))

if settings.get("maintenance_mode_enabled") and not is_admin_user:
    st.error(settings.get("maintenance_mode_message") or "⚠️ App is under maintenance.")
    st.stop()

# ─────────────────────────────────────────────
#  SESSION STATE INIT
# ─────────────────────────────────────────────
for key, default in {
    "connected":   False,
    "conn":        None,
    "conn_info":   {},
    "show_dialog": False,
    "batch_summary":   None,
    "batch_processor": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ─────────────────────────────────────────────
#  CONNECTION DIALOG
# ─────────────────────────────────────────────
@st.dialog(t("dialog.title"), width="large")
def connection_dialog():
    st.markdown(t("dialog.subtitle"))

    # ── One-click "Business application account" connect ────────────────
    # Lets users flagged "Business" (see auth/user_store.is_business_user)
    # connect instantly with a pre-defined shared Oracle service account,
    # instead of typing personal DB credentials every time. Only shown
    # when both the account is actually configured (deployment-time
    # secrets/env vars — see config/db_config.py) AND the current user is
    # allowed to use it (Business flag or admin). The manual login flow
    # below remains unchanged and available to everyone as a fallback.
    can_use_app_account = is_app_account_configured() and (
        user_store.is_business_user(current_cws) or is_admin_user
    )
    if can_use_app_account:
        st.info(t("dialog.app_account_hint"), icon="🔑")
        if st.button(t("dialog.app_account_connect"), type="primary", width="stretch", key="conn_app_account_btn"):
            acct = get_app_account()
            with st.spinner(t("dialog.connecting")):
                ok, msg = test_connection(acct["host"], acct["port"], acct["service"], acct["user"], acct["password"], app_cws=current_cws)
            if ok:
                conn = get_connection(acct["host"], acct["port"], acct["service"], acct["user"], acct["password"], app_cws=current_cws)
                st.session_state["conn"]      = conn
                st.session_state["connected"] = True
                st.session_state["conn_info"] = {
                    "host": acct["host"], "port": acct["port"],
                    "service": acct["service"], "user": acct["user"],
                }
                # The Oracle login itself is now a shared account, so it no
                # longer identifies who's using it — log the app-level CWS
                # here to keep that traceability.
                audit_log.record_event(
                    "app_account_connect", cws=current_cws,
                    detail=f"Connected to {acct['host']}:{acct['service']} via Business application account",
                    app="ilt", category="db_connection", severity="info",
                )
                st.success(t("dialog.connected_success"))
                st.rerun()
            else:
                audit_log.record_event(
                    "db_connect_failed", cws=current_cws,
                    detail=f"Business account connect to {acct['host']}:{acct['service']} failed: {msg}",
                    app="ilt", category="db_connection", severity="error",
                )
                st.error(t("dialog.connect_failed", msg=msg))
        st.divider()

    # ── "Create your Oracle profile first" gate ──────────────────────────
    # Users without a registered personal Oracle account (see auth/
    # user_store.get_oracle_username/set_oracle_username — set either at
    # registration via the profile picker, or here) used to be able to
    # freely type ANY Oracle username in the fields below, with no record
    # of what their "own" account even is. That blank slate is exactly
    # what let the earlier shared-login incident happen unnoticed — there
    # was nothing to compare against. So: before showing ANY manual
    # connection fields, require creating this one-time profile.
    # (Business-account users can still use the one-click button above
    # without this, since that's a shared account, not a personal one.)
    registered_oracle_user = user_store.get_oracle_username(current_cws)
    if not registered_oracle_user:
        st.warning(t("dialog.oracle_profile_required_title"), icon="🔐")
        st.caption(t("dialog.oracle_profile_required_caption"))
        new_oracle_username = st.text_input(
            t("dialog.oracle_profile_setup_label"),
            key="conn_oracle_profile_setup",
            placeholder="e.g. demo_user_db",
        )
        if st.button(t("dialog.oracle_profile_setup_save"), type="primary", width="stretch", key="conn_oracle_profile_setup_btn"):
            if not new_oracle_username.strip():
                st.error(t("dialog.oracle_profile_setup_required"))
            else:
                ok, msg = user_store.set_oracle_username(current_cws, new_oracle_username.strip())
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
        return

    profiles = connection_profiles.list_profiles()
    new_option = t("dialog.new_connection")
    profile_names = [p["name"] for p in profiles]
    choice = st.selectbox(
        t("dialog.saved_connection"),
        options=[new_option] + profile_names,
        key="conn_profile_choice",
    )
    selected_profile = next((p for p in profiles if p["name"] == choice), None)

    if selected_profile:
        st.caption(
            f"🖥️ {selected_profile['host']}:{selected_profile['port']} — "
            f"{t('dialog.service')}: {selected_profile['service']}"
        )
        host, port, service = selected_profile["host"], selected_profile["port"], selected_profile["service"]
        # NOTE: username is intentionally left blank here even though the
        # profile has a `default_username` — this connection list is shared
        # across everyone using the app, so pre-filling one person's DB
        # login would leak it into every other user's screen. Each user
        # must type their own DB username/password every time.
        user = st.text_input(t("dialog.username"), value="", placeholder="db_user")
        password = st.text_input(t("dialog.password"), type="password", placeholder="••••••••")
        save_as_new = False
        profile_name_input = None

        # Same "shared login" safeguard as the new-connection flow below.
        oracle_mismatch = bool(
            registered_oracle_user
            and user.strip()
            and user.strip().lower() != registered_oracle_user.strip().lower()
        )
        mismatch_ack = True
        if oracle_mismatch:
            st.warning(
                t("dialog.oracle_mismatch_warning", registered=registered_oracle_user, typed=user.strip()),
                icon="⚠️",
            )
            mismatch_ack = st.checkbox(t("dialog.oracle_mismatch_ack"), key="conn_oracle_mismatch_ack")
    else:
        col1, col2 = st.columns([3, 1])
        host    = col1.text_input(t("dialog.host"),     value=DEFAULT_HOST,    placeholder="e.g. db.company.com")
        port    = col2.text_input(t("dialog.port"),     value=DEFAULT_PORT,    placeholder="1521")
        service = st.text_input( t("dialog.service"),   value=DEFAULT_SERVICE, placeholder="e.g. ORCL or PROD.company.com")

        col3, col4 = st.columns(2)
        # Pre-fill with the user's own registered Oracle account (if an
        # admin has set one for them) instead of the generic DEFAULT_USER
        # — a small nudge towards using their own account by default,
        # while still letting them type a different one if they need to
        # (e.g. a legitimate shared/diagnostic account) — see the
        # mismatch warning below for why that's flagged either way.
        user     = col3.text_input(t("dialog.username"), value=registered_oracle_user or DEFAULT_USER, placeholder="db_user")
        password = col4.text_input(t("dialog.password"), type="password",   placeholder="••••••••")

        # ── "Shared login" safeguard ──────────────────────────────────
        # DBA account-sharing monitoring flags Oracle sessions where the
        # DB username doesn't belong to the person actually running the
        # connection (e.g. User A connecting with User B's Oracle
        # account) — exactly the incident that prompted this check. If
        # this user has a registered Oracle account on file and they've
        # typed a *different* one, require an explicit acknowledgement
        # before letting them connect, and log it either way.
        oracle_mismatch = bool(
            registered_oracle_user
            and user.strip()
            and user.strip().lower() != registered_oracle_user.strip().lower()
        )
        mismatch_ack = True
        if oracle_mismatch:
            st.warning(
                t("dialog.oracle_mismatch_warning", registered=registered_oracle_user, typed=user.strip()),
                icon="⚠️",
            )
            mismatch_ack = st.checkbox(t("dialog.oracle_mismatch_ack"), key="conn_oracle_mismatch_ack")

        save_as_new = st.checkbox(t("dialog.save_as_profile"), key="conn_save_as_new")
        profile_name_input = (
            st.text_input(t("dialog.profile_name"), placeholder="e.g. Prod Oracle", key="conn_new_profile_name")
            if save_as_new else None
        )


    auto_import_schema = st.checkbox(t("dialog.schema_import_checkbox"), value=True)
    suggested_owner = (user or DEFAULT_USER or "").upper()
    owner_state_key = "conn_schema_import_owner"
    owner_source_key = "conn_schema_import_owner_source"
    current_owner_value = st.session_state.get(owner_state_key, "")
    previous_suggested_owner = st.session_state.get(owner_source_key, "")
    if not current_owner_value or current_owner_value == previous_suggested_owner:
        st.session_state[owner_state_key] = suggested_owner
    st.session_state[owner_source_key] = suggested_owner
    import_owner = st.text_input(
        t("dialog.schema_import_owner_label"),
        key=owner_state_key,
        placeholder=suggested_owner,
    )

    st.divider()
    col_btn1, col_btn2, _ = st.columns([1, 1, 3])

    if col_btn1.button(t("dialog.connect"), type="primary", width="stretch"):
        if not all([host, port, service, user, password]):
            st.error(t("dialog.fill_all_fields"))
        elif oracle_mismatch and not mismatch_ack:
            st.error(t("dialog.oracle_mismatch_blocked"))
        else:
            with st.spinner(t("dialog.connecting")):
                ok, msg = test_connection(host, port, service, user, password, app_cws=current_cws)
            if ok:
                if save_as_new and profile_name_input:
                    # Only host/port/service are saved — never the username,
                    # since this profile list is shared by every user of the
                    # app and each person must use their own DB login.
                    connection_profiles.add_profile(profile_name_input, host, port, service)
                conn = get_connection(host, port, service, user, password, app_cws=current_cws)
                st.session_state["conn"]      = conn
                st.session_state["connected"] = True
                st.session_state["conn_info"] = {
                    "host": host, "port": port,
                    "service": service, "user": user,
                }
                # Traceability: record who (app-level CWS) connected with
                # which Oracle username, flagging it clearly whenever it
                # doesn't match their own registered account — this is the
                # in-app audit trail DBA account-sharing monitoring alerts
                # can be cross-referenced against.
                audit_log.record_event(
                    "oracle_username_mismatch" if oracle_mismatch else "oracle_connect",
                    cws=current_cws,
                    detail=(
                        f"Connected to {host}:{service} as Oracle user '{user}', which differs from "
                        f"their registered account '{registered_oracle_user}'"
                        if oracle_mismatch else
                        f"Connected to {host}:{service} as Oracle user '{user}'"
                    ),
                    app="ilt", category="db_connection", severity="warning" if oracle_mismatch else "info",
                )
                st.success(t("dialog.connected_success"))
                if auto_import_schema:
                    from ai.schema_manager import SchemaManager
                    from database.schema_introspection import import_into_catalog

                    if "schema_manager" not in st.session_state:
                        st.session_state["schema_manager"] = SchemaManager()

                    try:
                        owner_to_import = (import_owner or user or "").strip().upper()
                        with st.spinner(t("dialog.schema_import_spinner", owner=owner_to_import)):
                            imported_count = import_into_catalog(
                                conn,
                                owner=owner_to_import,
                                source_label=f"{host}:{service}",
                                manager=st.session_state.get("schema_manager"),
                            )
                        st.success(t("dialog.schema_import_success", count=imported_count))
                    except Exception as e:
                        st.warning(t("dialog.schema_import_skipped", error=e))
                st.rerun()
            else:
                audit_log.record_event(
                    "db_connect_failed", cws=current_cws,
                    detail=f"Manual connect to {host}:{service} as Oracle user '{user}' failed: {msg}",
                    app="ilt", category="db_connection", severity="error",
                )
                st.error(t("dialog.connect_failed", msg=msg))
        st.rerun()

    with st.expander(t("dialog.manage_profiles")):
        if not profiles:
            st.caption(t("dialog.no_profiles"))
        for p in profiles:
            with st.container(border=True):
                pc1, pc3 = st.columns([4, 1])
                new_name = pc1.text_input(t("dialog.profile_name"), value=p["name"], key=f"pn_{p['id']}")
                if pc3.button("💾", key=f"psave_{p['id']}", help=t("dialog.save_changes"), width="stretch"):
                    connection_profiles.update_profile(p["id"], name=new_name)
                    st.rerun()
                pe1, pe2, pe3, pe4 = st.columns([2, 1, 1, 1])
                new_host = pe1.text_input(t("dialog.host"), value=p["host"], key=f"ph_{p['id']}")
                new_port = pe2.text_input(t("dialog.port"), value=p["port"], key=f"pp_{p['id']}")
                new_service = pe3.text_input(t("dialog.service"), value=p["service"], key=f"ps_{p['id']}")
                if pe4.button("🗑️", key=f"pdel_{p['id']}", help=t("dialog.delete_profile"), width="stretch"):
                    connection_profiles.delete_profile(p["id"])
                    st.rerun()
                if (new_host, new_port, new_service) != (p["host"], p["port"], p["service"]):
                    connection_profiles.update_profile(p["id"], host=new_host, port=new_port, service=new_service)


# ─────────────────────────────────────────────
#  SIDEBAR — Language selector (must run before header so t() reflects choice)
# ─────────────────────────────────────────────
with st.sidebar:
    language_selector()
    render_theme_selector()
    st.divider()
    auth_ui.render_user_sidebar(cookie_controller)
    st.divider()

    # ── Online users ──────────────────────────────────────
    online_users = presence.list_online_users()
    with st.expander(f"🟢 {t('online.title')} ({len(online_users)})", expanded=False):
        st.caption(t("online.caption"))
        if not online_users:
            st.caption(t("online.none"))
        else:
            for u in online_users:
                is_me = u["cws"].strip().upper() == current_cws.strip().upper()
                suffix = f" — {t('online.you')}" if is_me else ""
                st.markdown(f"🟢 **{u['name']}** ({u['cws']}){suffix}")
    st.divider()

# Floating messenger widget (bottom-right, available from every tab)
if settings.get("enable_messaging", True):
    render_floating_messenger(st.session_state["auth_user"])

# ─────────────────────────────────────────────
#  HEADER
# ─────────────────────────────────────────────
st.markdown(f"""
<div class="main-header">
    <h1>{settings.get("app_name") or t("app.title")}</h1>
</div>
""", unsafe_allow_html=True)
render_global_announcement_banner()
if settings.get("maintenance_mode_enabled") and is_admin_user:
    st.caption("⚠️ Maintenance mode is enabled. Admin access is bypassing the public lockout.")

# ─────────────────────────────────────────────
#  SIDEBAR — Connection Status + Controls
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## {t('sidebar.connection')}")

    if st.session_state["connected"]:
        info = st.session_state["conn_info"]
        st.markdown(
            f'<span class="status-connected">{t("sidebar.connected")}</span>',
            unsafe_allow_html=True,
        )
        st.caption(t("sidebar.host", host=info.get('host'), port=info.get('port')))
        st.caption(t("sidebar.service", service=info.get('service')))
        st.caption(t("sidebar.user", user=info.get('user')))

        if st.button(t("sidebar.disconnect"), width="stretch"):
            try:
                st.session_state["conn"].close()
            except Exception:
                pass
            st.session_state["conn"]      = None
            st.session_state["connected"] = False
            st.session_state["conn_info"] = {}
            st.rerun()
    else:
        st.markdown(
            f'<span class="status-disconnected">{t("sidebar.not_connected")}</span>',
            unsafe_allow_html=True,
        )
        if st.button(t("sidebar.connect_button"), type="primary", width="stretch"):
            connection_dialog()

    st.divider()
    with st.expander(t("sidebar.about"), expanded=False):
        st.caption(t("sidebar.about_text"))
        st.markdown(f"- {t('about.feature_report')}")
        st.markdown(f"- {t('about.feature_troubleshoot')}")
        st.markdown(f"- {t('about.feature_kb')}")
        st.markdown(f"- {t('about.feature_ai')}")
        st.markdown(f"- {t('about.feature_messaging')}")
        st.caption(f"🏷️ {t('about.version')}: 2.0")
        st.caption(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    with st.expander(t("help.title"), expanded=False):
        st.markdown(f"**{t('help.q1')}**")
        st.caption(t("help.a1"))
        st.markdown(f"**{t('help.q2')}**")
        st.caption(t("help.a2"))
        st.markdown(f"**{t('help.q3')}**")
        st.caption(t("help.a3"))
        st.markdown(f"**{t('help.q4')}**")
        st.caption(t("help.a4"))
        st.info(t("help.support_hint"), icon="💬")

    # ── History: recent shipment IDs + recent queries ────
    st.divider()
    st.markdown(f"## {t('history.title')}")

    with st.expander(t("history.recent_shipments"), expanded=False):
        recent_ships = get_shipment_history(limit=15)
        if not recent_ships:
            st.caption(t("history.no_shipments"))
        else:
            for h in recent_ships:
                if st.button(
                    f"🚢 {h['shipment_id']}",
                    key=f"hist_ship_{h['shipment_id']}",
                    help=t("history.times", count=h.get("times_searched", 1), when=h.get("last_searched", "")[:16]),
                    width="stretch",
                ):
                    st.session_state["r_ship"] = h["shipment_id"]
                    st.session_state["t_ship"] = h["shipment_id"]
                    st.rerun()

    with st.expander(t("history.recent_queries"), expanded=False):
        recent_queries = get_query_history(limit=15)
        if not recent_queries:
            st.caption(t("history.no_queries"))
        else:
            for q in recent_queries:
                status_icon = "❌" if q.get("error") else "✅"
                params_str = ", ".join(f"{k}={v}" for k, v in q.get("params", {}).items())
                st.caption(f"{status_icon} **{q['type']}** — {params_str or '—'}")
                st.caption(f"　　{q['timestamp'][:16]} · {q.get('row_count', '—')} rows")

    if recent_ships or recent_queries:
        if st.button(t("history.clear"), key="btn_clear_history", width="stretch"):
            clear_query_history()
            clear_shipment_history()
            st.rerun()

# ─────────────────────────────────────────────
#  TRIGGER DIALOG ON FIRST LOAD
# ─────────────────────────────────────────────
if not st.session_state["connected"] and st.session_state.get("show_dialog"):
    connection_dialog()

# NOTE: we intentionally do NOT st.stop() here anymore. Help, SQL Glossary,
# Knowledge Base and Administration are usable right after login, without a
# DB connection — each DB-dependent tab below shows its own "connect first"
# notice and gates its own content instead of blocking the whole page.

# ─────────────────────────────────────────────
#  MAIN TABS
# ─────────────────────────────────────────────
_tab_defs = [
    ("report", t("tabs.report")),
    ("troubleshooter", t("tabs.troubleshooter")),
    ("batch", t("tabs.batch")),
    ("knowledge_base", t("tabs.knowledge_base")),
    ("pending", t("tabs.pending")),
    ("sql", t("tabs.sql_queries")),
]
if settings.get("enable_ai_query_builder", True):
    _tab_defs.append(("ai", t("tabs.ai_query")))
if settings.get("enable_copilot_chat", True):
    _tab_defs.append(("chat", t("tabs.copilot_chat")))
if settings.get("enable_schema_manager", True):
    _tab_defs.append(("schema", t("tabs.schema_manager")))
_tab_defs.append(("learning", t("tabs.learning")))
if user_store.can_approve_autonomous_fixes(current_cws):
    _tab_defs.append(("autonomous_fix", t("tabs.autonomous_fix")))
if is_admin_user:
    _tab_defs.append(("admin", t("tabs.admin")))
if is_root_admin_user and False:
    # Lab Test tab retired — all of its functionality (ServiceNow login,
    # AI Control Center, PSLD experimentation, resolution KB, etc.) has
    # been fully migrated to the standalone PSLD - Parts app (psld_app.py,
    # port 8502). Kept as a `False`-gated no-op (instead of deleting the
    # code outright) in case something buried in here still needs to be
    # referenced/ported later — see ui/lab_test_tab.py for the original
    # implementation.
    _tab_defs.append(("lab", t("tabs.lab")))
_tab_defs.append(("qbuilder", t("tabs.query_builder")))
_tab_defs.append(("glossary", t("tabs.sql_glossary")))
_tab_defs.append(("help", t("tabs.help")))

# Per-user screen access control (see auth/user_store.py's SCREEN_REGISTRY) —
# admin/lab are already gated by role above, so they're exempt from this
# extra per-screen check; everything else can be individually hidden per
# user from the Central Admin Dashboard / Administration tab.
_screen_key_map = {
    "report": "ilt.report", "troubleshooter": "ilt.troubleshooter", "batch": "ilt.batch",
    "knowledge_base": "ilt.knowledge_base", "pending": "ilt.pending", "sql": "ilt.sql",
    "ai": "ilt.ai", "chat": "ilt.chat", "schema": "ilt.schema", "learning": "ilt.learning",
    "qbuilder": "ilt.qbuilder", "glossary": "ilt.glossary", "help": "ilt.help",
}
_tab_defs = [
    (key, label) for key, label in _tab_defs
    if key in ("admin", "lab", "autonomous_fix") or user_store.is_screen_enabled(current_cws, _screen_key_map.get(key, ""))
]

if not _tab_defs:
    st.warning(
        "Your account currently has no screens enabled. Ask an administrator "
        "to grant access from the Central Admin Dashboard.",
        icon="🔒",
    )
    st.stop()

_all_tabs = st.tabs([label for _, label in _tab_defs])
_tabs_by_key = dict(zip([key for key, _ in _tab_defs], _all_tabs))
tab_report = _tabs_by_key.get("report")
tab_trouble = _tabs_by_key.get("troubleshooter")
tab_batch = _tabs_by_key.get("batch")
tab_kb = _tabs_by_key.get("knowledge_base")
tab_pending = _tabs_by_key.get("pending")
tab_sql = _tabs_by_key.get("sql")
tab_ai = _tabs_by_key.get("ai")
tab_chat = _tabs_by_key.get("chat")
tab_schema = _tabs_by_key.get("schema")
tab_learn = _tabs_by_key.get("learning")
tab_autonomous_fix = _tabs_by_key.get("autonomous_fix")
tab_admin = _tabs_by_key.get("admin")
tab_lab = _tabs_by_key.get("lab")
tab_qbuilder = _tabs_by_key.get("qbuilder")
tab_glossary = _tabs_by_key.get("glossary")
tab_help = _tabs_by_key.get("help")


# ═══════════════════════════════════════════════
#  TAB 1 — REPORT
# ═══════════════════════════════════════════════
if tab_report is not None:
    with tab_report:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            st.markdown(f'<div class="section-title">{t("common.search_filters")}</div>', unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            r_shipment_id = col1.text_input(
                t("report.shipment_id"),
                placeholder="e.g. 123456  or  123,456,789",
                help=t("report.shipment_id_help"),
                key="r_ship",
            )
            r_origin = col2.text_input(
                t("report.origin"),
                placeholder="e.g. US, NYC",
                help=t("report.partial_match"),
                key="r_orig",
            )
            r_destination = col3.text_input(
                t("report.destination"),
                placeholder="e.g. BR, GRU",
                help=t("report.partial_match"),
                key="r_dest",
            )

            search_col, refresh_col, _ = st.columns([1, 1, 4])
            run_report = search_col.button(t("report.run_query"), type="primary", width="stretch", key="btn_report")
            # The preview only ever auto-runs ONCE per browser session (see
            # below), so without an explicit way to re-run it, the "10 most
            # recent shipments" grid looked static/stale for the rest of the
            # session — it never reflected new shipments created after the
            # tab was first opened. Let the user force a fresh preview pull.
            # Only shown/active while still in preview mode, so it can never
            # clobber a real filtered search result the user already ran.
            still_in_preview = st.session_state.get("report_is_preview", True)
            refresh_preview = still_in_preview and refresh_col.button(
                t("report.refresh_preview"), width="stretch", key="btn_refresh_preview",
            )

            # ── Live preview on first load (and on manual refresh) ───────
            # The very first time this tab renders in a session (before the user
            # has searched anything), auto-run a small, filter-free query so the
            # user immediately sees real rows coming from the database — proof
            # the connection actually works — before they start using the search
            # tools above.
            if refresh_preview or "report_df" not in st.session_state:
                try:
                    with st.spinner(t("report.loading_preview")):
                        preview_df = run_shipaudit_query(st.session_state["conn"], limit=10)
                    st.session_state["report_df"] = preview_df
                    st.session_state["report_is_preview"] = True
                except Exception:
                    st.session_state["report_df"] = None
                    st.session_state["report_is_preview"] = False

            if run_report:
                if not any([r_shipment_id, r_origin, r_destination]):
                    st.warning(t("common.fill_one_filter"), icon="⚠️")
                else:
                    with st.spinner(t("report.querying")):
                        try:
                            df_result = run_shipaudit_query(
                                st.session_state["conn"],
                                shipment_id=r_shipment_id or None,
                                origin=r_origin or None,
                                destination=r_destination or None,
                            )
                            st.session_state["report_df"] = df_result
                            st.session_state["report_is_preview"] = False

                            # ── History logging ──────────────────────────
                            log_query(
                                "report",
                                {"shipment_id": r_shipment_id, "origin": r_origin, "destination": r_destination},
                                row_count=len(df_result),
                            )
                            searched_ids = parse_ids(r_shipment_id)
                            if not searched_ids and "SHIPMENT_ID" in df_result.columns:
                                searched_ids = df_result["SHIPMENT_ID"].dropna().astype(str).unique().tolist()
                            log_shipment_search(searched_ids, source="report")
                        except Exception as e:
                            st.error(t("common.query_error", error=e), icon="❌")
                            log_query(
                                "report",
                                {"shipment_id": r_shipment_id, "origin": r_origin, "destination": r_destination},
                                error=str(e),
                            )
                            st.session_state["report_df"] = None
                            st.session_state["report_is_preview"] = False

            if "report_df" in st.session_state and st.session_state["report_df"] is not None:
                df = st.session_state["report_df"]

                if df.empty:
                    st.info(t("common.no_records"), icon="🔎")
                else:
                    if st.session_state.get("report_is_preview"):
                        st.caption(t("report.preview_caption"))

                    st.markdown(f'<div class="section-title">{t("common.results")}</div>', unsafe_allow_html=True)

                    # Metrics row
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric(t("report.total_records"),   len(df))
                    m2.metric(t("report.unique_shipments"), df["SHIPMENT_ID"].nunique() if "SHIPMENT_ID" in df.columns else "—")
                    m3.metric(t("report.unique_errors"),    _count_real_errors(df["ERR_MSG"]) if "ERR_MSG" in df.columns else "—")
                    m4.metric(t("report.columns"),          len(df.columns))

                    # Highlight ERR_MSG column if present
                    if "ERR_MSG" in df.columns:
                        st.dataframe(
                            highlight_err_msg_df(df),
                            width="stretch",
                            height=420,
                        )
                    else:
                        st.dataframe(df, width="stretch", height=420)

                    render_query_details(df, key_prefix="report", title=t("report.query_details_title"))

                    # Export buttons
                    st.markdown(f'<div class="section-title">{t("common.export")}</div>', unsafe_allow_html=True)
                    exp1, exp2, _ = st.columns([1, 1, 4])
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                    exp1.download_button(
                        label=t("common.download_csv"),
                        data=df_to_csv_bytes(df),
                        file_name=f"shipaudit_report_{timestamp}.csv",
                        mime="text/csv",
                        width="stretch",
                    )
                    exp2.download_button(
                        label=t("common.download_excel"),
                        data=df_to_excel_bytes(df),
                        file_name=f"shipaudit_report_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width="stretch",
                    )


# ═══════════════════════════════════════════════
#  TAB 2 — TROUBLESHOOTER
# ═══════════════════════════════════════════════
if tab_trouble is not None:
    with tab_trouble:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            st.markdown(f'<div class="section-title">{t("common.search_filters")}</div>', unsafe_allow_html=True)

            tc1, tc2, tc3 = st.columns(3)
            t_shipment_id = tc1.text_input(
                t("report.shipment_id"),
                placeholder="e.g. 123456  or  123,456,789",
                key="t_ship",
            )
            t_origin = tc2.text_input(
                t("report.origin"),
                placeholder="e.g. US, NYC",
                key="t_orig",
            )
            t_destination = tc3.text_input(
                t("report.destination"),
                placeholder="e.g. BR, GRU",
                key="t_dest",
            )

            with st.expander(t("trouble.tariff_pool"), expanded=False):
                st.caption(t("trouble.tariff_pool_caption"))

                tf1, tf2, tf3 = st.columns(3)
                tf1.text_input(
                    t("trouble.mstr_tff_required"),
                    placeholder="e.g. 90001  or  90001,90002",
                    key="t_tariff_master_rate_card_ids",
                )
                tf2.text_input("CARRIER_CODE", placeholder="e.g. CARR-01", key="t_tariff_carrier_code")
                tf3.text_input("SERVICE_CODE", placeholder="e.g. SRV-01", key="t_tariff_service_code")

                tf4, tf5, tf6 = st.columns(3)
                tf4.text_input("ORIGIN_ZONE_CODE", placeholder="e.g. ZONE-EAST", key="t_tariff_origin_zone_code")
                tf5.text_input("ORIGIN_COUNTRY_CODE", placeholder="e.g. US", key="t_tariff_origin_country_code")
                tf6.text_input("CHARGE_CODE", placeholder="e.g. LINEHAUL", key="t_tariff_charge_code")

                tf7, tf8, tf9 = st.columns(3)
                tf7.text_input("DESTINATION_ZONE_CODE", placeholder="e.g. ZONE-WEST", key="t_tariff_destination_zone_code")
                tf8.text_input("DESTINATION_COUNTRY_CODE", placeholder="e.g. BR", key="t_tariff_destination_country_code")
                tf9.text_input("EQUIPMENT_TYPE_CODE", placeholder="e.g. DRYBOX", key="t_tariff_equipment_type_code")

                tf10, _, _ = st.columns(3)
                tf10.text_input("RATE_CODE", placeholder="e.g. RATE01", key="t_tariff_rate_code")

                tariff_btn_col, _ = st.columns([1, 5])
                run_tariff_manual = tariff_btn_col.button(
                    t("trouble.query_tariff_pool_btn"),
                    width="stretch",
                    key="btn_tariff_manual",
                )

                if run_tariff_manual:
                    tariff_filters = collect_tariff_filters("t_tariff")
                    if not tariff_filters["master_rate_card_ids"]:
                        st.warning(t("trouble.tariff_missing_field"), icon="⚠️")
                    else:
                        with st.spinner(t("trouble.querying_tariff_pool")):
                            try:
                                df_tariff_manual = run_tariff_query(st.session_state["conn"], **tariff_filters)
                                st.session_state["tariff_manual_df"] = df_tariff_manual
                                st.session_state["trouble_tariff_filters"] = tariff_filters
                            except Exception as e:
                                st.error(t("trouble.tariff_query_error", error=e), icon="❌")
                                st.session_state["tariff_manual_df"] = None

                if "tariff_manual_df" in st.session_state and st.session_state["tariff_manual_df"] is not None:
                    df_tariff_manual = st.session_state["tariff_manual_df"]
                    if df_tariff_manual.empty:
                        st.info(t("trouble.tariff_no_results"), icon="🔎")
                    else:
                        st.success(t("trouble.tariff_rows_returned", count=len(df_tariff_manual)))
                        st.dataframe(df_tariff_manual, width="stretch", height=280)

                        ts_tariff = datetime.now().strftime("%Y%m%d_%H%M%S")
                        tcsv, txlsx, _ = st.columns([1, 1, 4])
                        tcsv.download_button(
                            t("trouble.export_tariff_csv"),
                            data=df_to_csv_bytes(df_tariff_manual),
                            file_name=f"tariff_manual_{ts_tariff}.csv",
                            mime="text/csv",
                            width="stretch",
                            key="exp_tariff_manual_csv",
                        )
                        txlsx.download_button(
                            t("trouble.export_tariff_excel"),
                            data=df_to_excel_bytes(df_tariff_manual),
                            file_name=f"tariff_manual_{ts_tariff}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            width="stretch",
                            key="exp_tariff_manual_xlsx",
                        )

            analyze_col, _ = st.columns([1, 5])
            run_trouble = analyze_col.button(t("trouble.analyze_btn"), type="primary", width="stretch", key="btn_trouble")

            if run_trouble:
                if not any([t_shipment_id, t_origin, t_destination]):
                    st.warning(t("trouble.fill_one_filter"), icon="⚠️")
                else:
                    with st.spinner(t("trouble.querying_and_analyzing")):
                        try:
                            df_audit = run_shipaudit_query(
                                st.session_state["conn"],
                                shipment_id=t_shipment_id or None,
                                origin=t_origin or None,
                                destination=t_destination or None,
                                exclude_success_messages=True,
                            )
                            st.session_state["trouble_df"] = df_audit
                            st.session_state["trouble_tariff_filters"] = collect_tariff_filters("t_tariff")

                            # ── History logging ──────────────────────────
                            log_query(
                                "troubleshooter",
                                {"shipment_id": t_shipment_id, "origin": t_origin, "destination": t_destination},
                                row_count=len(df_audit),
                            )
                            searched_ids = parse_ids(t_shipment_id)
                            if not searched_ids and "SHIPMENT_ID" in df_audit.columns:
                                searched_ids = df_audit["SHIPMENT_ID"].dropna().astype(str).unique().tolist()
                            log_shipment_search(searched_ids, source="troubleshooter")
                        except Exception as e:
                            st.error(t("common.query_error", error=e), icon="❌")
                            log_query(
                                "troubleshooter",
                                {"shipment_id": t_shipment_id, "origin": t_origin, "destination": t_destination},
                                error=str(e),
                            )
                            st.session_state["trouble_df"] = None

            if "trouble_df" in st.session_state and st.session_state["trouble_df"] is not None:
                df_audit = st.session_state["trouble_df"]

                if df_audit.empty:
                    st.info(t("common.no_records"), icon="🔎")
                else:
                    # ── Multi-shipment ID support ──────────────────────────
                    # If the search matched more than one distinct Shipment ID,
                    # show one tab per shipment instead of a single giant page —
                    # each tab contains that shipment's own audit rows, error
                    # analysis, auto-queries and tariff results.
                    shipment_ids_found = (
                        sorted(df_audit["SHIPMENT_ID"].dropna().astype(str).unique().tolist())
                        if "SHIPMENT_ID" in df_audit.columns else []
                    )

                    if len(shipment_ids_found) > 1:
                        st.caption(t("trouble.multi_shipment_caption", count=len(shipment_ids_found)))
                        shipment_tabs = st.tabs([f"🚢 {sid}" for sid in shipment_ids_found])
                        for tab_obj, sid in zip(shipment_tabs, shipment_ids_found):
                            with tab_obj:
                                df_scope = df_audit[df_audit["SHIPMENT_ID"].astype(str) == sid].reset_index(drop=True)
                                render_troubleshoot_results(df_scope, key_suffix=sid, shipment_label=sid)
                    else:
                        single_label = shipment_ids_found[0] if shipment_ids_found else "single"
                        render_troubleshoot_results(df_audit, key_suffix="single", shipment_label=single_label)


# ═══════════════════════════════════════════════
#  TAB 3 — BATCH PROCESSING
# ═══════════════════════════════════════════════
if tab_batch is not None:
    with tab_batch:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            st.markdown(f'<div class="section-title">{t("batch.title")}</div>', unsafe_allow_html=True)
            st.caption(t("batch.caption"))

            batch_input = st.text_area(
                t("batch.ids_label"),
                placeholder="123456, 123457\n123458",
                height=120,
                key="batch_ids_input",
            )

            col_workers, col_run = st.columns([1, 3])
            max_workers = col_workers.slider(t("batch.workers"), min_value=1, max_value=10, value=5, key="batch_max_workers")
            run_batch = col_run.button(t("batch.run"), type="primary", key="btn_run_batch")

            if run_batch:
                shipment_ids = parse_shipment_list(batch_input)
                if not shipment_ids:
                    st.warning(t("batch.enter_id"), icon="⚠️")
                else:
                    conn = st.session_state["conn"]
                    processor = BatchProcessor(max_workers=max_workers)

                    progress_bar = st.progress(0.0)
                    status_text = st.empty()

                    def _progress_callback(processed, total, ship_id):
                        progress_bar.progress(processed / total)
                        status_text.caption(t("batch.processed_status", processed=processed, total=total, id=ship_id))

                    def _query_fn(shipment_id):
                        return run_shipaudit_query(conn, shipment_id=shipment_id, exclude_success_messages=True)

                    with st.spinner(t("batch.processing", count=len(shipment_ids))):
                        summary = processor.process_shipments(
                            shipment_ids=shipment_ids,
                            query_fn=_query_fn,
                            troubleshoot_fn=match_errors,
                            progress_callback=_progress_callback,
                        )

                    progress_bar.progress(1.0)
                    status_text.empty()
                    st.session_state["batch_processor"] = processor
                    st.session_state["batch_summary"] = summary

                    # ── History logging ──────────────────────────
                    log_query("batch", {"shipment_ids": ", ".join(shipment_ids)}, row_count=summary.get("total_processed"))
                    log_shipment_search(shipment_ids, source="batch")

            if st.session_state.get("batch_summary"):
                summary = st.session_state["batch_summary"]
                processor = st.session_state["batch_processor"]

                st.markdown(f'<div class="section-title">{t("batch.results_title")}</div>', unsafe_allow_html=True)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric(t("batch.total_processed"), summary["total_processed"])
                m2.metric(t("batch.successful"), summary["successful"])
                m3.metric(t("batch.failed"), summary["failed"])
                m4.metric(t("batch.elapsed_time"), f"{summary['elapsed_time']:.1f}s")

                summary_df = processor.generate_summary_report()
                st.dataframe(summary_df, width="stretch", height=360)

                st.markdown(f'<div class="section-title">{t("common.export")}</div>', unsafe_allow_html=True)
                exp1, exp2, _ = st.columns([1, 1, 4])
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                exp1.download_button(
                    label=t("batch.download_summary"),
                    data=df_to_csv_bytes(summary_df),
                    file_name=f"batch_summary_{timestamp}.csv",
                    mime="text/csv",
                    width="stretch",
                    key="batch_dl_csv",
                )

                detailed_sheets = processor.generate_detailed_report()
                import io as _io
                _excel_buf = _io.BytesIO()
                with pd.ExcelWriter(_excel_buf, engine="openpyxl") as _writer:
                    for _sheet_name, _sheet_df in detailed_sheets.items():
                        _sheet_df.to_excel(_writer, sheet_name=_sheet_name[:31], index=False)
                exp2.download_button(
                    label=t("batch.download_detailed"),
                    data=_excel_buf.getvalue(),
                    file_name=f"batch_detailed_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                    key="batch_dl_xlsx",
                )


# ═══════════════════════════════════════════════
#  TAB 3 — SQL QUERIES
# ═══════════════════════════════════════════════
if tab_sql is not None:
    with tab_sql:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            st.markdown(f'<div class="section-title">{t("sql.title")}</div>', unsafe_allow_html=True)
            st.caption(t("sql.caption"))

            # Query selector
            query_options = {k: v["label"] for k, v in QUERY_CATALOGUE.items()}
            selected_key = st.selectbox(
                t("sql.select_query"),
                options=list(query_options.keys()),
                format_func=lambda k: query_options[k],
                key="sql_query_select",
            )

            selected_meta = QUERY_CATALOGUE[selected_key]
            st.markdown(t("sql.description_label", description=selected_meta['description']))
            st.caption(t("sql.auto_runs_on", categories=', '.join(selected_meta.get('auto_on', []))))

            st.divider()

            # Parameter input
            param_key   = selected_meta["param_key"]
            param_label = selected_meta["param_label"]

            if param_key == "location_code":
                param_value = st.text_input(
                    param_label,
                    placeholder="e.g. HUB-ATL-01  or  NODE-11",
                    key="sql_param_loc",
                    help="Location Code — one at a time for this query",
                )
            elif param_key == "shipment_id":
                param_value = st.text_input(
                    param_label,
                    placeholder="e.g. DEMO-44542",
                    key="sql_param_shipment_id",
                    help="SHIPMENT_ID from DEMO_AUDIT",
                )
            else:
                param_value = st.text_input(
                    param_label,
                    placeholder="e.g. DEMO-SHP-2026-000001",
                    key="sql_param_shipment",
                )

            run_sql_col, _ = st.columns([1, 5])
            run_sql = run_sql_col.button(t("sql.execute_query_btn"), type="primary", width="stretch", key="btn_run_sql")

            if run_sql:
                if not param_value or not param_value.strip():
                    st.warning(t("sql.fill_param", param=param_label), icon="⚠️")
                else:
                    with st.spinner(t("sql.executing")):
                        try:
                            df_sql_result = selected_meta["fn"](
                                st.session_state["conn"],
                                param_value.strip(),
                            )
                            st.session_state["sql_result_df"]  = df_sql_result
                            st.session_state["sql_result_key"] = selected_key
                            st.session_state["sql_result_param"] = param_value.strip()

                            # ── History logging ──────────────────────────
                            log_query(f"sql:{selected_key}", {param_label: param_value.strip()}, row_count=len(df_sql_result))
                            if param_key == "shipment_id":
                                log_shipment_search(parse_ids(param_value), source=f"sql:{selected_key}")
                        except Exception as e:
                            st.error(t("sql.run_error", error=e), icon="❌")
                            log_query(f"sql:{selected_key}", {param_label: param_value.strip()}, error=str(e))
                            st.session_state["sql_result_df"] = None

            if st.session_state.get("sql_result_df") is not None:
                df_sql = st.session_state["sql_result_df"]
                rkey   = st.session_state.get("sql_result_key", "")
                rparam = st.session_state.get("sql_result_param", "")

                st.markdown(
                    f'<div class="section-title">{t("sql.result_title", query=query_options.get(rkey, rkey), param=rparam)}</div>',
                    unsafe_allow_html=True,
                )

                if df_sql.empty:
                    st.info(t("sql.no_results"), icon="🔎")
                else:
                    # Highlight ACTIVE_FLAG if location query
                    if rkey == "location_status" and "ACTIVE_FLAG" in df_sql.columns:
                        actv = str(df_sql.iloc[0].get("ACTIVE_FLAG", "")).upper()
                        if actv in ("1", "Y", "YES", "ACTIVE", "A"):
                            st.success(t("sql.location_active"))
                        else:
                            st.error(t("sql.location_inactive", value=actv), icon="🔴")

                    # Origin/destination validation badges
                    if rkey == "origin_dest_validation" and "ORIGIN_STATUS" in df_sql.columns:
                        for _, row in df_sql.iterrows():
                            orig_s = str(row.get("ORIGIN_STATUS", "")).upper()
                            dest_s = str(row.get("DESTINATION_STATUS", "")).upper()
                            status_icons = {
                                "ACTIVE":    ("🟢", "success"),
                                "INACTIVE":  ("🔴", "error"),
                                "NOT FOUND": ("❌", "error"),
                            }
                            orig_icon, orig_fn = status_icons.get(orig_s, ("❓", "warning"))
                            dest_icon, dest_fn = status_icons.get(dest_s, ("❓", "warning"))

                            c1, c2 = st.columns(2)
                            getattr(c1, orig_fn)(
                                f"{orig_icon} **ORIGIN** `{row.get('AUDIT_ORIGIN', '—')}` → {orig_s}"
                            )
                            getattr(c2, dest_fn)(
                                f"{dest_icon} **DESTINATION** `{row.get('AUDIT_DESTINATION', '—')}` → {dest_s}"
                            )

                    st.metric(t("sql.total_rows"), len(df_sql))
                    st.dataframe(df_sql, width="stretch", height=420)

                    # Export
                    st.markdown(f'<div class="section-title">{t("common.export")}</div>', unsafe_allow_html=True)
                    ts_sql = datetime.now().strftime("%Y%m%d_%H%M%S")
                    exp_s1, exp_s2, _ = st.columns([1, 1, 4])
                    exp_s1.download_button(
                        t("trouble.export_csv"),
                        data=df_to_csv_bytes(df_sql),
                        file_name=f"{rkey}_{rparam[:30]}_{ts_sql}.csv",
                        mime="text/csv",
                        width="stretch",
                        key="exp_sql_csv",
                    )
                    exp_s2.download_button(
                        t("trouble.export_excel"),
                        data=df_to_excel_bytes(df_sql),
                        file_name=f"{rkey}_{rparam[:30]}_{ts_sql}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width="stretch",
                        key="exp_sql_xlsx",
                    )

            st.divider()
            st.markdown(f'<div class="section-title">{t("sql.catalogue_title")}</div>', unsafe_allow_html=True)
            st.caption(t("sql.catalogue_caption"))
            catalogue_rows = [
                {
                    t("sql.col_query"): v["label"],
                    t("sql.col_description"): v["description"],
                    t("sql.col_parameter"): v["param_label"],
                    t("sql.col_auto_runs_on"): ", ".join(v.get("auto_on", [])),
                }
                for v in QUERY_CATALOGUE.values()
            ]
            st.dataframe(pd.DataFrame(catalogue_rows), width="stretch", hide_index=True)


# ═══════════════════════════════════════════════
#  TAB 4 — AI QUERY BUILDER
# ═══════════════════════════════════════════════
if tab_ai is not None:
    with tab_ai:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            # Pega o shipment_id atual se houver
            current_shipment = st.session_state.get("selected_shipment_id", None)
            render_ai_query_tab(st.session_state["conn"], current_shipment_id=current_shipment)


# ═══════════════════════════════════════════════
#  TAB 5 — SCHEMA MANAGER
# ═══════════════════════════════════════════════
if tab_schema is not None:
    with tab_schema:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            render_schema_manager_tab()


# ═══════════════════════════════════════════════
#  TAB 6 — KNOWLEDGE BASE
# ═══════════════════════════════════════════════
if tab_kb is not None:
    with tab_kb:
        render_knowledge_base_tab()


# ═══════════════════════════════════════════════
#  TAB 6b — PENDÊNCIAS (unmatched errors worklist)
# ═══════════════════════════════════════════════
if tab_pending is not None:
    with tab_pending:
        render_pending_tab()


# ═══════════════════════════════════════════════
#  TAB 7 — COPILOT CHAT
# ═══════════════════════════════════════════════
if tab_chat is not None:
    with tab_chat:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            render_copilot_chat_tab()


# ═══════════════════════════════════════════════
#  TAB 9 — CORRECTIONS & LEARNING
# ═══════════════════════════════════════════════
if tab_learn is not None:
    with tab_learn:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            st.markdown(f'<div class="section-title">{t("learning.title")}</div>', unsafe_allow_html=True)
            st.caption(t("learning.caption"))

            # ── Standalone correction-entry form ─────────────────
            # Shipment ID is OPTIONAL — you can give feedback on any existing KB
            # fix pattern (or free-typed error text) even if you never ran a
            # shipment search. The shipment ID is only used as a label in the
            # correction history log, not as a requirement to submit feedback.
            ship_history = get_shipment_history(limit=50)
            ship_options = [h["shipment_id"] for h in ship_history]

            try:
                kb_patterns_all = sorted(
                    load_troubleshoot_db()[COL_ERROR_PATTERN].dropna().astype(str).unique().tolist()
                )
            except Exception:
                kb_patterns_all = []

            col_l1, col_l2 = st.columns(2)
            with col_l1:
                if ship_options:
                    learn_shipment_id = st.selectbox(
                        f"{t('learning.select_shipment')} ({t('common.optional')})",
                        options=[""] + ship_options,
                        key="learn_shipment_select",
                    )
                else:
                    learn_shipment_id = st.text_input(
                        f"{t('learning.select_shipment')} ({t('common.optional')})", key="learn_shipment_text"
                    )
            with col_l2:
                if kb_patterns_all:
                    learn_pattern_pick = st.selectbox(
                        t("learning.select_existing_fix"),
                        options=[""] + kb_patterns_all,
                        key="learn_pattern_select",
                    )
                else:
                    learn_pattern_pick = ""

            learn_err_msg = st.text_input(
                t("learning.select_error"),
                value=learn_pattern_pick,
                key="learn_err_msg",
            )

            if learn_err_msg:
                render_feedback_widget(learn_shipment_id or "N/A", learn_err_msg, key_suffix="learn_tab")
            else:
                st.info(t("learning.no_data"), icon="ℹ️")

            st.divider()

            # ── Corrections history ──────────────────────────────
            st.markdown(f"### {t('learning.history_title')}")
            corrections = get_corrections_history(limit=100)
            if not corrections:
                st.caption(t("learning.history_empty"))
            else:
                df_corrections = pd.DataFrame(corrections)
                preferred_cols = [c for c in ["timestamp", "shipment_id", "error_message", "corrected",
                                               "correction_text", "kb_action", "ai_used"] if c in df_corrections.columns]
                other_cols = [c for c in df_corrections.columns if c not in preferred_cols]
                st.dataframe(df_corrections[preferred_cols + other_cols], width="stretch", hide_index=True)

            st.divider()

            # ── Requests & Approvals ──────────────────────────────
            auth_user = st.session_state.get("auth_user") or {}
            my_cws = auth_user.get("cws", "")
            pending_n = get_pending_count(my_cws) if my_cws else 0
            badge = f" 🔔 ({pending_n})" if pending_n else ""
            st.markdown(f"### {t('requests.tab_title')}{badge}")

            req_col_in, req_col_out = st.columns(2)

            with req_col_in:
                st.markdown(f"**{t('requests.incoming')}**")
                incoming = get_incoming(my_cws) if my_cws else []
                if not incoming:
                    st.caption(t("requests.no_incoming"))
                else:
                    for req in incoming:
                        status = req["status"]
                        status_label = {
                            "pending": t("requests.status_pending"),
                            "accepted": t("requests.status_accepted"),
                            "rejected": t("requests.status_rejected"),
                        }.get(status, status)
                        with st.expander(f"#{req['id']} — {req['request_type']} — {status_label}", expanded=(status == "pending")):
                            st.caption(f"🧑 {req['requester_name']} ({req['requester_cws']}) — {req['created_at'][:16]}")
                            st.markdown(f"**Pattern:** {req['err_pattern']}")
                            st.markdown(f"**Message:** {req['message']}")
                            if req.get("proposed_action"):
                                st.markdown(f"**Proposed fix:** {req['proposed_action']}")
                            if status == "pending":
                                reason = st.text_input(t("requests.reason_required"), key=f"reason_{req['id']}")
                                c_acc, c_rej = st.columns(2)
                                if c_acc.button(t("requests.accept"), key=f"accept_{req['id']}", type="primary", width="stretch"):
                                    try:
                                        respond_to_request(req["id"], my_cws, accept=True, reason=reason)
                                        if req["request_type"] in ("new_fix", "improvement") and req.get("proposed_action"):
                                            from troubleshooter.feedback_store import submit_correction as _submit
                                            _submit(
                                                shipment_id="request_" + str(req["id"]),
                                                err_msg=req["err_pattern"],
                                                correction_text=req["proposed_action"],
                                                corrected=True,
                                                cws=req["requester_cws"],
                                                user_name=req["requester_name"],
                                                force_direct=True,
                                            )
                                        st.success(t("requests.status_accepted"))
                                        st.rerun()
                                    except ValueError as e:
                                        st.error(str(e))
                                if c_rej.button(t("requests.reject"), key=f"reject_{req['id']}", width="stretch"):
                                    try:
                                        respond_to_request(req["id"], my_cws, accept=False, reason=reason)
                                        st.warning(t("requests.status_rejected"))
                                        st.rerun()
                                    except ValueError as e:
                                        st.error(str(e))
                            else:
                                st.caption(f"↩️ {req.get('response_reason', '')}")

            with req_col_out:
                st.markdown(f"**{t('requests.outgoing')}**")
                outgoing = get_outgoing(my_cws) if my_cws else []
                if not outgoing:
                    st.caption(t("requests.no_outgoing"))
                else:
                    for req in outgoing:
                        status = req["status"]
                        status_label = {
                            "pending": t("requests.status_pending"),
                            "accepted": t("requests.status_accepted"),
                            "rejected": t("requests.status_rejected"),
                        }.get(status, status)
                        with st.expander(f"#{req['id']} — {req['request_type']} — {status_label}", expanded=False):
                            st.caption(f"👤 {t('kb.owner')}: {req['owner_cws']} — {req['created_at'][:16]}")
                            st.markdown(f"**Pattern:** {req['err_pattern']}")
                            st.markdown(f"**Message:** {req['message']}")
                            if req.get("response_reason"):
                                st.caption(f"↩️ {req['response_reason']}")

            st.divider()
            st.caption(t("msg.moved_notice"))


# ═══════════════════════════════════════════════
#  TAB 9b — AUTONOMOUS FIX (admins + ILT Support)
# ═══════════════════════════════════════════════
if tab_autonomous_fix is not None:
    with tab_autonomous_fix:
        render_autonomous_fix_tab()


# ═══════════════════════════════════════════════
#  TAB 10 — ADMINISTRATION (admins only)
# ═══════════════════════════════════════════════
if tab_admin is not None:
    with tab_admin:
        # PSLD - Parts is a fully standalone app now (psld_app.py) — its
        # user-management flags/screens and AI section belong only in the
        # Portal's Central Admin Dashboard super-panel, not duplicated
        # here in ILT Troubleshooter's own local Administration tab.
        render_admin_tab(show_psld=False)


# ═══════════════════════════════════════════════
#  TAB 10b — LAB TEST (root admin / DEMOADMIN only)
# ═══════════════════════════════════════════════
if tab_lab is not None:
    with tab_lab:
        render_lab_test_tab()


# ═══════════════════════════════════════════════
#  TAB 11 — VISUAL QUERY BUILDER
# ═══════════════════════════════════════════════
if tab_qbuilder is not None:
    with tab_qbuilder:
        if not st.session_state["connected"]:
            st.info(t("tabs.connect_required"), icon="🔒")
        else:
            render_query_builder_tab()

# ═══════════════════════════════════════════════
#  TAB 12 — SQL GLOSSARY
# ═══════════════════════════════════════════════
if tab_glossary is not None:
    with tab_glossary:
        render_sql_glossary_tab()


# ═══════════════════════════════════════════════
#  HELP TAB
# ═══════════════════════════════════════════════
if tab_help is not None:
    with tab_help:
        render_help_tab()

