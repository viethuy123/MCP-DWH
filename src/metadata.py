import json
import os
from typing import Dict, List, Optional, Union

import psycopg2
import psycopg2.extras

from config import ALLOWED_SCHEMAS, DB_CONFIG, detect_table_type


# =============================================================================
# TEMPORAL LOGIC
# =============================================================================

class TemporalLogic:
    def __init__(self, name: str, config: Union[str, dict]):
        self.name = name

        if isinstance(config, str):
            self.condition_sql = config.strip()
        elif isinstance(config, dict):
            self.condition_sql = config.get("condition_sql", "").strip()
        else:
            self.condition_sql = ""

    def render(self, target_date: str = "CURRENT_DATE") -> str:
        if not self.condition_sql:
            return ""
        return self.condition_sql.replace(":target_date", target_date)

    def __repr__(self):
        return f"<TemporalLogic {self.name}>"


# =============================================================================
# FOREIGN KEY
# =============================================================================

class ForeignKey:
    def __init__(self, config: dict):
        self.column = config.get("column")
        self.references = config.get("references")

        self.ref_schema = None
        self.ref_table = None
        self.ref_column = None

        if self.references:
            parts = self.references.split(".")
            if len(parts) == 3:
                self.ref_schema, self.ref_table, self.ref_column = parts
            elif len(parts) == 2:
                self.ref_table, self.ref_column = parts

    def ref_full_table(self) -> Optional[str]:
        if self.ref_schema and self.ref_table:
            return f"{self.ref_schema}.{self.ref_table}"
        if self.ref_table:
            return self.ref_table
        return None

    def __repr__(self):
        return f"<ForeignKey {self.column} -> {self.references}>"


# =============================================================================
# COLUMN META
# =============================================================================

class ColumnMeta:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.description: str = config.get("description", "").strip()
        self.data_type: str = config.get("data_type", "")
        self.nullable: Optional[bool] = config.get("nullable")
        self.meta: dict = config.get("meta", {})
        self.synonyms: List[str] = list(self.meta.get("synonyms", []) or [])

    def __repr__(self):
        return f"<ColumnMeta {self.name}>"


# =============================================================================
# TABLE META
# =============================================================================

