"""
ui/psld_parts_tab.py
======================
EXPERIMENTAL (Lab Test tab -> "📦 PSLD - Parts" sub-menu) — a dedicated
sandbox for the PSLD - Parts team's own ticket-resolution assistant.

This is a SEPARATE system from the main ILT Troubleshooter (which is
about ACME_OMS.DEMO_AUDIT DB errors), adapted to PSLD - Parts'
actual workflow:
  - Resolutions are curated by the team as PDF runbooks (+ title, long
    description, step-by-step), not DB error-code patterns.
  - New problems arrive as ServiceNow ticket NUMBERS, not shipment IDs.
  - Matching uses similarity + a local self-learning layer (see
    troubleshooter/psld_semantic_engine.py) against both the curated KB
    and the team's own already-fetched Resolved/Closed/Cancelled
    tickets, instead of an exact ERR_MSG lookup.

ServiceNow LOGIN itself (Azure AD device-code flow, Basic Auth, cookie
and browser-session experiments) intentionally stays in the separate
"🔐 ServiceNow Login (testing)" sub-menu of the Lab Test tab — it's
shared infrastructure another team (not just PSLD - Parts) may end up
using, and today it's still not production-usable (see that sub-menu's
"CONFIRMED DEAD END" notes for the cookie-based options). This tab reads
the Azure AD token/cached tickets from session_state IF that other
sub-menu already produced them in the same session, but never renders
any login UI itself.
"""
import os
import platform

import streamlit as st

from auth import user_store
from i18n import t
from integrations import servicenow_poc
from troubleshooter import (
    psld_abend_registry,
    psld_mock_tickets,
    psld_review_queue,
    psld_semantic_engine,
    servicenow_resolution_kb,
)
from troubleshooter import document_viewer
from utils.teams_link import teams_chat_link


def _current_user_cws() -> str:
    user = st.session_state.get("auth_user") or {}
    return (user.get("cws", "") or "?").strip().upper()


def _render_about() -> None:
    st.markdown(f"#### {t('psld.title')}")
    st.caption(t("psld.caption"))
    status = psld_semantic_engine.semantic_status()
    if status["available"]:
        st.success(t("psld.semantic_ok", model=status["model"]), icon="🧠")
    else:
        st.warning(t("psld.semantic_unavailable", reason=status.get("reason", "?")), icon="⚠️")


def render_psld_parts_tab() -> None:
    current_cws = _current_user_cws()
    is_reviewer = user_store.is_parts_reviewer(current_cws)

    # Per-user screen access control (see auth/user_store.py's
    # SCREEN_REGISTRY) — admins always see everything; everyone else can
    # have individual PSLD screens hidden from the Central Admin
    # Dashboard / Administration tab without losing PSLD - Parts access
    # as a whole.
    screen_defs = [
        ("psld.analyze", t("psld.tab_analyze"), _render_analyze),
        ("psld.kb", t("psld.tab_kb"), _render_kb_manage),
        ("psld.mock", t("psld.tab_mock"), _render_mock_data),
        ("psld.abend", t("psld.tab_abend"), _render_abend_registry),
        ("psld.stats", t("psld.tab_stats"), _render_stats),
    ]
    if is_reviewer:
        screen_defs.append(("psld.review", t("psld.tab_review"), _render_review_queue))

    screen_defs = [
        (key, label, fn) for key, label, fn in screen_defs
        if user_store.is_screen_enabled(current_cws, key)
    ]
    # "About" is always available, regardless of per-screen access — it's
    # just descriptive text + the self-learning model status (previously
    # shown as clutter at the very top of every screen; moved here on
    # request so the main working tabs stay clean).
    screen_defs.append(("psld.about", "About", _render_about))

    if not screen_defs:
        st.warning(
            "Your account currently has no PSLD - Parts screens enabled. "
            "Ask an administrator to grant access.",
            icon="🔒",
        )
        return

    tabs = st.tabs([label for _, label, _fn in screen_defs])
    for tab, (_key, _label, fn) in zip(tabs, screen_defs):
        with tab:
            fn()



