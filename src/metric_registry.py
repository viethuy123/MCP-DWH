from typing import Dict, Any, List, Optional, Tuple

# =============================================================================
# METRIC REGISTRY
# =============================================================================
# Single source of truth cho metric definitions.
#
# metric_type:
#   point_in_time - tinh tai 1 thoi diem
#   period        - tinh trong 1 khoang thoi gian
#
# Params duoc tach thanh 2 loai ro rang:
#   default_sql     - Postgres functions, inject thang vao SQL (khong bind)
#                     vi du: CURRENT_DATE, DATE_TRUNC('month', CURRENT_DATE)
#   default_values  - literal values, bind qua psycopg2 %(key)s
#                     vi du: "2024-06-30"
#
# render_metric() tra ve enforced unit gom:
#   select_expr   - aggregation expression
#   from_table    - bang chinh
#   where_clause  - BAT BUOC dung, khong the tach roi select_expr
#   bindings      - dict cho psycopg2, chi chua literal values
# =============================================================================

METRICS: Dict[str, Dict[str, Any]] = {

    # -------------------------------------------------------------------------
    # HEADCOUNT
    # -------------------------------------------------------------------------
    "headcount": {
        "metric_type": "point_in_time",
        "table":        "dim.dim_odoo_members",
        "select_expr":  "COUNT(DISTINCT member_id)",
        "where_clause": (
            "(official_date <= {target_date} OR official_date IS NULL)"
            "AND (end_date IS NULL OR end_date > {target_date})"
        ),
        "required_params": ["target_date"],
        "default_sql": {
            "target_date": "CURRENT_DATE",
        },
        "constraints": [],
        "description": (
            "Number of active employees at a given point in time. "
            "Always COUNT DISTINCT. Always apply point-in-time filter. "
            "Do NOT filter by member_status."
        ),
        "aggregation": {
            "function": "COUNT DISTINCT",
            "column":   "member_id",
            "note":     "Never COUNT(*) - will overcount if table has duplicates",
        },
        "grain": [
            "division_name", "branch_name", "position_group",
            "member_level", "contract_type", "member_type",
        ],
        "joins": [],
        "warnings": [
            "Never use member_status to determine active employees",
            "Always apply: (official_date <= target OR official_date IS NULL) AND (end_date IS NULL OR end_date > target)",
            "Use COUNT DISTINCT member_id - not COUNT(*)",
        ],
        "synonyms": [
            "số nhân viên", "headcount", "đầu người",
            "tổng nhân viên", "nhân viên đang làm", "số lượng nhân viên",
        ],
    },

    # -------------------------------------------------------------------------
    # ATTRITION
    # -------------------------------------------------------------------------
    "attrition": {
        "metric_type": "period",
        "table":        "dim.dim_odoo_members",
        "select_expr":  "COUNT(DISTINCT member_id)",
        "where_clause": (
            "end_date >= {start_date} "
            "AND end_date <= {end_date}"
        ),
        "required_params": ["start_date", "end_date"],
        "default_sql": {
            "start_date": "DATE_TRUNC('month', CURRENT_DATE)",
            "end_date":   "CURRENT_DATE",
        },
        "partial_sql": {
            "end_date":   "CURRENT_DATE",
            "start_date": "DATE_TRUNC('month', %(end_date)s::date)",
        },
        "constraints": [],
        "description": (
            "Number of employees who left during a given period. "
            "Filter by end_date within the period. "
            "Do NOT use member_status."
        ),
        "aggregation": {
            "function": "COUNT DISTINCT",
            "column":   "member_id",
            "note":     "Count employees who left, not leave events",
        },
        "grain": [
            "division_name", "branch_name",
            "position_group", "member_level", "contract_type",
        ],
        "joins": [],
        "warnings": [
            "Use end_date to identify leavers - not member_status",
            "end_date must be within target period",
            "Do not mix with headcount filter logic",
        ],
        "synonyms": [
            "nghỉ việc", "attrition", "off board", "số người nghỉ",
            "nhân viên nghỉ", "tỉ lệ nghỉ việc", "turnover",
        ],
    },

    # -------------------------------------------------------------------------
    # NEW HIRE
    # -------------------------------------------------------------------------
    "new_hire": {
        "metric_type": "period",
        "table":        "dim.dim_odoo_members",
        "select_expr":  "COUNT(DISTINCT member_id)",
        "where_clause": (
            "official_date >= {start_date}  "
            "AND official_date <= {end_date}"
        ),
        "required_params": ["start_date", "end_date"],
        "default_sql": {
            "start_date": "DATE_TRUNC('month', CURRENT_DATE)",
            "end_date":   "CURRENT_DATE",
        },
        "partial_sql": {
            "end_date":   "CURRENT_DATE",
            "start_date": "DATE_TRUNC('month', %(end_date)s::date)",
        },
        "constraints": [],
        "description": (
            "Number of employees who joined during a given period. "
            "Filter by official_date within the period. "
            "official_date uses fallback logic across multiple date columns."
        ),
        "aggregation": {
            "function": "COUNT DISTINCT",
            "column":   "member_id",
            "note":     "Use official_date - not joining_date",
        },
        "grain": [
            "division_name", "branch_name",
            "position_group", "member_level", "contract_type",
        ],
        "joins": [],
        "warnings": [
            "Use official_date - not joining_date alone",
            "official_date is a derived field with fallback logic",
        ],
        "synonyms": [
            "tuyển mới", "new hire", "onboard", "nhân viên mới",
            "số người vào", "tuyển dụng",
        ],
    },

    # -------------------------------------------------------------------------
    # ABSENT DAYS
    # -------------------------------------------------------------------------
    "absent_days": {
        "metric_type": "period",
        "table":        "fct.fct_attendance_daily",
        "select_expr":  "SUM(daily_absent_unit)",
        "where_clause": (
            "date_actual >= {start_date} "
            "AND date_actual <= {end_date}"
        ),
        "required_params": ["start_date", "end_date"],
        "default_sql": {
            "start_date": "DATE_TRUNC('month', CURRENT_DATE)",
            "end_date":   "CURRENT_DATE",
        },
        "partial_sql": {
            "end_date":   "CURRENT_DATE",
            "start_date": "DATE_TRUNC('month', %(end_date)s::date)",
        },
        "constraints": [],
        "description": (
            "Total absent days (work-day units) within a period. "
            "Always SUM(daily_absent_unit). "
            "Never COUNT(*) or SUM(total_absent_days_original) - both cause double-counting."
        ),
        "aggregation": {
            "function": "SUM",
            "column":   "daily_absent_unit",
            "note":     "Each row = 1 employee on 1 day. attendance_id repeats for multi-day requests.",
        },
        "grain": [
            "member_id", "absent_reason", "attendance_type_id",
            "division_name", "branch_name",
        ],
        "joins": [
            {
                "table":              "dim.dim_odoo_members",
                "on":                 "fct.fct_attendance_daily.member_id = dim.dim_odoo_members.member_id",
                "type":               "LEFT",
                "required_for_grain": ["division_name", "branch_name", "position_group"],
                "purpose":            "employee attributes for grouping",
            }
        ],
        "warnings": [
            "attendance_id is NOT unique - repeats for multi-day requests",
            "Always SUM(daily_absent_unit) - never COUNT(*)",
            "Never SUM(total_absent_days_original) - double-counts multi-day requests",
            "Always filter by date_actual range",
            "Join dim.dim_odoo_members on member_id for employee attributes",
        ],
        "synonyms": [
            "ngày nghỉ", "nghỉ phép", "absent", "số ngày nghỉ",
            "số công nghỉ", "leave days", "ngày phép",
        ],
    },

    # -------------------------------------------------------------------------
    # TENURE
    # -------------------------------------------------------------------------
    "tenure": {
        "metric_type": "point_in_time",
        "table":        "dim.dim_odoo_members",
        "select_expr": (
            "EXTRACT(YEAR FROM AGE({target_date}::date, official_date::date)) * 12 "
            "+ EXTRACT(MONTH FROM AGE({target_date}::date, official_date::date))"
        ),
        "select_note": "Unit: months. Divide by 12 for years.",
        "where_clause": (
            "official_date <= {target_date} "
            "AND (end_date IS NULL OR end_date > {target_date})"
        ),
        "required_params": ["target_date"],
        "default_sql": {
            "target_date": "CURRENT_DATE",
        },
        "constraints": ["active_only"],
        "description": (
            "Employee tenure in months from official_date to target_date. "
            "Active employees only at target_date. "
            "Use official_date - not joining_date. Divide by 12 for years."
        ),
        "aggregation": {
            "function": "AVG / MIN / MAX",
            "column":   "tenure_months",
            "note":     "Compute per employee first, then aggregate across group",
        },
        "grain": [
            "member_id", "division_name",
            "branch_name", "position_group",
        ],
        "joins": [],
        "warnings": [
            "Always use official_date - not joining_date",
            "where_clause already enforces active_only - do not add extra status filter",
            "Result is in months - divide by 12 for years",
        ],
        "synonyms": [
            "thâm niên", "tenure", "số năm làm việc",
            "kinh nghiệm", "năm công tác", "thời gian làm việc",
        ],
    },
}


