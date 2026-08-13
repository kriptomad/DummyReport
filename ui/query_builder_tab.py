"""
ui/query_builder_tab.py
=======================
Visual "block" SQL query builder — available to all logged-in users.

Real drag-and-drop isn't available in plain Streamlit (would require a
custom JS component), so "blocks" are represented as ordered, colored
cards that you build up, reorder with ▲/▼ buttons, and remove — plus a
side panel with a searchable/filterable field picker (dropdown) and a
categorized SQL function palette that inserts ready-to-edit snippets.

Flow: build blocks (SELECT → FROM → JOIN → WHERE/AND/OR → GROUP BY →
HAVING → ORDER BY → LIMIT → FINISH) → the assembled SQL is shown live →
click RUN to execute it against the currently active Oracle connection.
"""
import re
import uuid
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd

from ai.sql_functions import SQL_FUNCTIONS, get_categories
from ai.sql_validator import validate_sql
from ai.type_hints import check_type_hints
from reports.exporter import df_to_csv_bytes, df_to_excel_bytes
from i18n import t

try:
    from ai.schema_manager import SchemaManager
except Exception:  # pragma: no cover - defensive, schema manager should always import
    SchemaManager = None  # type: ignore

# Matches a SCHEMA.TABLE token (e.g. "TMS_OMS.SHIPMENT") so we can tell
# which tables are already referenced by the FROM/JOIN blocks — used to
# cross-reference against the Schema Manager's registered relationships
# and suggest useful JOINs.
_SCHEMA_TABLE_RE = re.compile(r"\b([A-Za-z_][\w$#]*)\.([A-Za-z_][\w$#]*)\b")


# Block type metadata: display color + short helper placeholder text.
BLOCK_TYPES: Dict[str, Dict[str, str]] = {
    "SELECT":    {"color": "#3b82f6", "icon": "🔵", "placeholder": "* or column1, column2, ..."},
    "DISTINCT":  {"color": "#3b82f6", "icon": "🔷", "placeholder": "(no value needed — makes SELECT unique)"},
    "FROM":      {"color": "#22c55e", "icon": "🟢", "placeholder": "SCHEMA.TABLE_NAME [alias]"},
    "JOIN":      {"color": "#eab308", "icon": "🟡", "placeholder": "INNER|LEFT|RIGHT JOIN SCHEMA.TABLE alias ON a.col = b.col"},
    "WHERE":     {"color": "#f59e0b", "icon": "🟠", "placeholder": "COLUMN = 'value'"},
    "AND":       {"color": "#a855f7", "icon": "🟣", "placeholder": "COLUMN = 'value'"},
    "OR":        {"color": "#a855f7", "icon": "🟣", "placeholder": "COLUMN = 'value'"},
    "GROUP BY":  {"color": "#06b6d4", "icon": "🔶", "placeholder": "column1, column2, ..."},
    "HAVING":    {"color": "#06b6d4", "icon": "🔷", "placeholder": "COUNT(*) > 1"},
    "ORDER BY":  {"color": "#14b8a6", "icon": "🟦", "placeholder": "COLUMN [ASC|DESC]"},
    "LIMIT":     {"color": "#64748b", "icon": "⚪", "placeholder": "e.g. 100"},
    "UNION":     {"color": "#ec4899", "icon": "🌸", "placeholder": "(leave blank for UNION, type ALL for UNION ALL)"},
    "CUSTOM":    {"color": "#0f172a", "icon": "⬛", "placeholder": "any raw SQL fragment"},
    "FINISH":    {"color": "#ef4444", "icon": "🔴", "placeholder": ""},
}

