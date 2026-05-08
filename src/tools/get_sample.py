from typing import Dict, Any

from metadata import metadata_store
from tools.run_query import run_query
from config import ALLOWED_SCHEMAS

SAMPLE_MAX_ROWS = 10


def get_sample(table: str, n: int = 5) -> Dict[str, Any]:
    if not metadata_store.is_loaded():
        return {
            "success": False,
            "error": "Metadata not loaded. Call init_metadata() first.",
        }

    n = max(1, min(n, SAMPLE_MAX_ROWS))

    table_meta = metadata_store.get_table(table)
    if not table_meta:
        all_names = metadata_store.list_table_names()
        table_lower = table.lower()
        suggestions = [
            name for name in all_names
            if name.lower().startswith(table_lower)
        ][:5]

        return {
            "success": False,
            "error": f"Table '{table}' not found.",
            "suggestions": suggestions or all_names[:5],
        }

    if table_meta.schema not in ALLOWED_SCHEMAS:
        return {
            "success": False,
            "error": f"Table '{table_meta.full_name}' is not allowed.",
        }

    full_name = table_meta.full_name
    sql = f"SELECT * FROM {full_name} LIMIT {n}"

    result = run_query(sql, mode="strict")
    if not result.get("success", False):
        return {
            "success": False,
            "error": result.get("error"),
            "table": full_name,
            "executed_sql": result.get("executed_sql"),
            "warnings": result.get("warnings", []),
            "severity": result.get("severity"),
        }

    rows_raw = result.get("data", [])
    if not rows_raw:
        return {
            "success": True,
            "table": full_name,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "note": "Table is empty or no data returned.",
            "warnings": result.get("warnings", []),
        }

    columns = list(rows_raw[0].keys())
    rows = [list(row.values()) for row in rows_raw]

    return {
        "success": True,
        "table": full_name,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "note": "Sample only - not representative of full dataset.",
        "warnings": result.get("warnings", []),
    }
