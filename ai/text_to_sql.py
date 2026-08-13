"""
Text-to-SQL generator — converts natural language to SQL.
Supports GitHub Copilot, OpenAI, Anthropic, or local LLMs.
"""
from typing import Optional
import os
import time
from ai.schema_manager import SchemaManager

# Simple module-level rate limiter shared by all providers (avoids hammering
# free-tier APIs when the user submits several questions in quick succession).
_MIN_REQUEST_INTERVAL = 1.0  # seconds
_last_request_time: Optional[float] = None


def _wait_for_rate_limit() -> None:
    global _last_request_time
    if _last_request_time is not None:
        elapsed = time.time() - _last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def generate_sql_from_text(
    user_question: str,
    api_provider: str = "github_copilot",  # "github_copilot" | "openai" | "anthropic" | "gemini" | "llama"
    api_key: Optional[str] = None,
    context_shipment_id: Optional[str] = None,
    schema_manager: Optional[SchemaManager] = None
) -> str:
    """
    Gera SQL a partir de pergunta em linguagem natural.

    Args:
        user_question: Pergunta do usuário
        api_provider: Provedor de API ("github_copilot", "openai", "anthropic", "gemini", "llama")
        api_key: Chave da API (não necessário para GitHub Copilot se já autenticado)
        context_shipment_id: Se já tem um shipment selecionado, inclui no contexto
        schema_manager: Instância do SchemaManager (se None, cria uma nova)

    Returns:
        SQL gerado
    """
    # Initialize schema manager if not provided
    if schema_manager is None:
        schema_manager = SchemaManager()

    schema_desc = schema_manager.export_for_llm()

    # Contexto adicional se já tem um shipment selecionado
    context = ""
    if context_shipment_id:
        context = f"\n**CONTEXTO**: O usuário está analisando o SHIPMENT_ID = '{context_shipment_id}'"

    prompt = f"""
Você é um especialista em SQL para Oracle Database.

{schema_desc}

## REGRAS IMPORTANTES:
1. Use APENAS as tabelas e colunas listadas acima
2. Sempre use bind parameters (`:param`) para valores dinâmicos
3. Prefira JOINs explícitos (INNER JOIN, LEFT JOIN) em vez de vírgulas
4. Retorne SOMENTE o SQL, sem explicações ou markdown
5. Use UPPER() para comparações de strings quando apropriado
6. Sempre inclua ORDER BY quando relevante
7. Para datas, use TO_DATE() ou comparações apropriadas

{context}

## PERGUNTA DO USUÁRIO:
{user_question}

## SQL:
"""

    # ─── GITHUB COPILOT ───────────────────────────────────
    if api_provider == "github_copilot":
        return _call_github_copilot_api(prompt, api_key)

    # ─── OPENAI (GPT-4) ───────────────────────────────────
    elif api_provider == "openai":
        return _call_openai_api(prompt, api_key)

    # ─── ANTHROPIC (CLAUDE) ───────────────────────────────────
    elif api_provider == "anthropic":
        return _call_anthropic_api(prompt, api_key)

    # ─── GOOGLE GEMINI ────────────────────────────────────────
    elif api_provider == "gemini":
        return _call_gemini_api(prompt, api_key)

    # ─── LLAMA (LOCAL) ────────────────────────────────────────
    elif api_provider == "llama":
        return _call_llama_api(prompt)

    else:
        raise ValueError(f"Provedor desconhecido: {api_provider}")


# ═══════════════════════════════════════════════════════════
#  API IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════

def _call_github_copilot_api(prompt: str, api_key: Optional[str]) -> str:
    """
    GitHub Copilot - usa OpenAI ou Gemini como backend.

    NOTA IMPORTANTE: GitHub Copilot não tem API REST pública para text-to-SQL.
    Esta implementação usa OpenAI (GPT-4) ou Gemini como fallback automático.

    GitHub Copilot Enterprise usa GPT-4 internamente, então você terá
    a mesma qualidade usando OpenAI diretamente ou Gemini (gratuito).
    """
    import subprocess

    # Verifica autenticação GitHub SSO (informativo)
    github_authenticated = False
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if result.returncode == 0:
            github_authenticated = True
            print("✓ GitHub SSO autenticado")
    except:
        pass

    # Tenta usar OpenAI primeiro (mesmo modelo do Copilot)
    print("→ GitHub Copilot: Usando OpenAI GPT-4 como backend...")

    try:
        return _call_openai_api(prompt, api_key)
    except Exception as openai_error:
        # Se OpenAI falhar, tenta Gemini (gratuito)
        print("→ OpenAI falhou, tentando Gemini (gratuito)...")

        try:
            return _call_gemini_api(prompt, api_key)
        except Exception as gemini_error:
            # Se ambos falharem, mostra mensagem clara
            raise ValueError(
                f"❌ Não foi possível gerar SQL.\n\n"
                f"GitHub Copilot não tem API pública para text-to-SQL.\n\n"
                f"💡 Configure uma destas opções:\n\n"
                f"1. OpenAI (mesmo modelo do Copilot):\n"
                f"   set OPENAI_API_KEY=sua_chave\n"
                f"   Obtenha em: https://platform.openai.com/api-keys\n\n"
                f"2. Gemini (GRATUITO! ⭐ RECOMENDADO):\n"
                f"   set GEMINI_API_KEY=sua_chave\n"
                f"   Obtenha em: https://aistudio.google.com/app/apikey\n\n"
                f"3. Llama (local, sem API):\n"
                f"   Instale Ollama: https://ollama.ai/\n\n"
                f"Erros:\n"
                f"- OpenAI: {openai_error}\n"
                f"- Gemini: {gemini_error}"
            )


