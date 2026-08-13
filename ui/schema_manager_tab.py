"""
Schema Manager UI — allows users to add/edit/delete tables and relationships.
"""
import streamlit as st
from ai.schema_manager import SchemaManager, TableSchema, SchemaRelationship
from i18n import t


def render_schema_manager_tab():
    """Renders the schema management interface."""
    st.header(t("schema_mgr.title"))
    st.markdown(t("schema_mgr.subtitle"))

    # Initialize manager
    if "schema_manager" not in st.session_state:
        st.session_state["schema_manager"] = SchemaManager()

    manager: SchemaManager = st.session_state["schema_manager"]

    # Tabs: Tables | Relationships | Import/Export
    tab_tables, tab_relationships, tab_export = st.tabs([
        t("schema_mgr.tab_tables"),
        t("schema_mgr.tab_relationships"),
        t("schema_mgr.tab_import_export")
    ])

    # ═══════════════════════════════════════════════════════════
    #  TAB 1: Tables Management
    # ═══════════════════════════════════════════════════════════
    with tab_tables:
        st.subheader(t("schema_mgr.manage_tables"))

        # Add new table
        with st.expander(t("schema_mgr.add_table_expander"), expanded=False):
            _render_add_table_form(manager)

        st.divider()

        # List existing tables
        st.subheader(t("schema_mgr.registered_tables"))
        grouped = manager.get_all_tables()

        if not grouped:
            st.info(t("schema_mgr.no_tables"))
        else:
            for schema_name in sorted(grouped.keys()):
                st.markdown(t("schema_mgr.schema_heading", schema_name=schema_name))
                tables = sorted(grouped[schema_name], key=lambda t: t.name)

                for table in tables:
                    with st.expander(f"**{table.name}** — {table.description}"):
                        col1, col2 = st.columns([3, 1])

                        with col1:
                            st.markdown(t("schema_mgr.primary_key_value", primary_key=table.primary_key))
                            st.markdown(t("schema_mgr.columns_count", count=len(table.columns)))
                            col_types = table.column_types or {}
                            if col_types:
                                st.dataframe(
                                    {"Column": list(col_types.keys()), "Type": list(col_types.values())},
                                    width="stretch", height=min(300, 38 + 35 * len(col_types)),
                                )
                            else:
                                st.code(", ".join(table.columns), language="")
                                st.caption(t("schema_mgr.no_column_types"))

                        with col2:
                            if st.button(t("schema_mgr.delete_button"), key=f"del_{schema_name}_{table.name}"):
                                if manager.remove_table(schema_name, table.name):
                                    st.success(t("schema_mgr.table_removed", table_name=table.name))
                                    st.rerun()

                            if st.button(t("schema_mgr.edit_button"), key=f"edit_{schema_name}_{table.name}"):
                                st.session_state["editing_table"] = f"{schema_name}.{table.name}"
                                st.rerun()

                            conn = st.session_state.get("conn")
                            connected = st.session_state.get("connected", False)
                            if st.button(
                                t("schema_mgr.retrieve_columns_button"),
                                key=f"retrieve_{schema_name}_{table.name}",
                                disabled=not (connected and conn is not None),
                                help=None if connected else t("schema_mgr.retrieve_columns_need_conn"),
                            ):
                                try:
                                    from database.schema_introspection import introspect_single_table
                                    with st.spinner(t("schema_mgr.retrieving_columns")):
                                        info = introspect_single_table(conn, schema_name, table.name)
                                    updated_table = TableSchema(
                                        name=table.name,
                                        schema=schema_name,
                                        columns=info["columns"],
                                        description=table.description,
                                        primary_key=info["primary_key"] or table.primary_key,
                                        column_types=info["column_types"],
                                        sample_joins=table.sample_joins,
                                    )
                                    manager.add_table(updated_table)
                                    st.success(t("schema_mgr.retrieve_columns_success", count=len(info["columns"])))
                                    st.rerun()
                                except Exception as e:
                                    st.error(t("schema_mgr.retrieve_columns_error", error=e))

        # Edit table (if selected)
        if "editing_table" in st.session_state:
            _render_edit_table_form(manager, st.session_state["editing_table"])

    # ═══════════════════════════════════════════════════════════
    #  TAB 2: Relationships Management
    # ═══════════════════════════════════════════════════════════
    with tab_relationships:
        st.subheader(t("schema_mgr.manage_relationships"))

        # Add new relationship
        with st.expander(t("schema_mgr.add_relationship_expander"), expanded=False):
            _render_add_relationship_form(manager)

        st.divider()

        # List existing relationships
        st.subheader(t("schema_mgr.registered_relationships"))
        if not manager.relationships:
            st.info(t("schema_mgr.no_relationships"))
        else:
            for i, rel in enumerate(manager.relationships):
                with st.expander(
                    f"**{rel.from_table}** → **{rel.to_table}** ({rel.join_type})"
                ):
                    st.markdown(t("schema_mgr.relationship_from_value", value=f"{rel.from_table}.{rel.from_column}"))
                    st.markdown(t("schema_mgr.relationship_to_value", value=f"{rel.to_table}.{rel.to_column}"))
                    st.markdown(t("schema_mgr.relationship_type_value", join_type=rel.join_type))
                    st.markdown(t("schema_mgr.relationship_description_value", description=rel.description))

                    if st.button(t("schema_mgr.delete_button"), key=f"del_rel_{i}"):
                        if manager.remove_relationship(i):
                            st.success(t("schema_mgr.relationship_removed"))
                            st.rerun()

    # ═══════════════════════════════════════════════════════════
    #  TAB 3: Import/Export
    # ═══════════════════════════════════════════════════════════
    with tab_export:
        st.subheader(t("schema_mgr.import_export"))

        # Export for LLM
        st.markdown(t("schema_mgr.export_llm_heading"))
        llm_export = manager.export_for_llm()
        st.text_area(t("schema_mgr.llm_format_label"), value=llm_export, height=300)
        st.download_button(
            label=t("schema_mgr.download_llm"),
            data=llm_export,
            file_name="schema_for_llm.txt",
            mime="text/plain"
        )

        st.divider()

        # Export as JSON
        st.markdown(t("schema_mgr.export_json_heading"))
        import json
        json_export = json.dumps(manager.export_to_dict(), indent=2, ensure_ascii=False)
        st.download_button(
            label=t("schema_mgr.download_json"),
            data=json_export,
            file_name="schema_catalog.json",
            mime="application/json"
        )

        st.divider()

        # Import from JSON
        st.markdown(t("schema_mgr.import_json_heading"))
        merge_mode = st.checkbox(t("schema_mgr.merge_checkbox"), value=True, key="schema_import_merge")
        uploaded_file = st.file_uploader(t("schema_mgr.upload_label"), type=["json"])
        if uploaded_file:
            try:
                data = json.load(uploaded_file)
                count = manager.import_from_dict(data, merge=merge_mode)
                st.success(t("schema_mgr.import_success", count=count))
                st.rerun()
            except Exception as e:
                st.error(t("schema_mgr.import_error", error=e))


