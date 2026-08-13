"""
📚 Knowledge Base Tab - Interface para Gestão de Base de Conhecimento
=====================================================================
Upload, visualização e gerenciamento de troubleshooting knowledge base.
VERSÃO MELHORADA - Com upload, merge inteligente, batch processing e estatísticas avançadas
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
import sys
from datetime import datetime
import tempfile
import os

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from troubleshooter.knowledge_manager import KnowledgeBaseManager
from reports.exporter import df_to_excel_bytes, df_to_csv_bytes
from troubleshooter import kb_ownership
from troubleshooter.kb_template import generate_feeder_template
from troubleshooter.feedback_store import delete_kb_entry, update_kb_entry
from i18n import t, get_language


def render_knowledge_base_tab():
    """Renderiza a aba de Knowledge Base Management"""

    st.markdown(f'<div class="section-title">{t("kbtab.title")}</div>', unsafe_allow_html=True)

    # Inicializa manager
    if 'kb_manager' not in st.session_state:
        st.session_state.kb_manager = KnowledgeBaseManager()

    kb_manager = st.session_state.kb_manager

    # Tabs secundárias
    sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs([
        t("kbtab.sub_dashboard"),
        t("kbtab.sub_upload"),
        t("kbtab.sub_browse"),
        t("kbtab.sub_stats"),
    ])

    # ═══════════════════════════════════════════════
    #  SUB-TAB 1: DASHBOARD
    # ═══════════════════════════════════════════════
    with sub_tab1:
        st.markdown(f"### {t('kbtab.overview')}")

        try:
            stats = kb_manager.get_statistics()

            # Métricas principais
            col1, col2, col3, col4 = st.columns(4)

            col1.metric(
                label=t("kbtab.total_entries"),
                value=stats['total_entries']
            )

            col2.metric(
                label=t("kbtab.categories"),
                value=stats['categories']
            )

            col3.metric(
                label=t("kbtab.version"),
                value=stats['version']
            )

            col4.metric(
                label=t("kbtab.last_updated"),
                value=stats.get('last_updated', t('kbtab.never'))[:10] if stats.get('last_updated') else t('kbtab.never')
            )

            st.divider()

            # Breakdown por categoria — interactive/clickable chart
            if stats.get('category_breakdown'):
                st.markdown(f"#### {t('kbtab.category_breakdown')}")
                st.caption(t("kbtab.category_breakdown_hint"))

                cat_df = pd.DataFrame(
                    list(stats['category_breakdown'].items()),
                    columns=['Category', 'Count']
                ).sort_values('Count', ascending=True)  # ascending so the biggest bar ends up on top

                fig = px.bar(
                    cat_df, x='Count', y='Category', orientation='h',
                    text='Count', color='Count', color_continuous_scale='Blues',
                )
                fig.update_traces(textposition='outside')
                fig.update_layout(
                    height=max(280, 32 * len(cat_df)),
                    margin=dict(l=10, r=10, t=10, b=10),
                    coloraxis_showscale=False,
                    yaxis_title=None, xaxis_title=t("kbtab.total_entries"),
                )

                click_event = st.plotly_chart(
                    fig, width="stretch", key="kb_dashboard_cat_chart",
                    on_select="rerun", selection_mode="points",
                )

                clicked_category = None
                points = (click_event or {}).get("selection", {}).get("points", [])
                if points:
                    clicked_category = points[0].get("y")

                if clicked_category:
                    st.markdown(f"**{t('kbtab.category_filtered_by', category=clicked_category)}**")
                    df_kb_full = kb_manager.load_knowledge_base()
                    if 'Categoria' in df_kb_full.columns:
                        df_cat = df_kb_full[df_kb_full['Categoria'] == clicked_category]
                        st.dataframe(df_cat, width="stretch", height=280)
                else:
                    st.caption(t("kbtab.category_breakdown_no_selection"))

            st.divider()

            # Quick actions
            st.markdown(f"#### {t('kbtab.quick_actions')}")

            qcol1, qcol2, qcol3, qcol4 = st.columns(4)

            if qcol1.button(t("kbtab.reload_kb"), width="stretch", help=t("kbtab.reload_kb_help")):
                kb_manager.load_knowledge_base(force_reload=True)
                st.success(t("kbtab.reloaded"))
                st.rerun()

            if qcol2.button(t("kbtab.export_kb"), width="stretch"):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                    kb_manager.export_to_excel(tmp.name, include_stats=True)

                    with open(tmp.name, 'rb') as f:
                        excel_data = f.read()

                    os.unlink(tmp.name)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    st.download_button(
                        label=t("kbtab.download_kb"),
                        data=excel_data,
                        file_name=f"knowledge_base_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width="stretch"
                    )

            if qcol3.button(t("kbtab.view_full"), width="stretch"):
                st.session_state['kb_view_full'] = True

            # NOTE: this only clears the in-memory pandas cache, forcing the
            # next read to come from disk. It does NOT delete any Knowledge
            # Base data — see the help tooltip, which we made explicit after
            # user feedback that the trash-can icon looked destructive.
            if qcol4.button(t("kbtab.refresh_cache"), width="stretch", help=t("kbtab.refresh_cache_help")):
                kb_manager._kb_cache = None
                kb_manager._kb_cache_time = None
                st.success(t("kbtab.cache_cleared"))

            # Visualização completa (se ativada)
            if st.session_state.get('kb_view_full'):
                st.markdown(f"#### {t('kbtab.full_kb_title')}")
                df_kb = kb_manager.load_knowledge_base()

                if not df_kb.empty:
                    st.dataframe(df_kb, width="stretch", height=400)
                else:
                    st.info(t("kbtab.empty"), icon="ℹ️")

                if st.button(t("kbtab.close")):
                    st.session_state['kb_view_full'] = False
                    st.rerun()

        except Exception as e:
            st.error(t("kbtab.load_error", error=e), icon="❌")

    # ═══════════════════════════════════════════════
    #  SUB-TAB 2: UPLOAD & MERGE
    # ═══════════════════════════════════════════════
    with sub_tab2:
        st.markdown(f"### {t('kbtab.upload_title')}")

        upload_mode_tab, manual_mode_tab = st.tabs([
            t("kbtab.mode_bulk"),
            t("kbtab.mode_manual"),
        ])

        # ── Manual single-entry add (one row at a time, "+" to keep going) ──
        with manual_mode_tab:
            st.info(t("kbtab.manual_info"), icon="✍️")

            if "kb_manual_add_count" not in st.session_state:
                st.session_state["kb_manual_add_count"] = 0

            # Bumping the form's key on every successful save gives us a
            # fresh, blank st.form widget tree for the next entry — this is
            # what makes the "+" button feel like "keep adding" instead of
            # leaving the previous entry's text stuck in the fields.
            form_key = f"kb_manual_form_{st.session_state['kb_manual_add_count']}"
            with st.form(key=form_key, clear_on_submit=True):
                m_pattern = st.text_input(t("kbtab.field_pattern"), placeholder=t("kbtab.manual_pattern_placeholder"))
                m_meaning = st.text_area(t("kbtab.field_meaning"), height=80)
                m_how = st.text_area(t("kbtab.field_how_to_check"), height=80)
                m_action = st.text_area(t("kbtab.field_action"), height=80)

                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    m_responsible = st.text_input(t("kbtab.field_responsible"))
                with mcol2:
                    m_category = st.text_input(t("kbtab.field_category"))

                add_clicked = st.form_submit_button(
                    t("kbtab.add_entry_btn"), type="primary", width="stretch",
                )

                if add_clicked:
                    if not m_pattern.strip() or not m_meaning.strip() or not m_action.strip():
                        st.error(t("kbtab.manual_missing_fields"))
                    else:
                        from troubleshooter.feedback_store import create_kb_entry_from_pending

                        auth_user = st.session_state.get("auth_user") or {}
                        result = create_kb_entry_from_pending(
                            err_msg=m_pattern,
                            meaning=m_meaning,
                            how_to_check=m_how,
                            action=m_action,
                            cws=auth_user.get("cws"),
                            responsible=m_responsible,
                            category=m_category,
                        )
                        if result.get("action") == "failed":
                            st.error(t("kbtab.manual_add_failed", reason=result.get("reason", "")))
                        else:
                            kb_manager._kb_cache = None
                            kb_manager._kb_cache_time = None
                            verb = t("kbtab.manual_add_updated") if result.get("action") == "updated" else t("kbtab.manual_add_created")
                            st.success(f"✅ {verb}")
                            # Bump the counter -> new form key -> blank fields,
                            # ready for the next "+" entry without a full rerun-reset gotcha.
                            st.session_state["kb_manual_add_count"] += 1
                            st.rerun()

            st.caption(t("kbtab.manual_add_more_hint"))

        # ── Bulk upload (Excel/CSV) ──
        with upload_mode_tab:
            st.info(t("kbtab.upload_info"), icon="ℹ️")

            # ── Downloadable predefined template for bulk feeding ──
            template_bytes = generate_feeder_template()
            st.download_button(
                label="📥 BAIXAR LAYOUT - KNOWLEDGE FEEDER FILE",
                data=template_bytes,
                file_name="knowledge_feeder_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
                type="primary",
                help=t("kbtab.download_feeder_help"),
            )

            st.divider()

            # File uploader
            uploaded_file = st.file_uploader(
                t("kbtab.choose_file"),
                type=['xlsx', 'xls', 'csv'],
                help=t("kbtab.choose_file_help")
            )

            if uploaded_file:
                st.success(t("kbtab.file_uploaded", name=uploaded_file.name))

                # Preview
                with st.expander(t("kbtab.preview"), expanded=True):
                    try:
                        if uploaded_file.name.endswith('.csv'):
                            df_preview = pd.read_csv(uploaded_file)
                        else:
                            df_preview = pd.read_excel(uploaded_file)

                        # Reset file pointer
                        uploaded_file.seek(0)

                        st.dataframe(df_preview.head(10), width="stretch")
                        st.caption(t("kbtab.showing_rows", count=len(df_preview)))

                        # Column validation
                        st.markdown(f"**{t('kbtab.detected_columns')}**")
                        cols_str = ", ".join([f"`{col}`" for col in df_preview.columns])
                        st.markdown(cols_str)

                    except Exception as e:
                        st.error(t("kbtab.preview_error", error=e), icon="❌")

                st.divider()

                # Merge options
                st.markdown(f"#### {t('kbtab.merge_options')}")

                mcol1, mcol2 = st.columns(2)

                version_type = mcol1.selectbox(
                    t("kbtab.version_increment"),
                    options=['patch', 'minor', 'major'],
                    help=t("kbtab.version_increment_help")
                )

                auto_categorize = mcol2.checkbox(
                    t("kbtab.auto_categorize"),
                    value=True,
                    help=t("kbtab.auto_categorize_help")
                )

                description = st.text_area(
                    t("kbtab.change_description"),
                    placeholder=t("kbtab.change_description_placeholder"),
                    height=100
                )

                st.divider()

                # Merge button
                if st.button(t("kbtab.merge_btn"), type="primary", width="stretch"):
                    with st.spinner(t("kbtab.merging")):
                        try:
                            # Save uploaded file temporarily. Sanitize the
                            # suffix — deriving it from the raw uploaded
                            # filename (attacker-controlled) let a crafted name
                            # like "x.xlsx; rm -rf.sh" or one containing path
                            # separators/NULs reach NamedTemporaryFile's suffix
                            # argument, which is unsafe on some platforms.
                            # Only allow the extensions this merge flow expects.
                            _suffix = Path(uploaded_file.name or "").suffix.lower()
                            if _suffix not in (".xlsx", ".xls"):
                                _suffix = ".xlsx"
                            with tempfile.NamedTemporaryFile(delete=False, suffix=_suffix) as tmp:
                                tmp.write(uploaded_file.read())
                                tmp_path = tmp.name

                            # Perform merge
                            auth_user = st.session_state.get("auth_user") or {}
                            result = kb_manager.upload_and_merge(
                                uploaded_file_path=tmp_path,
                                file_type='auto',
                                version_type=version_type,
                                description=description,
                                cws=auth_user.get("cws"),
                            )

                            # Clean up
                            os.unlink(tmp_path)

                            if result['success']:
                                st.success(t("kbtab.merge_success"), icon="✅")

                                # Show stats
                                st.markdown(f"#### {t('kbtab.merge_stats')}")

                                scol1, scol2, scol3 = st.columns(3)

                                scol1.metric(t("kbtab.new_version"), result['version'])
                                scol2.metric(t("kbtab.total_entries"), result['stats']['total'])
                                scol3.metric(t("kbtab.duplicates_removed"), result['stats']['duplicates_removed'])

                                if result.get('backup_file'):
                                    st.info(t("kbtab.backup_saved", name=result['backup_file']), icon="💾")

                                # Reload button
                                if st.button(t("kbtab.reload_dashboard")):
                                    st.rerun()
                            else:
                                st.error(t("kbtab.merge_failed", error=result.get('error', 'Unknown error')), icon="❌")

                        except Exception as e:
                            st.error(t("kbtab.merge_error", error=e), icon="❌")

    # ═══════════════════════════════════════════════
    #  SUB-TAB 3: BROWSE & SEARCH
    # ═══════════════════════════════════════════════
    with sub_tab3:
        st.markdown(f"### {t('kbtab.browse_title')}")

        df_kb = kb_manager.load_knowledge_base()

        if df_kb.empty:
            st.info(t("kbtab.empty"), icon="ℹ️")
        else:
            # Search bar
            search_query = st.text_input(
                t("kbtab.search_placeholder"),
                placeholder="Type keywords to search...",
                help=t("kbtab.search_help")
            )

            # Filter by category
            categories = ['All'] + sorted(df_kb['Categoria'].unique().tolist())
            selected_category = st.selectbox(
                t("kbtab.filter_category"),
                options=categories
            )

            # Apply filters
            df_filtered = df_kb.copy()

            if selected_category != 'All':
                df_filtered = df_filtered[df_filtered['Categoria'] == selected_category]

            if search_query:
                mask = df_filtered['Mensagem de erro / padrão identificado'].str.contains(
                    search_query, case=False, na=False
                )
                df_filtered = df_filtered[mask]

            # Results
            st.markdown(f"#### {t('kbtab.results_count', count=len(df_filtered))}")

            pattern_col = 'Mensagem de erro / padrão identificado'

            if not df_filtered.empty:
                # ── Ownership + freshness badges ──────────
                owners, freshness_labels, created_ats, updated_ats = [], [], [], []
                for pattern in df_filtered[pattern_col]:
                    meta = kb_ownership.get_meta(pattern)
                    color, _ = kb_ownership.freshness(meta.get("updated_at", ""))
                    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(color, "⚪")
                    owners.append(meta.get("updated_by") or meta.get("created_by") or "SYSTEM")
                    freshness_labels.append(icon)
                    created_ats.append(str(meta.get("created_at", ""))[:10])
                    updated_ats.append(str(meta.get("updated_at", ""))[:10])

                df_display = df_filtered.copy()
                df_display.insert(0, "🚦", freshness_labels)
                df_display["Owner"] = owners
                df_display["Created"] = created_ats
                df_display["Last Updated"] = updated_ats

                st.caption(t("kbtab.freshness_caption"))
                st.dataframe(df_display, width="stretch", height=500)

                # Export filtered results
                ecol1, ecol2, _ = st.columns([1, 1, 4])

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                ecol1.download_button(
                    t("kbtab.export_csv"),
                    data=df_to_csv_bytes(df_filtered),
                    file_name=f"kb_filtered_{timestamp}.csv",
                    mime="text/csv",
                    width="stretch"
                )

                ecol2.download_button(
                    t("kbtab.export_excel"),
                    data=df_to_excel_bytes(df_filtered),
                    file_name=f"kb_filtered_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch"
                )
            else:
                st.warning(t("kbtab.no_match"), icon="⚠️")

            auth_user = st.session_state.get("auth_user") or {}
            my_cws = auth_user.get("cws", "")

            # ── Request a change to ANY fix (no shipment ID needed) ──────
            st.divider()
            st.markdown(f"#### {t('kbtab.request_change_title')}")
            st.caption(t("kbtab.request_change_caption"))

            if df_filtered.empty:
                st.caption(t("kbtab.no_match"))
            else:
                from troubleshooter.fix_requests import create_request

                req_pattern = st.selectbox(
                    t("kbtab.select_fix"),
                    options=df_filtered[pattern_col].tolist(),
                    key="kb_request_pattern_select",
                )
                req_owner = kb_ownership.get_owner(req_pattern)
                is_mine = my_cws and req_owner.strip().lower() == my_cws.strip().lower()

                if is_mine:
                    st.info(t("kbtab.owned_by_you"), icon="✅")
                elif req_owner == "SYSTEM":
                    st.info(t("kbtab.owned_by_system"), icon="ℹ️")
                elif not my_cws:
                    st.caption(t("kbtab.sign_in_to_manage"))
                else:
                    with st.form(key="kb_request_form"):
                        req_type_label = st.selectbox(
                            t("requests.type"),
                            options=[t("requests.type_question"), t("requests.type_improvement")],
                            key="kb_req_type",
                        )
                        req_message = st.text_area(t("requests.message"), key="kb_req_message")
                        req_proposed = st.text_area(t("requests.proposed_action"), key="kb_req_proposed")
                        submitted = st.form_submit_button(t("requests.submit"), type="primary", width="stretch")
                        if submitted:
                            rtype = "question" if req_type_label == t("requests.type_question") else "improvement"
                            create_request(
                                requester_cws=my_cws,
                                requester_name=auth_user.get("name", "Unknown"),
                                owner_cws=req_owner,
                                err_pattern=req_pattern,
                                request_type=rtype,
                                message=req_message,
                                proposed_action=req_proposed or None,
                            )
                            st.success(t("requests.sent", owner=req_owner))

            # ── Manage my own fixes (pick one from a dropdown, then edit/delete) ──
            st.divider()
            st.markdown(f"#### {t('kbtab.manage_my_fixes')}")
            st.caption(t("kbtab.manage_my_fixes_caption"))

            if not my_cws:
                st.caption(t("kbtab.sign_in_to_manage"))
            else:
                my_patterns = [
                    p for p in df_kb[pattern_col]
                    if kb_ownership.get_owner(p).strip().lower() in (my_cws.strip().lower(), "system")
                ]
                if not my_patterns:
                    st.caption(t("kbtab.no_owned_fixes"))
                else:
                    pattern_to_pick = st.selectbox(
                        t("kbtab.pick_fix_to_manage"),
                        options=my_patterns,
                        key="myfix_pick_select",
                        format_func=lambda p: (
                            f"{ {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}.get(kb_ownership.freshness(kb_ownership.get_meta(p).get('updated_at', ''))[0], '⚪') } {p}"
                        ),
                    )
                    meta = kb_ownership.get_meta(pattern_to_pick)
                    color, label = kb_ownership.freshness(meta.get("updated_at", ""))
                    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(color, "⚪")
                    st.caption(f"{icon} {label} · {t('kbtab.created_by_label')}: {meta.get('created_by', '—')} · {t('kbtab.updated_by_label')}: {meta.get('updated_by', '—')}")

                    row = df_kb[df_kb[pattern_col] == pattern_to_pick].iloc[0]
                    row_key = f"myfix_{abs(hash(pattern_to_pick)) % 100000}"
                    edit_key = f"{row_key}_editing"
                    confirm_key = f"{row_key}_confirm"

                    ecol1, ecol2 = st.columns(2)
                    if ecol1.button(t("kbtab.edit_this"), key=f"{row_key}_editbtn", width="stretch"):
                        st.session_state[edit_key] = True
                    if ecol2.button(t("kbtab.delete_this"), key=f"{row_key}_delbtn", width="stretch"):
                        st.session_state[confirm_key] = True

                    if st.session_state.get(edit_key):
                        with st.form(key=f"{row_key}_form"):
                            f_pattern = st.text_input(t("kbtab.field_pattern"), value=str(row.get(pattern_col, "")))

                            pt_meaning_val = str(row.get("Significado provável", ""))
                            pt_how_val = str(row.get("Como validar", ""))
                            pt_action_val = str(row.get("Ação recomendada", ""))
                            en_meaning_val = str(row.get("Significado provável (English)", ""))
                            en_how_val = str(row.get("Como validar (English)", ""))
                            en_action_val = str(row.get("Ação recomendada (English)", ""))

                            # When the UI language is English, the English
                            # fields become the primary/required inputs and
                            # Portuguese moves into the optional expander
                            # (and vice-versa) — whichever side is left
                            # blank gets auto-translated on save.
                            show_en_primary = get_language() == "en"
                            if show_en_primary:
                                f_meaning = st.text_area(t("kbtab.field_meaning_en"), value=en_meaning_val)
                                f_how = st.text_area(t("kbtab.field_how_to_check_en"), value=en_how_val)
                                f_action = st.text_area(t("kbtab.field_action_en"), value=en_action_val)
                            else:
                                f_meaning = st.text_area(t("kbtab.field_meaning"), value=pt_meaning_val)
                                f_how = st.text_area(t("kbtab.field_how_to_check"), value=pt_how_val)
                                f_action = st.text_area(t("kbtab.field_action"), value=pt_action_val)

                            f_responsible = st.text_input(t("kbtab.field_responsible"), value=str(row.get("Responsável sugerido", "")))
                            f_category = st.text_input(t("kbtab.field_category"), value=str(row.get("Categoria", "")))
                            tariff_options = ["Não", "Sim"]
                            current_tariff = str(row.get("Precisa usar a Tariff Pool Query?", "Não")).strip() or "Não"
                            f_tariff = st.selectbox(
                                t("kbtab.field_needs_tariff"), options=tariff_options,
                                index=tariff_options.index(current_tariff) if current_tariff in tariff_options else 0,
                            )

                            secondary_label = t("kbtab.portuguese_section") if show_en_primary else t("kbtab.english_section")
                            secondary_caption = t("kbtab.portuguese_section_caption") if show_en_primary else t("kbtab.english_section_caption")
                            with st.expander(secondary_label, expanded=bool(
                                pt_meaning_val.strip() or pt_action_val.strip() if show_en_primary
                                else en_meaning_val.strip() or en_action_val.strip()
                            )):
                                st.caption(secondary_caption)
                                if show_en_primary:
                                    f_meaning_secondary = st.text_area(t("kbtab.field_meaning"), value=pt_meaning_val)
                                    f_how_secondary = st.text_area(t("kbtab.field_how_to_check"), value=pt_how_val)
                                    f_action_secondary = st.text_area(t("kbtab.field_action"), value=pt_action_val)
                                else:
                                    f_meaning_secondary = st.text_area(t("kbtab.field_meaning_en"), value=en_meaning_val)
                                    f_how_secondary = st.text_area(t("kbtab.field_how_to_check_en"), value=en_how_val)
                                    f_action_secondary = st.text_area(t("kbtab.field_action_en"), value=en_action_val)

                            fcol1, fcol2 = st.columns(2)
                            save_clicked = fcol1.form_submit_button(t("kbtab.save_changes"), type="primary", width="stretch")
                            cancel_clicked = fcol2.form_submit_button(t("common.cancel"), width="stretch")

                            if save_clicked:
                                if show_en_primary:
                                    meaning_en, how_en, action_en = f_meaning, f_how, f_action
                                    meaning_pt, how_pt, action_pt = f_meaning_secondary, f_how_secondary, f_action_secondary
                                else:
                                    meaning_pt, how_pt, action_pt = f_meaning, f_how, f_action
                                    meaning_en, how_en, action_en = f_meaning_secondary, f_how_secondary, f_action_secondary

                                result = update_kb_entry(
                                    pattern_to_pick, my_cws,
                                    new_pattern=f_pattern,
                                    meaning=meaning_pt,
                                    how_to_check=how_pt,
                                    action=action_pt,
                                    responsible=f_responsible,
                                    category=f_category,
                                    needs_tariff=f_tariff,
                                    meaning_en=meaning_en,
                                    how_to_check_en=how_en,
                                    action_en=action_en,
                                )
                                if result.get("updated"):
                                    st.success(t("kbtab.update_success"))
                                    kb_manager._kb_cache = None
                                    st.session_state[edit_key] = False
                                    st.rerun()
                                else:
                                    st.error(t("kbtab.update_failed", reason=result.get("reason", "")))
                            if cancel_clicked:
                                st.session_state[edit_key] = False
                                st.rerun()

                    if st.session_state.get(confirm_key):
                        wcol1, wcol2, wcol3 = st.columns([3, 1, 1])
                        wcol1.warning(t("kbtab.delete_confirm_row"), icon="⚠️")
                        if wcol2.button(t("kbtab.delete_confirm_yes"), key=f"{row_key}_yes", type="primary", width="stretch"):
                            result = delete_kb_entry(pattern_to_pick, my_cws)
                            if result.get("deleted"):
                                st.success(t("kbtab.delete_success"))
                                kb_manager._kb_cache = None
                                st.session_state[confirm_key] = False
                                st.rerun()
                            else:
                                st.error(t("kbtab.delete_failed", reason=result.get("reason", "")))
                        if wcol3.button(t("kbtab.delete_confirm_no"), key=f"{row_key}_no", width="stretch"):
                            st.session_state[confirm_key] = False
                            st.rerun()

    # ═══════════════════════════════════════════════
    #  SUB-TAB 4: STATISTICS
    # ═══════════════════════════════════════════════
    with sub_tab4:
        st.markdown(f"### {t('kbtab.stats_title')}")

        try:
            stats = kb_manager.get_statistics()
            df_kb = kb_manager.load_knowledge_base()

            if df_kb.empty:
                st.info("No statistics available. Knowledge Base is empty.", icon="ℹ️")
            else:
                # Overall stats
                st.markdown("#### 📊 Overall Statistics")

                col1, col2, col3 = st.columns(3)

                col1.metric("Total Entries", stats['total_entries'])
                col2.metric("Unique Categories", stats['categories'])
                col3.metric("Current Version", stats['version'])

                st.divider()

                # Category distribution
                st.markdown("#### 🏷️ Category Distribution")

                if stats.get('category_breakdown'):
                    cat_df = pd.DataFrame(
                        list(stats['category_breakdown'].items()),
                        columns=['Category', 'Count']
                    ).sort_values('Count', ascending=True)

                    fig_cat = px.bar(
                        cat_df, x='Count', y='Category', orientation='h',
                        text='Count', color='Count', color_continuous_scale='Blues',
                    )
                    fig_cat.update_traces(textposition='outside')
                    fig_cat.update_layout(
                        height=max(280, 32 * len(cat_df)),
                        margin=dict(l=10, r=10, t=10, b=10),
                        coloraxis_showscale=False,
                        yaxis_title=None, xaxis_title=t("kbtab.total_entries"),
                    )
                    st.plotly_chart(fig_cat, width="stretch", key="kb_stats_cat_chart")
                    with st.expander(t("kbtab.view_as_table")):
                        st.dataframe(cat_df.sort_values('Count', ascending=False), width="stretch")

                st.divider()

                # Tariff requirement analysis
                st.markdown("#### 📦 Tariff Query Requirements")

                if stats.get('needs_tariff'):
                    tariff_df = pd.DataFrame(
                        list(stats['needs_tariff'].items()),
                        columns=['Needs Tariff', 'Count']
                    )

                    st.dataframe(tariff_df, width="stretch")

                st.divider()

                # Advanced analytics
                st.markdown("#### 🔬 Advanced Analytics")

                # Most common action words
                if 'Ação recomendada' in df_kb.columns:
                    actions = df_kb['Ação recomendada'].dropna()

                    # Extract action verbs (simple approach)
                    action_words = []
                    for action in actions:
                        words = str(action).split()[:3]  # First 3 words
                        action_words.extend(words)

                    from collections import Counter
                    top_actions = Counter(action_words).most_common(10)

                    if top_actions:
                        st.markdown("**🎯 Most Common Action Keywords:**")
                        action_df = pd.DataFrame(top_actions, columns=['Keyword', 'Frequency'])
                        st.dataframe(action_df, width="stretch")

                # Responsible team distribution
                if 'Responsável sugerido' in df_kb.columns:
                    responsible_counts = df_kb['Responsável sugerido'].value_counts().head(10)

                    if not responsible_counts.empty:
                        st.markdown("**👥 Top Responsible Teams:**")
                        resp_df = responsible_counts.reset_index()
                        resp_df.columns = ['Team', 'Count']
                        resp_df = resp_df.sort_values('Count', ascending=True)
                        fig_resp = px.bar(
                            resp_df, x='Count', y='Team', orientation='h',
                            text='Count', color='Count', color_continuous_scale='Teal',
                        )
                        fig_resp.update_traces(textposition='outside')
                        fig_resp.update_layout(
                            height=max(240, 32 * len(resp_df)),
                            margin=dict(l=10, r=10, t=10, b=10),
                            coloraxis_showscale=False,
                            yaxis_title=None, xaxis_title="Entries",
                        )
                        st.plotly_chart(fig_resp, width="stretch", key="kb_stats_resp_chart")

                st.divider()

                # ── Freshness & ownership analytics ──────────────────
                # Highlights which KB fixes are stale/at-risk of being
                # outdated (red/yellow) vs actively maintained (green),
                # and who's contributing the most fixes — makes it easy
                # to prioritize "which fixes need a review" at a glance
                # instead of opening every single entry.
                st.markdown(f"#### 🕓 {t('kbtab.freshness_title')}")

                pattern_col = "Mensagem de erro / padrão identificado"

                fresh_counts = {"green": 0, "yellow": 0, "red": 0}
                stale_rows = []
                owner_counter: dict[str, int] = {}

                if pattern_col in df_kb.columns:
                    for _, row in df_kb.iterrows():
                        pattern = str(row.get(pattern_col, "")).strip()
                        meta = kb_ownership.get_meta(pattern)
                        color, detail = kb_ownership.freshness(meta.get("updated_at", ""))
                        fresh_counts[color] = fresh_counts.get(color, 0) + 1

                        owner = meta.get("updated_by") or meta.get("created_by") or "SYSTEM"
                        owner_counter[owner] = owner_counter.get(owner, 0) + 1

                        if color in ("yellow", "red"):
                            stale_rows.append({
                                "Error Pattern": pattern[:80],
                                "Category": row.get("Categoria", ""),
                                "Freshness": "🟡 Aging" if color == "yellow" else "🔴 Stale",
                                "Detail": detail,
                                "Owner": owner,
                            })

                fc1, fc2, fc3 = st.columns(3)
                fc1.metric("🟢 Recent (< 3mo)", fresh_counts.get("green", 0))
                fc2.metric("🟡 Aging (3-12mo)", fresh_counts.get("yellow", 0))
                fc3.metric("🔴 Stale (1y+)", fresh_counts.get("red", 0))

                if stale_rows:
                    st.markdown(f"**⚠️ {t('kbtab.stale_fixes_label')}**")
                    stale_df = pd.DataFrame(stale_rows).sort_values("Freshness")
                    st.dataframe(stale_df, width="stretch", height=220)
                else:
                    st.success(t("kbtab.no_stale_fixes"), icon="✅")

                if owner_counter:
                    st.markdown(f"**🏆 {t('kbtab.top_contributors_label')}**")
                    owner_df = pd.DataFrame(
                        sorted(owner_counter.items(), key=lambda x: x[1], reverse=True),
                        columns=["Owner (CWS)", "Fixes Owned"],
                    ).head(10)
                    st.bar_chart(owner_df.set_index("Owner (CWS)"))

        except Exception as e:
            st.error(f"❌ Error generating statistics: {e}", icon="❌")


if __name__ == "__main__":
    # Para testar isoladamente
    st.set_page_config(page_title="Knowledge Base", layout="wide")
    render_knowledge_base_tab()