def _call_openai_api(prompt: str, api_key: Optional[str]) -> str:
    """
    OpenAI GPT-4 API.
    """
    try:
        import openai

        key = api_key or os.getenv("OPENAI_API_KEY")

        if not key:
            raise ValueError(
                "OpenAI API key não encontrada. "
                "Configure OPENAI_API_KEY no ambiente ou passe api_key."
            )

        client = openai.OpenAI(api_key=key, timeout=30.0)

        _wait_for_rate_limit()
        response = client.chat.completions.create(
            model="gpt-4o",  # current flagship model (gpt-4-turbo-preview was retired)
            messages=[
                {"role": "system", "content": "You are an expert SQL generator for Oracle Database. Return ONLY the SQL query, no explanations or markdown."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=1500
        )

        sql = response.choices[0].message.content.strip()

        # Remove markdown code blocks
        sql = sql.replace("```sql", "").replace("```", "").strip()

        return sql

    except ImportError:
        raise ImportError(
            "openai package não instalado. Execute: pip install openai"
        )
    except Exception as e:
        raise RuntimeError(f"Erro ao chamar OpenAI API: {e}")


def _call_anthropic_api(prompt: str, api_key: Optional[str]) -> str:
    """
    Anthropic Claude API, with a fallback list of models in case the
    pinned snapshot model gets retired.
    """
    try:
        import anthropic

        key = api_key or os.getenv("ANTHROPIC_API_KEY")

        if not key:
            raise ValueError(
                "Anthropic API key não encontrada. "
                "Configure ANTHROPIC_API_KEY no ambiente ou passe api_key."
            )

        client = anthropic.Anthropic(api_key=key, timeout=30.0)

        # Ordered by preference; falls back to older/other snapshots if the
        # newest one is unavailable for this account/region.
        ANTHROPIC_MODELS = [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-sonnet-20240620",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
        ]

        last_error = None
        _wait_for_rate_limit()
        for model_name in ANTHROPIC_MODELS:
            try:
                response = client.messages.create(
                    model=model_name,
                    max_tokens=2000,
                    temperature=0.1,
                    system="You are an expert SQL generator for Oracle Database. Return ONLY the SQL query, no explanations or markdown.",
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )

                sql = response.content[0].text.strip()

                # Remove markdown code blocks
                sql = sql.replace("```sql", "").replace("```", "").strip()

                return sql

            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                if "not_found" in error_msg or "404" in error_msg or "model" in error_msg:
                    continue
                raise

        raise RuntimeError(
            f"Nenhum modelo Anthropic disponível. Último erro: {last_error}"
        )

    except ImportError:
        raise ImportError(
            "anthropic package não instalado. Execute: pip install anthropic"
        )
    except Exception as e:
        raise RuntimeError(f"Erro ao chamar Anthropic API: {e}")


def _discover_gemini_models(client) -> list:
    """
    Queries the Gemini API for the models this specific API key can
    actually use with generateContent, instead of guessing from a hardcoded
    name list (Google frequently renames/retires Gemini model IDs, which is
    exactly what caused persistent 404 "model not found" errors before this
    was added).

    Returns model names ordered with "flash" models first (fast/cheap, and
    usually have more free-tier quota headroom than "pro"), newest first.
    Returns [] on any failure so the caller can fall back to a static list.
    """
    try:
        names = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            name = name.replace("models/", "").strip()
            if not name or "gemini" not in name.lower():
                continue

            # Skip non-text-generation model variants.
            skip_markers = ("embedding", "aqa", "vision", "tts", "image", "imagen", "veo", "learnlm")
            if any(marker in name.lower() for marker in skip_markers):
                continue

            methods = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            if methods and "generateContent" not in methods:
                continue

            names.append(name)

        # Prefer "flash" models (cheaper/faster, usually more free-tier
        # quota) over "pro", and keep the API's own ordering (newest first)
        # within each group.
        flash = [n for n in names if "flash" in n.lower()]
        others = [n for n in names if n not in flash]
        return flash + others
    except Exception:
        return []


def _call_gemini_api(prompt: str, api_key: Optional[str]) -> str:
    """
    Google Gemini API com fallback automático de modelos.

    Usa o SDK novo `google-genai` (pacote `google.genai`), que é o que está
    listado em requirements.txt. O SDK antigo `google-generativeai`
    (`google.generativeai`) NÃO é instalado por este projeto — usá-lo
    causava ImportError em tempo de execução.
    """
    try:
        from google import genai
        from google.genai import types

        key = api_key or os.getenv("GEMINI_API_KEY")

        if not key:
            raise ValueError(
                "Gemini API key não encontrada. "
                "Configure GEMINI_API_KEY no ambiente ou passe api_key.\n"
                "Obtenha em: https://aistudio.google.com/app/apikey"
            )

        # Modelos Gemini ordenados por preferência (mais novo → mais antigo).
        # Usados como fallback caso a descoberta dinâmica de modelos (abaixo)
        # falhe — mas a Google renomeia/aposenta IDs de modelo com frequência
        # (foi exatamente isso que causou os 404 "model not found" vistos
        # antes), então a lista dinâmica é sempre tentada primeiro.
        GEMINI_MODELS = [
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
            api_key=key,
            http_options=types.HttpOptions(timeout=30_000),  # ms
        )

        # Descobre dinamicamente quais modelos esta API key realmente pode
        # usar com generateContent, em vez de confiar cegamente em nomes
        # fixos que podem já ter sido descontinuados pela Google.
        discovered = _discover_gemini_models(client)
        # Modelos descobertos primeiro (refletem o que a key tem acesso
        # agora), depois a lista fixa como rede de segurança — sem duplicar.
        candidate_models = list(dict.fromkeys(discovered + GEMINI_MODELS))

        last_error = None
        _wait_for_rate_limit()

        for model_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=1500,
                    ),
                )

                sql = (response.text or "").strip()

                # Remove markdown code blocks
                sql = sql.replace("```sql", "").replace("```", "").strip()

                if sql:
                    return sql
                last_error = RuntimeError(f"Empty response from model {model_name}")
                continue

            except Exception as e:
                last_error = e
                error_msg = str(e)

                # Se é erro de modelo não encontrado, quota excedida (rate
                # limit / 429) ou o modelo está sobrecarregado (503), tenta
                # o próximo modelo da lista de fallback em vez de desistir
                # imediatamente — a quota do free-tier costuma variar por
                # modelo (ex.: gemini-2.5-pro pode estar zerado enquanto
                # gemini-2.0-flash ainda tem cota disponível).
                retryable = (
                    "404" in error_msg or "not found" in error_msg.lower() or "NOT_FOUND" in error_msg
                    or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "quota" in error_msg.lower()
                    or "503" in error_msg or "UNAVAILABLE" in error_msg
                )
                if retryable:
                    continue
                else:
                    # Outro tipo de erro, propaga
                    raise

        # Se chegou aqui, todos os modelos falharam
        quota_hint = ""
        if last_error and ("429" in str(last_error) or "RESOURCE_EXHAUSTED" in str(last_error) or "quota" in str(last_error).lower()):
            quota_hint = (
                "\n\nTodos os modelos Gemini tentados retornaram cota excedida (429). "
                "Isso indica que a API key está no plano gratuito e já atingiu o limite diário/por minuto. "
                "Aguarde alguns minutos, verifique seu plano em https://ai.google.dev/gemini-api/docs/rate-limits, "
                "ou tente outro provedor (OpenAI/Anthropic) enquanto isso."
            )
        raise RuntimeError(
            f"Erro ao chamar Gemini API: Nenhum modelo Gemini disponível encontrado. "
            f"Último erro: {last_error}. "
            f"Modelos tentados: {', '.join(candidate_models)}.\n\n"
            f"Verifique sua API key em: https://aistudio.google.com/app/apikey"
            f"{quota_hint}"
        )

    except ImportError:
        raise ImportError(
            "google-genai package não instalado. Execute: pip install google-genai"
        )
    except Exception as e:
        raise RuntimeError(f"Erro ao chamar Gemini API: {e}")


def _call_llama_api(prompt: str) -> str:
    """
    Llama local via Ollama.
    """
    try:
        import requests

        # URL configurável via env var (padrão: localhost:11434)
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        url = f"{base_url}/api/generate"
        model_name = os.getenv("OLLAMA_MODEL", "llama2")

        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        }

        response = requests.post(url, json=payload, timeout=60)

        if response.status_code != 200:
            raise ValueError(
                f"Ollama retornou erro {response.status_code}. "
                "Certifique-se de que Ollama está rodando: ollama serve"
            )

        data = response.json()
        sql = data.get("response", "").strip()

        # Remove markdown code blocks
        sql = sql.replace("```sql", "").replace("```", "").strip()

        return sql

    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Não foi possível conectar ao Ollama. "
            "Certifique-se de que está rodando: ollama serve\n"
            "Instale: https://ollama.ai/"
        )
    except Exception as e:
        raise RuntimeError(f"Erro ao chamar Llama local: {e}")

