from typing import Dict, Any, List, Optional

from tools.get_metric import get_metric_tool
from tools.plan_query import plan_query
from tools.run_query import run_query


def run_metric_query(
    metric_name: str,
    params: Optional[Dict[str, Any]] = None,
    group_by: Optional[List[str]] = None,
) -> Dict[str, Any]:

    # 1. get metric definition
    metric = get_metric_tool(
        metric_name,
        params=params or {},
        group_by=group_by or []
    )

    if metric.get("error"):
        return metric

    # 2. build SQL
    plan = plan_query(
        metric_result=metric,
        group_by=group_by or []
    )

    if plan.get("error"):
        return plan

    # 3. run query
    result = run_query(
        plan["sql"],
        bindings=plan.get("bindings"),
        mode="strict"
    )

    # 4. attach debug info (rất hữu ích)
    result["sql"] = plan["sql"]
    result["metric"] = metric_name

    return {
        "success": result.get("success"),
        "data": result.get("data"),
        "row_count": result.get("row_count"),
        "sql": plan["sql"],
        "metric": metric_name,
        "warnings": result.get("warnings", []),
    }