def _render_analyze() -> None:
    st.caption(t("psld.analyze_caption"))

    token_result = st.session_state.get("lab_sn_token_result")
    mock_tickets_all = psld_mock_tickets.list_tickets()
    mock_open = [tk for tk in mock_tickets_all if tk["state"] in psld_mock_tickets.OPEN_STATES]
    mock_resolved = [tk for tk in mock_tickets_all if tk["state"] in psld_mock_tickets.RESOLVED_STATES]

    source_options = [t("psld.source_real"), t("psld.source_mock")]
    # Default to mock data when there's nothing real to work with yet —
    # this is exactly the "dry-run without ServiceNow" use case.
    default_idx = 0 if token_result else (1 if mock_tickets_all else 0)
    ticket_source = st.radio(
        t("psld.ticket_source_label"), options=source_options, index=default_idx,
        key="psld_ticket_source", horizontal=True,
    )
    using_mock = ticket_source == t("psld.source_mock")

    if using_mock:
        st.info(t("psld.mock_dry_run_hint"), icon="🧪")
        if not mock_open:
            st.caption(t("psld.mock_no_open_tickets"))
        else:
            mock_pick = st.selectbox(
                t("psld.mock_pick_open_ticket"),
                options=mock_open,
                format_func=lambda tk: f"{tk['number']} — {tk['short_description']}",
                key="psld_mock_pick",
            )
            if st.button(t("psld.mock_load_button"), key="psld_mock_load"):
                st.session_state["psld_ticket_number"] = mock_pick["number"]
                st.session_state["psld_ticket_text"] = (
                    f"{mock_pick['short_description']}\n\n{mock_pick['description']}"
                ).strip()
                st.rerun()

    ticket_number = st.text_input(t("psld.ticket_number"), key="psld_ticket_number", placeholder="TASK0012345")

    if not using_mock:
        if token_result:
            if st.button(t("psld.fetch_from_sn"), key="psld_fetch_ticket"):
                with st.spinner(t("lab.fetching")):
                    try:
                        ticket = servicenow_poc.fetch_ticket_by_number_aad(
                            ticket_number, token_result["access_token"]
                        )
                        if ticket:
                            text = f"{ticket.get('short_description', '')}\n\n{ticket.get('description', '')}".strip()
                            st.session_state["psld_ticket_text"] = text
                            st.success(t("psld.fetch_ticket_success"))
                        else:
                            st.warning(t("psld.fetch_ticket_not_found"), icon="⚠️")
                    except Exception as e:
                        st.error(t("lab.fetch_failed", reason=str(e)))
        else:
            st.caption(t("psld.no_sn_login_hint"))

    ticket_text = st.text_area(
        t("psld.ticket_text"),
        key="psld_ticket_text",
        height=140,
        placeholder=t("psld.ticket_text_placeholder"),
    )

    if st.button(t("psld.find_button"), type="primary", key="psld_find"):
        if not ticket_text.strip():
            st.warning(t("psld.missing_text"), icon="⚠️")
        else:
            query = f"{ticket_number} {ticket_text}".strip()
            # Ask for more TF-IDF candidates than we'll finally show, so
            # the semantic/self-learning re-rank has real room to promote
            # an entry that TF-IDF alone ranked lower (few shared literal
            # words but similar meaning / confirmed-similar history).
            tfidf_matches = servicenow_resolution_kb.find_similar(query, top_n=20)
            blended = psld_semantic_engine.blended_kb_matches(query, tfidf_matches)
            st.session_state["psld_kb_matches"] = blended[:3]

            past_tickets = mock_resolved if using_mock else (st.session_state.get("lab_sn_tickets") or [])
            st.session_state["psld_ticket_matches"] = servicenow_resolution_kb.find_similar_tickets(
                query, past_tickets, top_n=3,
            )
            st.session_state["psld_ticket_matches_source"] = "mock" if using_mock else "real"
            st.session_state["psld_last_query"] = query

    kb_matches = st.session_state.get("psld_kb_matches")
    if kb_matches is not None:
        st.markdown(f"### {t('psld.resolution_matches')}")
        st.caption(t("psld.resolution_matches_caption"))
        shown = [(e, s, b) for e, s, b in kb_matches if s > 0.05]
        if not shown:
            st.info(t("psld.no_matches"))
        medals = ["🥇", "🥈", "🥉"]
        for rank, (entry, score, breakdown) in enumerate(shown):
            score_pct = round(score * 100)
            score_color = "#155724" if score_pct >= 75 else ("#856404" if score_pct >= 45 else "#721c24")
            score_bg = "#d4edda" if score_pct >= 75 else ("#fff3cd" if score_pct >= 45 else "#f8d7da")
            with st.container(border=True):
                head_l, head_r = st.columns([4, 1])
                with head_l:
                    st.markdown(f"**{medals[rank] if rank < len(medals) else '•'} {entry['title']}**")
                with head_r:
                    st.markdown(
                        f'<div style="text-align:right;">'
                        f'<span style="background:{score_bg};color:{score_color};padding:0.2rem 0.6rem;'
                        f'border-radius:12px;font-weight:600;font-size:0.9rem;">{score_pct}%</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                st.caption(
                    t(
                        "psld.match_breakdown",
                        tfidf=round(breakdown["tfidf"] * 100),
                        semantic=round(breakdown["semantic"] * 100),
                        feedback=round(breakdown["feedback"] * 100),
                    )
                )
                if entry.get("description_long"):
                    st.write(entry["description_long"])

                # ── Proposed solution — highlighted, same "this is the
                # answer" visual treatment as the main ILT Troubleshooter's
                # recommended-action box, whether it came from a manually
                # typed step-by-step or from an auto-extracted attachment.
                proposed = entry.get("steps", "").strip()
                if proposed:
                    st.markdown(f"**{t('psld.proposed_solution')}:**")
                    st.success(proposed, icon="✅")
                if entry.get("key_points"):
                    if not proposed:
                        st.markdown(f"**{t('psld.proposed_solution')}:**")
                    with st.container():
                        st.markdown(f"**{t('psld.key_points_title')}:**")
                        for kp in entry["key_points"]:
                            st.markdown(f"- {kp}")

                _render_resolution_file_buttons(entry, key_prefix="psld_match")
                if st.button(t("psld.confirm_match"), key=f"psld_confirm_{entry['id']}"):
                    psld_semantic_engine.record_feedback(
                        ticket_text=st.session_state.get("psld_last_query", ""),
                        entry_id=entry["id"],
                        entry_title=entry["title"],
                        confirmed_by=_current_user_cws(),
                    )
                    st.success(t("psld.confirm_saved"))

    ticket_matches = st.session_state.get("psld_ticket_matches")
    if ticket_matches is not None:
        st.markdown(f"### {t('psld.ticket_matches')}")
        matches_from_mock = st.session_state.get("psld_ticket_matches_source") == "mock"
        if matches_from_mock:
            st.caption(t("psld.ticket_matches_mock_caption"))
        if not matches_from_mock and not st.session_state.get("lab_sn_tickets"):
            st.caption(t("psld.no_tickets_cached"))
        else:
            shown_tickets = [(tk, s) for tk, s in ticket_matches if s > 0.05]
            if not shown_tickets:
                st.info(t("psld.no_matches"))
            medals = ["🥇", "🥈", "🥉"]
            for rank, (tk, score) in enumerate(shown_tickets):
                score_pct = round(score * 100)
                score_color = "#155724" if score_pct >= 75 else ("#856404" if score_pct >= 45 else "#721c24")
                score_bg = "#d4edda" if score_pct >= 75 else ("#fff3cd" if score_pct >= 45 else "#f8d7da")
                with st.container(border=True):
                    tcol_l, tcol_r = st.columns([4, 1])
                    with tcol_l:
                        st.markdown(f"{medals[rank] if rank < len(medals) else '•'} **{tk.get('number', '?')}** — {tk.get('short_description', '')}")
                    with tcol_r:
                        st.markdown(
                            f'<div style="text-align:right;">'
                            f'<span style="background:{score_bg};color:{score_color};padding:0.2rem 0.6rem;'
                            f'border-radius:12px;font-weight:600;font-size:0.9rem;">{score_pct}%</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    if tk.get("description"):
                        st.caption(tk["description"][:300])


def _render_mock_data() -> None:
    st.caption(t("psld.mock_caption"))

    tickets = psld_mock_tickets.list_tickets()

    top1, top2, top3 = st.columns([2, 1, 1])
    with top1:
        st.metric(t("psld.mock_total"), len(tickets))
    with top2:
        if st.button(t("psld.mock_seed_button"), key="psld_mock_seed", width="stretch"):
            created = psld_mock_tickets.seed_sample_data()
            if created:
                st.success(t("psld.mock_seed_success", count=len(created)))
                st.rerun()
            else:
                st.info(t("psld.mock_seed_noop"))
    with top3:
        if st.button(t("psld.mock_clear_button"), key="psld_mock_clear", width="stretch"):
            st.session_state["psld_mock_confirm_clear"] = True

    if st.session_state.get("psld_mock_confirm_clear"):
        wcol1, wcol2, wcol3 = st.columns([3, 1, 1])
        wcol1.warning(t("psld.mock_clear_confirm"), icon="⚠️")
        if wcol2.button(t("kbtab.delete_confirm_yes"), key="psld_mock_clear_yes", type="primary", width="stretch"):
            n = psld_mock_tickets.clear_all()
            st.session_state["psld_mock_confirm_clear"] = False
            st.success(t("psld.mock_clear_success", count=n))
            st.rerun()
        if wcol3.button(t("kbtab.delete_confirm_no"), key="psld_mock_clear_no", width="stretch"):
            st.session_state["psld_mock_confirm_clear"] = False
            st.rerun()

    st.divider()

    add_manual_tab, add_excel_tab = st.tabs([t("psld.mock_mode_manual"), t("psld.mock_mode_excel")])

    with add_manual_tab:
        with st.form("psld_mock_add_form", clear_on_submit=True):
            mcol1, mcol2 = st.columns(2)
            with mcol1:
                m_number = st.text_input(t("psld.mock_field_number"), placeholder="INC0012345")
            with mcol2:
                m_state = st.selectbox(t("psld.mock_field_state"), options=psld_mock_tickets.STATE_OPTIONS)
            m_short = st.text_input(t("psld.mock_field_short_desc"))
            m_desc = st.text_area(t("psld.mock_field_desc"), height=100)
            add_clicked = st.form_submit_button(t("psld.mock_add_button"), type="primary")

        if add_clicked:
            try:
                psld_mock_tickets.add_ticket(
                    number=m_number, state=m_state, short_description=m_short,
                    description=m_desc, created_by=_current_user_cws(),
                )
                st.success(t("psld.mock_add_success"))
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    with add_excel_tab:
        st.caption(t("psld.mock_excel_caption"))
        excel_file = st.file_uploader(
            t("psld.mock_excel_upload"), type=["xlsx", "xls", "csv"], key="psld_mock_excel_file",
        )
        if excel_file:
            import pandas as pd
            try:
                if excel_file.name.endswith(".csv"):
                    df_import = pd.read_csv(excel_file)
                else:
                    df_import = pd.read_excel(excel_file)
            except Exception as e:
                st.error(t("psld.mock_excel_read_error", error=e))
                df_import = None

            if df_import is not None and not df_import.empty:
                st.dataframe(df_import.head(5), width="stretch")
                st.caption(t("kbtab.showing_rows", count=len(df_import)))

                st.markdown(f"**{t('psld.mock_excel_mapping_title')}**")
                st.caption(t("psld.mock_excel_mapping_caption"))
                columns = list(df_import.columns)
                guessed = psld_mock_tickets.guess_column_mapping(columns)

                def _col_index(field: str) -> int:
                    # +1 because option 0 is always the "— none —" placeholder
                    guess = guessed.get(field)
                    return (columns.index(guess) + 1) if guess in columns else 0

                options_none = [t("psld.mock_excel_col_none")] + columns
                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    map_number = st.selectbox(t("psld.mock_field_number"), options=options_none, index=_col_index("number"), key="psld_map_number")
                    map_short = st.selectbox(t("psld.mock_field_short_desc"), options=options_none, index=_col_index("short_description"), key="psld_map_short")
                with mcol2:
                    map_state = st.selectbox(t("psld.mock_field_state"), options=options_none, index=_col_index("state"), key="psld_map_state")
                    map_desc = st.selectbox(t("psld.mock_field_desc"), options=options_none, index=_col_index("description"), key="psld_map_desc")

                ready = map_number != t("psld.mock_excel_col_none") and map_short != t("psld.mock_excel_col_none")
                if not ready:
                    st.warning(t("psld.mock_excel_mapping_required"), icon="⚠️")
                else:
                    detect_abends = st.checkbox(t("psld.mock_excel_detect_abends"), value=True, key="psld_mock_excel_detect_abends")
                    st.caption(t("psld.mock_excel_detect_abends_caption"))
                    link_kb_filenames = st.checkbox(t("psld.mock_excel_link_kb_filenames"), value=True, key="psld_mock_excel_link_kb")
                    st.caption(t("psld.mock_excel_link_kb_filenames_caption"))
                    if st.button(t("psld.mock_excel_import_button"), type="primary", key="psld_mock_excel_import"):
                        mapping = {
                            "number": map_number,
                            "short_description": map_short,
                            "state": map_state if map_state != t("psld.mock_excel_col_none") else None,
                            "description": map_desc if map_desc != t("psld.mock_excel_col_none") else None,
                        }
                        extra_mapping = psld_mock_tickets.guess_extra_column_mapping(columns)
                        result = psld_mock_tickets.import_from_excel(
                            df_import.to_dict("records"), mapping, created_by=_current_user_cws(),
                            extra_column_mapping=extra_mapping,
                        )
                        st.success(
                            t(
                                "psld.mock_excel_import_result",
                                created=result["created"],
                                skipped_existing=result["skipped_existing"],
                                skipped_invalid=result["skipped_invalid"],
                            )
                        )

                        if detect_abends:
                            detected = 0
                            need_program = 0
                            for row in df_import.to_dict("records"):
                                number = str(row.get(map_number, "") or "").strip()
                                short_desc = str(row.get(map_short, "") or "")
                                resolution_notes = str(row.get(map_desc, "") or "") if map_desc != t("psld.mock_excel_col_none") else ""
                                entry = psld_abend_registry.ingest_ticket_for_abend(
                                    ticket_number=number, short_description=short_desc,
                                    resolution_notes=resolution_notes, created_by=_current_user_cws(),
                                )
                                if entry:
                                    detected += 1
                                    if psld_abend_registry.status_of(entry) == "pending_program":
                                        need_program += 1
                            if detected:
                                st.success(t("psld.mock_excel_abend_detect_result", detected=detected, pending=need_program), icon="🚨")
                            else:
                                st.caption(t("psld.mock_excel_abend_detect_none"))

                        if link_kb_filenames:
                            linked = 0
                            for row in df_import.to_dict("records"):
                                short_desc = str(row.get(map_short, "") or "")
                                resolution_notes = str(row.get(map_desc, "") or "") if map_desc != t("psld.mock_excel_col_none") else ""
                                if not resolution_notes.strip():
                                    continue
                                kb_hits = servicenow_resolution_kb.find_entries_mentioning_filename(resolution_notes)
                                for kb_entry in kb_hits:
                                    psld_semantic_engine.record_feedback(
                                        ticket_text=f"{short_desc}\n\n{resolution_notes}".strip(),
                                        entry_id=kb_entry["id"],
                                        entry_title=kb_entry["title"],
                                        confirmed_by=t("psld.mock_excel_link_kb_auto_confirmed_by"),
                                    )
                                    linked += 1
                            if linked:
                                st.success(t("psld.mock_excel_link_kb_result", linked=linked), icon="🔗")

                        st.rerun()

    st.divider()
    st.markdown(f"#### {t('psld.mock_list_title')}")
    if not tickets:
        st.info(t("psld.mock_empty"), icon="ℹ️")
        return

    state_filter = st.selectbox(
        t("psld.mock_filter_state"),
        options=[t("psld.mock_filter_all")] + psld_mock_tickets.STATE_OPTIONS,
        key="psld_mock_state_filter",
    )
    filtered = tickets if state_filter == t("psld.mock_filter_all") else [
        tk for tk in tickets if tk["state"] == state_filter
    ]

    state_icons = {
        "New": "🆕", "In Progress": "🔧", "On Hold": "⏸️",
        "Resolved": "✅", "Closed": "🔒", "Cancelled": "🚫",
    }
    for tk in filtered:
        icon = state_icons.get(tk["state"], "❔")
        with st.expander(f"{icon} {tk['number']} — {tk['short_description']} ({tk['state']})"):
            st.write(tk.get("description", ""))
            extra = tk.get("extra") or {}
            if extra:
                st.caption(" · ".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in extra.items()))
            st.caption(f"{tk.get('created_by', '?')} · {tk.get('created_at', '')}")
            if st.button(t("psld.delete"), key=f"psld_mock_del_{tk['id']}"):
                psld_mock_tickets.delete_ticket(tk["id"])
                st.rerun()