# =============================================================================
# RENDER
# =============================================================================

def render_metric(
    metric_name: str,
    params: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Render metric thanh enforced unit.

    Tach ro 2 loai params:
      - SQL functions (CURRENT_DATE, DATE_TRUNC...) -> inject vao SQL string
      - Literal values ("2024-06-30") -> psycopg2 bindings %(key)s

    Returns:
      {
        metric_name:   str
        metric_type:   str
        table:         str
        select_expr:   str   - aggregation, da inject SQL functions
        where_clause:  str   - REQUIRED, da inject SQL functions, %(key)s cho literals
        bindings:      dict  - chi chua literal values -> psycopg2
        missing_params: list - params can user cung cap, khong co default
        joins:         list
        aggregation:   dict
        warnings:      list
        grain:         list
        constraints:   list
      }

    Caller BAT BUOC dung where_clause - khong chi dung select_expr.
    """
    metric = get_metric(metric_name)
    if not metric:
        return None

    provided     = params or {}
    sql_resolved: Dict[str, str] = {}
    val_resolved: Dict[str, str] = {}
    missing:      List[str]      = []

    for param in metric.get("required_params", []):
        if param in provided:
            val_resolved[param] = provided[param]
        elif param in metric.get("default_sql", {}):
            sql_resolved[param] = metric["default_sql"][param]
        else:
            partial_sql = metric.get("partial_sql", {})
            if param in partial_sql:
                sql_resolved[param] = partial_sql[param]
            else:
                missing.append(param)

    select_expr  = metric["select_expr"]
    where_clause = metric["where_clause"]

    for key, sql_val in sql_resolved.items():
        placeholder = "{" + key + "}"
        select_expr  = select_expr.replace(placeholder, sql_val)
        where_clause = where_clause.replace(placeholder, sql_val)

    for key in val_resolved:
        placeholder = "{" + key + "}"
        select_expr  = select_expr.replace(placeholder, f"%({key})s")
        where_clause = where_clause.replace(placeholder, f"%({key})s")

    return {
        "metric_name":    metric_name,
        "metric_type":    metric.get("metric_type"),
        "table":          metric["table"],
        "select_expr":    select_expr,
        "where_clause":   where_clause,
        "bindings":       val_resolved,
        "missing_params": missing,
        "joins":          metric.get("joins", []),
        "aggregation":    metric.get("aggregation", {}),
        "warnings":       metric.get("warnings", []),
        "grain":          metric.get("grain", []),
        "constraints":    metric.get("constraints", []),
    }


def resolve_joins_for_grain(
    metric_name: str,
    group_by_columns: List[str]
) -> List[Dict[str, Any]]:
    """
    Tra ve chi nhung joins can thiet dua tren GROUP BY columns thuc te.
    """
    metric = get_metric(metric_name)
    if not metric:
        return []
    return [
        join for join in metric.get("joins", [])
        if any(col in group_by_columns for col in join.get("required_for_grain", []))
    ]


# =============================================================================
# LOOKUP
# =============================================================================

def get_metric(name: str) -> Optional[Dict[str, Any]]:
    return METRICS.get(name)


def find_metric_by_synonym(word: str) -> Optional[str]:
    """
    Exact match only - tranh false positive voi tieng Viet.
    De LLM tu map intent -> metric name thay vi code tu doan.
    Tra ve None neu khong co exact match.
    """
    word_lower = word.lower().strip()
    for metric_name, metric in METRICS.items():
        for syn in metric.get("synonyms", []):
            if word_lower == syn.lower().strip():
                return metric_name
    return None


def list_metrics() -> Dict[str, str]:
    """Tra ve {metric_name: description} cho get_metric MCP tool."""
    return {
        name: metric["description"]
        for name, metric in METRICS.items()
    }


def validate_grain(metric_name: str, group_by_columns: List[str]) -> List[str]:
    metric = get_metric(metric_name)
    if not metric:
        return []

    allowed = set(metric.get("grain", []))
    return [col for col in group_by_columns if col not in allowed]


# =============================================================================
# DEBUG
# =============================================================================

if __name__ == "__main__":
    from pprint import pprint

    print("=== headcount (no params -> inject CURRENT_DATE) ===")
    pprint(render_metric("headcount"))

    print("\n=== headcount (literal target_date) ===")
    pprint(render_metric("headcount", {"target_date": "2024-06-30"}))

    print("\n=== attrition (no params -> default thang hien tai) ===")
    pprint(render_metric("attrition"))

    print("\n=== attrition (chi co start_date) ===")
    pprint(render_metric("attrition", {"start_date": "2024-01-01"}))

    print("\n=== absent_days + resolve joins ===")
    result = render_metric("absent_days", {
        "start_date": "2024-01-01",
        "end_date":   "2024-06-30",
    })
    pprint(result)
    joins = resolve_joins_for_grain("absent_days", ["division_name"])
    print("Required joins:", joins)

    print("\n=== tenure constraints ===")
    t = render_metric("tenure")
    print("constraints:", t["constraints"])
    print("select_expr:", t["select_expr"])
    print("where_clause:", t["where_clause"])

    print("\n=== exact synonym match ===")
    print(find_metric_by_synonym("số ngày nghỉ"))
    print(find_metric_by_synonym("số ngày"))

    print("\n=== validate grain ===")
    print(validate_grain("headcount", ["division_name", "etl_datetime"]))