def _render_add_table_form(manager: SchemaManager):
    """Renders form to add a new table."""
    prefill = st.session_state.get("schema_prefill") or {}

    # ── Auto-fill from database (outside the form: st.form only allows a
    # single submit button, so this helper lives above it and stores its
    # result in session_state, which the form widgets below pick up as
    # their `value=`). Lets a user retrieve a table's real column names +
    # data types straight from Oracle before ever typing anything by hand.
    st.markdown(f"##### {t('schema_mgr.autofill_heading')}")
    af1, af2, af3 = st.columns([2, 2, 1])
    af_schema = af1.text_input(t("schema_mgr.schema_label"), key="schema_autofill_schema", placeholder="I2TM_APP")
    af_table = af2.text_input(t("schema_mgr.table_name_label"), key="schema_autofill_table", placeholder="SHIPMENTS")
    conn = st.session_state.get("conn")
    connected = st.session_state.get("connected", False)
    if af3.button(
        t("schema_mgr.retrieve_columns_button"), key="schema_autofill_btn", width="stretch",
        disabled=not (connected and conn is not None),
        help=None if connected else t("schema_mgr.retrieve_columns_need_conn"),
    ):
        if not af_schema or not af_table:
            st.warning(t("schema_mgr.autofill_need_schema_table"), icon="⚠️")
        else:
            try:
                from database.schema_introspection import introspect_single_table
                with st.spinner(t("schema_mgr.retrieving_columns")):
                    info = introspect_single_table(conn, af_schema, af_table)
                st.session_state["schema_prefill"] = {
                    "schema": info["owner"],
                    "table": info["table_name"],
                    "columns": info["columns"],
                    "column_types": info["column_types"],
                    "primary_key": info["primary_key"],
                }
                st.success(t("schema_mgr.retrieve_columns_success", count=len(info["columns"])))
                st.rerun()
            except Exception as e:
                st.error(t("schema_mgr.retrieve_columns_error", error=e))
    if prefill:
        st.caption(t("schema_mgr.autofill_applied", table=f"{prefill.get('schema')}.{prefill.get('table')}"))

    st.divider()

    with st.form("add_table_form"):
        col1, col2 = st.columns(2)

        with col1:
            schema = st.text_input(
                t("schema_mgr.schema_label"), value=prefill.get("schema", "I2TM_APP"), help=t("schema_mgr.schema_help"),
            )
            table_name = st.text_input(
                t("schema_mgr.table_name_label"), value=prefill.get("table", ""), help=t("schema_mgr.table_name_help"),
            )

        with col2:
            primary_key = st.text_input(
                t("schema_mgr.primary_key_label"), value=prefill.get("primary_key", ""), help=t("schema_mgr.primary_key_help"),
            )

        description = st.text_area(
            t("schema_mgr.description_label"),
            help=t("schema_mgr.table_description_help")
        )

        columns_input = st.text_area(
            t("schema_mgr.columns_multiline_label"),
            value="\n".join(prefill.get("columns", [])),
            help=t("schema_mgr.columns_multiline_help")
        )

        submitted = st.form_submit_button(t("schema_mgr.add_table_button"), type="primary")

        if submitted:
            if not all([schema, table_name, description, columns_input]):
                st.error(t("schema_mgr.fill_required"))
                return

            # Parse columns
            if "," in columns_input:
                columns = [c.strip() for c in columns_input.split(",") if c.strip()]
            else:
                columns = [c.strip() for c in columns_input.split("\n") if c.strip()]

            if not columns:
                st.error(t("schema_mgr.add_one_column"))
                return

            # If the columns still match what we retrieved from the DB,
            # carry the discovered data types over onto the new table too.
            prefill_columns = prefill.get("columns") or []
            column_types = prefill.get("column_types") if columns == prefill_columns else None

            table = TableSchema(
                name=table_name.upper(),
                schema=schema.upper(),
                columns=columns,
                description=description,
                primary_key=primary_key or table_name.lower() + "_id",
                column_types=column_types,
            )

            if manager.add_table(table):
                st.session_state.pop("schema_prefill", None)
                st.success(t("schema_mgr.table_added", schema=schema, table_name=table_name))
                st.rerun()


