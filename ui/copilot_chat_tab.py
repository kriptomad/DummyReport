"""
🤖 Copilot Chat - Interface de Chat Interativa com GitHub Copilot
Similar ao chat do PyCharm/VS Code, mas integrado na aplicação web.

Features:
- Autenticação SSO via GitHub
- Chat conversacional em tempo real
- Contexto do banco de dados e queries
- Histórico de conversas
- Sugestões de código SQL
"""

import streamlit as st
import subprocess
import json
import os
from datetime import datetime
from typing import List, Dict, Optional
import requests
from i18n import t


class CopilotChat:
    """Chat interativo com GitHub Copilot via SSO"""

    def __init__(self):
        self.authenticated = False
        self.token = None
        self.chat_history = []
        self.session_id = None

        # Verifica autenticação ao inicializar
        self._check_auth()

    def _check_auth(self) -> bool:
        """Verifica se está autenticado com GitHub SSO"""
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Obtém token
                token_result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if token_result.returncode == 0:
                    self.token = token_result.stdout.strip()
                    self.authenticated = True
                    return True

            return False

        except Exception as e:
            st.error(t("copilot_chat.auth_check_error", error=e))
            return False

    def get_auth_status(self) -> Dict:
        """Retorna status de autenticação detalhado"""
        if not self.authenticated:
            return {
                "authenticated": False,
                "message": t("copilot_chat.auth_status_message"),
                "instructions": [
                    t("copilot_chat.auth_instruction_1"),
                    t("copilot_chat.auth_instruction_2"),
                    t("copilot_chat.auth_instruction_3"),
                    t("copilot_chat.auth_instruction_4"),
                ]
            }

        # Obtém informações do usuário
        try:
            result = subprocess.run(
                ["gh", "api", "user"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                user_info = json.loads(result.stdout)

                return {
                    "authenticated": True,
                    "user": user_info.get("login", "Unknown"),
                    "name": user_info.get("name", ""),
                    "email": user_info.get("email", ""),
                    "avatar": user_info.get("avatar_url", "")
                }
        except:
            pass

        return {
            "authenticated": True,
            "message": t("copilot_chat.auth_status_fallback")
        }

    def send_message(
        self,
        user_message: str,
        context: Optional[Dict] = None
    ) -> str:
        """
        Envia mensagem para Copilot e recebe resposta

        Args:
            user_message: Mensagem do usuário
            context: Contexto adicional (schemas, queries anteriores, etc)

        Returns:
            Resposta do Copilot
        """
        if not self.authenticated:
            error_msg = t("copilot_chat.auth_required_error")
            self._add_to_history(user_message, error_msg)
            return error_msg

        # Monta prompt com contexto
        full_prompt = self._build_prompt(user_message, context)

        # Usa o GitHub Copilot CLI de verdade (via `gh copilot`), não um
        # backend de terceiros. `gh` já baixa/gerencia o binário do Copilot
        # CLI automaticamente e reaproveita a mesma sessão SSO usada acima
        # para autenticação — não precisa de nenhuma API key própria.
        try:
            response = self._call_copilot_cli(full_prompt)
        except Exception as e:
            response = t("copilot_chat.cli_call_error", error=str(e))

        # Sempre registra no histórico — sucesso ou erro — para que a
        # mensagem do usuário e a resposta (mesmo de erro) fiquem visíveis
        # no chat após o rerun. Antes, se a chamada falhasse, nada era
        # adicionado ao histórico e a tela simplesmente recarregava sem
        # mostrar nada, dando a impressão de que o chat não respondia.
        self._add_to_history(user_message, response)

        return response

    def _call_copilot_cli(self, prompt: str) -> str:
        """
        Runs the prompt through the real GitHub Copilot CLI (`gh copilot`),
        non-interactively, and returns just the agent's text response.

        - `-s` / `--silent`: only the response text, no token/credit stats.
        - `--no-color`: avoids ANSI escape codes in the captured output.
        - `--no-ask-user`: the CLI must answer directly instead of trying to
          ask a follow-up question (which would hang in non-interactive mode).
        - No `--allow-*-tools` flags: this is a text Q&A/SQL-generation
          assistant, so no file/shell tool use is granted or needed.
        """
        result = subprocess.run(
            ["gh", "copilot", "-p", prompt, "-s", "--no-color", "--no-ask-user"],
            capture_output=True,
            text=True,
            timeout=90,
        )

        output = (result.stdout or "").strip()
        if result.returncode != 0 or not output:
            err = (result.stderr or "").strip() or "sem saída do Copilot CLI"
            raise RuntimeError(err)

        return output

    def _build_prompt(self, user_message: str, context: Optional[Dict]) -> str:
        """Constrói prompt com contexto"""

        # Base do prompt
        prompt = f"""Você é um assistente SQL especializado em Oracle Database.

## CONTEXTO DO PROJETO:
- Banco de dados: Oracle
- Schema principal: ACME_TMS, RTG_APP
- Tabelas principais:
  * ACME_OMS.DEMO_AUDIT (shipments e erros)
  * RTG_APP.DEMO_ROUTE_RATE (rotas)
  * RTG_APP.DEMO_RATE_CARD (tarifas)
  * RTG_APP.DEMO_RATE (rates)

"""

        # Adiciona contexto extra se fornecido
        if context:
            if 'current_shipment' in context:
                prompt += f"\n## SHIPMENT ATUAL:\n{context['current_shipment']}\n"

            if 'recent_queries' in context:
                prompt += f"\n## QUERIES RECENTES:\n{context['recent_queries']}\n"

            if 'schemas' in context:
                prompt += f"\n## SCHEMAS DISPONÍVEIS:\n{context['schemas']}\n"

        # Adiciona histórico recente (últimas 3 mensagens)
        if self.chat_history:
            prompt += "\n## HISTÓRICO DA CONVERSA:\n"
            for msg in self.chat_history[-3:]:
                prompt += f"User: {msg['user']}\n"
                prompt += f"Assistant: {msg['assistant']}\n\n"

        # Mensagem atual
        prompt += f"\n## PERGUNTA ATUAL:\n{user_message}\n\n"
        prompt += "## SUA RESPOSTA:\n"

        return prompt

    def _add_to_history(self, user_msg: str, assistant_msg: str):
        """Adiciona mensagem ao histórico"""
        self.chat_history.append({
            "user": user_msg,
            "assistant": assistant_msg,
            "timestamp": datetime.now().isoformat()
        })

        # Mantém apenas últimas 20 mensagens
        if len(self.chat_history) > 20:
            self.chat_history = self.chat_history[-20:]

    def clear_history(self):
        """Limpa histórico de chat"""
        self.chat_history = []

    def export_history(self) -> str:
        """Exporta histórico para markdown"""
        if not self.chat_history:
            return "Nenhuma conversa ainda."

        md = "# 💬 Histórico de Chat - Copilot\n\n"
        md += f"**Sessão:** {self.session_id}\n"
        md += f"**Data:** {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        md += "---\n\n"

        for i, msg in enumerate(self.chat_history, 1):
            md += f"## Conversa {i}\n\n"
            md += f"**👤 Você:**\n{msg['user']}\n\n"
            md += f"**🤖 Copilot:**\n{msg['assistant']}\n\n"
            md += "---\n\n"

        return md


def render_copilot_chat_tab():
    """
    Renderiza aba de Copilot Chat no Streamlit
    Interface similar ao chat do PyCharm
    """

    st.title(t("copilot_chat.title"))
    st.markdown(t("copilot_chat.subtitle"))
    st.markdown("---")

    # Inicializa chat na sessão
    if 'copilot_chat' not in st.session_state:
        st.session_state.copilot_chat = CopilotChat()

    chat = st.session_state.copilot_chat

    # Verifica autenticação
    auth_status = chat.get_auth_status()

    # Sidebar com status e controles
    with st.sidebar:
        st.subheader(t("copilot_chat.auth_subheader"))

        if auth_status["authenticated"]:
            st.success(t("copilot_chat.auth_status_ok"))

            if "user" in auth_status:
                st.info(t("copilot_chat.user_label", user=auth_status["user"]))
                if auth_status.get("name"):
                    st.write(f"📝 {auth_status['name']}")

            if st.button(t("copilot_chat.refresh_status")):
                chat._check_auth()
                st.rerun()

        else:
            st.error(t("copilot_chat.not_authenticated"))

            with st.expander(t("copilot_chat.how_to_authenticate")):
                for instruction in auth_status.get("instructions", []):
                    st.write(instruction)

            if st.button(t("copilot_chat.verify_authentication")):
                chat._check_auth()
                st.rerun()

        st.markdown("---")

        # Controles do chat
        st.subheader(t("copilot_chat.controls_subheader"))

        if st.button(t("copilot_chat.clear_history")):
            chat.clear_history()
            st.success(t("copilot_chat.history_cleared"))
            st.rerun()

        if st.button(t("copilot_chat.export_conversation")):
            md = chat.export_history()
            st.download_button(
                t("copilot_chat.download_markdown"),
                md,
                "copilot_chat_history.md",
                "text/markdown"
            )

        st.markdown("---")
        st.caption(t("copilot_chat.message_count", count=len(chat.chat_history)))

    # Área principal de chat
    if not auth_status["authenticated"]:
        st.warning(t("copilot_chat.auth_required_warning"))
        st.info(t("copilot_chat.follow_sidebar_instructions"))
        return

    # Container de histórico de chat
    chat_container = st.container()

    with chat_container:
        if not chat.chat_history:
            st.info(t("copilot_chat.welcome"))

            # Sugestões
            st.markdown(t("copilot_chat.try_asking"))

            col1, col2 = st.columns(2)

            with col1:
                if st.button(t("copilot_chat.suggestion_search_errors_label")):
                    st.session_state.suggested_message = t("copilot_chat.suggestion_search_errors_prompt")

                if st.button(t("copilot_chat.suggestion_tariff_label")):
                    st.session_state.suggested_message = t("copilot_chat.suggestion_tariff_prompt")

            with col2:
                if st.button(t("copilot_chat.suggestion_origin_dest_label")):
                    st.session_state.suggested_message = t("copilot_chat.suggestion_origin_dest_prompt")

                if st.button(t("copilot_chat.suggestion_optimize_label")):
                    st.session_state.suggested_message = t("copilot_chat.suggestion_optimize_prompt")

        else:
            # Exibe histórico
            for i, msg in enumerate(chat.chat_history):
                # Mensagem do usuário
                with st.chat_message("user"):
                    st.markdown(msg["user"])

                # Resposta do Copilot
                with st.chat_message("assistant", avatar="🤖"):
                    st.markdown(msg["assistant"])

    # Input de mensagem (sempre no final)
    st.markdown("---")

    # Usa mensagem sugerida se houver
    default_message = st.session_state.get('suggested_message', '')
    if default_message:
        del st.session_state.suggested_message

    # Container de contexto (opcional)
    with st.expander(t("copilot_chat.add_context")):
        context_shipment = st.text_input(
            t("copilot_chat.current_shipment_label"),
            placeholder=t("copilot_chat.current_shipment_placeholder")
        )

        context_query = st.text_area(
            t("copilot_chat.working_query_label"),
            placeholder=t("copilot_chat.working_query_placeholder")
        )

    # Input principal
    user_message = st.chat_input(
        t("copilot_chat.chat_input_placeholder"),
        key="chat_input"
    )

    # Processa mensagem
    if user_message or default_message:
        message = user_message or default_message

        # Monta contexto
        context = {}

        if context_shipment:
            context['current_shipment'] = f"Shipment ID: {context_shipment}"

        if context_query:
            context['recent_queries'] = context_query

        # Adiciona schemas do banco (se disponível)
        if 'schema_manager' in st.session_state:
            context['schemas'] = st.session_state.schema_manager.export_for_llm()

        # Envia mensagem (a resposta já fica registrada no histórico dentro
        # de send_message, seja sucesso ou erro — não precisamos capturá-la
        # aqui, só disparar o rerun para exibi-la)
        with st.spinner(t("copilot_chat.thinking")):
            chat.send_message(message, context)

        # Recarrega para mostrar nova mensagem
        st.rerun()


# Função auxiliar para integrar na aba principal
def render_in_main_app():
    """
    Adicione esta função no app.py para criar a aba de chat

    Exemplo:
    ```python
    with tab_copilot_chat:
        render_copilot_chat_tab()
    ```
    """
    render_copilot_chat_tab()


if __name__ == "__main__":
    # Teste standalone
    st.set_page_config(
        page_title="Copilot Chat",
        page_icon="🤖",
        layout="wide"
    )

    render_copilot_chat_tab()
