
import psycopg2
import psycopg2.extras
import sqlglot
from sqlglot import exp
from typing import Dict, Any

from plan_query import plan_query
from config import DB_CONFIG


# =========================
# CONFIG
# =========================

MAX_ROWS = 1000
QUERY_TIMEOUT_MS = 10000  # 10s


# =========================
# MAIN
# =========================

def run_query(sql: str, bindings: dict = None, mode: str = "smart") -> Dict[str, Any]:

    sql_for_validation = _render_sql_for_validation(sql, bindings)

    validate_select_only(sql_for_validation)
    plan = plan_query(sql_for_validation)

    if plan.get("block"):
        return {
            "success": False,
            "error": "Query blocked by guardrails",
            "warnings": plan.get("warnings"),
            "planned_sql": plan.get("planned_sql")
        }

    final_sql = (
        plan["suggested_sql"]
        if mode == "smart" and plan.get("suggested_sql")
        else plan["planned_sql"]
    )

    final_sql = enforce_limit(final_sql)

    conn = None

    try:
        # 🔥 FIX #4: timeout ngay từ session
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # set timeout cho session
            cur.execute(f"SET statement_timeout = {QUERY_TIMEOUT_MS}")

            cur.execute(final_sql, bindings or {})
            rows = cur.fetchall()

        return {
            "success": True,
            "data": normalize_result(rows),
            "row_count": len(rows),
            "executed_sql": final_sql,
            "warnings": plan.get("warnings")
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "executed_sql": final_sql
        }

    finally:
        # 🔥 FIX #2: luôn đóng connection
        if conn:
            conn.close()


# =========================
# FIX #1: SELECT ONLY (AST)
# =========================

def validate_select_only(sql: str):
    try:
        parsed = sqlglot.parse_one(sql)
    except Exception:
        raise ValueError("Invalid SQL")

    if not isinstance(parsed, exp.Select):
        raise ValueError("Only SELECT queries are allowed")

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
# FIX #3: LIMIT CLAMP
# =========================

def enforce_limit(sql: str) -> str:
    parsed = sqlglot.parse_one(sql)

    limit = parsed.args.get("limit")

    if limit:
        try:
            value = int(limit.expression.name)
            if value > MAX_ROWS:
                parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
        except Exception:
            # nếu parse fail → override luôn
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
    else:
        parsed.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))

    return parsed.sql()


# =========================
# RESULT NORMALIZER
# =========================

def normalize_result(rows):
    """
    Make output LLM-friendly
    """
    normalized = []

    for row in rows:
        clean = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):  # datetime
                clean[k] = v.isoformat()
            elif isinstance(v, str) and len(v) > 200:
                clean[k] = v[:200] + "..."
            else:
                clean[k] = v
        normalized.append(clean)

    return normalized

def apply_timeout(cursor: Any) -> None:
    """
    Áp dụng statement_timeout cho session hiện tại của Postgres.
    """
    # cursor thường là một đối tượng từ psycopg2 hoặc tương tự
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
    
    