class TableMeta:
    def __init__(self, node: dict):
        self.name: str = node.get("name", "")
        self.schema: str = node.get("schema", "")
        self.full_name: str = f"{self.schema}.{self.name}"
        self.description: str = node.get("description", "").strip()
        self.materialization: str = node.get("config", {}).get("materialized", "")
        self.db_table_type: str = ""
        self.db_validated: bool = False

        meta = node.get("meta", {}) or {}

        self.semantic_type: str = meta.get("semantic_type") or detect_table_type(self.name)
        self.grain: List[str] = meta.get("grain", [])

        pk = meta.get("primary_key")
        if isinstance(pk, list):
            self.primary_key: List[str] = pk
        elif isinstance(pk, str):
            self.primary_key = [pk]
        else:
            self.primary_key = []

        raw_columns = node.get("columns", {})
        self.columns: Dict[str, ColumnMeta] = {
            col_name: ColumnMeta(col_name, col_data)
            for col_name, col_data in raw_columns.items()
        }

        self.foreign_keys: List[ForeignKey] = [
            ForeignKey(fk) for fk in meta.get("foreign_keys", [])
        ]
        self.db_foreign_keys: List[ForeignKey] = []

        self.temporal_logic: Dict[str, TemporalLogic] = {}
        for logic_name, logic_cfg in meta.get("temporal_logic", {}).items():
            self.temporal_logic[logic_name] = TemporalLogic(logic_name, logic_cfg)

        time_context = meta.get("time_context", {}) or {}
        self.reference_date_column: Optional[str] = time_context.get("reference_date_column")

        usage = meta.get("usage", {}) or {}
        self.preferred_for: List[str] = usage.get("preferred_for", [])
        self.avoid_for: List[str] = usage.get("avoid_for", [])
        self.warnings: List[str] = list(usage.get("warnings", []))

        self.data_characteristics: dict = meta.get("data_characteristics", {}) or {}
        self.synonyms: Dict[str, List[str]] = meta.get("synonyms", {}) or {}

        # Merge column-level synonyms into the table synonym index.
        for col_name, col_meta in self.columns.items():
            if not col_meta.synonyms:
                continue
            bucket = self.synonyms.setdefault(col_name, [])
            for syn in col_meta.synonyms:
                if syn not in bucket:
                    bucket.append(syn)

    def is_fact(self) -> bool:
        return self.semantic_type == "fact"

    def is_dimension(self) -> bool:
        return self.semantic_type == "dimension"

    def is_report(self) -> bool:
        return self.semantic_type == "report"

    def get_temporal_condition(
        self,
        logic_name: str,
        target_date: str = "CURRENT_DATE"
    ) -> Optional[str]:
        logic = self.temporal_logic.get(logic_name)
        if not logic:
            return None
        return logic.render(target_date)

    def has_temporal_logic(self, logic_name: str) -> bool:
        return logic_name in self.temporal_logic

    def get_column(self, col_name: str) -> Optional[ColumnMeta]:
        return self.columns.get(col_name)

    def column_names(self) -> List[str]:
        return list(self.columns.keys())

    def resolve_column_name(self, column: str) -> Optional[str]:
        if not column:
            return None

        target = column.lower().strip()

        for col_name in self.columns:
            if col_name.lower().strip() == target:
                return col_name

        for col_name, col_meta in self.columns.items():
            for syn in col_meta.synonyms:
                if syn.lower().strip() == target:
                    return col_name

        for col_name, synonyms in self.synonyms.items():
            if col_name.lower().strip() == target:
                return col_name
            for syn in synonyms:
                if syn.lower().strip() == target:
                    return col_name

        return None

    def find_column_by_synonym(self, word: str) -> Optional[str]:
        resolved = self.resolve_column_name(word)
        if resolved:
            return resolved

        word_lower = word.lower().strip()
        for col_name, synonyms in self.synonyms.items():
            for syn in synonyms:
                syn_lower = syn.lower().strip()
                if syn_lower in word_lower:
                    return col_name

        return None

    def get_foreign_key(self, column: str) -> Optional[ForeignKey]:
        for fk in self.foreign_keys + self.db_foreign_keys:
            if fk.column == column:
                return fk
        return None

    def get_related_tables(self) -> List[str]:
        return list(dict.fromkeys([
            fk.ref_full_table()
            for fk in (self.foreign_keys + self.db_foreign_keys)
            if fk.ref_full_table()
        ]))

    def find_join_key(self, other_table_name: str) -> Optional[tuple]:
        for fk in self.foreign_keys + self.db_foreign_keys:
            ref_table = fk.ref_full_table() or ""
            if other_table_name == ref_table or ref_table.endswith(other_table_name):
                return (fk.column, fk.ref_column)
        return None

    def __repr__(self):
        return f"<TableMeta {self.full_name} ({self.semantic_type})>"


# =============================================================================
# METADATA STORE
# =============================================================================

