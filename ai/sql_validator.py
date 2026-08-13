"""
SQL validator — ensures only safe queries are executed.
"""
import re
from typing import Tuple
from ai.schema_manager import SchemaManager
from i18n import t


def get_allowed_tables() -> set:
    """Get list of allowed tables (SCHEMA.TABLE) from schema manager."""
    manager = SchemaManager()
    return set(manager.tables.keys())


def _bare_table_names(allowed_tables: set) -> set:
    """Maps bare (unqualified) table names -> set of full SCHEMA.TABLE names."""
    bare = {}
    for full in allowed_tables:
        _, _, name = full.partition(".")
        bare.setdefault(name, set()).add(full)
    return bare


# Comandos proibidos — DML/DDL plus Oracle packages commonly abused for
# SSRF / blind data exfiltration (UTL_HTTP etc.) even from a read-only
# SELECT statement.
FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "TRUNCATE", "ALTER",
    "CREATE", "GRANT", "REVOKE", "EXECUTE", "EXEC",
    "MERGE", "CALL", "REPLACE",
    # Oracle packages/objects that can perform network calls, LDAP lookups,
    # scheduling, or inter-process communication from within a SELECT.
    "UTL_HTTP", "UTL_TCP", "UTL_SMTP", "UTL_INADDR", "UTL_FILE",
    "HTTPURITYPE", "DBMS_LDAP", "DBMS_SCHEDULER", "DBMS_PIPE",
    "DBMS_JAVA", "DBMS_LOB", "DBMS_XMLQUERY",
]


def validate_sql(sql: str) -> Tuple[bool, str]:
    """
    Valida SQL antes de executar.
    Retorna (is_valid, error_message).
    """
    sql_upper = sql.upper()

    # 1. Bloqueia comandos/pacotes perigosos
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{keyword}\b', sql_upper):
            return False, t("sqlvalidator.forbidden_keyword", keyword=keyword)

    # 2. Verifica se começa com SELECT (ou WITH ... SELECT, para CTEs
    #    somente-leitura — nada aqui permite DML pois já bloqueamos os
    #    keywords acima em qualquer posição da string).
    stripped = sql_upper.strip()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        return False, t("sqlvalidator.select_only")

    # 3. Verifica se não há comentários (podem ser usados para ofuscar
    #    keywords proibidos ou esconder SQL adicional).
    if "--" in sql or "/*" in sql:
        return False, t("sqlvalidator.no_comments")

    # 4. Verifica se não há múltiplas instruções (";" separando statements).
    #    Um único ";" opcional no final é tolerado.
    if ";" in sql.strip().rstrip(";"):
        return False, t("sqlvalidator.no_multi_statement")

    # 5. Extrai nomes de tabelas — tanto qualificados (SCHEMA.TABLE) quanto
    #    não-qualificados (TABLE). Um nome sem schema NÃO é mais aceito
    #    silenciosamente: precisa resolver, sem ambiguidade, a exatamente
    #    uma tabela permitida.
    table_pattern = r'(?:FROM|JOIN)\s+([A-Z0-9_]+(?:\.[A-Z0-9_]+)?)'
    found_tables = set(re.findall(table_pattern, sql_upper))

    allowed_tables = get_allowed_tables()
    bare_lookup = _bare_table_names(allowed_tables)

    invalid_tables = []
    for ref in found_tables:
        if "." in ref:
            if ref not in allowed_tables:
                invalid_tables.append(ref)
        else:
            matches = bare_lookup.get(ref)
            if not matches:
                invalid_tables.append(ref)
            elif len(matches) > 1:
                # Ambiguous unqualified name (same table name in >1 schema)
                # — reject rather than guess which one was intended.
                invalid_tables.append(t("sqlvalidator.table_ambiguous", ref=ref, matches=", ".join(sorted(matches))))

    if invalid_tables:
        return False, t("sqlvalidator.tables_not_allowed", tables=", ".join(invalid_tables))

    return True, ""


def sanitize_bind_params(params: dict) -> dict:
    """
    Pass-through for bind parameters. Bind variables passed to
    cursor.execute(sql, params) are already immune to SQL injection by
    construction — stripping characters like quotes/dashes here provided
    no additional safety and silently corrupted legitimate values (e.g.
    "O'Brien" -> "OBrien", or shipment codes containing "--"). Kept as a
    function (rather than removed) so existing call sites don't need to
    change, but it no longer mutates the values.
    """
    return dict(params)

