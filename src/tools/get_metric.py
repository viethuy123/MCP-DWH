from typing import Dict, Any, List, Optional
from metric_registry import (
    get_metric,
    list_metrics,
    render_metric,
    find_metric_by_synonym,
    validate_grain,
    resolve_joins_for_grain,
)


def get_metric_tool(
    metric_name: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    group_by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    MCP Tool: get_metric
    Trả về definition và rendered SQL unit cho một metric.

    Input:
        metric_name: tên metric — "headcount", "attrition", "new_hire",
                                   "absent_days", "tenure"
                     nếu None → trả về list tất cả metrics
        params:      date params nếu có — {"target_date": "2024-06-30"}
                     nếu None → tự resolve về default (CURRENT_DATE hoặc tháng hiện tại)
        group_by:    list columns muốn GROUP BY — để resolve joins cần thiết

    Output (khi có metric_name):
        {
            "metric_name":    "headcount",
            "metric_type":    "point_in_time",
            "description":    "...",
            "table":          "dim.dim_odoo_members",
            "select_expr":    "COUNT(DISTINCT member_id)",
            "where_clause":   "official_date <= CURRENT_DATE AND ...",
            "bindings":       {},
            "missing_params": [],
            "required_joins": [...],        ← chỉ joins cần cho group_by đã chỉ định
            "grain_warnings": [...],        ← columns trong group_by không thuộc grain
            "aggregation":    {...},
            "warnings":       [...],
            "constraints":    [...],
            "usage_hint":     "SELECT {select_expr} FROM {table} WHERE {where_clause} GROUP BY ..."
        }

    Output (khi metric_name = None):
        {
            "available_metrics": {
                "headcount":  "...",
                "attrition":  "...",
                ...
            }
        }
    """

    # --- list mode ---
    if not metric_name:
        return {
            "available_metrics": list_metrics(),
            "hint": (
                "Call get_metric with a metric_name to get the full definition. "
                "Use find_metric_by_synonym to look up by Vietnamese term."
            ),
        }

    # --- lookup ---
    if not get_metric(metric_name):
        # thử tìm qua synonym
        found = find_metric_by_synonym(metric_name)
        if found:
            metric_name = found
        else:
            return {
                "error":             f"Metric '{metric_name}' not found.",
                "available_metrics": list(list_metrics().keys()),
            }

    # --- render ---
    rendered = render_metric(metric_name, params=params)
    if not rendered:
        return {"error": f"Failed to render metric '{metric_name}'."}

    # --- grain validation ---
    group_by       = group_by or []
    grain_warnings = []

    if group_by:
        invalid_cols = validate_grain(metric_name, group_by)
        if invalid_cols:
            grain_warnings = [
                f"Column '{col}' is not in the defined grain for '{metric_name}'. "
                f"Valid grain: {rendered['grain']}"
                for col in invalid_cols
            ]

    # --- resolve joins ---
    required_joins = resolve_joins_for_grain(metric_name, group_by) if group_by else []

    # --- usage hint ---
    usage_hint = (
        f"SELECT {rendered['select_expr']} "
        f"FROM {rendered['table']} "
        f"WHERE {rendered['where_clause']}"
    )
    if group_by:
        usage_hint += f" GROUP BY {', '.join(group_by)}"

    return {
        "metric_name":    rendered["metric_name"],
        "metric_type":    rendered["metric_type"],
        "description":    get_metric(metric_name).get("description", ""),
        "table":          rendered["table"],
        "select_expr":    rendered["select_expr"],
        "where_clause":   rendered["where_clause"],   # REQUIRED — dùng cùng select_expr
        "bindings":       rendered["bindings"],
        "missing_params": rendered["missing_params"],
        "required_joins": required_joins,
        "grain_warnings": grain_warnings,
        "aggregation":    rendered["aggregation"],
        "warnings":       rendered["warnings"],
        "constraints":    rendered["constraints"],
        "usage_hint":     usage_hint,
    }


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    from pprint import pprint

    print("=== list all metrics ===")
    pprint(get_metric_tool())

    print("\n=== headcount (no params) ===")
    pprint(get_metric_tool("headcount"))

    print("\n=== headcount (with target_date + group_by) ===")
    pprint(get_metric_tool(
        "headcount",
        params={"target_date": "2024-06-30"},
        group_by=["division_name", "branch_name"]
    ))

    print("\n=== headcount group_by invalid column ===")
    pprint(get_metric_tool(
        "headcount",
        group_by=["division_name", "etl_datetime"]   # etl_datetime không trong grain
    ))

    print("\n=== absent_days (resolve joins for division_name) ===")
    pprint(get_metric_tool(
        "absent_days",
        params={"start_date": "2024-01-01", "end_date": "2024-06-30"},
        group_by=["division_name"]
    ))

    print("\n=== synonym lookup ===")
    pprint(get_metric_tool("thâm niên"))   # → tenure