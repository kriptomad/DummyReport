"""
GitHub Copilot Integration - Text-to-SQL usando autenticação SSO corporativa
Versão 2.0 - Julho 2026

Este módulo implementa a integração com GitHub Copilot Enterprise usando:
- Autenticação SSO via GitHub CLI
- Fallback automático para OpenAI/Gemini
- Cache de queries
- Rate limiting inteligente
"""

import os
import subprocess
import json
import time
import hashlib
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime, timedelta
import requests


class GitHubCopilotSSO:
    """
    Cliente GitHub Copilot com autenticação SSO corporativa.

    Fluxo de autenticação:
    1. Verifica se GitHub CLI está instalado
    2. Verifica autenticação SSO
    3. Obtém token de sessão
    4. Usa token para chamadas API
    """

    def __init__(self, cache_dir: str = ".cache"):
        """
        Args:
            cache_dir: Diretório para cache de queries
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

        self.authenticated = False
        self.token = None
        self.session_expires = None

        # Rate limiting
        self.last_request_time = None
        self.min_request_interval = 1.0  # segundos entre requests

        # Inicializa autenticação
        self._initialize_auth()

    def _initialize_auth(self):
        """Inicializa autenticação SSO"""

        # 1. Verifica GitHub CLI
        if not self._check_github_cli():
            raise RuntimeError(
                "GitHub CLI não encontrado.\n"
                "Instale: winget install --id GitHub.cli\n"
                "Ou baixe: https://cli.github.com/"
            )

        # 2. Verifica autenticação
        if not self._check_auth_status():
            raise RuntimeError(
                "GitHub CLI não está autenticado.\n"
                "Execute: gh auth login --web\n"
                "Use sua conta corporativa SSO"
            )

        # 3. Obtém token
        self.token = self._get_token()

        if self.token:
            self.authenticated = True
            self.session_expires = datetime.now() + timedelta(hours=8)
            print("✅ GitHub Copilot SSO autenticado com sucesso!")
        else:
            print("⚠️ Token não obtido, usando fallback")

    def _check_github_cli(self) -> bool:
        """Verifica se GitHub CLI está instalado"""
        try:
            result = subprocess.run(
                ["gh", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False

    def _check_auth_status(self) -> bool:
        """Verifica se está autenticado com SSO"""
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5
            )

            # `gh auth status` exits with code 0 only when authenticated;
            # avoid matching on locale-specific text like "Logged in".
            return result.returncode == 0
        except:
            return False

    def _get_token(self) -> Optional[str]:
        """Obtém token de autenticação"""

        # Tenta variáveis de ambiente primeiro
        token = (
            os.getenv("GITHUB_COPILOT_TOKEN") or
            os.getenv("GITHUB_TOKEN") or
            os.getenv("GH_TOKEN")
        )

        if token:
            return token

        # Tenta via GitHub CLI
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                return result.stdout.strip()
        except:
            pass

        return None

    def _wait_for_rate_limit(self):
        """Implementa rate limiting simples"""
        if self.last_request_time:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)

        self.last_request_time = time.time()

    def _get_cache_key(self, prompt: str) -> str:
        """Gera chave de cache para query"""
        return hashlib.md5(prompt.encode()).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """Busca resultado em cache"""
        cache_file = self.cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Verifica se cache ainda é válido (24h)
                cached_time = datetime.fromisoformat(data['timestamp'])
                if datetime.now() - cached_time < timedelta(hours=24):
                    print("📦 Usando resultado em cache")
                    return data['result']
            except:
                pass

        return None

    def _save_to_cache(self, cache_key: str, result: str):
        """Salva resultado em cache (com permissões restritas ao owner)."""
        cache_file = self.cache_dir / f"{cache_key}.json"

        data = {
            'timestamp': datetime.now().isoformat(),
            'result': result
        }

        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        try:
            os.chmod(cache_file, 0o600)  # read/write for owner only
        except OSError:
            # chmod semantics differ on Windows; safe to ignore there.
            pass

    def generate_sql(
        self,
        user_question: str,
        schema_context: str,
        use_cache: bool = True
    ) -> str:
        """
        Gera SQL a partir de linguagem natural

        Args:
            user_question: Pergunta do usuário
            schema_context: Contexto do schema (tabelas, colunas)
            use_cache: Se True, usa cache de queries

        Returns:
            SQL gerado
        """

        # Monta prompt
        prompt = self._build_prompt(user_question, schema_context)

        # Verifica cache
        if use_cache:
            cache_key = self._get_cache_key(prompt)
            cached = self._get_from_cache(cache_key)
            if cached:
                return cached

        # Tenta GitHub Copilot API (não disponível publicamente ainda)
        # Por enquanto usa OpenAI ou Gemini como fallback

        try:
            # Tenta OpenAI primeiro (mesmo modelo do Copilot)
            sql = self._call_openai(prompt)
        except Exception as openai_error:
            print(f"⚠️ OpenAI falhou: {openai_error}")

            try:
                # Fallback para Gemini (gratuito)
                sql = self._call_gemini(prompt)
            except Exception as gemini_error:
                raise RuntimeError(
                    f"❌ Erro ao gerar SQL:\n"
                    f"- OpenAI: {openai_error}\n"
                    f"- Gemini: {gemini_error}\n\n"
                    f"Configure uma API key:\n"
                    f"$env:GEMINI_API_KEY = 'sua_chave'\n"
                    f"Obtenha em: https://aistudio.google.com/app/apikey"
                )

        # Limpa resultado
        sql = self._clean_sql(sql)

        # Salva em cache
        if use_cache:
            self._save_to_cache(cache_key, sql)

        return sql

    def _build_prompt(self, question: str, schema: str) -> str:
        """Constrói prompt otimizado para geração SQL"""

        return f"""You are an expert Oracle SQL generator.

