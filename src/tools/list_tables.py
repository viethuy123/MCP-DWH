from typing import Dict, Any, List, Optional
from metadata import metadata_store

# Allowed schemas trong DWH
ALLOWED_SCHEMAS = {"dim", "fct", "reports"}


def list_tables(schema: Optional[str] = None) -> Dict[str, Any]:
    """
    MCP Tool: list_tables

    Liệt kê tables trong metadata store.

    Input:
        schema: optional — filter theo schema ("dim", "fct", "reports")

    Output:
        {
            "tables": [
                {
                    "full_name":     str,
                    "schema":        str,
                    "name":          str,
                    "semantic_type": str,
                    "description":   str,
                    "grain":         list[str],
                    "columns_count": int,
                }
            ],
            "count": int,
            "schema_filter": str | None
        }
    """

    # -------------------------------------------------------------------------
    # Validate metadata loaded
    # -------------------------------------------------------------------------
    if not metadata_store.is_loaded():
        return {
            "error": "Metadata not loaded. Call init_metadata() first."
        }

    # -------------------------------------------------------------------------
    # Validate schema input
    # -------------------------------------------------------------------------
    if schema:
        schema = schema.strip().lower()
        if schema not in ALLOWED_SCHEMAS:
            return {
                "error": f"Invalid schema: '{schema}'. Must be one of {sorted(ALLOWED_SCHEMAS)}"
            }

    # -------------------------------------------------------------------------
    # Fetch tables
    # -------------------------------------------------------------------------
    tables = metadata_store.list_tables(schema=schema)

    # -------------------------------------------------------------------------
    # Stable sort (quan trọng cho LLM consistency)
    # -------------------------------------------------------------------------
    tables = sorted(tables, key=lambda t: t.full_name)

    # -------------------------------------------------------------------------
    # Build response
    # -------------------------------------------------------------------------
    result: List[Dict[str, Any]] = []

    for t in tables:
        result.append({
            "full_name":     t.full_name,
            "schema":        t.schema,
            "name":          t.name,
            "semantic_type": t.semantic_type,
            "description":   t.description,
            "materialization": getattr(t, "materialization", ""),
            "db_table_type": getattr(t, "db_table_type", ""),
            "db_validated": getattr(t, "db_validated", False),
            "grain":         t.grain,
            "columns_count": len(getattr(t, "columns", [])),
        })

    return {
        "tables":        result,
        "count":         len(result),
        "schema_filter": schema,
    }


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    from metadata import init_metadata
    from pprint import pprint

    init_metadata("../../dbt/manifest.json")

    print("=== All tables ===")
    pprint(list_tables())

    print("\n=== dim schema only ===")
    pprint(list_tables(schema="dim"))

    print("\n=== invalid schema ===")
    pprint(list_tables(schema="abc"))
