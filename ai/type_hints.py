import re


NUMERIC_TYPE_TOKENS = ("NUMBER", "INTEGER", "DECIMAL", "NUMERIC", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE")
TEXTUAL_TYPE_TOKENS = ("VARCHAR", "CHAR", "CLOB", "TEXT", "DATE", "TIMESTAMP", "NCHAR", "NVARCHAR")
COMPARISON_PATTERN = re.compile(
    r"(?P<column>(?:[A-Z_][\w$#]*\.){0,2}[A-Z_][\w$#]*)\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*(?P<literal>'[^']*'|\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def check_type_hints(sql: str, manager) -> list[str]:
    warnings = []
    if not sql or manager is None:
        return warnings

    column_types = {}
    for tables in manager.get_all_tables().values():
        for table in tables:
            for column_name, column_type in (table.column_types or {}).items():
                column_types.setdefault(column_name.upper(), column_type)

    if not column_types:
        return warnings

    for match in COMPARISON_PATTERN.finditer(sql):
        column_name = match.group("column").split(".")[-1].upper()
        literal = match.group("literal")
        column_type = column_types.get(column_name)
        if not column_type:
            continue

        type_upper = column_type.upper()
        is_quoted = literal.startswith("'") and literal.endswith("'")
        is_numeric_type = any(token in type_upper for token in NUMERIC_TYPE_TOKENS)
        is_textual_type = any(token in type_upper for token in TEXTUAL_TYPE_TOKENS)

        if is_numeric_type and is_quoted:
            warnings.append(
                f"⚠️ Column {column_name} looks like {column_type} but you're comparing it to a quoted string literal — double-check."
            )
        elif is_textual_type and not is_quoted:
            warnings.append(
                f"⚠️ Column {column_name} looks like {column_type} but you're comparing it to a numeric literal — double-check."
            )

    return warnings
