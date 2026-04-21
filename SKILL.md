---
name: postgres-hr-mcp
description: >
  Build, extend, or modify a Model Context Protocol (MCP) server that connects to a
  PostgreSQL-based Data Warehouse built with dbt using the Kimball methodology. Use this
  skill whenever the user wants to: create the MCP server from scratch, add new tools or
  resources, change guardrail rules, update schema exposure, integrate dbt manifest metadata,
  or connect an LLM to the DWH for HR self-serve analytics. Also trigger when the user
  mentions "MCP + postgres", "HR analytics MCP", "dbt MCP", or wants to let non-technical
  users query a DWH without writing SQL.
---

# Postgres HR MCP Server

An MCP (Model Context Protocol) server that exposes a PostgreSQL Data Warehouse — built
with dbt on a Kimball model — so that HR users (or any LLM agent) can explore data beyond
fixed reports without writing SQL.\

The goal is not just to run SQL, but to ensure:

Correct business logic
Safe query execution
Consistent metrics

---

## Architecture Overview

```
HR User / LLM Agent
        │
        ▼
  MCP Client (e.g. Claude Desktop, custom app)
        │  MCP Protocol (stdio or HTTP/SSE)
        ▼
  MCP Server  ◄── this project
  ├── resources/   metadata about tables
  └── tools/       query execution with guardrails
        │
        ▼
  PostgreSQL DWH (dbt Kimball)
  ├── reports      ← primary entry point for HR
  ├── dim          ← dimension tables
  └── fct          ← fact tables
```

Core Design Principles
Fail closed — unsafe queries are rejected
Plan before execution — never run raw SQL directly
Metrics are centralized — no duplication of logic
LLM is guided, not trusted blindly

### dbt Layer Strategy

| Schema        | Expose via MCP | Reason                                      |
|---------------|---------------|---------------------------------------------|
| `reports`     | ✅ Yes         | HR starting point, stable, pre-aggregated   |
| `dim`         | ✅ Yes         | Join extra attributes, enrich context        |
| `fct`         | ✅ Yes         | Re-aggregate in new ways                    |
| `intermediates`| ❌ No         | Internal transform logic, unstable           |
| `stg`         | ❌ No         | Raw/uncleaned data, may contain PII          |
| `snapshots`   | ❌ No         | dbt internal, not for consumption            |
| `*_fdw`       | ❌ No         | Source systems already absorbed into stg/dim |
| `public`      | ❌ No         | Postgres default, uncontrolled               |

---

## Project File Structure

```
postgres-hr-mcp/
├── SKILL.md                  ← this file
├── src/
│   ├── server.py             ← MCP server entry point
│   ├── config.py             ← DB connection, allowed schemas, row limit
│   ├── guardrails.py         ← SQL validation (SELECT only, schema whitelist, fct rules)
│   ├── metadata.py           ← table/column info from Postgres + dbt manifest
│   ├── metric_registry.py    ← lightweight metric definitions (single source of truth)
│   └── tools/
│       ├── list_tables.py
│       ├── describe_table.py
│       ├── run_query.py
│       ├── plan_query.py     ← pre-execution validation, same priority as guardrails
│       └── get_sample.py
├── dbt/
│   └── manifest.json         ← copy from dbt project's target/ folder
├── requirements.txt
└── README.md
```
NOTE:

plan_query.py is a required safety layer, not optional.
run_query.py MUST call plan_query before executing any SQL.
metric_registry.py acts as a consistency layer to prevent metric drift.
---

## MCP Tools (6 tools)

### 1. `list_tables`
Lists all tables/views in allowed schemas.

**Input:**
```json
{ "schema": "reports" }   // optional — omit to list all allowed schemas
```

**Output:**
```json
[
  { "schema": "reports", "table": "rpt_headcount", "description": "Monthly headcount by dept" },
  { "schema": "dim",     "table": "dim_employee",  "description": "Employee master data" }
]
```

---

### 2. `describe_table`
Returns column-level schema for a table, enriched with dbt descriptions when available.

**Input:**
```json
{ "table": "dim.dim_employee" }
```

**Output:**
```json
{
  "schema": "dim",
  "table": "dim_employee",
  "description": "Employee master data, updated daily from HRIS",
  "columns": [
    { "name": "employee_id",   "type": "integer", "description": "Unique employee identifier" },
    { "name": "full_name",     "type": "varchar", "description": "Full name" },
    { "name": "department",    "type": "varchar", "description": "Current department" },
    { "name": "hire_date",     "type": "date",    "description": "Date of hire" },
    { "name": "is_active",     "type": "boolean", "description": "Currently employed" }
  ]
}
```

> **Note:** Descriptions come from `dbt/manifest.json` if available; falls back to
> Postgres column comments; falls back to `null`.

---

### 3. `run_query`
Executes a free-form SQL SELECT query against allowed schemas.

⚠️ IMPORTANT:

