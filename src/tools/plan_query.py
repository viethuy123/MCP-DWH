import sqlglot
from sqlglot import exp
from typing import Dict, List, Optional, Any

from metadata import metadata_store


# =========================
# MAIN ENTRY
# =========================

def plan_query(
    sql: Optional[str] = None,
    metric_result: Optional[Dict] = None,
    group_by: Optional[List[str]] = None,
) -> Dict:
    """
    MCP Tool: plan_query

    Mode 1: SQL validation (existing)
        plan_query(sql="SELECT ...")

    Mode 2: Metric → SQL → validate (NEW)
        plan_query(metric_result=..., group_by=[...])
    """

    # =========================================================================
    # MODE 2: BUILD FROM METRIC (PRIORITY)
    # =========================================================================
    if metric_result:

        if metric_result.get("status") == "missing_params":
            return {
                "error": "Missing required parameters",
                "missing_params": metric_result.get("missing_params", []),
            }

        # --- normalize group_by ---
        group_by = [c.strip() for c in (group_by or []) if c.strip()]

        select_expr  = metric_result["select_expr"]
        table        = metric_result["table"]
        where_clause = metric_result["where_clause"]
        # =========================
        # AUTO JOIN (NEW)
        # =========================
        auto_joins = []

        for col in group_by:
            if not metadata_store.table_has_column(table, col):

                candidates = metadata_store.find_tables_by_column(col)

                if not candidates:
                    return {"error": f"Column '{col}' not found"}

                target = candidates[0]

                join = metadata_store.find_join_path(table, target.full_name)

                if not join:
                    return {"error": f"No join path for '{col}'"}

                auto_joins.append({
                    "type": "LEFT",
                    "table": join["right"],
                    "on": join["on"]
                })

        # merge với joins cũ (nếu có)
        joins = metric_result.get("required_joins", []) + auto_joins

        # --- SELECT ---
        if group_by:
            select_cols = []

            for col in group_by:
                if metadata_store.table_has_column(table, col):
                    select_cols.append(f"{table}.{col}")
                else:
                    target = metadata_store.find_tables_by_column(col)[0]
                    select_cols.append(f"{target.full_name}.{col}")

            select_clause = ", ".join(select_cols) + f", {select_expr} AS value"
        else:
            select_clause = f"{select_expr} AS value"

        # --- FROM ---
        from_clause = f"FROM {table}"

        # --- JOIN ---
        join_clauses = [
            f"{j.get('type', 'LEFT')} JOIN {j['table']} ON {j['on']}"
            for j in joins
        ]

        # --- WHERE ---
        where = f"WHERE {where_clause}"

        # --- GROUP ---
        if group_by:
            group_cols = []

            for col in group_by:
                if metadata_store.table_has_column(table, col):
                    group_cols.append(f"{table}.{col}")
                else:
                    target = metadata_store.find_tables_by_column(col)[0]
                    group_cols.append(f"{target.full_name}.{col}")

            group_clause = f"GROUP BY {', '.join(group_cols)}"
        else:
            group_clause = ""

        # --- ORDER ---
        order_clause = "ORDER BY value DESC" if group_by else ""

        sql_built = "\n".join([
            f"SELECT {select_clause}",
            from_clause,
            *join_clauses,
            where,
            group_clause,
            order_clause,
        ])

        # 👉 validate luôn
        validation = plan_query(sql=sql_built)

        return {
            "mode": "metric",
            "sql": sql_built,
            "bindings": metric_result.get("bindings", {}),
            "validation": validation,
        }

    # =========================================================================
    # MODE 1: VALIDATE SQL (OLD LOGIC)
    # =========================================================================
    if not sql:
        return {"error": "Either sql or metric_result must be provided."}

    try:
        parsed = sqlglot.parse_one(sql)
    except Exception as e:
        return {
            "error": f"SQL parse error: {str(e)}"
        }

    tables = extract_tables(parsed)

    warnings = []
    suggested_parsed = parsed.copy()

    for table_name in tables:
        table_meta = metadata_store.get_table(table_name)
        if not table_meta:
            continue

        warnings += check_fact_rules(parsed, table_meta)
        warnings += check_grain(parsed, table_meta)
        warnings += check_temporal_missing(parsed, table_meta)

        suggested_parsed = apply_safe_temporal_fix(
            suggested_parsed,
            table_meta
        )

    return {
        "mode": "sql",
        "planned_sql": sql,
        "suggested_sql": suggested_parsed.sql(),
        "tables": tables,
        "warnings": warnings
    }
