from typing import Dict, Any, Optional, List
from metadata import metadata_store


def _normalize_table_name(table: str) -> str:
    """
    Normalize table name:
    - trim
    - lower
    """
    return table.strip().lower()


def describe_table(table: str) -> Dict[str, Any]:
    """
    MCP Tool: describe_table
    Trả về schema đầy đủ của một table.
    """

    # -------------------------------------------------------------------------
    # Validate metadata loaded
    # -------------------------------------------------------------------------
    if not metadata_store.is_loaded():
        return {"error": "Metadata not loaded. Call init_metadata() first."}

    # -------------------------------------------------------------------------
    # Validate input
    # -------------------------------------------------------------------------
    if not table or not table.strip():
        return {"error": "Table name cannot be empty"}

    table_norm = _normalize_table_name(table)

    # -------------------------------------------------------------------------
    # Fetch metadata
    # -------------------------------------------------------------------------
    result = metadata_store.describe_table(table_norm)

    if not result:
        # ---------------------------------------------------------------------
        # Suggest similar tables (simple scoring)
        # ---------------------------------------------------------------------
        all_names: List[str] = metadata_store.list_table_names()

        scored = []
        for name in all_names:
            name_lower = name.lower()

            if table_norm == name_lower:
                score = 3
            elif name_lower.startswith(table_norm):
                score = 2
            elif table_norm in name_lower:
                score = 1
            else:
                continue

            scored.append((score, name))

        scored.sort(reverse=True)

        suggestions = [name for _, name in scored[:5]]

        return {
            "error":       f"Table '{table}' not found.",
            "suggestions": suggestions or all_names[:5],
        }

    # -------------------------------------------------------------------------
    # Normalize output (optional but recommended)
    # -------------------------------------------------------------------------
    result["columns"] = sorted(
        result.get("columns", []),
        key=lambda c: c.get("name", "")
    )

    result["columns_count"] = len(result.get("columns", []))
    result["foreign_keys_count"] = len(result.get("foreign_keys", []))
    result["db_foreign_keys_count"] = len(result.get("db_foreign_keys", []))

    return result


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    from metadata import init_metadata
    from pprint import pprint

    init_metadata("../../dbt/manifest.json")

    print("=== dim_odoo_members ===")
    pprint(describe_table("dim.dim_odoo_members"))

    print("\n=== short name ===")
    pprint(describe_table("dim_odoo_members"))

    print("\n=== not found ===")
    pprint(describe_table("dim.unknown_table"))
