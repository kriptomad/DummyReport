"""
AI Query Builder tab — converts natural language to SQL.
"""
import streamlit as st
import pandas as pd
import subprocess
import os
from ai.text_to_sql import generate_sql_from_text
from ai.sql_validator import validate_sql, sanitize_bind_params
from ai.schema_manager import SchemaManager
from ai.type_hints import check_type_hints
from i18n import t
from typing import Optional


def check_github_sso_status():
    """Verifica status da autenticação SSO do GitHub."""
    return _check_github_sso_status_cached()


@st.cache_data(ttl=60, show_spinner=False)
def _check_github_sso_status_cached():
    try:
        # Tenta gh CLI
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=3
        )

        if result.returncode == 0:
            return {
                "status": "✅ Conectado",
                "method": "GitHub CLI (SSO)",
                "details": result.stdout
            }
        else:
            return {
                "status": "❌ Não autenticado",
                "method": "GitHub CLI",
                "details": "Execute: gh auth login --web"
            }
    except FileNotFoundError:
        # gh CLI não instalado, verifica variáveis de ambiente
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token:
            return {
                "status": "✅ Token configurado",
                "method": "Variável de ambiente",
                "details": "Token configured (hidden)"
            }
        else:
            return {
                "status": "⚠️ GitHub CLI não instalado",
                "method": "N/A",
                "details": "Instale: winget install --id GitHub.cli"
            }
    except Exception as e:
        return {
            "status": "⚠️ Erro ao verificar",
            "method": "N/A",
            "details": str(e)
        }


def render_ai_query_tab(conn, current_shipment_id: Optional[str] = None):
    """
    Renderiza a aba de AI Query Builder.

    Args:
        conn: Conexão Oracle
        current_shipment_id: ID do shipment atualmente selecionado (se houver)
    """
    st.markdown(
        f'<div style="font-size:1.4rem;font-weight:700;margin-bottom:0.1rem;">{t("ai_query.title")}</div>',
        unsafe_allow_html=True,
    )
    st.caption(t("ai_query.intro_caption"))

    # Initialize schema manager
    if "schema_manager" not in st.session_state:
        st.session_state["schema_manager"] = SchemaManager()

    manager: SchemaManager = st.session_state["schema_manager"]

    # ─── TOP CONTROL BAR — provider as small rounded pills ──────
    # Small, clean, clickable pills at the top of the page instead of a
    # buried selectbox inside an expander — the provider choice is the
    # first thing a user should see and pick.
    provider_labels = {
        "github_copilot": "🐙 Copilot",
        "openai":         "🧠 OpenAI",
        "anthropic":      "🎭 Anthropic",
        "gemini":         "✨ Gemini",
        "llama":          "🦙 Llama",
    }
    api_provider = st.pills(
        t("ai_query.provider_label"),
        options=list(provider_labels.keys()),
        format_func=lambda k: provider_labels[k],
        default="github_copilot",
        key="ai_provider_pill",
        label_visibility="collapsed",
    ) or "github_copilot"

    api_key = None
    if api_provider != "llama":
        key_col, status_col = st.columns([2, 1])
        with key_col:
            help_text = t("ai_query.api_key_env_help")
            if api_provider == "github_copilot":
                help_text = t("ai_query.api_key_gh_help")
            api_key = st.text_input(
                t("ai_query.api_key_label", provider=provider_labels[api_provider]),
                type="password",
                help=help_text,
                placeholder=t("ai_query.api_key_placeholder"),
                label_visibility="collapsed",
            )
        with status_col:
            if api_provider == "github_copilot":
                sso_status = check_github_sso_status()
                st.caption(f"{sso_status['status']} · {sso_status['method']}")
            elif api_provider == "gemini":
                st.caption(t("ai_query.requires_env_caption", env_var="GEMINI_API_KEY"))
            elif api_provider == "openai":
                st.caption(t("ai_query.requires_env_caption", env_var="OPENAI_API_KEY"))
            elif api_provider == "anthropic":
                st.caption(t("ai_query.requires_env_caption", env_var="ANTHROPIC_API_KEY"))

        if api_provider == "github_copilot":
            with st.expander(t("ai_query.sso_expander"), expanded=False):
                sso_status = check_github_sso_status()
                st.code(sso_status["details"], language="text")
                if "❌" in sso_status["status"] or "⚠️" in sso_status["status"]:
                    st.warning(t("ai_query.sso_warning"))

    st.divider()

    # ─── CONTEXTO (se já tem shipment selecionado) ───────
    if current_shipment_id:
        st.info(t("ai_query.context_info", shipment_id=current_shipment_id))

    # ─── INPUT DA PERGUNTA ───────────────────────────────
    with st.expander(t("ai_query.examples_expander"), expanded=False):
        st.markdown(t("ai_query.examples_markdown"))

    user_question = st.text_area(
        t("ai_query.question_label"),
        height=100,
        placeholder=t("ai_query.question_placeholder")
    )

    # ─── GERAÇÃO DO SQL ──────────────────────────────────
    if st.button(t("ai_query.generate_sql"), type="primary", disabled=not user_question, width="stretch"):
        with st.spinner(t("ai_query.generating_sql")):
            try:
                sql = generate_sql_from_text(
                    user_question=user_question,
                    api_provider=api_provider,
                    api_key=api_key,
                    context_shipment_id=current_shipment_id,
                    schema_manager=manager
                )

                # Armazena no session_state
                st.session_state["ai_generated_sql"] = sql
                st.session_state["ai_user_question"] = user_question

            except Exception as e:
                st.error(t("ai_query.generate_error", error=e))
                return

    # ─── EXIBIÇÃO E VALIDAÇÃO DO SQL ─────────────────────
    if "ai_generated_sql" in st.session_state:
        sql = st.session_state["ai_generated_sql"]

        st.subheader(t("ai_query.generated_sql"))
        st.code(sql, language="sql")
        _render_column_lookup(manager, "generated")

        # Validação
        is_valid, error_msg = validate_sql(sql)

        if not is_valid:
            st.error(error_msg)
            st.warning(t("ai_query.edit_sql_warning"))
            sql = st.text_area(t("ai_query.edited_sql_label"), value=sql, height=200)
            _render_column_lookup(manager, "edited")
            is_valid, error_msg = validate_sql(sql)

        for warning in check_type_hints(sql, manager):
            st.warning(warning)

        # ─── PARÂMETROS (se houver bind params) ──────────
        params = _extract_bind_params(sql)
        param_values = {}

        if params:
            st.subheader(t("ai_query.parameters"))
            cols = st.columns(len(params))
            for i, param in enumerate(params):
                with cols[i]:
                    # Auto-preenche se for shipment_id
                    default = current_shipment_id if param in ["shpm_num", "shipment_id"] else ""
                    param_values[param] = st.text_input(
                        f":{param}",
                        value=default,
                        key=f"ai_param_{param}"
                    )

        # ─── EXECUÇÃO ────────────────────────────────────
        can_execute = is_valid and all(param_values.values())

        if st.button(t("ai_query.execute_query"), type="primary", disabled=not can_execute):
            try:
                # Sanitiza parâmetros
                safe_params = sanitize_bind_params(param_values)

                # Executa — com um teto de linhas para não estourar
                # memória/UI se a query (gerada por IA ou editada à mão)
                # não tiver uma cláusula de limite própria.
                MAX_ROWS = 5000
                cursor = conn.cursor()
                cursor.execute(sql, safe_params)

                # Resultados
                cols = [c[0] for c in cursor.description]
                rows = cursor.fetchmany(MAX_ROWS)
                truncated = cursor.fetchone() is not None
                cursor.close()

                df = pd.DataFrame(rows, columns=cols)

                st.success(t("ai_query.rows_found", count=len(df)))
                if truncated:
                    st.warning(
                        f"⚠️ Result set truncated to the first {MAX_ROWS} rows. "
                        "Add a filter or ORDER BY + FETCH FIRST to narrow the query."
                    )
                st.dataframe(df, width="stretch")

                # Botão para exportar
                csv = df.to_csv(index=False)
                st.download_button(
                    label=t("ai_query.download_csv"),
                    data=csv,
                    file_name="ai_query_result.csv",
                    mime="text/csv"
                )

                # Salva no histórico
                _save_to_history(user_question, sql, param_values, len(df))

            except Exception as e:
                st.error(t("ai_query.execute_error", error=e))

    # ─── HISTÓRICO ───────────────────────────────────────
    _render_query_history()


