"""
Schema Manager — persistent storage and management of database schemas.
Allows users to add/edit/delete tables and columns via UI.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class TableSchema:
    """Represents a database table schema."""
    name: str
    schema: str  # ACME_TMS, RTG_APP, etc.
    columns: List[str]
    description: str
    primary_key: str
    column_types: Optional[Dict[str, str]] = None
    sample_joins: Optional[List[str]] = None  # Common JOINs envolvendo esta tabela


@dataclass
class SchemaRelationship:
    """Represents a relationship between two tables."""
    from_table: str  # formato: SCHEMA.TABLE
    to_table: str
    from_column: str
    to_column: str
    join_type: str  # INNER, LEFT, RIGHT
    description: str


class SchemaManager:
    """Manages database schema catalog with persistent storage."""

    def __init__(self, storage_path: Optional[str] = None):
        if storage_path is None:
            # Default: store in config/schema_catalog.json
            base_dir = Path(__file__).parent.parent / "config"
            base_dir.mkdir(exist_ok=True)
            storage_path = str(base_dir / "schema_catalog.json")

        self.storage_path = storage_path
        self.tables: Dict[str, TableSchema] = {}
        self.relationships: List[SchemaRelationship] = []
        self._load()

    def _load(self):
        """Load schema catalog from disk."""
        if not os.path.exists(self.storage_path):
            # Initialize with default schemas
            self._initialize_defaults()
            self._save()
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Load tables
            for table_data in data.get("tables", []):
                key = f"{table_data['schema']}.{table_data['name']}"
                self.tables[key] = TableSchema(**table_data)

            # Load relationships
            for rel_data in data.get("relationships", []):
                self.relationships.append(SchemaRelationship(**rel_data))

        except Exception as e:
            print(f"Error loading schema catalog: {e}")
            self._initialize_defaults()

    def _save(self):
        """Save schema catalog to disk."""
        data = {
            "tables": [asdict(t) for t in self.tables.values()],
            "relationships": [asdict(r) for r in self.relationships]
        }

        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _initialize_defaults(self):
        """Initialize with default schemas from support_queries.py."""
        default_tables = [
            TableSchema(
                name="SHIPMENT",
                schema="ACME_TMS",
                columns=[
                    "shipment_number", "customer_code", "status", "equipment_type_code",
                    "origin_location_code", "origin_name", "origin_country_code", "origin_state_code", "origin_city_name",
                    "destination_location_code", "destination_name", "destination_country_code", "destination_state_code", "destination_city_name",
                    "preferred_carrier_code", "preferred_service_code", "charge_override_code",
                    "origin_pickup_at", "destination_pickup_at", "origin_delivery_at", "destination_delivery_at",
                    "created_at", "created_by_user", "updated_at", "updated_by_user",
                    "planned_weight", "vol", "urgent_flag", "freight_terms", "shipment_description", "consolidation_class"
                ],
                description="Tabela principal de shipments — contém origem, destino, carrier, equipment, status",
                primary_key="shipment_number"
            ),
            TableSchema(
                name="SHIPMENT_HISTORY",
                schema="ACME_TMS",
                columns=["shipment_number", "status_step_id", "status", "created_at", "created_by_user"],
                description="Histórico de transições de status do shipment",
                primary_key="shipment_number, status_step_id"
            ),
            TableSchema(
                name="DEMO_AUDIT",
                schema="ACME_OMS",
                columns=[
                    "SEQ_NO", "SHIPMENT_ID", "PLAN_ID", "REQUEST_ID", "RULES_FILE",
                    "TOTAL_ROUTE_SEGMENTS", "STATUS", "ERR_MSG", "CRTD_DTT", "CREATED_BY",
                    "UPDT_DTT", "UPDATED_BY", "SOURCE_FILE_NAME", "EMAIL_SENT",
                    "ORIGIN", "DESTINATION", "LOGISTICS_GROUP", "DIVISION_CODE", "CHARGE_OVERRIDE",
                    "EQUIPMENT_TYPE_CODE", "RECORD_STATUS", "LOAD_ID", "LOAD_CREATED", "SHIPMENT_CREATED", "SUPPORT_GROUP"
                ],
                description="Auditoria de shipments internacionais — contém erros e validações de origem/destino",
                primary_key="SHIPMENT_ID"
            ),
            TableSchema(
                name="DEMO_EVENT_LOG",
                schema="ACME_TMS",
                columns=[
                    "root_object_id", "event_notification_id", "completed_at",
                    "event_id", "event_type_code", "status", "message_text"
                ],
                description="Fila de eventos do Routing — logs de processamento de loads",
                primary_key="event_notification_id"
            ),
            TableSchema(
                name="DEMO_LOCATION",
                schema="RTG_APP",
                columns=[
                    "location_code", "location_type", "active_flag",
                    "customer_code", "address_id", "created_at", "updated_at"
                ],
                description="Códigos de localização (origem/destino) — valida se está ativo",
                primary_key="location_code"
            ),
            TableSchema(
                name="DEMO_ADDRESS",
                schema="RTG_APP",
                columns=[
                    "address_id", "location_name", "street_name", "city_name",
                    "state_code", "country_code", "postal_code", "loc"
                ],
                description="Endereços das localizações (join com DEMO_LOCATION)",
                primary_key="address_id"
            ),
            TableSchema(
                name="DEMO_LOAD",
                schema="RTG_APP",
                columns=[
                    "load_segment_id", "carrier_code", "current_status_id",
                    "created_at", "updated_at", "total_planned_weight"
                ],
                description="Legs de carga (loads) — verifica se load foi criado",
                primary_key="load_segment_id"
            ),
            TableSchema(
                name="DEMO_LOAD_DETAIL",
                schema="RTG_APP",
                columns=[
                    "load_segment_id", "shipment_key", "customer_code",
                    "origin_location_code", "destination_location_code", "service_code"
                ],
                description="Detalhes do leg — liga shipment ao load",
                primary_key="load_segment_id, shipment_key"
            ),
            TableSchema(
                name="DEMO_SHIPMENT_LINK",
                schema="RTG_APP",
                columns=[
                    "shipment_key", "shipment_number", "equipment_type_code",
                    "origin_location_code", "destination_location_code", "customer_code"
                ],
                description="Tabela de shipments no RTG_APP (mirror do ACME_TMS)",
                primary_key="shipment_key"
            ),
            TableSchema(
                name="DEMO_REFERENCE",
                schema="RTG_APP",
                columns=[
                    "shipment_key", "reference_type", "reference_value", "created_at"
                ],
                description="Números de referência — container, ocean BOL, vessel, DeliveryPlan",
                primary_key="shipment_key, reference_type"
            ),
            # Rate Card Lookup tables
            TableSchema(
                name="DEMO_RATE_CARD",
                schema="RTG_APP",
                columns=[
                    "RATE_CARD_ID", "RATE_CARD_CODE", "MASTER_RATE_CARD_ID", "EFFECTIVE_DATE", "EXPIRATION_DATE", "CARRIER_CODE"
                ],
                description="Tariff master — contém carrier e datas de validade",
                primary_key="RATE_CARD_ID"
            ),
            TableSchema(
                name="DEMO_ROUTE_RATE",
                schema="RTG_APP",
                columns=[
                    "RATE_CARD_ID", "RATE_CODE", "SERVICE_CODE", "ORIGIN_ZONE_CODE", "ORIGIN_COUNTRY_CODE",
                    "DESTINATION_ZONE_CODE", "DESTINATION_COUNTRY_CODE", "SERVICE_GRADE", "COMMODITY_CODE", "BASE_DIVISION_CODE"
                ],
                description="Lane associations — liga tariff a rotas específicas",
                primary_key="RATE_CARD_ID, RATE_CODE, SERVICE_CODE"
            ),
            TableSchema(
                name="DEMO_RATE",
                schema="RTG_APP",
                columns=[
                    "RATE_RECORD_ID", "RATE_CARD_ID", "RATE_CODE", "SERVICE_CODE", "CHARGE_CODE",
                    "EQUIPMENT_TYPE_CODE", "MINIMUM_CHARGE_AMOUNT", "CURRENCY_TYPE", "EFFECTIVE_DATE", "EXPIRATION_DATE"
                ],
                description="Rates — taxas por serviço e equipment",
                primary_key="RATE_RECORD_ID"
            ),
            TableSchema(
                name="DEMO_RATE_BREAK",
                schema="RTG_APP",
                columns=[
                    "RATE_RECORD_ID", "RANGE_CODE", "BREAK_AMOUNT", "RANGE_END"
                ],
                description="Rate ranges — faixas de valores da tarifa",
                primary_key="RATE_RECORD_ID, RANGE_CODE"
            ),
            TableSchema(
                name="DEMO_CURRENCY",
                schema="RTG_APP",
                columns=[
                    "CURRENCY_TYPE", "CURRENCY_CODE"
                ],
                description="Currency types — moedas",
                primary_key="CURRENCY_TYPE"
            ),
        ]

        for table in default_tables:
            key = f"{table.schema}.{table.name}"
            self.tables[key] = table

        # Default relationships
        self.relationships = [
            SchemaRelationship(
                from_table="ACME_TMS.SHIPMENT",
                to_table="RTG_APP.DEMO_LOCATION",
                from_column="origin_location_code",
                to_column="location_code",
                join_type="LEFT",
                description="Origem do shipment"
            ),
            SchemaRelationship(
                from_table="ACME_TMS.SHIPMENT",
                to_table="RTG_APP.DEMO_LOCATION",
                from_column="destination_location_code",
                to_column="location_code",
                join_type="LEFT",
                description="Destino do shipment"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_LOCATION",
                to_table="RTG_APP.DEMO_ADDRESS",
                from_column="address_id",
                to_column="address_id",
                join_type="INNER",
                description="Endereço da localização"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_SHIPMENT_LINK",
                to_table="RTG_APP.DEMO_LOAD_DETAIL",
                from_column="shipment_key",
                to_column="shipment_key",
                join_type="LEFT",
                description="Shipment → Load mapping"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_LOAD_DETAIL",
                to_table="RTG_APP.DEMO_LOAD",
                from_column="load_segment_id",
                to_column="load_segment_id",
                join_type="INNER",
                description="Load mapping → Load record"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_SHIPMENT_LINK",
                to_table="RTG_APP.DEMO_REFERENCE",
                from_column="shipment_key",
                to_column="shipment_key",
                join_type="LEFT",
                description="Reference numbers"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_ROUTE_RATE",
                to_table="RTG_APP.DEMO_RATE_CARD",
                from_column="RATE_CARD_ID",
                to_column="RATE_CARD_ID",
                join_type="INNER",
                description="Route mapping → Rate card"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_RATE",
                to_table="RTG_APP.DEMO_ROUTE_RATE",
                from_column="RATE_CARD_ID",
                to_column="RATE_CARD_ID",
                join_type="INNER",
                description="Rate → Route mapping (via RATE_CARD_ID + SERVICE_CODE + RATE_CODE)"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_RATE_BREAK",
                to_table="RTG_APP.DEMO_RATE",
                from_column="RATE_RECORD_ID",
                to_column="RATE_RECORD_ID",
                join_type="INNER",
                description="Rate ranges"
            ),
            SchemaRelationship(
                from_table="RTG_APP.DEMO_RATE",
                to_table="RTG_APP.DEMO_CURRENCY",
                from_column="CURRENCY_TYPE",
                to_column="CURRENCY_TYPE",
                join_type="INNER",
                description="Currency"
            ),
        ]

    # ═══════════════════════════════════════════════════════════
    #  CRUD Operations for Tables
    # ═══════════════════════════════════════════════════════════

    def add_table(self, table: TableSchema, persist: bool = True) -> bool:
        """Add or update a table in the catalog."""
        key = f"{table.schema}.{table.name}"
        self.tables[key] = table
        if persist:
            self._save()
        return True

    def remove_table(self, schema: str, table_name: str) -> bool:
        """Remove a table from the catalog."""
        key = f"{schema}.{table_name}"
        if key in self.tables:
            del self.tables[key]
            # Remove related relationships
            self.relationships = [
                r for r in self.relationships
                if r.from_table != key and r.to_table != key
            ]
            self._save()
            return True
        return False

    def get_table(self, schema: str, table_name: str) -> Optional[TableSchema]:
        """Get a specific table."""
        key = f"{schema}.{table_name}"
        return self.tables.get(key)

    def get_all_tables(self) -> Dict[str, List[TableSchema]]:
        """Get all tables grouped by schema."""
        result = {}
        for table in self.tables.values():
            if table.schema not in result:
                result[table.schema] = []
            result[table.schema].append(table)
        return result

    # ═══════════════════════════════════════════════════════════
    #  CRUD Operations for Relationships
    # ═══════════════════════════════════════════════════════════

    def add_relationship(self, rel: SchemaRelationship) -> bool:
        """Add a relationship between tables."""
        # Check if tables exist
        if rel.from_table not in self.tables:
            raise ValueError(f"Table {rel.from_table} not found")
        if rel.to_table not in self.tables:
            raise ValueError(f"Table {rel.to_table} not found")

        self.relationships.append(rel)
        self._save()
        return True

    def remove_relationship(self, index: int) -> bool:
        """Remove a relationship by index."""
        if 0 <= index < len(self.relationships):
            del self.relationships[index]
            self._save()
            return True
        return False

    def get_relationships_for_table(self, schema: str, table_name: str) -> List[SchemaRelationship]:
        """Get all relationships involving a specific table."""
        key = f"{schema}.{table_name}"
        return [
            r for r in self.relationships
            if r.from_table == key or r.to_table == key
        ]

    # ═══════════════════════════════════════════════════════════
    #  Export for LLM
    # ═══════════════════════════════════════════════════════════

    def export_for_llm(self) -> str:
        """Export schema catalog in LLM-friendly format."""
        lines = []

        # Tables by schema
        grouped = self.get_all_tables()
        for schema_name in sorted(grouped.keys()):
            lines.append(f"\n## Schema: {schema_name}\n")
            for table in sorted(grouped[schema_name], key=lambda t: t.name):
                lines.append(f"### {table.name}")
                lines.append(f"**Descrição**: {table.description}")
                lines.append(f"**PK**: {table.primary_key}")
                lines.append(f"**Colunas**: {', '.join(table.columns)}\n")

        # Relationships
        lines.append("\n## Relacionamentos Comuns (JOINs):\n")
        for rel in self.relationships:
            lines.append(
                f"- {rel.from_table}.{rel.from_column} "
                f"→ ({rel.join_type}) {rel.to_table}.{rel.to_column} "
                f"— {rel.description}"
            )

        return "\n".join(lines)

    def export_to_dict(self) -> dict:
        """Export entire catalog as dictionary."""
        return {
            "tables": {k: asdict(v) for k, v in self.tables.items()},
            "relationships": [asdict(r) for r in self.relationships]
        }

    def import_from_dict(self, data: dict, merge: bool = True) -> int:
        """
        Import tables/relationships from a dictionary produced either by
        `export_to_dict()` (tables as a dict keyed by "SCHEMA.TABLE") or by
        the on-disk `schema_catalog.json` format (tables as a list).

        Args:
            data: Parsed JSON payload.
            merge: If True (default), merges into the existing catalog
                (adding/overwriting tables and appending new relationships).
                If False, replaces the catalog entirely.

        Returns:
            Number of tables imported.
        """
        raw_tables = data.get("tables", [])
        raw_relationships = data.get("relationships", [])

        # Normalize "tables" to a list of dicts, supporting both formats.
        if isinstance(raw_tables, dict):
            table_dicts = list(raw_tables.values())
        else:
            table_dicts = list(raw_tables)

        new_tables: Dict[str, TableSchema] = {}
        for table_data in table_dicts:
            try:
                table = TableSchema(**table_data)
            except TypeError as e:
                raise ValueError(f"Invalid table entry in import: {e}")
            key = f"{table.schema}.{table.name}"
            new_tables[key] = table

        new_relationships: List[SchemaRelationship] = []
        for rel_data in raw_relationships:
            try:
                new_relationships.append(SchemaRelationship(**rel_data))
            except TypeError as e:
                raise ValueError(f"Invalid relationship entry in import: {e}")

        if merge:
            self.tables.update(new_tables)
            # Avoid duplicate relationships when merging.
            existing = {
                (r.from_table, r.to_table, r.from_column, r.to_column)
                for r in self.relationships
            }
            for rel in new_relationships:
                sig = (rel.from_table, rel.to_table, rel.from_column, rel.to_column)
                if sig not in existing:
                    self.relationships.append(rel)
                    existing.add(sig)
        else:
            self.tables = new_tables
            self.relationships = new_relationships

        self._save()
        return len(new_tables)
