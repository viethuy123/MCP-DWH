import json
from typing import Dict, List, Optional, Union

from config import ALLOWED_SCHEMAS, detect_table_type


# =============================================================================
# TEMPORAL LOGIC
# =============================================================================

class TemporalLogic:
    def __init__(self, name: str, config: Union[str, dict]):
        self.name = name

        # yaml value có thể là string thẳng hoặc dict với key condition_sql
        if isinstance(config, str):
            self.condition_sql = config.strip()
        elif isinstance(config, dict):
            self.condition_sql = config.get("condition_sql", "").strip()
        else:
            self.condition_sql = ""

    def render(self, target_date: str = "CURRENT_DATE") -> str:
        """
        Render condition, replace :target_date placeholder nếu có.
        Nếu không có placeholder → trả về condition gốc (đã hardcode CURRENT_DATE).
        """
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
        self.references = config.get("references")  # format: schema.table.column

        self.ref_schema = None
        self.ref_table = None
        self.ref_column = None

        if self.references:
            parts = self.references.split(".")
            if len(parts) == 3:
                self.ref_schema, self.ref_table, self.ref_column = parts
            elif len(parts) == 2:
                # fallback: table.column không có schema
                self.ref_table, self.ref_column = parts

    def ref_full_table(self) -> Optional[str]:
        if self.ref_schema and self.ref_table:
            return f"{self.ref_schema}.{self.ref_table}"
        if self.ref_table:
            return self.ref_table
        return None

    def __repr__(self):
        return f"<ForeignKey {self.column} → {self.references}>"


# =============================================================================
# COLUMN META
# =============================================================================

