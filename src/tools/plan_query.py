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
    mode: str = "smart",
) -> Dict:
    """
    MCP Tool: plan_query

    Mode 1: SQL validation (existing)
        plan_query(sql="SELECT ...")

    Mode 2: Metric → SQL → validate (NEW)
        plan_query(metric_result=..., group_by=[...])

    mode:
        Currently accepted for compatibility with MCP tool dispatch.
        The implementation does not branch on mode yet.
    """

    # =========================================================================
    # MODE 2: BUILD FROM METRIC (PRIORITY)
    # =========================================================================
    if metric_result:

        if metric_result.get("status") == "missing_params":
            return {
                "error": "Missing required parameters",
                "missing_params": metric_result.get("missing_params", []),
                "block": True,
                "severity": "HIGH",
            }

        # --- normalize group_by ---
        group_by = [c.strip() for c in (group_by or []) if c.strip()]
        group_by_resolved = list(metric_result.get("group_by_resolved") or [])

        select_expr  = metric_result["select_expr"]
        table        = metric_result["table"]
        where_clause = metric_result["where_clause"]
        # =========================
        # AUTO JOIN (NEW)
        # =========================
        auto_joins = []

        for idx, col in enumerate(group_by):
            resolved_spec = group_by_resolved[idx] if idx < len(group_by_resolved) else {}
            resolved_column = resolved_spec.get("column") or col

            if not metadata_store.table_has_column(table, resolved_column):

                candidates = sorted(
                    metadata_store.find_tables_by_column(col),
                    key=lambda t: t.full_name,
                )

                if not candidates:
                    return {
                        "error": f"Column '{col}' not found",
                        "block": True,
                        "severity": "HIGH",
                    }

                target = candidates[0]

                join = metadata_store.find_join_path(table, target.full_name)

                if not join:
                    return {
                        "error": f"No join path for '{col}'",
                        "block": True,
                        "severity": "HIGH",
                    }

                auto_joins.append({
                    "type": "LEFT",
                    "table": join["right"],
                    "on": join["on"]
                })

                resolved_column = metadata_store.resolve_column_name(target.full_name, col) or col
                resolved_spec = {
                    "input": col,
                    "table": target.full_name,
                    "column": resolved_column,
                    "qualified_name": f"{target.full_name}.{resolved_column}",
                }
                if idx < len(group_by_resolved):
                    group_by_resolved[idx] = resolved_spec
                else:
                    group_by_resolved.append(resolved_spec)
            else:
                resolved_column = metadata_store.resolve_column_name(table, resolved_column) or resolved_column
                resolved_spec = {
                    "input": col,
                    "table": table,
                    "column": resolved_column,
                    "qualified_name": f"{table}.{resolved_column}",
                }
                if idx < len(group_by_resolved):
                    group_by_resolved[idx] = resolved_spec
                else:
                    group_by_resolved.append(resolved_spec)

        # merge với joins cũ (nếu có)
        joins = metric_result.get("required_joins", []) + auto_joins

        # --- SELECT ---
        if group_by:
            select_cols = []

            for idx, col in enumerate(group_by):
                resolved_spec = group_by_resolved[idx] if idx < len(group_by_resolved) else {}
                resolved_table = resolved_spec.get("table") or table
                resolved_column = resolved_spec.get("column") or col
                select_cols.append(f"{resolved_table}.{resolved_column}")

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

            for idx, col in enumerate(group_by):
                resolved_spec = group_by_resolved[idx] if idx < len(group_by_resolved) else {}
                resolved_table = resolved_spec.get("table") or table
                resolved_column = resolved_spec.get("column") or col
                group_cols.append(f"{resolved_table}.{resolved_column}")

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

        sql_for_validation = render_sql_for_validation(
            sql_built,
            metric_result.get("bindings", {}),
        )
        validation = plan_query(sql=sql_for_validation)

        return {
            "mode": "metric",
            "sql": sql_built,
            "bindings": metric_result.get("bindings", {}),
            "validation": validation,
            "warnings": validation.get("warnings", []),
            "severity": validation.get("severity", "LOW"),
            "block": validation.get("block", False),
            "group_by_resolved": group_by_resolved,
        }

    # =========================================================================
    # MODE 1: VALIDATE SQL (OLD LOGIC)
    # =========================================================================
    if not sql:
        return {
            "error": "Either sql or metric_result must be provided.",
            "block": True,
            "severity": "HIGH",
        }

    try:
        parsed = sqlglot.parse_one(sql)
    except Exception as e:
        return {
            "error": f"SQL parse error: {str(e)}",
            "block": True,
            "severity": "HIGH",
        }

    tables = extract_tables(parsed)

    warnings = []
    suggested_parsed = parsed.copy()
    warnings += check_join_safety(parsed)

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

    severity = summarize_severity(warnings)

    return {
        "mode": "sql",
        "planned_sql": sql,
        "suggested_sql": suggested_parsed.sql(),
        "tables": tables,
        "warnings": warnings,
        "severity": severity,
        "block": severity == "HIGH",
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

    where = parsed.args.get("where")

    if not where:
        warnings.append({
            "type": "FACT_NO_FILTER",
            "message": f"{table_meta.name} is a fact table without WHERE",
            "severity": "HIGH"
        })
        return warnings

    reference_date_column = getattr(table_meta, "reference_date_column", None)
    if reference_date_column and not query_mentions_column(parsed, reference_date_column):
        warnings.append({
            "type": "FACT_MISSING_REFERENCE_DATE_FILTER",
            "message": (
                f"{table_meta.full_name} should usually be filtered by "
                f"reference date column '{reference_date_column}'."
            ),
            "severity": "MEDIUM"
        })

    if getattr(table_meta, "db_table_type", "") == "VIEW":
        warnings.append({
            "type": "FACT_VIEW_SOURCE",
            "message": f"{table_meta.full_name} is exposed as a database VIEW.",
            "severity": "LOW"
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

    if missing:
        warnings.append({
            "type": "GRAIN_MISMATCH",
            "message": (
                f"Query groups {table_meta.full_name} without full declared grain. "
                f"Missing grain columns: {missing}"
            ),
            "severity": "MEDIUM"
        })

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

    expected_columns = extract_temporal_columns(table_meta)

    for where in where_clauses:
        where_sql = where.sql().lower()
        if any(column in where_sql for column in expected_columns):
            found = True
            break

    if not found:
        warnings.append({
            "type": "MISSING_TEMPORAL_FILTER",
            "message": "No temporal condition found in any WHERE clause",
            "severity": "HIGH"
        })

    return warnings


def extract_temporal_columns(table_meta) -> List[str]:
    columns = set()

    if getattr(table_meta, "reference_date_column", None):
        columns.add(str(table_meta.reference_date_column).lower())

    for logic in table_meta.temporal_logic.values():
        condition_sql = getattr(logic, "condition_sql", "") or ""
        condition_lower = condition_sql.lower()
        for candidate in table_meta.column_names():
            candidate_lower = candidate.lower()
            if candidate_lower in condition_lower:
                columns.add(candidate_lower)

    if not columns:
        columns.update({"official_date", "end_date"})

    return sorted(columns)


def query_mentions_column(parsed, column_name: str) -> bool:
    target = column_name.lower()

    for column in parsed.find_all(exp.Column):
        if column.name.lower() == target:
            return True

    where = parsed.args.get("where")
    if where and target in where.sql().lower():
        return True

    return False


def check_join_safety(parsed) -> List[Dict[str, Any]]:
    warnings = []
    joins = list(parsed.find_all(exp.Join))

    for join in joins:
        joined_table_expr = join.this
        if not isinstance(joined_table_expr, exp.Table):
            continue

        joined_name = joined_table_expr.sql()
        joined_meta = metadata_store.get_table(joined_name)
        if not joined_meta:
            continue

        left_name = resolve_join_left_table_name(join)
        if not left_name:
            warnings.append({
                "type": "JOIN_CONTEXT_UNKNOWN",
                "message": f"Could not resolve left side of join for table '{joined_name}'.",
                "severity": "MEDIUM",
            })
            continue

        left_meta = metadata_store.get_table(left_name)
        if not left_meta:
            continue

        expected = (
            metadata_store.find_join_path(left_meta.full_name, joined_meta.full_name)
            or metadata_store.find_join_path(joined_meta.full_name, left_meta.full_name)
        )

        on_expr = join.args.get("on")
        if not expected:
            warnings.append({
                "type": "JOIN_NO_KNOWN_PATH",
                "message": (
                    f"No known foreign-key path between '{left_meta.full_name}' "
                    f"and '{joined_meta.full_name}'."
                ),
                "severity": "MEDIUM",
            })
            continue

        if not on_expr:
            warnings.append({
                "type": "JOIN_MISSING_ON",
                "message": (
                    f"Join between '{left_meta.full_name}' and '{joined_meta.full_name}' "
                    f"is missing an ON clause."
                ),
                "severity": "HIGH",
            })
            continue

        on_sql = normalize_sql(on_expr.sql())
        expected_sql = normalize_sql(expected["on"])
        if expected_sql not in on_sql and on_sql not in expected_sql:
            warnings.append({
                "type": "JOIN_CONDITION_MISMATCH",
                "message": (
                    f"Join condition '{on_expr.sql()}' does not match expected path "
                    f"'{expected['on']}'."
                ),
                "severity": "MEDIUM",
            })

    return dedupe_warnings(warnings)


def resolve_join_left_table_name(join_expr) -> Optional[str]:
    parent = getattr(join_expr, "parent", None)
    if parent is None:
        return None

    if isinstance(parent, exp.From):
        base = parent.this
        if isinstance(base, exp.Table):
            return base.sql()

    if isinstance(parent, exp.Join):
        base = parent.this
        if isinstance(base, exp.Table):
            return base.sql()

    if hasattr(parent, "args"):
        from_expr = parent.args.get("from")
        if isinstance(from_expr, exp.From) and isinstance(from_expr.this, exp.Table):
            return from_expr.this.sql()

    return None


def normalize_sql(sql: str) -> str:
    return " ".join(sql.lower().split())


def dedupe_warnings(warnings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []

    for warning in warnings:
        key = (
            warning.get("type"),
            warning.get("message"),
            warning.get("severity"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)

    return result


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


def summarize_severity(warnings: List[Dict[str, Any]]) -> str:
    severities = {
        str(w.get("severity", "")).upper()
        for w in warnings
    }

    if "HIGH" in severities:
        return "HIGH"
    if "MEDIUM" in severities:
        return "MEDIUM"
    return "LOW"


def render_sql_for_validation(sql: str, bindings: Optional[Dict[str, Any]] = None) -> str:
    if not bindings:
        return sql

    rendered = sql
    for key, value in bindings.items():
        placeholder = f"%({key})s"
        if isinstance(value, str):
            rendered = rendered.replace(placeholder, f"'{value}'")
        else:
            rendered = rendered.replace(placeholder, str(value))
    return rendered

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