run_query MUST call plan_query internally before execution
Queries with severity = high MUST be rejected

Execution flow:

plan = plan_query(sql)

if plan["severity"] == "high":
    raise ValueError(plan)

result = execute_query(sql)

return {
    "columns": [...],
    "rows": [...],
    "warnings": plan["warnings"]
}

**Input:**
```json
{ "sql": "SELECT department, COUNT(*) FROM dim.dim_employee WHERE is_active = true GROUP BY 1" }
```

**Output:**
```json
{
  "columns": ["department", "count"],
  "rows": [
    ["Engineering", 42],
    ["HR", 10]
  ],
  "row_count": 2,
  "truncated": false
}
```

**Guardrails enforced (see `guardrails.py`):**
- Only `SELECT` statements — block `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, etc.
- Only references tables/schemas in the whitelist (`reports`, `dim`, `fct`)
- Row limit: **1000 rows** (configurable in `config.py`)
- Query timeout: **30 seconds** (configurable)

---

### 4. `get_sample`
Returns N sample rows from a table. Useful for LLM to understand data shape before querying.

**Input:**
```json
{ "table": "reports.rpt_headcount", "n": 5 }
```

**Output:**
```json
{
  "columns": ["month", "department", "headcount", "new_hires", "exits"],
  "rows": [...]
}
```

---

### 5. `plan_query`
Validates a SQL query and returns structural analysis **before execution** — no data is read.
This is a **safety layer**, not a performance tool. Primary value: catch join key mismatches,
missing filters on `fct` tables, and schema violations before bad data reaches the user.

**Input:**
```json
{ "sql": "SELECT * FROM fct.fct_attendance f JOIN dim.dim_employee d ON f.employee_id = d.id" }
```

**Output:**
```json
{
  "valid": false,
  "tables_referenced": ["fct.fct_attendance", "dim.dim_employee"],
  "warnings": [
    {
      "type": "join_key_mismatch",
      "detail": "f.employee_id joined to d.id — expected d.employee_id",
      "severity": "high"
    },
    {
      "type": "missing_date_filter",
      "detail": "fct.fct_attendance has no WHERE on a date/time column",
      "severity": "medium"
    }
  ],
  "suggestion": "Fix join key to d.employee_id and add a date range filter before running."
}
```

> **LLM workflow:** Always call `plan_query` before `run_query` for any cross-schema
> query or any query touching `fct` tables. Self-correct based on warnings before executing.

---

### 6. `get_metric`
Returns the canonical definition of a named metric from the metric registry.
Prevents metric drift when LLM generates SQL outside the `reports` layer.

**Input:**
```json
{ "metric": "headcount" }
```

**Output:**
```json
{
  "metric": "headcount",
  "table": "dim.dim_employee",
  "sql": "COUNT(DISTINCT employee_id)",
  "filters": "is_active = true",
  "description": "Number of active employees. Always use COUNT DISTINCT to avoid double-counting.",
  "grain": ["department", "location", "month"]
}
```

> **LLM workflow:** When HR asks about a known metric (headcount, attrition, hiring rate),
> call `get_metric` first to get the canonical definition, then build SQL from it —
> never infer metric logic from column names alone.

---

## MCP Resources

Resources provide static metadata that MCP clients can read without calling a tool.

### `dwh://schema-overview`
Returns a summary of all exposed schemas and their purpose. Loaded once by LLM at session start.

```json
{
  "project": "HR Data Warehouse",
  "stack": "dbt + PostgreSQL, Kimball model",
  "schemas": {
    "reports": "Pre-built HR reports. Start here.",
    "dim":     "Dimension tables: employees, departments, positions, etc.",
    "fct":     "Fact tables: attendance, payroll events, headcount snapshots, etc."
  },
  "usage_hint": "Use list_tables() first, then describe_table() to understand columns, then run_query() to answer questions."
}
```

---

## Configuration (`config.py`)

```python
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "dbname":   "your_db",
    "user":     "your_user",
    "password": "your_password",   # use env var in production: os.environ["PG_PASSWORD"]
}

ALLOWED_SCHEMAS = ["reports", "dim", "fct"]

QUERY_ROW_LIMIT  = 1000
QUERY_TIMEOUT_S  = 30

DBT_MANIFEST_PATH = "dbt/manifest.json"   # set to None to skip dbt enrichment
```

---

## dbt Manifest Integration

When `dbt/manifest.json` is present, `metadata.py` reads it to enrich `describe_table` responses.

**How to keep it updated:**
```bash
# In your dbt project, after any model change:
dbt compile   # or dbt run
cp target/manifest.json /path/to/mcp/dbt/manifest.json
```

**What gets extracted:**
- `nodes[*].description` → table-level description
- `nodes[*].columns[*].description` → column-level description
- Only `model.*` and `source.*` node types are used

> **Priority:** dbt description → Postgres column comment → `null`
---

## Metric Registry (`metric_registry.py`)