class ColumnMeta:
    def __init__(self, name: str, config: dict):
        self.name = name
        self.description: str = config.get("description", "").strip()
        self.data_type: str = config.get("data_type", "")
        self.meta: dict = config.get("meta", {})

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

        meta = node.get("meta", {}) or {}

        # --- core ---
        self.semantic_type: str = meta.get("semantic_type") or detect_table_type(self.name)
        self.grain: List[str] = meta.get("grain", [])

        # primary_key có thể là string hoặc list
        pk = meta.get("primary_key")
        if isinstance(pk, list):
            self.primary_key: List[str] = pk
        elif isinstance(pk, str):
            self.primary_key: List[str] = [pk]
        else:
            self.primary_key: List[str] = []

        # --- columns ---
        raw_columns = node.get("columns", {})
        self.columns: Dict[str, ColumnMeta] = {
            col_name: ColumnMeta(col_name, col_data)
            for col_name, col_data in raw_columns.items()
        }

        # --- relationships ---
        self.foreign_keys: List[ForeignKey] = [
            ForeignKey(fk) for fk in meta.get("foreign_keys", [])
        ]

        # --- temporal ---
        self.temporal_logic: Dict[str, TemporalLogic] = {}
        for logic_name, logic_cfg in meta.get("temporal_logic", {}).items():
            self.temporal_logic[logic_name] = TemporalLogic(logic_name, logic_cfg)

        # time_context: reference_date_column cho fact tables
        time_context = meta.get("time_context", {}) or {}
        self.reference_date_column: Optional[str] = time_context.get("reference_date_column")

        # --- usage & warnings ---
        usage = meta.get("usage", {}) or {}
        self.preferred_for: List[str] = usage.get("preferred_for", [])
        self.avoid_for: List[str] = usage.get("avoid_for", [])
        self.warnings: List[str] = usage.get("warnings", [])

        # --- data characteristics ---
        self.data_characteristics: dict = meta.get("data_characteristics", {}) or {}

        # --- synonyms ---
        # format: { column_name: [synonym1, synonym2] }
        self.synonyms: Dict[str, List[str]] = meta.get("synonyms", {}) or {}

    # =========================================================================
    # TYPE HELPERS
    # =========================================================================

    def is_fact(self) -> bool:
        return self.semantic_type == "fact"

    def is_dimension(self) -> bool:
        return self.semantic_type == "dimension"

    def is_report(self) -> bool:
        return self.semantic_type == "report"

    # =========================================================================
    # TEMPORAL HELPERS
    # =========================================================================

    def get_temporal_condition(
        self,
        logic_name: str,
        target_date: str = "CURRENT_DATE"
    ) -> Optional[str]:
        """
        Trả về rendered SQL condition cho temporal logic được chỉ định.
        target_date chỉ có tác dụng khi condition có :target_date placeholder.
        """
        logic = self.temporal_logic.get(logic_name)
        if not logic:
            return None
        return logic.render(target_date)

    def has_temporal_logic(self, logic_name: str) -> bool:
        return logic_name in self.temporal_logic

    # =========================================================================
    # COLUMN HELPERS
    # =========================================================================

    def get_column(self, col_name: str) -> Optional[ColumnMeta]:
        return self.columns.get(col_name)

    def column_names(self) -> List[str]:
        return list(self.columns.keys())

    def find_column_by_synonym(self, word: str) -> Optional[str]:
        """
        Tìm column name từ synonym — support exact match và partial match.
        Trả về column name đầu tiên khớp.
        """
        word_lower = word.lower().strip()

        for col_name, synonyms in self.synonyms.items():
            for syn in synonyms:
                syn_lower = syn.lower().strip()
                # exact match trước
                if word_lower == syn_lower:
                    return col_name
                # partial match: synonym nằm trong word (ví dụ: "theo phòng ban")
                if syn_lower in word_lower:
                    return col_name

        return None

    # =========================================================================
    # FOREIGN KEY HELPERS
    # =========================================================================

    def get_foreign_key(self, column: str) -> Optional[ForeignKey]:
        for fk in self.foreign_keys:
            if fk.column == column:
                return fk
        return None

    def get_related_tables(self) -> List[str]:
        return [
            fk.ref_full_table()
            for fk in self.foreign_keys
            if fk.ref_full_table()
        ]

    def find_join_key(self, other_table_name: str) -> Optional[tuple]:
        """
        Tìm join key giữa bảng này và bảng khác dựa trên foreign_keys.
        Trả về (local_column, ref_column) nếu tìm thấy.
        """
        for fk in self.foreign_keys:
            ref_table = fk.ref_full_table() or ""
            # match full name hoặc chỉ table name
            if other_table_name in ref_table or ref_table.endswith(other_table_name):
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

    # =========================================================================
    # LOAD
    # =========================================================================

    def load_from_manifest(self, path: str):
        """
        Load từ dbt manifest.json (target/manifest.json sau khi dbt compile/run).
        Chỉ load các model thuộc ALLOWED_SCHEMAS.
        """
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        count = 0
        for node_id, node in manifest.get("nodes", {}).items():
            if node.get("resource_type") != "model":
                continue

            schema = node.get("schema", "")
            if schema not in ALLOWED_SCHEMAS:
                continue

            table = TableMeta(node)
            # self.tables[table.full_name] = table
            self.tables[table.full_name] = table
            self.tables[table.name] = table
            count += 1

        self._loaded = True
        print(f"[MetadataStore] Loaded {count} tables from {path}")

    # =========================================================================
    # LOOKUP
    # =========================================================================

    def get_table(self, name: str) -> Optional[TableMeta]:

        # exact match
        if name in self.tables:
            return self.tables[name]

        # fallback: strip schema
        if "." in name:
            short_name = name.split(".")[-1]
            return self.tables.get(short_name)

        return None

    def list_tables(self, schema: Optional[str] = None) -> List[TableMeta]:
        """
        List tất cả tables, có thể filter theo schema.
        """
        if schema:
            return [t for t in self.tables.values() if t.schema == schema]
        return list(self.tables.values())

    def list_table_names(self, schema: Optional[str] = None) -> List[str]:
        return [t.full_name for t in self.list_tables(schema)]

    # =========================================================================
    # SEMANTIC SEARCH
    # =========================================================================

    def find_column_by_synonym(self, word: str) -> List[tuple]:
        """
        Tìm column trên toàn bộ tables theo synonym.
        Trả về list [(full_table_name, column_name), ...]
        """
        results = []
        for table in self.tables.values():
            col = table.find_column_by_synonym(word)
            if col:
                results.append((table.full_name, col))
        return results

    def find_tables_for_intent(self, intent: str) -> List[TableMeta]:
        """
        Tìm tables phù hợp dựa trên preferred_for descriptions.
        Simple substring match — đủ dùng cho scale hiện tại.
        """
        intent_lower = intent.lower()
        results = []

        for table in self.tables.values():
            for pref in table.preferred_for:
                if intent_lower in pref.lower() or pref.lower() in intent_lower:
                    results.append(table)
                    break

        return results

    # =========================================================================
    # RELATIONSHIP HELPERS
    # =========================================================================

    def get_related_tables(self, table_name: str) -> List[str]:
        table = self.get_table(table_name)
        if not table:
            return []
        return table.get_related_tables()

    def find_join_path(self, from_table: str, to_table: str) -> Optional[tuple]:
        """
        Tìm join path trực tiếp giữa 2 tables.
        Trả về (local_col, ref_col) nếu có FK từ from → to.
        """
        table = self.get_table(from_table)
        if not table:
            return None
        return table.find_join_key(to_table)

    # =========================================================================
    # DESCRIBE (dùng cho describe_table tool)
    # =========================================================================

    def describe_table(self, name: str) -> Optional[dict]:
        """
        Trả về full metadata của một table — dùng cho MCP describe_table tool.
        """
        table = self.get_table(name)
        if not table:
            return None

        return {
            "table": table.full_name,
            "description": table.description,
            "semantic_type": table.semantic_type,
            "grain": table.grain,
            "primary_key": table.primary_key,
            "columns": [
                {
                    "name": col.name,
                    "type": col.data_type or "unknown",
                    "description": col.description
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
            "temporal_logic": list(table.temporal_logic.keys()),
            "warnings": table.warnings,
            "preferred_for": table.preferred_for,
            "avoid_for": table.avoid_for,
        }
    
    def find_tables_by_column(self, column: str):
        matches = []
        print("Looking for column:", column)
        print("Tables in metadata: ", self.tables.values())
        for table in self.tables.values():
            print(table.columns)
            if column in table.columns:
                matches.append(table)
        return matches
    
    def table_has_column(self, table_name: str, column: str) -> bool:
        table = self.get_table(table_name)
        if not table:
            return False
        return column in table.columns
    
    def find_join_path(self, base_table_name: str, target_table_name: str):
        base = self.get_table(base_table_name)
        target = self.get_table(target_table_name)

        if not base or not target:
            return None

        for fk in base.foreign_keys:
            ref_table = fk.ref_full_table()

            if ref_table == target.full_name:
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


def init_metadata(manifest_path: str):
    """
    Gọi 1 lần khi server start.
    """
    metadata_store.load_from_manifest(manifest_path)


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    init_metadata("../dbt/manifest.json")

    print("\n=== Tables loaded ===")
    for name in metadata_store.list_table_names():
        print(f"  {name}")

    print("\n=== dim_odoo_members ===")
    from pprint import pprint
    pprint(metadata_store.describe_table("dim.dim_odoo_members"))

    print("\n=== temporal condition: active_employee ===")
    t = metadata_store.get_table("dim.dim_odoo_members")
    if t:
        print(t.get_temporal_condition("active_employee"))
        print(t.get_temporal_condition("point_in_time", target_date="'2024-06-01'"))

    print("\n=== join path: fct_attendance_daily → dim_odoo_members ===")
    path = metadata_store.find_join_path(
        "fct.fct_attendance_daily",
        "dim.dim_odoo_members"
    )
    print(path)