def _extract_bind_params(sql: str) -> list[str]:
    """Extrai nomes de bind parameters (`:param`) do SQL."""
    import re
    return list(set(re.findall(r':(\w+)', sql)))


def _render_column_lookup(manager: SchemaManager, key_suffix: str):
    lookup_value = st.text_input(
        t("ai_query.column_lookup_label"),
        key=f"ai_column_lookup_{key_suffix}",
        placeholder=t("ai_query.column_lookup_placeholder"),
    ).strip()

    if not lookup_value:
        return

    matches = []
    lookup_upper = lookup_value.upper()
    for schema_name, tables in manager.get_all_tables().items():
        for table in tables:
            for column in table.columns:
                if lookup_upper in column.upper():
                    matches.append({
                        t("ai_query.column_lookup_result"): f"{schema_name}.{table.name}.{column}",
                        t("ai_query.column_lookup_type"): (table.column_types or {}).get(column, "—"),
                    })

    if not matches:
        st.info(t("ai_query.column_lookup_no_matches", value=lookup_value))
        return

    st.dataframe(pd.DataFrame(matches[:15]), width="stretch", hide_index=True)


def _save_to_history(question: str, sql: str, params: dict, row_count: int):
    """Salva query no histórico do session_state."""
    if "ai_query_history" not in st.session_state:
        st.session_state["ai_query_history"] = []

    st.session_state["ai_query_history"].insert(0, {
        "question": question,
        "sql": sql,
        "params": params,
        "row_count": row_count,
        "timestamp": pd.Timestamp.now()
    })

    # Limita a 20 últimas
    st.session_state["ai_query_history"] = st.session_state["ai_query_history"][:20]


def _render_query_history():
    """Renderiza histórico de queries anteriores."""
    if "ai_query_history" not in st.session_state or not st.session_state["ai_query_history"]:
        return

    st.divider()
    st.subheader(t("ai_query.history_subheader"))

    for i, entry in enumerate(st.session_state["ai_query_history"][:10]):  # Últimas 10
        with st.expander(
            t(
                "ai_query.history_entry",
                time=entry["timestamp"].strftime("%H:%M:%S"),
                question=entry["question"][:60],
                count=entry["row_count"],
            )
        ):
            st.code(entry["sql"], language="sql")
            if entry["params"]:
                st.json(entry["params"])

            if st.button(t("ai_query.reuse_button"), key=f"reuse_{i}"):
                st.session_state["ai_generated_sql"] = entry["sql"]
                st.session_state["ai_user_question"] = entry["question"]
                st.rerun()
