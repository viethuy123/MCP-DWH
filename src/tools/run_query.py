import psycopg2
import psycopg2.extras
import sqlglot
from sqlglot import exp
from typing import Dict, Any

from tools.plan_query import plan_query
from config import DB_CONFIG, DEFAULT_LIMIT, QUERY_TIMEOUT_MS


# =========================
# CONFIG
# =========================

MAX_ROWS = DEFAULT_LIMIT


# =========================
# MAIN
# =========================

def run_query(sql: str, bindings: dict = None, mode: str = "smart") -> Dict[str, Any]:
    sql_for_validation = _render_sql_for_validation(sql, bindings)

    validate_select_only(sql_for_validation)
    plan = plan_query(sql=sql_for_validation, mode=mode)

    if plan.get("block", False):
        return {
            "success": False,
            "error": "Query blocked by guardrails",
            "warnings": plan.get("warnings", []),
            "planned_sql": plan.get("planned_sql"),
            "suggested_sql": plan.get("suggested_sql"),
            "severity": plan.get("severity", "HIGH"),
        }

    final_sql = (
        plan.get("suggested_sql")
        if mode == "smart" and plan.get("suggested_sql")
        else plan.get("planned_sql", sql)
    )

    final_sql = enforce_limit(final_sql)

    conn = None

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            apply_timeout(cur)
            cur.execute(final_sql, bindings or {})
            rows = cur.fetchall()

        return {
            "success": True,
            "data": normalize_result(rows),
            "row_count": len(rows),
            "executed_sql": final_sql,
            "warnings": plan.get("warnings", []),
            "severity": plan.get("severity", "LOW"),
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "executed_sql": final_sql,
            "warnings": plan.get("warnings", []),
        }

    finally:
        if conn:
            conn.close()


# =========================
# VALIDATION
# =========================

FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
)

FORBIDDEN_KEYWORDS = (
    "TRUNCATE",
    "EXECUTE",
    "COPY",
)

def validate_select_only(sql: str):
    try:
        parsed = sqlglot.parse_one(sql)
    except Exception:
        raise ValueError("Invalid SQL")

    if not isinstance(parsed, (exp.Select, exp.With)):
        raise ValueError("Only SELECT queries are allowed")

    for node in parsed.walk():
        if isinstance(node, FORBIDDEN):
            raise ValueError(
                f"Forbidden SQL operation: {node.__class__.__name__}"
            )

    sql_upper = sql.upper()
    for keyword in FORBIDDEN_KEYWORDS:
        if keyword in sql_upper:
            raise ValueError(f"Forbidden SQL operation: {keyword}")


def _render_sql_for_validation(sql: str, bindings: dict) -> str:
    if not bindings:
        return sql

    rendered = sql
    for k, v in bindings.items():
        if isinstance(v, str):
            rendered = rendered.replace(f"%({k})s", f"'{v}'")
        else:
            rendered = rendered.replace(f"%({k})s", str(v))
    return rendered


# =========================
# LIMIT CLAMP
# =========================

def enforce_limit(sql: str) -> str:
    parsed = sqlglot.parse_one(sql)
    limit = parsed.args.get("limit")

    if limit:
        try:
            value_expr = limit.expression or limit.this
            value = int(value_expr.name)
            if value > MAX_ROWS:
                parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
        except Exception:
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
    else:
        parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))

    return parsed.sql()


# =========================
# RESULT NORMALIZER
# =========================

def normalize_result(rows):
    normalized = []

    for row in rows:
        clean = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            elif isinstance(v, str) and len(v) > 200:
                clean[k] = v[:200] + "..."
            else:
                clean[k] = v
        normalized.append(clean)

    return normalized


def apply_timeout(cursor: Any) -> None:
    cursor.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS};")


# =========================
# DEBUG
# =========================

if __name__ == "__main__":
    sql = """
    SELECT * FROM dim.dim_employee LIMIT 100000
    """

    from pprint import pprint
    pprint(run_query(sql))
