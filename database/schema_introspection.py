import logging
from collections import defaultdict

from ai.schema_manager import SchemaManager, TableSchema

logger = logging.getLogger(__name__)


def _format_oracle_type(data_type, data_length=None, data_precision=None, data_scale=None) -> str:
    data_type = (data_type or "").upper()

    if data_type in {"VARCHAR2", "VARCHAR", "CHAR", "NCHAR", "NVARCHAR2"} and data_length:
        return f"{data_type}({int(data_length)})"

    if data_type == "NUMBER":
        if data_precision is None:
            return "NUMBER"
        if data_scale in (None, 0):
            return f"NUMBER({int(data_precision)})"
        return f"NUMBER({int(data_precision)},{int(data_scale)})"

    return data_type or "UNKNOWN"


def introspect_schema(conn, owner: str, source_label: str = "", max_tables: int = 200) -> dict:
    """
    Queries Oracle data dictionary views and returns a schema import payload.
    """
    normalized_owner = (owner or "").strip().upper()
    if not normalized_owner:
        raise RuntimeError("Schema/owner is required for schema import.")

    cursor = conn.cursor()
    try:
        pk_map = defaultdict(list)
        try:
            cursor.execute(
                """
                SELECT acc.table_name, acc.column_name
                FROM all_constraints ac
                JOIN all_cons_columns acc
                  ON ac.constraint_name = acc.constraint_name
                 AND ac.owner = acc.owner
                WHERE ac.owner = :owner
                  AND ac.constraint_type = 'P'
                ORDER BY acc.table_name, acc.position
                """,
                owner=normalized_owner,
            )
            for table_name, column_name in cursor.fetchall():
                pk_map[table_name].append(column_name)
        except Exception:
            logger.warning(
                "Failed to retrieve primary key metadata for owner %s",
                normalized_owner,
                exc_info=True,
            )
            pk_map = defaultdict(list)

        cursor.execute(
            """
            SELECT table_name, column_name, data_type, data_length, data_precision, data_scale, nullable
            FROM all_tab_columns
            WHERE owner = :owner
            ORDER BY table_name, column_id
            """,
            owner=normalized_owner,
        )

        tables = []
        current_table_name = None
        current_columns = []
        current_types = {}
        current_nullability = {}
        nullability = {}
        column_count = 0
        truncated = False

        for table_name, column_name, data_type, data_length, data_precision, data_scale, nullable in cursor:
            if current_table_name and table_name != current_table_name:
                nullability[current_table_name] = current_nullability
                tables.append(
                    TableSchema(
                        name=current_table_name,
                        schema=normalized_owner,
                        columns=current_columns,
                        description=f"Auto-imported from {source_label or normalized_owner}",
                        primary_key=", ".join(pk_map.get(current_table_name, [])),
                        column_types=current_types or None,
                    )
                )
                if len(tables) >= max_tables:
                    truncated = True
                    break
                current_columns = []
                current_types = {}
                current_nullability = {}

            if table_name != current_table_name:
                current_table_name = table_name

            current_columns.append(column_name)
            current_types[column_name] = _format_oracle_type(
                data_type=data_type,
                data_length=data_length,
                data_precision=data_precision,
                data_scale=data_scale,
            )
            current_nullability[column_name] = (nullable == "Y")
            column_count += 1

        if current_table_name and len(tables) < max_tables:
            nullability[current_table_name] = current_nullability
            tables.append(
                TableSchema(
                    name=current_table_name,
                    schema=normalized_owner,
                    columns=current_columns,
                    description=f"Auto-imported from {source_label or normalized_owner}",
                    primary_key=", ".join(pk_map.get(current_table_name, [])),
                    column_types=current_types or None,
                )
            )

        if not tables:
            raise RuntimeError(f"No tables found for owner {normalized_owner}.")

        return {
            "owner": normalized_owner,
            "tables": tables,
            "table_count": len(tables),
            "column_count": column_count,
            "nullability": nullability,
            "truncated": truncated,
        }
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Database error while introspecting: {e}") from e
    finally:
        cursor.close()


def introspect_single_table(conn, owner: str, table_name: str) -> dict:
    """
    Queries Oracle data dictionary views for a single table and returns its
    column names + data types (and primary key columns). Used by the
    "🔄 Retrieve column details" button in the Schema tab so a user can
    refresh/discover the exact data type each column accepts without doing
    a full-schema import.
    """
    normalized_owner = (owner or "").strip().upper()
    normalized_table = (table_name or "").strip().upper()
    if not normalized_owner or not normalized_table:
        raise RuntimeError("Schema/owner and table name are required.")

    cursor = conn.cursor()
    try:
        pk_columns = []
        try:
            cursor.execute(
                """
                SELECT acc.column_name
                FROM all_constraints ac
                JOIN all_cons_columns acc
                  ON ac.constraint_name = acc.constraint_name
                 AND ac.owner = acc.owner
                WHERE ac.owner = :owner
                  AND ac.table_name = :table_name
                  AND ac.constraint_type = 'P'
                ORDER BY acc.position
                """,
                owner=normalized_owner, table_name=normalized_table,
            )
            pk_columns = [row[0] for row in cursor.fetchall()]
        except Exception:
            logger.warning(
                "Failed to retrieve primary key metadata for table %s.%s",
                normalized_owner,
                normalized_table,
                exc_info=True,
            )
            pk_columns = []

        cursor.execute(
            """
            SELECT column_name, data_type, data_length, data_precision, data_scale, nullable
            FROM all_tab_columns
            WHERE owner = :owner AND table_name = :table_name
            ORDER BY column_id
            """,
            owner=normalized_owner, table_name=normalized_table,
        )
        columns = []
        column_types = {}
        nullability = {}
        for column_name, data_type, data_length, data_precision, data_scale, nullable in cursor:
            columns.append(column_name)
            column_types[column_name] = _format_oracle_type(
                data_type=data_type,
                data_length=data_length,
                data_precision=data_precision,
                data_scale=data_scale,
            )
            nullability[column_name] = (nullable == "Y")

        if not columns:
            raise RuntimeError(f"No columns found for {normalized_owner}.{normalized_table}.")

        return {
            "owner": normalized_owner,
            "table_name": normalized_table,
            "columns": columns,
            "column_types": column_types,
            "nullability": nullability,
            "primary_key": ", ".join(pk_columns),
        }
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Database error while retrieving column details: {e}") from e
    finally:
        cursor.close()


def import_into_catalog(conn, owner: str, source_label: str = "", manager=None) -> int:
    manager = manager or SchemaManager()
    result = introspect_schema(conn, owner=owner, source_label=source_label)
    imported = 0
    for table in result["tables"]:
        if manager.add_table(table, persist=False):
            imported += 1
    if result["tables"]:
        manager._save()
    return imported