# =========================
# TABLE EXTRACTION (AST)
# =========================

def extract_tables(parsed) -> List[str]:
    tables = []
    for t in parsed.find_all(exp.Table):
        tables.append(t.sql())   # FIX: giữ schema
    return list(set(tables))


# =========================
# FACT RULES
# =========================

def check_fact_rules(parsed, table_meta):
    warnings = []

    if not table_meta.is_fact():
        return warnings

    if not parsed.args.get("where"):
        warnings.append({
            "type": "FACT_NO_FILTER",
            "message": f"{table_meta.name} is a fact table without WHERE",
            "severity": "HIGH"
        })

    return warnings


# =========================
# GRAIN CHECK (FIXED)
# =========================

def check_grain(parsed, table_meta):
    warnings = []

    if not table_meta.grain:
        return warnings

    group = parsed.args.get("group")

    if not group:
        return warnings

    group_cols = [
        col.name for col in group.find_all(exp.Column)
    ]

    missing = [
        col for col in table_meta.grain
        if col not in group_cols
    ]

    has_agg = any(isinstance(e, exp.Count) for e in parsed.find_all(exp.Count))

    if has_agg:
        return warnings


# =========================
# TEMPORAL DETECTION (NO KEYWORD)
# =========================

def check_temporal_missing(parsed, table_meta):
    warnings = []

    if "active_employee" not in table_meta.temporal_logic:
        return warnings

    # 🔥 collect ALL where clauses in AST
    where_clauses = list(parsed.find_all(exp.Where))

    if not where_clauses:
        warnings.append({
            "type": "MISSING_TEMPORAL_FILTER",
            "message": "No WHERE clause found in query",
            "severity": "HIGH"
        })
        return warnings

    # 🔥 check ANY where contains temporal condition
    found = False

    for where in where_clauses:
        where_sql = where.sql().lower()
        if "official_date" in where_sql or "end_date" in where_sql:
            found = True
            break

    if not found:
        warnings.append({
            "type": "MISSING_TEMPORAL_FILTER",
            "message": "No temporal condition found in any WHERE clause",
            "severity": "HIGH"
        })

    return warnings


# =========================
# SAFE FIX (OPTIONAL)
# =========================
def contains_table(node, table_name: str) -> bool:
    for t in node.find_all(exp.Table):
        if t.name == table_name or f"{t.args.get('db')}.{t.name}" == table_name:
            return True
    return False

def apply_safe_temporal_fix(parsed, table_meta):

    if "active_employee" not in table_meta.temporal_logic:
        return parsed

    condition = table_meta.get_temporal_condition("active_employee")
    if not condition:
        return parsed

    condition_expr = sqlglot.parse_one(condition)

    new_parsed = parsed.copy()

    # =========================
    # 1. HANDLE CTE FIRST
    # =========================

    for cte in new_parsed.find_all(exp.CTE):
        subquery = cte.this

        if contains_table(subquery, table_meta.name):
            where = subquery.args.get("where")

            if where and ("official_date" in where.sql()):
                continue

            if where:
                subquery.set(
                    "where",
                    exp.and_(where, condition_expr)
                )
            else:
                subquery.set("where", condition_expr)

            return new_parsed

    # =========================
    # 2. FALLBACK: ROOT QUERY
    # =========================

    if contains_table(new_parsed, table_meta.name):
        where = new_parsed.args.get("where")

        if where and ("official_date" in where.sql()):
            return new_parsed

        if where:
            new_parsed.set(
                "where",
                exp.and_(where, condition_expr)
            )
        else:
            new_parsed.set("where", condition_expr)

    return new_parsed

# =========================
# DEBUG
# =========================

if __name__ == "__main__":
    from metadata import init_metadata

    init_metadata("../dbt/manifest.json")

    sql = """
    SELECT COUNT(DISTINCT member_id)
    FROM dim_employee
    """

    result = plan_query(sql)

    print("=== RESULT ===")
    print(result)