{schema}

## RULES:
1. Use ONLY tables and columns listed above
2. Always use bind parameters (:param) for values
3. Use explicit JOINs (INNER JOIN, LEFT JOIN)
4. Return ONLY the SQL query, no explanations
5. Use UPPER() for string comparisons when appropriate
6. Always include ORDER BY when relevant
7. For dates, use TO_DATE() or appropriate comparisons

## USER QUESTION:
{question}

## SQL:
"""

    def _call_openai(self, prompt: str) -> str:
        """Chama OpenAI API (mesmo modelo do Copilot)"""

        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY não configurada")

        self._wait_for_rate_limit()

        client = openai.OpenAI(api_key=api_key, timeout=30.0)

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert Oracle SQL generator. Return ONLY SQL, no explanations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.1,
            max_tokens=1500
        )

        return response.choices[0].message.content.strip()

    def _call_gemini(self, prompt: str) -> str:
        """Chama Gemini API (gratuito) usando o SDK novo `google-genai`."""

        from google import genai
        from google.genai import types
        from ai.text_to_sql import _discover_gemini_models

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não configurada")

        self._wait_for_rate_limit()

        # Lista de modelos para fallback (mais novo → mais antigo), usada
        # apenas se a descoberta dinâmica de modelos (abaixo) falhar.
        static_models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-exp",
            "gemini-1.5-flash-latest",
            "gemini-1.5-pro-latest",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-1.5-pro",
        ]

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=30_000),  # ms
        )

        # Descobre dinamicamente quais modelos esta key realmente pode usar
        # (a Google renomeia/aposenta IDs de modelo com frequência — foi
        # isso que causava os 404 "model not found" antes desta mudança).
        discovered = _discover_gemini_models(client)
        models = list(dict.fromkeys(discovered + static_models))

        last_error = None
        for model_name in models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=1500,
                    ),
                )

                text = (response.text or "").strip()
                if text:
                    return text
                last_error = RuntimeError(f"Empty response from model {model_name}")
                continue

            except Exception as e:
                last_error = e
                if "404" in str(e) or "not found" in str(e).lower():
                    continue
                raise

        raise RuntimeError(f"Nenhum modelo Gemini disponível. Último erro: {last_error}")

    def _clean_sql(self, sql: str) -> str:
        """Limpa e valida SQL gerado"""

        # Remove markdown code blocks
        sql = sql.replace("```sql", "").replace("```", "").strip()

        # Remove comentários de linha
        lines = []
        for line in sql.split('\n'):
            if not line.strip().startswith('--'):
                lines.append(line)

        sql = '\n'.join(lines).strip()

        return sql

    def clear_cache(self):
        """Limpa cache de queries"""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
        print("🗑️ Cache limpo")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas de cache"""

        cache_files = list(self.cache_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in cache_files)

        return {
            'total_queries': len(cache_files),
            'total_size_mb': total_size / (1024 * 1024),
            'cache_dir': str(self.cache_dir)
        }


# Singleton instance
_copilot_instance: Optional[GitHubCopilotSSO] = None


def get_copilot_instance() -> GitHubCopilotSSO:
    """Retorna instância singleton do Copilot"""
    global _copilot_instance

    if _copilot_instance is None:
        _copilot_instance = GitHubCopilotSSO()

    return _copilot_instance


def generate_sql_with_copilot(
    user_question: str,
    schema_context: str,
    use_cache: bool = True
) -> str:
    """
    Função helper para gerar SQL com Copilot

    Args:
        user_question: Pergunta em linguagem natural
        schema_context: Contexto do schema
        use_cache: Usar cache de queries

    Returns:
        SQL gerado
    """
    copilot = get_copilot_instance()
    return copilot.generate_sql(user_question, schema_context, use_cache)