def _render_edit_table_form(manager: SchemaManager, table_key: str):
    """Renders form to edit an existing table."""
    schema_name, table_name = table_key.split(".")
    table = manager.get_table(schema_name, table_name)

    if not table:
        st.error(t("schema_mgr.table_not_found"))
        del st.session_state["editing_table"]
        return

    st.subheader(t("schema_mgr.editing_table", table_key=table_key))

    with st.form("edit_table_form"):
        description = st.text_area(t("schema_mgr.description_label"), value=table.description)
        primary_key = st.text_input(t("schema_mgr.primary_key_label"), value=table.primary_key)
        columns_input = st.text_area(
            t("schema_mgr.columns_one_per_line_label"),
            value="\n".join(table.columns),
            height=200
        )

        col1, col2 = st.columns(2)
        with col1:
            save = st.form_submit_button(t("schema_mgr.save_button"), type="primary")
        with col2:
            cancel = st.form_submit_button(t("schema_mgr.cancel_button"))

        if save:
            columns = [c.strip() for c in columns_input.split("\n") if c.strip()]

            updated_table = TableSchema(
                name=table.name,
                schema=table.schema,
                columns=columns,
                description=description,
                primary_key=primary_key,
                column_types={
                    column: column_type
                    for column, column_type in (table.column_types or {}).items()
                    if column in columns
                } or None,
                sample_joins=table.sample_joins,
            )

            manager.add_table(updated_table)
            st.success(t("schema_mgr.table_updated"))
            del st.session_state["editing_table"]
            st.rerun()

        if cancel:
            del st.session_state["editing_table"]
            st.rerun()


def _render_add_relationship_form(manager: SchemaManager):
    """Renders form to add a new relationship."""
    with st.form("add_relationship_form"):
        # Get list of all tables
        all_tables = sorted(manager.tables.keys())

        if len(all_tables) < 2:
            st.warning(t("schema_mgr.need_two_tables"))
            st.form_submit_button(t("schema_mgr.add_button"), disabled=True)
            return

        col1, col2 = st.columns(2)

        with col1:
            st.markdown(t("schema_mgr.from_heading"))
            from_table = st.selectbox(t("schema_mgr.table_label"), all_tables, key="from_table")
            from_table_obj = manager.tables[from_table]
            from_column = st.selectbox(t("schema_mgr.column_label"), from_table_obj.columns, key="from_column")

        with col2:
            st.markdown(t("schema_mgr.to_heading"))
            to_table = st.selectbox(t("schema_mgr.table_label"), all_tables, key="to_table")
            to_table_obj = manager.tables[to_table]
            to_column = st.selectbox(t("schema_mgr.column_label"), to_table_obj.columns, key="to_column")

        join_type = st.selectbox(
            t("schema_mgr.join_type_label"),
            ["INNER", "LEFT", "RIGHT", "FULL OUTER"],
            help=t("schema_mgr.join_type_help")
        )

        description = st.text_input(
            t("schema_mgr.description_label"),
            help=t("schema_mgr.relationship_description_help")
        )

        submitted = st.form_submit_button(t("schema_mgr.add_relationship_button"), type="primary")

        if submitted:
            if not description:
                st.error(t("schema_mgr.add_description"))
                return

            rel = SchemaRelationship(
                from_table=from_table,
                to_table=to_table,
                from_column=from_column,
                to_column=to_column,
                join_type=join_type,
                description=description
            )

            try:
                manager.add_relationship(rel)
                st.success(t("schema_mgr.relationship_added"))
                st.rerun()
            except ValueError as e:
                st.error(str(e))
