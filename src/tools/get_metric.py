from typing import Dict, Any, List, Optional

from metric_registry import (
    get_metric,
    list_metrics,
    render_metric,
    find_metric_by_synonym,
    validate_grain,
    resolve_joins_for_grain,
)
from metadata import metadata_store


def _resolve_group_by_columns(table_name: str, group_by: List[str]) -> List[Dict[str, str]]:
    resolved = []
    seen = set()

    for raw_col in group_by:
        raw_col = raw_col.strip()
        if not raw_col:
            continue

        resolved_table = table_name
        resolved_column = metadata_store.resolve_column_name(table_name, raw_col)

        if not resolved_column:
            candidates = metadata_store.find_tables_by_column(raw_col)
            if not candidates:
                continue

            target = sorted(candidates, key=lambda t: t.full_name)[0]
            resolved_table = target.full_name
            resolved_column = metadata_store.resolve_column_name(target.full_name, raw_col) or raw_col

        key = (resolved_table, resolved_column)
        if key in seen:
            continue
        seen.add(key)
        resolved.append({
            "input": raw_col,
            "table": resolved_table,
            "column": resolved_column,
            "qualified_name": f"{resolved_table}.{resolved_column}",
        })

    return resolved


def get_metric_tool(
    metric_name: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    group_by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    group_by = group_by or []
    params = params or {}

    if not metric_name:
        return {
            "success": True,
            "available_metrics": list_metrics(),
            "hint": (
                "Call get_metric with a metric_name to get the full definition. "
                "Use exact Vietnamese synonyms when needed."
            ),
        }

    resolved_metric_name = metric_name
    metric_def = get_metric(resolved_metric_name)

    if not metric_def:
        synonym_match = find_metric_by_synonym(metric_name)
        if synonym_match:
            resolved_metric_name = synonym_match
            metric_def = get_metric(resolved_metric_name)

    if not metric_def:
        return {
            "success": False,
            "error": f"Metric '{metric_name}' not found.",
            "available_metrics": list(list_metrics().keys()),
        }

    rendered = render_metric(resolved_metric_name, params=params)
    if not rendered:
        return {
            "success": False,
            "error": f"Failed to render metric '{resolved_metric_name}'.",
        }

    resolved_group_by = _resolve_group_by_columns(rendered["table"], group_by)
    effective_group_by = [item["column"] for item in resolved_group_by]

    invalid_cols = validate_grain(resolved_metric_name, effective_group_by) if effective_group_by else []
    grain_warnings = []
    for col in invalid_cols:
        if metadata_store.table_has_column(rendered["table"], col):
            continue

        grain_warnings.append(
            f"Column '{col}' is not in the preferred grain for '{resolved_metric_name}' "
            f"and is not present on base table '{rendered['table']}'. "
            f"Preferred grain: {rendered['grain']}"
        )

    required_joins = (
        resolve_joins_for_grain(resolved_metric_name, effective_group_by)
        if effective_group_by else []
    )

    usage_hint = (
        f"SELECT {rendered['select_expr']} "
        f"FROM {rendered['table']} "
        f"WHERE {rendered['where_clause']}"
    )
    if group_by:
        usage_hint += f" GROUP BY {', '.join(group_by)}"

    warnings = list(rendered["warnings"])
    warnings.extend(grain_warnings)

    return {
        "success": True,
        "metric_name": rendered["metric_name"],
        "metric_type": rendered["metric_type"],
        "description": metric_def.get("description", ""),
        "table": rendered["table"],
        "select_expr": rendered["select_expr"],
        "where_clause": rendered["where_clause"],
        "bindings": rendered["bindings"],
        "missing_params": rendered["missing_params"],
        "required_joins": required_joins,
        "group_by_resolved": resolved_group_by,
        "group_by_effective": effective_group_by,
        "grain_warnings": grain_warnings,
        "aggregation": rendered["aggregation"],
        "warnings": warnings,
        "constraints": rendered["constraints"],
        "usage_hint": usage_hint,
        "resolved_from_synonym": resolved_metric_name != metric_name,
    }
