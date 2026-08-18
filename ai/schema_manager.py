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
                    "shpm_num", "cust_cd", "status", "eq_typ_cd",
                    "frm_shpg_loc_cd", "frm_name", "frm_ctry_cd", "frm_sta_cd", "frm_cty_name",
                    "to_shpg_loc_cd", "to_name", "to_ctry_cd", "to_sta_cd", "to_cty_name",
                    "pref_ap_carr_cd", "pref_ap_srv_cd", "chg_ovr_chg_cd",
                    "frm_pkup_dtt", "to_pkup_dtt", "frm_dlvy_dtt", "to_dlvy_dtt",
                    "crtd_dtt", "crtd_usr_cd", "updt_dtt", "updt_usr_cd",
                    "scld_wgt", "vol", "urgt_yn", "frht_trms_enu", "shpm_desc", "csld_cls"
                ],
                description="Tabela principal de shipments — contém origem, destino, carrier, equipment, status",
                primary_key="shpm_num"
            ),
            TableSchema(
                name="SHIPMENT_HISTORY",
                schema="ACME_TMS",
                columns=["shpm_num", "trans_id", "status", "crtd_dtt", "crtd_usr_cd"],
                description="Histórico de transições de status do shipment",
                primary_key="shpm_num, trans_id"
            ),
            TableSchema(
                name="DEMO_AUDIT",
                schema="ACME_OMS",
                columns=[
                    "SEQ_NO", "SHIPMENT_ID", "PLAN_ID", "OPT_REQ_ID", "CONSTRAINTS_FILE",
                    "TOTAL_NO_OF_SHPM_LEGS", "STATUS", "ERR_MSG", "CRTD_DTT", "CRTD_BY",
                    "UPDT_DTT", "UPDT_BY", "EXCEL_FILE_NAME", "EMAIL_TRIGGERED",
                    "ORIGIN", "DESTINATION", "LOGISTICS_GROUP", "DIV_CD", "CHRG_OVRD",
                    "EQUIP_TYP_CD", "RECORD_STATUS", "LOAD_ID", "LOAD_CRTD", "SHIP_CRTD", "RESP_GRP"
                ],
                description="Auditoria de shipments internacionais — contém erros e validações de origem/destino",
                primary_key="SHIPMENT_ID"
            ),
            TableSchema(
                name="EVNT_QUE_T",
                schema="ACME_TMS",
                columns=[
                    "root_obj_id", "evnt_notf_id", "cpld_dtt",
                    "evnt_id", "evnt_typ_cd", "status", "msg_txt"
                ],
                description="Fila de eventos do TM — logs de processamento de loads",
                primary_key="evnt_notf_id"
            ),
            TableSchema(
                name="SHPG_LOC_T",
                schema="RTG_APP",
                columns=[
                    "shpg_loc_cd", "shpg_loc_typ_enu", "actv_enu",
                    "cust_cd", "addr_id", "crtd_dtt", "updt_dtt"
                ],
                description="Códigos de localização (origem/destino) — valida se está ativo",
                primary_key="shpg_loc_cd"
            ),
            TableSchema(
                name="ADDR_T",
                schema="RTG_APP",
                columns=[
                    "addr_id", "loc_name", "st_name", "cty_name",
                    "sta_cd", "ctry_cd", "pstl_cd", "loc"
                ],
                description="Endereços das localizações (join com SHPG_LOC_T)",
                primary_key="addr_id"
            ),
            TableSchema(
                name="LD_LEG_T",
                schema="RTG_APP",
                columns=[
                    "ld_leg_id", "carr_cd", "cur_optlstat_id",
                    "crtd_dtt", "updt_dtt", "tot_scld_wgt"
                ],
                description="Legs de carga (loads) — verifica se load foi criado",
                primary_key="ld_leg_id"
            ),
            TableSchema(
                name="LD_LEG_DETL_T",
                schema="RTG_APP",
                columns=[
                    "ld_leg_id", "shpm_id", "cust_cd",
                    "frm_shpg_loc_cd", "to_shpg_loc_cd", "srvc_cd"
                ],
                description="Detalhes do leg — liga shipment ao load",
                primary_key="ld_leg_id, shpm_id"
            ),
            TableSchema(
                name="SHPM_T",
                schema="RTG_APP",
                columns=[
                    "shpm_id", "shpm_num", "eq_typ_cd",
                    "frm_shpg_loc_cd", "to_shpg_loc_cd", "cust_cd"
                ],
                description="Tabela de shipments no RTG_APP (mirror do ACME_TMS)",
                primary_key="shpm_id"
            ),
            TableSchema(
                name="RFRC_NUM_T",
                schema="RTG_APP",
                columns=[
                    "shpm_id", "rfrc_num_typ", "rfrc_num", "crtd_dtt"
                ],
                description="Números de referência — container, ocean BOL, vessel, LDTP",
                primary_key="shpm_id, rfrc_num_typ"
            ),
            # Tariff Pool tables
            TableSchema(
                name="TFF_T",
                schema="RTG_APP",
                columns=[
                    "TFF_ID", "TFF_CD", "MSTR_TFF_ID", "EFCT_DT", "EXPD_DT", "CARR_CD"
                ],
                description="Tariff master — contém carrier e datas de validade",
                primary_key="TFF_ID"
            ),
            TableSchema(
                name="LANE_ASSC_T",
                schema="RTG_APP",
                columns=[
                    "TFF_ID", "RATE_CD", "SRVC_CD", "ORIG_ZN_CD", "ORIG_CTRY_CD",
                    "DEST_ZN_CD", "DEST_CTRY_CD", "SRVC_GRD_TYP", "CDTY_CD", "BASE_DIV_CD"
                ],
                description="Lane associations — liga tariff a rotas específicas",
                primary_key="TFF_ID, RATE_CD, SRVC_CD"
            ),
            TableSchema(
                name="RATE_T",
                schema="RTG_APP",
                columns=[
                    "RATE_ID", "TFF_ID", "RATE_CD", "SRVC_CD", "CHRG_CD",
                    "EQMT_TYP_CD", "MIN_CHRG_DLR", "CNCY_TYP", "EFCT_DT", "EXPD_DT"
                ],
                description="Rates — taxas por serviço e equipment",
                primary_key="RATE_ID"
            ),
            TableSchema(
                name="RNG_RATE_T",
                schema="RTG_APP",
                columns=[
                    "RATE_ID", "RNG_CD", "BRK_AMT_DLR", "RNG_TO"
                ],
                description="Rate ranges — faixas de valores da tarifa",
                primary_key="RATE_ID, RNG_CD"
            ),
            TableSchema(
                name="CNCY_T",
                schema="RTG_APP",
                columns=[
                    "CNCY_TYP", "CNCY_CD"
                ],
                description="Currency types — moedas",
                primary_key="CNCY_TYP"
            ),
        ]

        for table in default_tables:
            key = f"{table.schema}.{table.name}"
            self.tables[key] = table

        # Default relationships
        self.relationships = [
            SchemaRelationship(
                from_table="ACME_TMS.SHIPMENT",
                to_table="RTG_APP.SHPG_LOC_T",
                from_column="frm_shpg_loc_cd",
                to_column="shpg_loc_cd",
                join_type="LEFT",
                description="Origem do shipment"
            ),
            SchemaRelationship(
                from_table="ACME_TMS.SHIPMENT",
                to_table="RTG_APP.SHPG_LOC_T",
                from_column="to_shpg_loc_cd",
                to_column="shpg_loc_cd",
                join_type="LEFT",
                description="Destino do shipment"
            ),
            SchemaRelationship(
                from_table="RTG_APP.SHPG_LOC_T",
                to_table="RTG_APP.ADDR_T",
                from_column="addr_id",
                to_column="addr_id",
                join_type="INNER",
                description="Endereço da localização"
            ),
            SchemaRelationship(
                from_table="RTG_APP.SHPM_T",
                to_table="RTG_APP.LD_LEG_DETL_T",
                from_column="shpm_id",
                to_column="shpm_id",
                join_type="LEFT",
                description="Shipment → Load details"
            ),
            SchemaRelationship(
                from_table="RTG_APP.LD_LEG_DETL_T",
                to_table="RTG_APP.LD_LEG_T",
                from_column="ld_leg_id",
                to_column="ld_leg_id",
                join_type="INNER",
                description="Load details → Load leg"
            ),
            SchemaRelationship(
                from_table="RTG_APP.SHPM_T",
                to_table="RTG_APP.RFRC_NUM_T",
                from_column="shpm_id",
                to_column="shpm_id",
                join_type="LEFT",
                description="Reference numbers"
            ),
            SchemaRelationship(
                from_table="RTG_APP.LANE_ASSC_T",
                to_table="RTG_APP.TFF_T",
                from_column="TFF_ID",
                to_column="TFF_ID",
                join_type="INNER",
                description="Lane → Tariff"
            ),
            SchemaRelationship(
                from_table="RTG_APP.RATE_T",
                to_table="RTG_APP.LANE_ASSC_T",
                from_column="TFF_ID",
                to_column="TFF_ID",
                join_type="INNER",
                description="Rate → Lane (via TFF_ID + SRVC_CD + RATE_CD)"
            ),
            SchemaRelationship(
                from_table="RTG_APP.RNG_RATE_T",
                to_table="RTG_APP.RATE_T",
                from_column="RATE_ID",
                to_column="RATE_ID",
                join_type="INNER",
                description="Rate ranges"
            ),
            SchemaRelationship(
                from_table="RTG_APP.RATE_T",
                to_table="RTG_APP.CNCY_T",
                from_column="CNCY_TYP",
                to_column="CNCY_TYP",
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
