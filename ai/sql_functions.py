"""
ai/sql_functions.py
====================
Catalog of Oracle SQL functions/keywords used to power:
  - the "Functions" quick-insert palette in the Visual Query Builder
    (ui/query_builder_tab.py), and
  - the "SQL Glossary" tab (ui/sql_glossary_tab.py) that explains what
    each keyword/function does, for users who feel lost.

Each entry is a plain dict so it's trivial to render/search/filter without
extra dependencies:
    {
        "name":        short display name, e.g. "COUNT()"
        "category":    grouping used for filters, e.g. "Aggregate"
        "syntax":      one-line syntax hint
        "description": plain-English explanation
        "example":     a runnable-looking SQL example
        "snippet":     text inserted into the query builder when clicked
    }
"""
from typing import Dict, List

SQL_FUNCTIONS: List[Dict[str, str]] = [
    # ── Aggregate functions ─────────────────────────────────────
    {"name": "COUNT()", "category": "Aggregate", "syntax": "COUNT(expr | *)",
     "description": "Counts the number of rows (or non-null values of a column) in a group.",
     "example": "SELECT COUNT(*) FROM shipments WHERE status = 'DELAYED'",
     "snippet": "COUNT(*)"},
    {"name": "SUM()", "category": "Aggregate", "syntax": "SUM(expr)",
     "description": "Adds up all the numeric values of a column in a group.",
     "example": "SELECT SUM(weight_kg) FROM shipments", "snippet": "SUM()"},
    {"name": "AVG()", "category": "Aggregate", "syntax": "AVG(expr)",
     "description": "Calculates the average (mean) of a numeric column in a group.",
     "example": "SELECT AVG(transit_days) FROM shipments", "snippet": "AVG()"},
    {"name": "MIN()", "category": "Aggregate", "syntax": "MIN(expr)",
     "description": "Returns the smallest value of a column in a group.",
     "example": "SELECT MIN(created_date) FROM shipments", "snippet": "MIN()"},
    {"name": "MAX()", "category": "Aggregate", "syntax": "MAX(expr)",
     "description": "Returns the largest value of a column in a group.",
     "example": "SELECT MAX(created_date) FROM shipments", "snippet": "MAX()"},
    {"name": "GROUP BY", "category": "Aggregate", "syntax": "GROUP BY col1, col2, ...",
     "description": "Groups rows that share the same value(s) so aggregate functions (COUNT, SUM, AVG...) apply per group instead of the whole table.",
     "example": "SELECT status, COUNT(*) FROM shipments GROUP BY status", "snippet": ""},
    {"name": "HAVING", "category": "Aggregate", "syntax": "HAVING aggregate_condition",
     "description": "Filters GROUPS after aggregation (unlike WHERE, which filters rows before grouping).",
     "example": "SELECT status, COUNT(*) FROM shipments GROUP BY status HAVING COUNT(*) > 10", "snippet": ""},
    {"name": "DISTINCT", "category": "Aggregate", "syntax": "SELECT DISTINCT col1, col2",
     "description": "Removes duplicate rows from the result, keeping only unique combinations of the selected columns.",
     "example": "SELECT DISTINCT status FROM shipments", "snippet": "DISTINCT "},

    # ── String functions ────────────────────────────────────────
    {"name": "UPPER()", "category": "String", "syntax": "UPPER(text)",
     "description": "Converts text to all UPPERCASE.",
     "example": "SELECT UPPER(customer_name) FROM shipments", "snippet": "UPPER()"},
    {"name": "LOWER()", "category": "String", "syntax": "LOWER(text)",
     "description": "Converts text to all lowercase.",
     "example": "SELECT LOWER(customer_name) FROM shipments", "snippet": "LOWER()"},
    {"name": "TRIM()", "category": "String", "syntax": "TRIM(text)",
     "description": "Removes leading and trailing spaces from text.",
     "example": "SELECT TRIM(customer_name) FROM shipments", "snippet": "TRIM()"},
    {"name": "SUBSTR()", "category": "String", "syntax": "SUBSTR(text, start [, length])",
     "description": "Extracts part of a text value, starting at position `start` for `length` characters.",
     "example": "SELECT SUBSTR(tracking_number, 1, 3) FROM shipments", "snippet": "SUBSTR(, 1, 3)"},
    {"name": "LENGTH()", "category": "String", "syntax": "LENGTH(text)",
     "description": "Returns the number of characters in a text value.",
     "example": "SELECT LENGTH(tracking_number) FROM shipments", "snippet": "LENGTH()"},
    {"name": "CONCAT / ||", "category": "String", "syntax": "col1 || col2  or  CONCAT(col1, col2)",
     "description": "Joins (concatenates) two text values together. Oracle prefers the `||` operator over CONCAT().",
     "example": "SELECT customer_name || ' - ' || status FROM shipments", "snippet": " || "},
    {"name": "REPLACE()", "category": "String", "syntax": "REPLACE(text, search, replacement)",
     "description": "Replaces every occurrence of `search` inside `text` with `replacement`.",
     "example": "SELECT REPLACE(status, 'DELAYED', 'LATE') FROM shipments", "snippet": "REPLACE(, '', '')"},
    {"name": "INSTR()", "category": "String", "syntax": "INSTR(text, substring)",
     "description": "Returns the position where `substring` first appears inside `text` (0 if not found).",
     "example": "SELECT INSTR(tracking_number, '-') FROM shipments", "snippet": "INSTR(, '')"},

    # ── Numeric functions ────────────────────────────────────────
    {"name": "ROUND()", "category": "Numeric", "syntax": "ROUND(number [, decimals])",
     "description": "Rounds a number to the given number of decimal places (default 0).",
     "example": "SELECT ROUND(weight_kg, 1) FROM shipments", "snippet": "ROUND(, 2)"},
    {"name": "TRUNC()", "category": "Numeric", "syntax": "TRUNC(number [, decimals])",
     "description": "Cuts off (truncates) a number to the given number of decimal places, without rounding.",
     "example": "SELECT TRUNC(weight_kg, 1) FROM shipments", "snippet": "TRUNC(, 2)"},
    {"name": "ABS()", "category": "Numeric", "syntax": "ABS(number)",
     "description": "Returns the absolute (always positive) value of a number.",
     "example": "SELECT ABS(delta_days) FROM shipments", "snippet": "ABS()"},
    {"name": "MOD()", "category": "Numeric", "syntax": "MOD(number, divisor)",
     "description": "Returns the remainder of dividing `number` by `divisor`.",
     "example": "SELECT MOD(quantity, 12) FROM shipments", "snippet": "MOD(, 12)"},

    # ── Date/time functions ──────────────────────────────────────
    {"name": "SYSDATE", "category": "Date/Time", "syntax": "SYSDATE",
     "description": "Returns the current date and time on the database server.",
     "example": "SELECT * FROM shipments WHERE created_date > SYSDATE - 7", "snippet": "SYSDATE"},
    {"name": "TO_CHAR()", "category": "Date/Time", "syntax": "TO_CHAR(date, 'format')",
     "description": "Formats a date (or number) as text, using a format mask like 'YYYY-MM-DD'.",
     "example": "SELECT TO_CHAR(created_date, 'YYYY-MM-DD') FROM shipments", "snippet": "TO_CHAR(, 'YYYY-MM-DD')"},
    {"name": "TO_DATE()", "category": "Date/Time", "syntax": "TO_DATE(text, 'format')",
     "description": "Parses a text value into a real DATE, using a format mask like 'YYYY-MM-DD'.",
     "example": "SELECT * FROM shipments WHERE created_date > TO_DATE('2026-01-01','YYYY-MM-DD')", "snippet": "TO_DATE('', 'YYYY-MM-DD')"},
    {"name": "ADD_MONTHS()", "category": "Date/Time", "syntax": "ADD_MONTHS(date, n)",
     "description": "Adds (or subtracts, if n is negative) whole months to a date.",
     "example": "SELECT ADD_MONTHS(created_date, 1) FROM shipments", "snippet": "ADD_MONTHS(, 1)"},
    {"name": "MONTHS_BETWEEN()", "category": "Date/Time", "syntax": "MONTHS_BETWEEN(date1, date2)",
     "description": "Returns the number of months between two dates.",
     "example": "SELECT MONTHS_BETWEEN(SYSDATE, created_date) FROM shipments", "snippet": "MONTHS_BETWEEN(SYSDATE, )"},

    # ── Conditional / logical ────────────────────────────────────
    {"name": "NVL()", "category": "Conditional", "syntax": "NVL(expr, default_value)",
     "description": "Returns `default_value` if `expr` is NULL, otherwise returns `expr` itself. Useful to avoid NULLs breaking calculations.",
     "example": "SELECT NVL(weight_kg, 0) FROM shipments", "snippet": "NVL(, 0)"},
    {"name": "CASE WHEN", "category": "Conditional", "syntax": "CASE WHEN cond THEN val1 ELSE val2 END",
     "description": "Returns different values depending on a condition — like an if/else inside a query.",
     "example": "SELECT CASE WHEN weight_kg > 100 THEN 'Heavy' ELSE 'Light' END FROM shipments",
     "snippet": "CASE WHEN  THEN  ELSE  END"},
    {"name": "COALESCE()", "category": "Conditional", "syntax": "COALESCE(expr1, expr2, ...)",
     "description": "Returns the first non-NULL value from the list of expressions.",
     "example": "SELECT COALESCE(actual_date, eta_date) FROM shipments", "snippet": "COALESCE(, )"},
    {"name": "IN", "category": "Conditional", "syntax": "col IN (val1, val2, ...)",
     "description": "Matches a column against a list of possible values — shorthand for multiple OR comparisons.",
     "example": "SELECT * FROM shipments WHERE status IN ('DELAYED','CANCELLED')", "snippet": "IN ()"},
    {"name": "LIKE", "category": "Conditional", "syntax": "col LIKE 'pattern%'",
     "description": "Matches text against a pattern. `%` = any sequence of characters, `_` = any single character.",
     "example": "SELECT * FROM shipments WHERE customer_name LIKE 'CAT%'", "snippet": "LIKE '%'"},
    {"name": "BETWEEN", "category": "Conditional", "syntax": "col BETWEEN val1 AND val2",
     "description": "Checks whether a value falls within an inclusive range.",
     "example": "SELECT * FROM shipments WHERE weight_kg BETWEEN 10 AND 100", "snippet": "BETWEEN  AND "},
    {"name": "IS NULL", "category": "Conditional", "syntax": "col IS NULL / col IS NOT NULL",
     "description": "Checks whether a column has no value (NULL). Use `IS NOT NULL` for the opposite.",
     "example": "SELECT * FROM shipments WHERE delivered_date IS NULL", "snippet": "IS NULL"},

    # ── Joins & set operations ───────────────────────────────────
    {"name": "INNER JOIN", "category": "Joins", "syntax": "INNER JOIN table ON cond",
     "description": "Combines rows from two tables, keeping only rows where the join condition matches in BOTH tables.",
     "example": "SELECT * FROM shipments s INNER JOIN customers c ON s.customer_id = c.id", "snippet": "INNER JOIN  ON "},
    {"name": "LEFT JOIN", "category": "Joins", "syntax": "LEFT JOIN table ON cond",
     "description": "Keeps ALL rows from the left (first) table, plus matching rows from the right table (NULLs if no match).",
     "example": "SELECT * FROM shipments s LEFT JOIN customers c ON s.customer_id = c.id", "snippet": "LEFT JOIN  ON "},
    {"name": "RIGHT JOIN", "category": "Joins", "syntax": "RIGHT JOIN table ON cond",
     "description": "Keeps ALL rows from the right (second) table, plus matching rows from the left table.",
     "example": "SELECT * FROM shipments s RIGHT JOIN customers c ON s.customer_id = c.id", "snippet": "RIGHT JOIN  ON "},
    {"name": "UNION", "category": "Joins", "syntax": "query1 UNION [ALL] query2",
     "description": "Combines the results of two queries into one result set. Plain UNION removes duplicate rows; UNION ALL keeps them.",
     "example": "SELECT status FROM shipments UNION SELECT status FROM archived_shipments", "snippet": ""},

    # ── Sorting / limiting ───────────────────────────────────────
    {"name": "ORDER BY", "category": "Sorting", "syntax": "ORDER BY col [ASC|DESC]",
     "description": "Sorts the result rows by one or more columns, ascending (default) or descending.",
     "example": "SELECT * FROM shipments ORDER BY created_date DESC", "snippet": ""},
    {"name": "FETCH FIRST n ROWS", "category": "Sorting", "syntax": "FETCH FIRST n ROWS ONLY",
     "description": "Oracle's modern way to limit how many rows come back (equivalent to LIMIT in other databases).",
     "example": "SELECT * FROM shipments ORDER BY created_date DESC FETCH FIRST 10 ROWS ONLY", "snippet": ""},
]


def get_categories() -> List[str]:
    seen = []
    for f in SQL_FUNCTIONS:
        if f["category"] not in seen:
            seen.append(f["category"])
    return seen


def search_functions(query: str = "", category: str = "") -> List[Dict[str, str]]:
    out = SQL_FUNCTIONS
    if category and category != "All":
        out = [f for f in out if f["category"] == category]
    if query:
        q = query.lower()
        out = [
            f for f in out
            if q in f["name"].lower() or q in f["description"].lower() or q in f["category"].lower()
        ]
    return out