# What block types make sense to suggest right after each block type —
# a very small "smart assistant" nudging the user toward a valid query
# shape without forcing any particular order.
_SUGGEST_AFTER: Dict[str, List[str]] = {
    None:       ["SELECT"],
    "SELECT":   ["DISTINCT", "FROM"],
    "DISTINCT": ["FROM"],
    "FROM":     ["JOIN", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "JOIN":     ["JOIN", "WHERE", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "WHERE":    ["AND", "OR", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "AND":      ["AND", "OR", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "OR":       ["AND", "OR", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "GROUP BY": ["HAVING", "ORDER BY", "LIMIT", "FINISH"],
    "HAVING":   ["ORDER BY", "LIMIT", "FINISH"],
    "ORDER BY": ["LIMIT", "FINISH"],
    "LIMIT":    ["UNION", "FINISH"],
    "UNION":    ["SELECT"],
    "CUSTOM":   ["WHERE", "AND", "OR", "GROUP BY", "ORDER BY", "LIMIT", "FINISH"],
    "FINISH":   [],
}


def _init_state() -> None:
    if "qb_blocks" not in st.session_state:
        st.session_state["qb_blocks"] = []  # list of {"id", "type", "value"}
    if "qb_active_block" not in st.session_state:
        st.session_state["qb_active_block"] = None


def _blocks() -> List[Dict[str, Any]]:
    return st.session_state["qb_blocks"]


def _add_block(block_type: str) -> None:
    blocks = _blocks()
    # FINISH is always the last block — inserting a new block pushes it
    # back down rather than appending after it.
    new_block = {"id": uuid.uuid4().hex[:8], "type": block_type, "value": ""}
    finish_idx = next((i for i, b in enumerate(blocks) if b["type"] == "FINISH"), None)
    if block_type == "FINISH" and finish_idx is not None:
        return  # only one FINISH block allowed
    if finish_idx is not None and block_type != "FINISH":
        blocks.insert(finish_idx, new_block)
    else:
        blocks.append(new_block)
    st.session_state["qb_active_block"] = new_block["id"]


def _move(block_id: str, direction: int) -> None:
    blocks = _blocks()
    idx = next((i for i, b in enumerate(blocks) if b["id"] == block_id), None)
    if idx is None:
        return
    new_idx = idx + direction
    if 0 <= new_idx < len(blocks):
        blocks[idx], blocks[new_idx] = blocks[new_idx], blocks[idx]


def _remove(block_id: str) -> None:
    st.session_state["qb_blocks"] = [b for b in _blocks() if b["id"] != block_id]
    if st.session_state.get("qb_active_block") == block_id:
        st.session_state["qb_active_block"] = None


def _assemble_sql(blocks: List[Dict[str, Any]]) -> str:
    """Builds an Oracle SQL string from the ordered blocks. Best-effort —
    this is an experimental visual helper, not a full SQL parser/builder."""
    lines: List[str] = []
    has_where = False
    select_distinct = False
    for b in blocks:
        if b["type"] == "DISTINCT":
            select_distinct = True

    for b in blocks:
        t_, v = b["type"], (b["value"] or "").strip()
        if t_ == "FINISH":
            break
        if t_ == "DISTINCT":
            continue  # folded into the SELECT line instead of its own line
        if not v and t_ not in ("FINISH",):
            continue
        if t_ == "SELECT":
            prefix = "SELECT DISTINCT" if select_distinct else "SELECT"
            lines.append(f"{prefix} {v or '*'}")
        elif t_ == "FROM":
            lines.append(f"FROM {v}")
        elif t_ == "JOIN":
            lines.append(v)
        elif t_ == "WHERE":
            lines.append(f"WHERE {v}")
            has_where = True
        elif t_ in ("AND", "OR"):
            if has_where:
                lines.append(f"{t_} {v}")
            else:
                # No WHERE yet — treat the first AND/OR as the WHERE clause
                # so the assembled SQL is still syntactically valid.
                lines.append(f"WHERE {v}")
                has_where = True
        elif t_ == "GROUP BY":
            lines.append(f"GROUP BY {v}")
        elif t_ == "HAVING":
            lines.append(f"HAVING {v}")
        elif t_ == "ORDER BY":
            lines.append(f"ORDER BY {v}")
        elif t_ == "LIMIT":
            lines.append(f"FETCH FIRST {v} ROWS ONLY")
        elif t_ == "UNION":
            lines.append("UNION ALL" if v.strip().upper() == "ALL" else "UNION")
        elif t_ == "CUSTOM":
            lines.append(v)
    return "\n".join(lines)


def _pk_columns(table) -> set:
    """Parses a TableSchema's `primary_key` string (e.g. "shpm_id, rfrc_num_typ")
    into a set of upper-cased column names for quick "is this a PK?" lookups."""
    raw = getattr(table, "primary_key", "") or ""
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def _all_columns(manager) -> List[Dict[str, Any]]:
    """Flattens the schema catalog into a simple list of
    {schema, table, column, type, is_pk} dicts for the field picker and
    for type/PK-aware validation while building a query."""
    if manager is None:
        return []
    out = []
    for schema_name, tables in manager.get_all_tables().items():
        for table in tables:
            col_types = getattr(table, "column_types", None) or {}
            pk_cols = _pk_columns(table)
            for col in table.columns:
                out.append({
                    "schema": schema_name,
                    "table": table.name,
                    "column": col,
                    "type": col_types.get(col, "—"),
                    "is_pk": col.upper() in pk_cols,
                })
    return out


def _extract_tables_in_use(blocks: List[Dict[str, Any]]) -> List[str]:
    """Scans FROM/JOIN block values for "SCHEMA.TABLE" tokens, returning
    the (uppercased, order-preserving, de-duplicated) list of tables the
    query currently touches."""
    tables: List[str] = []
    for b in blocks:
        if b["type"] in ("FROM", "JOIN"):
            m = _SCHEMA_TABLE_RE.search(b.get("value") or "")
            if m:
                key = f"{m.group(1).upper()}.{m.group(2).upper()}"
                if key not in tables:
                    tables.append(key)
    return tables


def _suggest_relationships(manager, tables_in_use: List[str]) -> List[Dict[str, Any]]:
    """Cross-references the tables already used in FROM/JOIN blocks against
    the Schema Manager's registered relationships (built in the
    Relationships sub-tab of the Schema tab). Returns a list of
    {rel, kind, other_table} — kind is "ready" (both sides already used,
    so this relationship can directly become a JOIN block) or "suggest"
    (only one side is used — joining the other table would likely make
    the results more precise)."""
    if manager is None or not tables_in_use:
        return []
    tables_set = set(tables_in_use)
    out: List[Dict[str, Any]] = []
    seen = set()
    for rel in manager.relationships:
        from_t, to_t = rel.from_table.upper(), rel.to_table.upper()
        from_in, to_in = from_t in tables_set, to_t in tables_set
        if not from_in and not to_in:
            continue
        if from_in and to_in:
            sig = ("ready", from_t, to_t)
            if sig in seen:
                continue
            seen.add(sig)
            out.append({"rel": rel, "kind": "ready", "other_table": to_t})
        else:
            other = to_t if from_in else from_t
            sig = ("suggest", other)
            if sig in seen:
                continue
            seen.add(sig)
            out.append({"rel": rel, "kind": "suggest", "other_table": other})
    return out


def _join_block_value(rel, tables_in_use: List[str]) -> str:
    """Builds a ready-to-use JOIN block value from a SchemaRelationship."""
    # Whichever side of the relationship ISN'T already in use is the table
    # we're joining onto the query.
    from_t, to_t = rel.from_table.upper(), rel.to_table.upper()
    if from_t in tables_in_use and to_t not in tables_in_use:
        target = rel.to_table
    elif to_t in tables_in_use and from_t not in tables_in_use:
        target = rel.from_table
    else:
        target = rel.to_table
    return f"{rel.join_type} JOIN {target} ON {rel.from_table}.{rel.from_column} = {rel.to_table}.{rel.to_column}"


def render_query_builder_tab() -> None:
    _init_state()

    st.markdown(f'<div class="section-title">{t("qbuilder.title")}</div>', unsafe_allow_html=True)
    st.caption(t("qbuilder.subtitle"))

    conn = st.session_state.get("conn")
    connected = st.session_state.get("connected", False)

    if "schema_manager" not in st.session_state and SchemaManager is not None:
        st.session_state["schema_manager"] = SchemaManager()
    manager = st.session_state.get("schema_manager")

    col_blocks, col_fields = st.columns([3, 2])

    # ── Block stack (left) ────────────────────────────────────
    with col_blocks:
        st.markdown(f"#### {t('qbuilder.blocks_heading')}")

        blocks = _blocks()
        if not blocks:
            st.info(t("qbuilder.no_blocks_hint"), icon="🧩")

        last_type = blocks[-1]["type"] if blocks and blocks[-1]["type"] != "FINISH" else (
            blocks[-2]["type"] if len(blocks) >= 2 and blocks[-1]["type"] == "FINISH" else None
        )

        for b in list(blocks):
            meta = BLOCK_TYPES.get(b["type"], {"color": "#334155", "icon": "◽"})
            is_active = st.session_state.get("qb_active_block") == b["id"]
            border_style = "3px solid #e2e8f0" if is_active else f"1px solid {meta['color']}"
            st.markdown(
                f'<div style="border-left: 6px solid {meta["color"]}; border-top:{border_style}; '
                f'border-right:{border_style}; border-bottom:{border_style}; border-radius:8px; '
                f'padding:6px 10px; margin-bottom:4px;">'
                f'<b>{meta["icon"]} {b["type"]}</b></div>',
                unsafe_allow_html=True,
            )
            bc1, bc2, bc3, bc4 = st.columns([5, 1, 1, 1])
            if b["type"] not in ("FINISH", "DISTINCT"):
                new_val = bc1.text_input(
                    "value", value=b["value"], key=f"qb_val_{b['id']}",
                    placeholder=meta.get("placeholder", ""), label_visibility="collapsed",
                    on_change=lambda bid=b["id"]: st.session_state.__setitem__("qb_active_block", bid),
                )
                b["value"] = new_val
            elif b["type"] == "DISTINCT":
                bc1.caption(t("qbuilder.distinct_caption"))
            else:
                bc1.caption(t("qbuilder.end_of_query"))
            if bc2.button("▲", key=f"qb_up_{b['id']}", width="stretch"):
                _move(b["id"], -1)
                st.rerun()
            if bc3.button("▼", key=f"qb_down_{b['id']}", width="stretch"):
                _move(b["id"], 1)
                st.rerun()
            if bc4.button("🗑️", key=f"qb_del_{b['id']}", width="stretch"):
                _remove(b["id"])
                st.rerun()
            if b["type"] not in ("FINISH", "DISTINCT") and st.button(
                t("qbuilder.set_active_button"), key=f"qb_active_{b['id']}", width="stretch"
            ):
                st.session_state["qb_active_block"] = b["id"]
                st.rerun()

        st.divider()
        st.markdown(f"##### {t('qbuilder.add_block_heading')}")
        suggested = _SUGGEST_AFTER.get(last_type, list(BLOCK_TYPES.keys()))
        if suggested:
            st.caption(t("qbuilder.suggested_next") + " " + " · ".join(suggested))
            sugg_cols = st.columns(len(suggested))
            for i, s in enumerate(suggested):
                if sugg_cols[i].button(f"{BLOCK_TYPES[s]['icon']} {s}", key=f"qb_sugg_{s}", width="stretch"):
                    _add_block(s)
                    st.rerun()

        add_col1, add_col2 = st.columns([3, 1])
        chosen_type = add_col1.selectbox(
            t("qbuilder.block_type_label"), options=list(BLOCK_TYPES.keys()), key="qb_add_type", label_visibility="collapsed",
        )
        if add_col2.button(t("qbuilder.add_button"), key="qb_add_btn", width="stretch"):
            _add_block(chosen_type)
            st.rerun()

        if blocks and st.button(t("qbuilder.clear_all_button"), key="qb_clear_all"):
            st.session_state["qb_blocks"] = []
            st.session_state["qb_active_block"] = None
            st.rerun()

    # ── Field picker + function palette (right) ─────────────────
    with col_fields:
        active_id = st.session_state.get("qb_active_block")
        active_block = next((b for b in _blocks() if b["id"] == active_id), None)
        if active_block:
            st.caption(t("qbuilder.inserting_into", block_type=active_block["type"]))
        else:
            st.caption(t("qbuilder.select_block_hint"))

        field_tab, func_tab, join_tab = st.tabs([t("qbuilder.fields_tab"), t("qbuilder.functions_tab"), t("qbuilder.joins_tab")])

        with field_tab:
            all_cols = _all_columns(manager)
            types_available = sorted({c["type"] for c in all_cols if c["type"] and c["type"] != "—"})
            fc1, fc2 = st.columns([2, 1])
            search = fc1.text_input(t("qbuilder.search_columns"), key="qb_field_search", placeholder=t("qbuilder.search_columns_placeholder"))
            type_filter = fc2.selectbox(t("qbuilder.filter_type"), ["All"] + types_available, key="qb_field_type_filter")

            if search:
                s_low = search.lower()
                all_cols = [c for c in all_cols if s_low in c["column"].lower() or s_low in c["table"].lower()]
            if type_filter and type_filter != "All":
                all_cols = [c for c in all_cols if c["type"] == type_filter]

            if not all_cols:
                st.caption(t("qbuilder.no_matching_columns"))
            else:
                options = [
                    f"{'🔑 ' if c['is_pk'] else ''}{c['schema']}.{c['table']}.{c['column']}  ({c['type']})"
                    for c in all_cols
                ]
                picked = st.selectbox(
                    t("qbuilder.column_dropdown_label"), options, key="qb_field_dropdown", label_visibility="collapsed",
                )
                if st.button(t("qbuilder.insert_field_button"), key="qb_insert_field_btn", width="stretch"):
                    if active_block is not None:
                        idx = options.index(picked)
                        col_name = all_cols[idx]["column"]
                        current = st.session_state.get(f"qb_val_{active_block['id']}", active_block["value"])
                        sep = ", " if current.strip() else ""
                        active_block["value"] = f"{current}{sep}{col_name}"
                        st.session_state[f"qb_val_{active_block['id']}"] = active_block["value"]
                        st.rerun()
                    else:
                        st.warning(t("qbuilder.set_active_first_warning"), icon="⚠️")
                st.caption(t("qbuilder.matches_count", count=len(all_cols)))
                st.caption(t("qbuilder.pk_legend"))

        with func_tab:
            func_categories = ["All"] + get_categories()
            fn1, fn2 = st.columns([2, 1])
            func_search = fn1.text_input(t("qbuilder.search_functions"), key="qb_func_search", placeholder=t("qbuilder.search_functions_placeholder"))
            func_category = fn2.selectbox(t("qbuilder.filter_category"), func_categories, key="qb_func_category_filter")

            funcs = SQL_FUNCTIONS
            if func_category != "All":
                funcs = [f for f in funcs if f["category"] == func_category]
            if func_search:
                fs_low = func_search.lower()
                funcs = [f for f in funcs if fs_low in f["name"].lower() or fs_low in f["description"].lower()]

            st.caption(t("qbuilder.functions_hint"))
            for f in funcs[:30]:
                with st.expander(f"{f['name']}"):
                    st.caption(f["description"])
                    st.code(f["syntax"], language="sql")
                    if st.button(t("qbuilder.insert_snippet_button"), key=f"qb_fn_{f['name']}", width="stretch"):
                        if active_block is not None:
                            current = st.session_state.get(f"qb_val_{active_block['id']}", active_block["value"])
                            active_block["value"] = f"{current}{f['snippet']}"
                            st.session_state[f"qb_val_{active_block['id']}"] = active_block["value"]
                            st.rerun()
                        else:
                            st.warning(t("qbuilder.set_active_first_warning"), icon="⚠️")

        with join_tab:
            tables_in_use = _extract_tables_in_use(_blocks())
            if not tables_in_use:
                st.caption(t("qbuilder.joins_need_from"))
            else:
                st.caption(t("qbuilder.joins_tables_in_use", tables=", ".join(tables_in_use)))
                suggestions = _suggest_relationships(manager, tables_in_use)
                if not suggestions:
                    st.caption(t("qbuilder.joins_no_suggestions"))
                for i, s in enumerate(suggestions):
                    rel = s["rel"]
                    if s["kind"] == "ready":
                        label = t(
                            "qbuilder.join_ready_label",
                            from_table=rel.from_table, to_table=rel.to_table,
                            join_type=rel.join_type, description=rel.description,
                        )
                        icon = "✅"
                    else:
                        label = t(
                            "qbuilder.join_suggest_label",
                            other_table=s["other_table"], description=rel.description,
                        )
                        icon = "💡"
                    with st.expander(f"{icon} {label}"):
                        st.code(
                            f"{rel.join_type} JOIN {rel.to_table} ON {rel.from_table}.{rel.from_column} = {rel.to_table}.{rel.to_column}",
                            language="sql",
                        )
                        if st.button(t("qbuilder.add_join_block_button"), key=f"qb_join_add_{i}", width="stretch"):
                            value = _join_block_value(rel, tables_in_use)
                            _add_block("JOIN")
                            new_block_id = st.session_state["qb_active_block"]
                            new_block = next(b for b in _blocks() if b["id"] == new_block_id)
                            new_block["value"] = value
                            st.session_state[f"qb_val_{new_block['id']}"] = value
                            st.rerun()

    # ── Assembled SQL + RUN ─────────────────────────────────────
    st.divider()
    st.markdown(f"#### {t('qbuilder.assembled_sql_heading')}")
    sql = _assemble_sql(_blocks())
    if not sql.strip():
        st.caption(t("qbuilder.add_blocks_hint"))
    else:
        st.code(sql, language="sql")

        # The "fine-tune" box is bound to a persistent widget key so manual
        # edits survive reruns — but that means Streamlit ignores `value=`
        # on later reruns (a well-known gotcha). Without this check, editing
        # the blocks again after having blanked/edited the box would silently
        # keep sending the STALE (possibly empty) text to Oracle, causing
        # "DPY-2066: an empty statement cannot be executed". So: only reset
        # the box to the freshly assembled SQL when the assembled SQL itself
        # actually changed (new/edited/reordered blocks) — otherwise leave
        # the user's manual edits alone.
        if st.session_state.get("qb_last_assembled_sql") != sql:
            st.session_state["qb_final_sql"] = sql
            st.session_state["qb_last_assembled_sql"] = sql

        edited_sql = st.text_area(t("qbuilder.fine_tune_label"), height=120, key="qb_final_sql")

        # Type-aware validation: cross-check every "column = literal"
        # comparison in the query against the column's real Oracle data
        # type (retrieved earlier via Schema → 🔄 Retrieve column details)
        # so the user gets a heads-up BEFORE running a query that Oracle
        # would reject (or silently misinterpret) due to a type mismatch.
        type_warnings = check_type_hints(edited_sql, manager)
        for w in type_warnings:
            st.warning(w, icon="🧭")

        run_col, _ = st.columns([1, 4])
        if run_col.button(t("qbuilder.run_button"), type="primary", width="stretch", key="qb_run_btn"):
            if not connected or conn is None:
                st.error(t("qbuilder.connect_first_error"), icon="🔌")
            elif not (edited_sql or "").strip():
                st.warning(t("qbuilder.empty_query_warning"), icon="📝")
            else:
                # Security gate: this box lets any logged-in user free-type
                # SQL (including a raw "CUSTOM" block), so it MUST go
                # through the same read-only + table-allowlist validation
                # as the AI Query Builder before ever reaching Oracle —
                # otherwise any user could run DELETE/DROP/UPDATE/etc.
                is_valid, validation_error = validate_sql(edited_sql)
                if not is_valid:
                    st.error(f"{validation_error} — {t('qbuilder.validation_blocked_hint')}", icon="🛡️")
                    st.session_state["qb_result_df"] = None
                else:
                    try:
                        MAX_ROWS = 5000
                        with st.spinner(t("qbuilder.running_spinner")):
                            cursor = conn.cursor()
                            cursor.execute(edited_sql)
                            cols = [c[0] for c in cursor.description] if cursor.description else []
                            rows = cursor.fetchmany(MAX_ROWS)
                            truncated = cursor.fetchone() is not None
                            cursor.close()
                        df = pd.DataFrame(rows, columns=cols)
                        st.session_state["qb_result_df"] = df
                        st.success(t("qbuilder.rows_returned", count=len(df)))
                        if truncated:
                            st.warning(
                                f"⚠️ Result set truncated to the first {MAX_ROWS} rows.",
                                icon="⚠️",
                            )
                    except Exception as e:
                        st.error(t("qbuilder.query_failed", error=e))
                        st.session_state["qb_result_df"] = None

        result_df: Optional[pd.DataFrame] = st.session_state.get("qb_result_df")
        if result_df is not None:
            st.dataframe(result_df, width="stretch", height=380)
            exp1, exp2, _ = st.columns([1, 1, 4])
            exp1.download_button(
                "⬇️ CSV", data=df_to_csv_bytes(result_df), file_name="query_builder_result.csv",
                mime="text/csv", width="stretch", key="qb_export_csv",
            )
            exp2.download_button(
                "⬇️ Excel", data=df_to_excel_bytes(result_df), file_name="query_builder_result.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch", key="qb_export_xlsx",
            )

