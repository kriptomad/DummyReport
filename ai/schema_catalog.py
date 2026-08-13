"""
Schema catalog for Text-to-SQL system.
DEPRECATED: Use ai.schema_manager.SchemaManager instead.
This file is kept for backwards compatibility only.
"""
from ai.schema_manager import SchemaManager


def get_schema_description() -> str:
    """
    Legacy function — use SchemaManager.export_for_llm() instead.
    Formata o catálogo em texto legível para o LLM.
    """
    manager = SchemaManager()
    return manager.export_for_llm()


# Legacy export for backwards compatibility
SCHEMA_CATALOG = None
COMMON_JOINS = None

def _initialize_legacy():
    """Initialize legacy variables on first import."""
    global SCHEMA_CATALOG, COMMON_JOINS
    if SCHEMA_CATALOG is None:
        manager = SchemaManager()
        # Convert to old format if needed
        SCHEMA_CATALOG = {}
        for table_key, table in manager.tables.items():
            schema = table.schema
            if schema not in SCHEMA_CATALOG:
                SCHEMA_CATALOG[schema] = {}
            SCHEMA_CATALOG[schema][table.name] = {
                "columns": table.columns,
                "description": table.description,
                "primary_key": table.primary_key
            }

        COMMON_JOINS = "\n".join([
            f"{r.from_table}.{r.from_column} → {r.to_table}.{r.to_column} ({r.join_type})"
            for r in manager.relationships
        ])

_initialize_legacy()