def _render_kb_manage() -> None:
    st.caption(t("psld.kb_manage_caption"))

    kb_overview_stats = servicenow_resolution_kb.kb_stats()
    if kb_overview_stats["docs_folder_import"] > 0:
        st.success(
            t(
                "psld.kb_docsimport_overview",
                count=kb_overview_stats["docs_folder_import"],
                categories=kb_overview_stats["categories"],
            ),
            icon="📚",
        )

    with st.container(border=True):
        st.markdown(f"**{t('psld.resdocs_scan_title')}**")
        st.caption(t("psld.resdocs_scan_caption", folder=str(servicenow_resolution_kb.RESOLUTION_DOCS_INBOX)))
        pending_files = servicenow_resolution_kb.scan_resolution_docs_inbox()
        new_files = servicenow_resolution_kb.pending_inbox_files()
        scol1, scol2 = st.columns([3, 1])
        with scol1:
            if pending_files:
                st.caption(t("psld.resdocs_scan_found", total=len(pending_files), new=len(new_files)))
            else:
                st.caption(t("psld.resdocs_scan_empty"))
        with scol2:
            if st.button(t("psld.resdocs_scan_button"), key="psld_resdocs_scan", type="primary", disabled=not new_files, width="stretch"):
                result = servicenow_resolution_kb.bulk_import_from_folder(created_by=_current_user_cws())
                st.success(
                    t(
                        "psld.resdocs_scan_result",
                        created=result["created"],
                        skipped_existing=result["skipped_existing"],
                        failed=len(result["failed"]),
                    )
                )
                for filename, reason in result["failed"]:
                    st.warning(f"{filename}: {reason}", icon="⚠️")
                st.rerun()

    with st.form("psld_kb_add_form", clear_on_submit=True):
        title = st.text_input(t("psld.field_title"), key="psld_new_title")
        description_long = st.text_area(t("psld.field_description"), height=120, key="psld_new_description")
        steps = st.text_area(
            t("psld.field_steps"), height=160, help=t("psld.steps_help"), key="psld_new_steps"
        )
        pdf_file = st.file_uploader(
            t("psld.pdf_upload"), type=["pdf", "docx", "doc", "xlsx", "xls", "txt"], key="psld_new_pdf",
            help=t("psld.pdf_upload_help"),
        )
        submitted = st.form_submit_button(t("psld.add_button"), type="primary")

    if submitted:
        try:
            new_entry = servicenow_resolution_kb.add_entry(
                title=title,
                description_long=description_long,
                steps=steps,
                created_by=_current_user_cws(),
                pdf_bytes=pdf_file.getvalue() if pdf_file else None,
                pdf_original_name=pdf_file.name if pdf_file else None,
            )
            if new_entry.get("extraction_error"):
                st.warning(
                    t("psld.extraction_failed", reason=new_entry["extraction_error"]),
                    icon="⚠️",
                )
            elif new_entry.get("key_points"):
                st.success(t("psld.add_success"))
                with st.expander(t("psld.key_points_preview", count=len(new_entry["key_points"])), expanded=True):
                    for kp in new_entry["key_points"]:
                        st.markdown(f"- {kp}")
            else:
                st.success(t("psld.add_success"))
            st.rerun()
        except ValueError as e:
            st.error(str(e))

    st.divider()
    entries = servicenow_resolution_kb.list_entries()
    categories = servicenow_resolution_kb.list_categories()

    fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
    with fcol1:
        category_options = [t("psld.kb_filter_all_categories")] + categories
        selected_category = st.selectbox(t("psld.kb_filter_category"), category_options, key="psld_kb_filter_category")
    with fcol2:
        search_text = st.text_input(t("psld.kb_filter_search"), key="psld_kb_filter_search", placeholder=t("psld.kb_filter_search_placeholder"))
    with fcol3:
        show_limit = st.selectbox(t("psld.kb_filter_show"), [25, 50, 100, 250], key="psld_kb_filter_limit")

    filtered = entries
    if selected_category != t("psld.kb_filter_all_categories"):
        filtered = [e for e in filtered if e.get("category") == selected_category]
    if search_text.strip():
        needle = search_text.strip().lower()
        filtered = [
            e for e in filtered
            if needle in (e.get("title") or "").lower()
            or needle in (e.get("description_long") or "").lower()
            or needle in " ".join(e.get("key_points") or []).lower()
        ]

    st.caption(t("psld.entries_count_filtered", shown=min(len(filtered), show_limit), filtered=len(filtered), total=len(entries)))
    for entry in filtered[:show_limit]:
        cat_badge = f" · 📁 {entry['category']}" if entry.get("category") else ""
        with st.expander(f"📄 {entry['title']}{cat_badge}"):
            if entry.get("description_long"):
                st.write(entry["description_long"])
            if entry.get("steps", "").strip():
                st.markdown(f"**{t('psld.field_steps')}:**")
                st.write(entry["steps"])
            if entry.get("key_points"):
                st.markdown(f"**{t('psld.key_points_title')}:**")
                for kp in entry["key_points"]:
                    st.markdown(f"- {kp}")
            if entry.get("extraction_error"):
                st.caption(f"⚠️ {t('psld.extraction_failed', reason=entry['extraction_error'])}")
            _render_resolution_file_buttons(entry, key_prefix="psld_kb")
            if entry.get("source") == "folder_import":
                st.caption(t("psld.resdocs_entry_source_badge"))
            elif entry.get("source") == "docs_folder_import":
                st.caption(t("psld.docsimport_entry_source_badge", relpath=entry.get("source_relpath", "")))
            st.caption(f"{entry.get('created_by', '?')} · {entry.get('created_at', '')}")
            if st.button(t("psld.delete"), key=f"psld_del_{entry['id']}"):
                servicenow_resolution_kb.delete_entry(entry["id"])
                st.rerun()