class MetadataStore:
    def __init__(self):
        self.tables: Dict[str, TableMeta] = {}
        self._loaded: bool = False
        self.load_report: Dict[str, object] = {}

    def is_loaded(self) -> bool:
        return self._loaded

    def load_from_sources(
        self,
        manifest_path: str,
        catalog_path: Optional[str] = None,
    ):
        manifest = self._load_json_if_exists(manifest_path, required=True)
        catalog = self._load_json_if_exists(catalog_path, required=False)
        db_relations = self._load_db_relations()

        manifest_models = self._manifest_models_by_relation(manifest)
        catalog_models = self._catalog_models_by_relation(catalog) if catalog else {}

        self.tables = {}
        stale_manifest = []
        db_only = []
        merged = 0

        for full_name, relation in db_relations.items():
            node = manifest_models.get(full_name)
            catalog_node = catalog_models.get(full_name)

            if node:
                table = TableMeta(node)
                self._enrich_table_from_db(table, relation)
                self._enrich_table_from_catalog(table, catalog_node)
                merged += 1
            else:
                table = self._build_table_from_db_relation(relation)
                table.warnings.append(
                    "No dbt manifest metadata available for this relation; semantic guidance is limited."
                )
                db_only.append(full_name)

            self._register_table(table)

        for full_name in manifest_models:
            if full_name not in db_relations:
                stale_manifest.append(full_name)

        self._loaded = True
        self.load_report = {
            "db_relations": len(db_relations),
            "manifest_models": len(manifest_models),
            "catalog_models": len(catalog_models),
            "merged_relations": merged,
            "db_only_relations": db_only,
            "stale_manifest_relations": stale_manifest,
            "catalog_used": bool(catalog),
        }

        print(
            "[MetadataStore] Loaded "
            f"{len(self.list_tables())} relations "
            f"(db={len(db_relations)}, manifest={len(manifest_models)}, catalog={len(catalog_models)})"
        )
        if stale_manifest:
            print(
                "[MetadataStore] Skipped stale manifest relations: "
                f"{len(stale_manifest)}"
            )

    def _load_json_if_exists(self, path: Optional[str], required: bool) -> Optional[dict]:
        if not path:
            if required:
                raise FileNotFoundError("Metadata JSON path is required")
            return None

        if not os.path.exists(path):
            if required:
                raise FileNotFoundError(path)
            return None

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_db_relations(self) -> Dict[str, dict]:
        sql = """
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            c.data_type,
            c.udt_name,
            c.is_nullable,
            t.table_type
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_schema = ANY(%s)
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """

        relations: Dict[str, dict] = {}
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (ALLOWED_SCHEMAS,))
                rows = cur.fetchall()
        finally:
            conn.close()

        for row in rows:
            full_name = f"{row['table_schema']}.{row['table_name']}"
            relation = relations.setdefault(
                full_name,
                {
                    "schema": row["table_schema"],
                    "name": row["table_name"],
                    "table_type": row["table_type"],
                    "columns": {},
                    "primary_key": [],
                    "foreign_keys": [],
                },
            )
            relation["columns"][row["column_name"]] = {
                "data_type": row["data_type"] or row["udt_name"] or "",
                "nullable": row["is_nullable"] == "YES",
            }

        self._attach_db_primary_keys(relations)
        self._attach_db_foreign_keys(relations)
        return relations

    def _attach_db_primary_keys(self, relations: Dict[str, dict]):
        sql = """
        SELECT
            tc.table_schema,
            tc.table_name,
            kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = ANY(%s)
        ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
        """

        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (ALLOWED_SCHEMAS,))
                rows = cur.fetchall()
        finally:
            conn.close()

        for row in rows:
            full_name = f"{row['table_schema']}.{row['table_name']}"
            relation = relations.get(full_name)
            if relation:
                relation["primary_key"].append(row["column_name"])

    def _attach_db_foreign_keys(self, relations: Dict[str, dict]):
        sql = """
        SELECT
            tc.table_schema,
            tc.table_name,
            kcu.column_name,
            ccu.table_schema AS foreign_table_schema,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = ANY(%s)
        ORDER BY tc.table_schema, tc.table_name, kcu.column_name
        """

        conn = psycopg2.connect(**DB_CONFIG)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, (ALLOWED_SCHEMAS,))
                rows = cur.fetchall()
        finally:
            conn.close()

        for row in rows:
            full_name = f"{row['table_schema']}.{row['table_name']}"
            relation = relations.get(full_name)
            if not relation:
                continue
            relation["foreign_keys"].append({
                "column": row["column_name"],
                "references": (
                    f"{row['foreign_table_schema']}."
                    f"{row['foreign_table_name']}."
                    f"{row['foreign_column_name']}"
                ),
            })

    def _manifest_models_by_relation(self, manifest: dict) -> Dict[str, dict]:
        models = {}
        for _, node in manifest.get("nodes", {}).items():
            if node.get("resource_type") != "model":
                continue
            schema = node.get("schema", "")
            name = node.get("name", "")
            if schema not in ALLOWED_SCHEMAS or not name:
                continue
            models[f"{schema}.{name}"] = node
        return models

    def _catalog_models_by_relation(self, catalog: dict) -> Dict[str, dict]:
        relations = {}
        for _, node in catalog.get("nodes", {}).items():
            metadata = node.get("metadata", {}) or {}
            schema = metadata.get("schema", "") or node.get("schema", "")
            name = metadata.get("name", "") or node.get("name", "")
            if schema not in ALLOWED_SCHEMAS or not name:
                continue
            relations[f"{schema}.{name}"] = node
        return relations

    def _build_table_from_db_relation(self, relation: dict) -> TableMeta:
        node = {
            "name": relation["name"],
            "schema": relation["schema"],
            "description": "",
            "meta": {},
            "columns": {
                column_name: {
                    "description": "",
                    "data_type": column_meta.get("data_type", ""),
                    "nullable": column_meta.get("nullable"),
                }
                for column_name, column_meta in relation["columns"].items()
            },
        }
        return TableMeta(node)

    def _enrich_table_from_db(self, table: TableMeta, relation: dict):
        table.db_table_type = relation.get("table_type", "")
        table.db_validated = True

        if relation.get("primary_key"):
            table.primary_key = relation["primary_key"]

        if relation.get("foreign_keys"):
            table.db_foreign_keys = [
                ForeignKey(fk_config)
                for fk_config in relation["foreign_keys"]
            ]

        for column_name, column_meta in relation["columns"].items():
            existing = table.columns.get(column_name)
            if existing:
                if not existing.data_type:
                    existing.data_type = column_meta.get("data_type", "")
                if existing.nullable is None:
                    existing.nullable = column_meta.get("nullable")
            else:
                table.columns[column_name] = ColumnMeta(
                    column_name,
                    {
                        "description": "",
                        "data_type": column_meta.get("data_type", ""),
                        "nullable": column_meta.get("nullable"),
                    },
                )

    def _enrich_table_from_catalog(self, table: TableMeta, catalog_node: Optional[dict]):
        if not catalog_node:
            return

        catalog_metadata = catalog_node.get("metadata", {}) or {}
        if not table.description:
            table.description = (
                (catalog_node.get("comment") or "")
                or (catalog_metadata.get("comment") or "")
            ).strip()

        catalog_columns = catalog_node.get("columns", {}) or {}
        for column_name, column_data in catalog_columns.items():
            existing = table.columns.get(column_name)
            description = (column_data.get("comment") or "").strip()
            data_type = column_data.get("type", "")

            if existing:
                if not existing.description and description:
                    existing.description = description
                if not existing.data_type and data_type:
                    existing.data_type = data_type
            else:
                table.columns[column_name] = ColumnMeta(
                    column_name,
                    {
                        "description": description,
                        "data_type": data_type,
                    },
                )

    def _register_table(self, table: TableMeta):
        self.tables[table.full_name] = table
        self.tables[table.name] = table

    def get_table(self, name: str) -> Optional[TableMeta]:
        if name in self.tables:
            return self.tables[name]

        if "." in name:
            short_name = name.split(".")[-1]
            return self.tables.get(short_name)

        return None

    def list_tables(self, schema: Optional[str] = None) -> List[TableMeta]:
        seen = set()
        result = []

        for table in self.tables.values():
            if table.full_name in seen:
                continue
            seen.add(table.full_name)
            if schema is None or table.schema == schema:
                result.append(table)

        return result

    def list_table_names(self, schema: Optional[str] = None) -> List[str]:
        return [t.full_name for t in self.list_tables(schema)]

    def find_column_by_synonym(self, word: str) -> List[tuple]:
        results = []
        seen = set()

        for table in self.list_tables():
            col = table.find_column_by_synonym(word)
            if col:
                key = (table.full_name, col)
                if key not in seen:
                    seen.add(key)
                    results.append(key)

        return results

    def resolve_column_name(self, table_name: str, column: str) -> Optional[str]:
        table = self.get_table(table_name)
        if not table:
            return None
        return table.resolve_column_name(column)

    def find_tables_for_intent(self, intent: str) -> List[TableMeta]:
        intent_lower = intent.lower()
        results = []
        seen = set()

        for table in self.list_tables():
            for pref in table.preferred_for:
                pref_lower = pref.lower()
                if intent_lower in pref_lower or pref_lower in intent_lower:
                    if table.full_name not in seen:
                        seen.add(table.full_name)
                        results.append(table)
                    break

        return results

    def get_related_tables(self, table_name: str) -> List[str]:
        table = self.get_table(table_name)
        if not table:
            return []
        return table.get_related_tables()

    def describe_table(self, name: str) -> Optional[dict]:
        table = self.get_table(name)
        if not table:
            return None

        return {
            "table": table.full_name,
            "description": table.description,
            "semantic_type": table.semantic_type,
            "materialization": table.materialization,
            "db_table_type": table.db_table_type,
            "db_validated": table.db_validated,
            "grain": table.grain,
            "primary_key": table.primary_key,
            "columns": [
                {
                    "name": col.name,
                    "type": col.data_type or "unknown",
                    "description": col.description,
                    "nullable": col.nullable,
                    "synonyms": col.synonyms,
                }
                for col in table.columns.values()
            ],
            "foreign_keys": [
                {
                    "column": fk.column,
                    "references": fk.references
                }
                for fk in table.foreign_keys
            ],
            "db_foreign_keys": [
                {
                    "column": fk.column,
                    "references": fk.references
                }
                for fk in table.db_foreign_keys
            ],
            "temporal_logic": list(table.temporal_logic.keys()),
            "warnings": table.warnings,
            "preferred_for": table.preferred_for,
            "avoid_for": table.avoid_for,
        }

    def find_tables_by_column(self, column: str) -> List[TableMeta]:
        matches = []
        seen = set()

        for table in self.list_tables():
            if table.resolve_column_name(column) and table.full_name not in seen:
                seen.add(table.full_name)
                matches.append(table)

        return sorted(matches, key=lambda t: t.full_name)

    def table_has_column(self, table_name: str, column: str, include_synonyms: bool = True) -> bool:
        table = self.get_table(table_name)
        if not table:
            return False
        if include_synonyms:
            return table.resolve_column_name(column) is not None
        return column in table.columns

    def find_join_path(self, base_table_name: str, target_table_name: str):
        base = self.get_table(base_table_name)
        target = self.get_table(target_table_name)

        if not base or not target:
            return None

        for fk in base.foreign_keys + base.db_foreign_keys:
            ref_table = fk.ref_full_table()
            if ref_table == target.full_name or ref_table == target.name:
                return {
                    "left": base.full_name,
                    "right": target.full_name,
                    "on": f"{base.full_name}.{fk.column} = {target.full_name}.{fk.ref_column}"
                }

        return None


# =============================================================================
# SINGLETON
# =============================================================================

metadata_store = MetadataStore()


def init_metadata(manifest_path: str, catalog_path: Optional[str] = None):
    metadata_store.load_from_sources(
        manifest_path=manifest_path,
        catalog_path=catalog_path,
    )


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    init_metadata("../dbt/manifest.json", "../dbt/catalog.json")

    print("\n=== Tables loaded ===")
    for name in metadata_store.list_table_names():
        print(f"  {name}")

    print("\n=== Load report ===")
    print(metadata_store.load_report)