A lightweight dictionary that serves as the **single source of truth** for metric definitions.
Prevents metric drift when LLM generates SQL outside the `reports` layer.

**Why this matters:**
- `rpt_headcount` uses `COUNT(DISTINCT employee_id)` — but LLM might write `COUNT(employee_id)` elsewhere
- Without a registry, the same metric can be computed differently across queries → silent inconsistency

**Structure:**
```python
METRICS = {
    "headcount": {
        "table":       "dim.dim_employee",
        "sql":         "COUNT(DISTINCT employee_id)",
        "filters":     "is_active = true",
        "description": "Active employee count. Always DISTINCT to avoid double-counting.",
        "grain":       ["department", "location", "month", "week", "quarter"]
    },
    "attrition_rate": {
        "table":       "fct.fct_headcount_snapshot",
        "sql":         "COUNT(DISTINCT CASE WHEN is_exit THEN employee_id END)::float / NULLIF(COUNT(DISTINCT employee_id), 0)",
        "filters":     None,
        "description": "Voluntary + involuntary exits divided by total headcount in period.",
        "grain":       ["department", "location", "month", "quarter"]
    }
    # add more as HR team identifies new recurring questions
}
```

**Usage rule:** When `get_metric` is called, serve directly from this dict.
When a new metric is needed that isn't in the registry, **add it here first** before writing SQL.



---

## Guardrails Detail (`guardrails.py`)

```
Input SQL
    │
    ├─ Strip comments & normalize whitespace
    ├─ Check: only one statement (no semicolon-chained queries)
    ├─ Check: statement starts with SELECT or WITH
    ├─ Check: no forbidden keywords (INSERT, UPDATE, DELETE, DROP,
    │         TRUNCATE, ALTER, CREATE, EXECUTE, COPY, pg_read_file, etc.)
    ├─ Check: all table references are in ALLOWED_SCHEMAS
    │         (parse schema.table or search_path)
    ├─ [fct only] Check: WHERE clause contains a date/time column filter
    │         → reject if missing, return actionable error message
    ├─ [fct only] Row limit tightened: 500 rows (vs 1000 for dim/reports)
    └─ Pass to Postgres with LIMIT injected if not present
```

> **Philosophy:** Fail closed. If the validator cannot confidently determine the query
> is safe, it rejects it and returns a clear error message.
>
> **fct rationale:** Fact tables can be large and unbounded. Requiring a date filter
> prevents accidental full-table scans and forces LLM to always scope queries in time.

---

## Implementation Notes

### Technology choices
- **Python** with [`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK
- **`psycopg2`** for Postgres connection
- **`sqlglot`** for SQL parsing in guardrails (more reliable than regex)
- Transport: **stdio** (simplest, works with Claude Desktop and most MCP clients)

### Dependencies (`requirements.txt`)
```
mcp>=1.0.0
psycopg2-binary>=2.9
sqlglot>=25.0
python-dotenv>=1.0
```

### Running the server
```bash
python src/server.py
```

### Claude Desktop config (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "postgres-hr": {
      "command": "python",
      "args": ["/absolute/path/to/src/server.py"],
      "env": {
        "PG_PASSWORD": "your_password"
      }
    }
  }
}
```

---

## Build Order (for LLM implementing this skill)

Follow this sequence to avoid rework:

1. **`config.py`** — DB connection + constants. No dependencies.
2. **`guardrails.py`** — Pure SQL validation logic including fct rules. No DB needed, fully testable offline.
3. **`metric_registry.py`** — Static dict, no dependencies. Define all known HR metrics here.
4. **`metadata.py`** — Reads Postgres `information_schema` + parses `manifest.json`.
5. **`tools/list_tables.py`** and **`tools/describe_table.py`** — metadata tools, no guardrails needed.
6. **`tools/plan_query.py`** — uses `guardrails.py` + `sqlglot` AST, no DB execution needed.
7. **`tools/run_query.py`** — calls `plan_query` internally before executing. If plan has `"valid": false`, reject.
8. **`tools/get_sample.py`** and **`tools/get_metric.py`** — thin wrappers.
9. **`server.py`** — Wire all tools and resources into MCP server.
10. **Test end-to-end** with a real Postgres connection using the test queries in `README.md`.

> **Key constraint:** `run_query` must call `plan_query` internally — never execute SQL
> that has not passed plan validation.

---

## Future Extensions (out of scope for now)

- **LLM agent integration** — point Claude/GPT at this MCP server; no server changes needed
- **Full semantic layer** — only if `metric_registry.py` + `reports` layer become insufficient (e.g. 10+ metrics, multi-team use)
- **Row-level security** — add `user_id` parameter to tools + filter injection in `guardrails.py`
- **Caching** — cache `list_tables` and `describe_table` responses (TTL ~1 hour)
- **Write-back tools** — e.g. `submit_annotation(table, row_id, note)` for HR to tag records
- **Streaming results** — for larger queries if row limit is raised