def _mime_for(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "txt": "text/plain",
    }.get(ext, "application/octet-stream")


def _render_resolution_file_buttons(entry: dict, key_prefix: str) -> None:
    """Renders the ways an analyst can follow a matched resolution's
    attached file: (1) an inline "👁️ View" preview (PDF rendered
    natively in-browser, DOCX converted to HTML) shown right below the
    buttons, height-capped to stay inside the page — see
    troubleshooter/document_viewer.py; (2) a normal download (always
    works, regardless of where Streamlit is running — the browser
    handles it); (3) an "Open locally" button that uses os.startfile()
    to pop the file open in Word/Acrobat in its OWN window — ONLY does
    anything useful if this Streamlit process is running on the
    analyst's own machine (if this app is ever centrally hosted for
    multiple remote users, os.startfile() would open the file on the
    SERVER, not the analyst's screen, so the button is labeled
    accordingly and never presented as the only way to get the file);
    and (4) a "🔗 Open in new tab" link — an ALTERNATIVE to the inline
    preview for when the height-capped inline view feels cramped: opens
    the same content (PDF, or DOCX converted to HTML) as a full,
    standalone browser tab using the browser's own native PDF viewer
    (proper zoom/search/print) instead of Streamlit's embedded iframe."""
    from_path = servicenow_resolution_kb.attachment_path_for(entry)
    if not from_path:
        return
    original_name = entry.get("pdf_original_name") or from_path.name

    bcol1, bcol2, bcol3, bcol4 = st.columns(4)
    with bcol1:
        view_key = f"{key_prefix}_view_open_{entry['id']}"
        if st.button(t("psld.view_inline"), key=f"{key_prefix}_view_btn_{entry['id']}", width="stretch", disabled=not document_viewer.can_view_inline(original_name)):
            st.session_state[view_key] = not st.session_state.get(view_key, False)
    with bcol2:
        st.download_button(
            t("psld.download_pdf"),
            data=from_path.read_bytes(),
            file_name=original_name,
            mime=_mime_for(from_path.name),
            key=f"{key_prefix}_dl_{entry['id']}",
            width="stretch",
        )
    with bcol3:
        if platform.system() == "Windows":
            if st.button(t("psld.open_locally"), key=f"{key_prefix}_open_{entry['id']}", width="stretch", help=t("psld.open_locally_help")):
                try:
                    os.startfile(str(from_path))  # noqa: S606 - local-only helper, see docstring
                    st.success(t("psld.open_locally_success"))
                except Exception as e:
                    st.error(t("psld.open_locally_failed", reason=str(e)))
    with bcol4:
        if document_viewer.can_view_inline(original_name):
            view_url, error_reason = document_viewer.build_new_tab_url(from_path.read_bytes(), original_name)
            if view_url:
                st.markdown(
                    f'<a href="{view_url}" target="_blank" rel="noopener" '
                    f'style="display:inline-block;width:100%;box-sizing:border-box;text-align:center;'
                    f'padding:0.4rem 0.75rem;border:1px solid rgba(250,250,250,0.2);border-radius:0.5rem;'
                    f'text-decoration:none;color:inherit;font-size:0.9rem;">{t("psld.view_new_tab")}</a>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption(f"⚠️ {error_reason}")

    if st.session_state.get(f"{key_prefix}_view_open_{entry['id']}"):
        html_content, error_reason = document_viewer.render_inline(from_path.read_bytes(), original_name)
        if html_content:
            st.components.v1.html(html_content, height=920, scrolling=True)
        else:
            st.warning(error_reason, icon="⚠️")


def _render_abend_registry() -> None:
    st.caption(t("psld.abend_caption"))

    eligible_users = user_store.list_psld_parts_users()
    if not eligible_users:
        st.warning(t("psld.abend_no_eligible_users"), icon="⚠️")

    all_abends = psld_abend_registry.list_abends()
    pending_program = psld_abend_registry.list_pending_program_abends()
    complete_or_pending_contact = [a for a in all_abends if psld_abend_registry.status_of(a) != "pending_program"]

    m1, m2, m3 = st.columns(3)
    m1.metric(t("psld.abend_total"), len(all_abends))
    m2.metric(t("psld.abend_pending_count"), len(pending_program))
    m3.metric(t("psld.abend_programs_known"), len(psld_abend_registry.list_programs()))

    st.divider()

    reg_tab, pending_tab = st.tabs(
        [t("psld.abend_tab_registry"), f"{t('psld.abend_tab_pending')} ({len(pending_program)})"]
    )

    with reg_tab:
        _render_abend_registry_list(complete_or_pending_contact, eligible_users)
    with pending_tab:
        _render_abend_pending_list(pending_program, eligible_users)


def _render_abend_registry_list(abends: list, eligible_users: list) -> None:
    with st.expander(t("psld.abend_add_title"), expanded=not abends):
        with st.form("psld_abend_add_form", clear_on_submit=True):
            fcol1, fcol2 = st.columns(2)
            with fcol1:
                a_number = st.text_input(t("psld.abend_field_number"), placeholder="S0C7")
            with fcol2:
                a_program = st.text_input(t("psld.abend_field_program"), placeholder="PGMABC01")

            a_resolution = st.text_area(t("psld.abend_field_resolution"), height=100)

            if eligible_users:
                contact_options = {
                    f"{u.get('name', '?')} ({u['cws']})": u["cws"] for u in eligible_users
                }
                contact_label = st.selectbox(t("psld.abend_field_contact"), options=list(contact_options.keys()))
                a_contact_cws = contact_options.get(contact_label, "")
            else:
                st.info(t("psld.abend_no_eligible_users_hint"), icon="ℹ️")
                a_contact_cws = ""

            add_clicked = st.form_submit_button(t("psld.abend_add_button"), type="primary", disabled=not eligible_users)

        if add_clicked:
            try:
                psld_abend_registry.add_abend(
                    abend_number=a_number, abend_program=a_program, resolution=a_resolution,
                    responsible_cws=a_contact_cws, created_by=_current_user_cws(),
                )
                st.success(t("psld.abend_add_success"))
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.markdown(f"#### {t('psld.abend_list_title')}")

    fcol1, fcol2 = st.columns([3, 2])
    with fcol1:
        query = st.text_input(t("psld.abend_search"), key="psld_abend_search", placeholder=t("psld.abend_search_placeholder"))
    with fcol2:
        program_options = [t("psld.abend_filter_all_programs")] + psld_abend_registry.list_programs()
        program_filter = st.selectbox(t("psld.abend_filter_program"), options=program_options, key="psld_abend_program_filter")

    shown = psld_abend_registry.search_abends(query) if query else abends
    shown = [a for a in shown if psld_abend_registry.status_of(a) != "pending_program"]
    if program_filter != t("psld.abend_filter_all_programs"):
        shown = psld_abend_registry.filter_by_program(shown, program_filter)

    if not shown:
        st.info(t("psld.abend_empty"), icon="ℹ️")
        return

    users_by_cws = {u["cws"]: u for u in user_store.list_users()}
    for a in shown:
        with st.container(border=True):
            hcol1, hcol2, hcol3 = st.columns([4, 1, 1])
            with hcol1:
                title = f"**🚨 {a['abend_number']}** · {t('psld.abend_field_program')}: `{a['abend_program']}`"
                if a.get("ticket_number"):
                    title += f" · 🎫 {a['ticket_number']}"
                st.markdown(title)
            with hcol2:
                edit_key = f"psld_abend_editing_{a['id']}"
                if st.button(t("psld.edit"), key=f"psld_abend_edit_btn_{a['id']}", width="stretch"):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                    st.rerun()
            with hcol3:
                if st.button(t("psld.delete"), key=f"psld_abend_del_{a['id']}", width="stretch"):
                    psld_abend_registry.delete_abend(a["id"])
                    st.rerun()

            if st.session_state.get(f"psld_abend_editing_{a['id']}", False):
                with st.form(f"psld_abend_edit_form_{a['id']}"):
                    st.caption(t("psld.abend_edit_title"))
                    ecol1, ecol2 = st.columns(2)
                    with ecol1:
                        e_number = st.text_input(t("psld.abend_field_number"), value=a.get("abend_number", ""), key=f"psld_abend_edit_number_{a['id']}")
                    with ecol2:
                        e_program = st.text_input(t("psld.abend_field_program"), value=a.get("abend_program", ""), key=f"psld_abend_edit_program_{a['id']}")
                    e_resolution = st.text_area(t("psld.abend_field_resolution"), value=a.get("resolution", ""), height=100, key=f"psld_abend_edit_resolution_{a['id']}")

                    if eligible_users:
                        contact_options = {f"{u.get('name', '?')} ({u['cws']})": u["cws"] for u in eligible_users}
                        cws_to_label = {v: k for k, v in contact_options.items()}
                        current_label = cws_to_label.get(a.get("responsible_cws", ""))
                        options_list = [t("psld.abend_contact_none")] + list(contact_options.keys())
                        default_idx = options_list.index(current_label) if current_label in options_list else 0
                        e_contact_label = st.selectbox(
                            t("psld.abend_field_contact"), options=options_list, index=default_idx,
                            key=f"psld_abend_edit_contact_{a['id']}",
                        )
                        e_contact_cws = contact_options.get(e_contact_label, "")
                    else:
                        st.info(t("psld.abend_no_eligible_users_hint"), icon="ℹ️")
                        e_contact_cws = a.get("responsible_cws", "")

                    save_col, cancel_col = st.columns(2)
                    with save_col:
                        save_clicked = st.form_submit_button(t("psld.abend_edit_save"), type="primary")
                    with cancel_col:
                        cancel_clicked = st.form_submit_button(t("psld.abend_edit_cancel"))

                if save_clicked:
                    try:
                        psld_abend_registry.update_abend(
                            a["id"], abend_number=e_number, abend_program=e_program,
                            resolution=e_resolution, responsible_cws=e_contact_cws,
                        )
                        st.session_state[f"psld_abend_editing_{a['id']}"] = False
                        st.success(t("psld.abend_edit_success"))
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                if cancel_clicked:
                    st.session_state[f"psld_abend_editing_{a['id']}"] = False
                    st.rerun()

            if psld_abend_registry.status_of(a) == "pending_contact":
                st.warning(t("psld.abend_pending_contact_badge"), icon="⏳")

            st.write(a.get("resolution", ""))

            contact = users_by_cws.get(a.get("responsible_cws", ""))
            contact_name = contact.get("name") if contact else a.get("responsible_cws", "?")
            contact_email = contact.get("email_teams") if contact else ""
            if contact_email:
                st.markdown(
                    f"👤 {t('psld.abend_field_contact')}: "
                    f"[{contact_name} 💬 Teams]({teams_chat_link(contact_email)})"
                )
            else:
                st.caption(f"👤 {t('psld.abend_field_contact')}: {contact_name or '—'}")

            st.caption(f"{a.get('created_by', '?')} · {a.get('created_at', '')}")


def _render_abend_pending_list(pending: list, eligible_users: list) -> None:
    st.caption(t("psld.abend_pending_caption"))
    if not pending:
        st.info(t("psld.abend_pending_empty"), icon="✅")
        return

    contact_options = {
        f"{u.get('name', '?')} ({u['cws']})": u["cws"] for u in eligible_users
    } if eligible_users else {}

    for a in pending:
        with st.container(border=True):
            title = f"**🚨 {a.get('abend_number', '?')}**"
            if a.get("ticket_number"):
                title += f" · 🎫 {a['ticket_number']}"
            st.markdown(title)
            if a.get("resolution"):
                st.caption(a["resolution"][:300])
            if a.get("job_number"):
                st.caption(f"{t('psld.abend_job_number')}: `{a['job_number']}`")

            with st.form(f"psld_abend_resolve_{a['id']}"):
                p_program = st.text_input(t("psld.abend_field_program"), key=f"psld_pending_program_{a['id']}", placeholder="PGMABC01")
                if contact_options:
                    p_contact_label = st.selectbox(
                        t("psld.abend_field_contact"), options=[t("psld.abend_contact_none")] + list(contact_options.keys()),
                        key=f"psld_pending_contact_{a['id']}",
                    )
                    p_contact_cws = contact_options.get(p_contact_label, "")
                else:
                    st.caption(t("psld.abend_no_eligible_users_hint"))
                    p_contact_cws = ""
                resolve_clicked = st.form_submit_button(t("psld.abend_resolve_button"), type="primary")

            if resolve_clicked:
                ok = psld_abend_registry.complete_pending_program(
                    a["id"], abend_program=p_program, responsible_cws=p_contact_cws,
                )
                if ok:
                    st.success(t("psld.abend_resolve_success"))
                    st.rerun()
                else:
                    st.error(t("psld.abend_field_program"))


def _render_stats() -> None:
    st.caption(t("psld.stats_caption"))
    col1, col2 = st.columns(2)
    col1.metric(t("psld.stat_kb_entries"), len(servicenow_resolution_kb.list_entries()))
    col2.metric(t("psld.stat_feedback"), psld_semantic_engine.feedback_count())
    status = psld_semantic_engine.semantic_status()
    st.write(f"**{t('psld.stat_model')}:** {status['model']}")
    st.caption(t("psld.stat_explainer"))


def _score_badge(score: float) -> str:
    score_pct = round(score * 100)
    score_color = "#155724" if score_pct >= 75 else ("#856404" if score_pct >= 45 else "#721c24")
    score_bg = "#d4edda" if score_pct >= 75 else ("#fff3cd" if score_pct >= 45 else "#f8d7da")
    return (
        f'<span style="background:{score_bg};color:{score_color};padding:0.2rem 0.6rem;'
        f'border-radius:12px;font-weight:600;font-size:0.9rem;">{score_pct}%</span>'
    )


def _render_review_queue() -> None:
    st.caption(t("psld.review_caption"))

    stats = psld_review_queue.queue_stats()
    m1, m2, m3 = st.columns(3)
    m1.metric(t("psld.review_pending_count"), stats["pending"])
    m2.metric(t("psld.review_approved_count"), stats["approved"])
    m3.metric(t("psld.review_rejected_count"), stats["rejected"])

    if st.button(t("psld.review_scan_button"), key="psld_review_scan", type="primary"):
        with st.spinner(t("psld.review_scan_spinner")):
            result = psld_review_queue.generate_pending_reviews()
        st.success(
            t(
                "psld.review_scan_result",
                scanned=result["scanned"],
                new=result["new_pending"],
                low=result["skipped_low_score"],
                existing=result["skipped_existing"],
            ),
            icon="✅",
        )
        st.rerun()

    st.divider()
    st.markdown(f"##### {t('psld.review_pending_title')}")
    pending = psld_review_queue.list_pending()
    if not pending:
        st.info(t("psld.review_empty_pending"), icon="ℹ️")
    for review in pending:
        with st.container(border=True):
            head_l, head_r = st.columns([4, 1])
            with head_l:
                st.markdown(f"**{t('psld.review_ticket_label')}:** {review['ticket_number']}")
            with head_r:
                st.markdown(f'<div style="text-align:right;">{_score_badge(review["score"])}</div>', unsafe_allow_html=True)
            st.caption(review["ticket_text"][:400])
            st.markdown(f"**{t('psld.review_suggested_resolution')}:** {review['entry_title']}")
            breakdown = review["breakdown"]
            st.caption(
                t(
                    "psld.match_breakdown",
                    tfidf=round(breakdown.get("tfidf", 0) * 100),
                    semantic=round(breakdown.get("semantic", 0) * 100),
                    feedback=round(breakdown.get("feedback", 0) * 100),
                )
            )
            entry = servicenow_resolution_kb.get_entry(review["entry_id"])
            if entry:
                if entry.get("description_long"):
                    st.write(entry["description_long"])
                if entry.get("steps", "").strip():
                    st.success(entry["steps"], icon="✅")
                _render_resolution_file_buttons(entry, key_prefix=f"psld_review_{review['id']}")

            note_key = f"psld_review_note_{review['id']}"
            approve_col, reject_col = st.columns(2)
            with approve_col:
                if st.button(t("psld.review_approve_button"), key=f"psld_review_approve_{review['id']}", type="primary", width="stretch"):
                    result = psld_review_queue.approve_review(review["id"], reviewer_cws=_current_user_cws())
                    if result["ok"]:
                        st.success(t("psld.review_approved_toast"))
                        st.rerun()
                    else:
                        st.error(result.get("reason", "?"))
            with reject_col:
                note = st.text_input(t("psld.review_reject_note_placeholder"), key=note_key, label_visibility="collapsed")
                if st.button(t("psld.review_reject_button"), key=f"psld_review_reject_{review['id']}", width="stretch"):
                    result = psld_review_queue.reject_review(review["id"], reviewer_cws=_current_user_cws(), note=note)
                    if result["ok"]:
                        st.success(t("psld.review_rejected_toast"))
                        st.rerun()
                    else:
                        st.error(result.get("reason", "?"))

    history = psld_review_queue.list_history(limit=30)
    with st.expander(t("psld.review_history_title", count=len(history)), expanded=False):
        if not history:
            st.caption(t("psld.review_history_empty"))
        for h in history:
            icon = "✅" if h["status"] == "approved" else "🚫"
            st.caption(
                f"{icon} **{h['ticket_number']}** → {h['entry_title']} "
                f"({t('psld.review_status_' + h['status'])}, {h.get('reviewed_by', '?')}, {h.get('reviewed_at', '')})"
            )
            if h.get("review_note"):
                st.caption(f"　↳ {h['review_note']}")
