from typing import Dict, Any, List, Optional

from tools.get_metric import get_metric_tool
from tools.plan_query import plan_query
from tools.run_query import run_query


def _build_headcount_last_n_months_sql(last_n_months: int) -> str:
    return """
WITH month_ends AS (
    SELECT
        (
            date_trunc('month', CURRENT_DATE)
            - (g.n || ' month')::interval
            + interval '1 month - 1 day'
        )::date AS month_end
    FROM generate_series(0, %(last_n_months)s - 1) AS g(n)
)
SELECT
    m.month_end,
    COUNT(DISTINCT d.member_id) AS value
FROM month_ends m
LEFT JOIN dim.dim_odoo_members d
    ON (d.official_date <= m.month_end OR d.official_date IS NULL)
   AND (d.end_date IS NULL OR d.end_date > m.month_end)
GROUP BY m.month_end
ORDER BY m.month_end DESC
"""


def run_metric_query(
    metric_name: str,
    params: Optional[Dict[str, Any]] = None,
    group_by: Optional[List[str]] = None,
) -> Dict[str, Any]:
    params = params or {}
    group_by = group_by or []

    # Support trend query for headcount in the last N months.
    # Example: run_metric_query("headcount", {"last_n_months": 6})
    if metric_name == "headcount" and "last_n_months" in params:
        try:
            last_n_months = int(params["last_n_months"])
        except Exception:
            return {
                "success": False,
                "error": "Invalid 'last_n_months'. It must be an integer.",
                "metric": metric_name,
            }

        if last_n_months <= 0:
            return {
                "success": False,
                "error": "'last_n_months' must be greater than 0.",
                "metric": metric_name,
            }

        sql = _build_headcount_last_n_months_sql(last_n_months)
        result = run_query(
            sql,
            bindings={"last_n_months": last_n_months},
            mode="strict",
        )

        return {
            "success": result.get("success", False),
            "data": result.get("data"),
            "row_count": result.get("row_count"),
            "sql": sql,
            "metric": metric_name,
            "warnings": result.get("warnings", []),
            "grain_warnings": [],
            "severity": result.get("severity", "LOW"),
            "error": result.get("error"),
        }

    metric = get_metric_tool(
        metric_name=metric_name,
        params=params,
        group_by=group_by,
    )

    if metric.get("error"):
        return {
            "success": False,
            "error": metric.get("error"),
            "metric": metric_name,
            "available_metrics": metric.get("available_metrics"),
        }

    if metric.get("missing_params"):
        return {
            "success": False,
            "error": "Missing required metric parameters",
            "metric": metric_name,
            "missing_params": metric.get("missing_params", []),
            "warnings": metric.get("warnings", []),
            "grain_warnings": metric.get("grain_warnings", []),
        }

    plan = plan_query(
        metric_result=metric,
        group_by=group_by,
        mode="strict",
    )

    if plan.get("error"):
        return {
            "success": False,
            "error": plan.get("error"),
            "metric": metric_name,
            "warnings": plan.get("warnings", []),
            "severity": plan.get("severity", "HIGH"),
            "block": plan.get("block", True),
        }

    if plan.get("block", False):
        return {
            "success": False,
            "error": "Metric query blocked by guardrails",
            "metric": metric_name,
            "sql": plan.get("sql"),
            "warnings": plan.get("warnings", []),
            "severity": plan.get("severity", "HIGH"),
            "block": True,
            "grain_warnings": metric.get("grain_warnings", []),
        }

    result = run_query(
        plan["sql"],
        bindings=plan.get("bindings"),
        mode="strict",
    )

    return {
        "success": result.get("success", False),
        "data": result.get("data"),
        "row_count": result.get("row_count"),
        "sql": plan.get("sql"),
        "metric": metric_name,
        "warnings": result.get("warnings", []),
        "grain_warnings": metric.get("grain_warnings", []),
        "severity": result.get("severity", plan.get("severity", "LOW")),
        "error": result.get("error"),
    }
