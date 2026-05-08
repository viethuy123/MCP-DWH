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

An MCP (Model Context Protocol) server that exposes a PostgreSQL Data Warehouse built
with dbt on a Kimball model so that HR users (or any LLM agent) can explore data beyond
fixed reports without writing SQL.

The goal is not just to run SQL, but to ensure:

Correct business logic
Safe query execution
Consistent metrics

---

## Architecture Overview

```text
HR User / LLM Agent
        |
        v
  MCP Client (e.g. Claude Desktop, custom app)
        |  MCP Protocol (HTTP/SSE in current code; stdio also possible conceptually)
        v
  MCP Server  <- this project
  |-- resources/   metadata about tables
  `-- tools/       query execution with guardrails
        |
        v
  PostgreSQL DWH (dbt Kimball)
  |-- reports      <- primary entry point for HR
  |-- dim          <- dimension tables
  `-- fct          <- fact tables
```

Core Design Principles
Fail closed - unsafe queries are rejected
Plan before execution - never run raw SQL directly
Metrics are centralized - no duplication of logic
LLM is guided, not trusted blindly

### dbt Layer Strategy

| Schema         | Expose via MCP | Reason                                       |
|----------------|----------------|----------------------------------------------|
| `reports`      | Yes            | HR starting point, stable, pre-aggregated    |
| `dim`          | Yes            | Join extra attributes, enrich context        |
| `fct`          | Yes            | Re-aggregate in new ways                     |
| `intermediates`| No             | Internal transform logic, unstable           |
| `stg`          | No             | Raw/uncleaned data, may contain PII          |
| `snapshots`    | No             | dbt internal, not for consumption            |
| `*_fdw`        | No             | Source systems already absorbed into stg/dim |
| `public`       | No             | Postgres default, uncontrolled               |

This section is still useful even though the code only hardcodes `dim`, `fct`, and
`reports` in `ALLOWED_SCHEMAS`; it explains the warehouse exposure policy and what
kind of data the LLM is expected to reason over.

---

## Project File Structure

```text
postgres-hr-mcp/
|-- SKILL.md                  <- this file
|-- PROJECT_LOGIC.md          <- runtime logic map and current behavior
|-- README.md
|-- requirements.txt
|-- Dockerfile
|-- docker-compose.yaml
|-- dbt/
|   `-- manifest.json         <- copy from dbt project's target/ folder
`-- src/
    |-- server.py             <- MCP server entry point
    |-- config.py             <- DB connection, allowed schemas, row/time limits
    |-- guardrails.py         <- reserved guardrail module; current validation lives mostly elsewhere
    |-- metadata.py           <- table/column info from dbt manifest
    |-- metric_registry.py    <- metric definitions (single source of truth)
    |-- test_mcp.py
    `-- tools/
        |-- list_tables.py
        |-- describe_table.py
        |-- get_sample.py
        |-- get_metric.py
        |-- plan_query.py     <- pre-execution validation and metric SQL planning
        |-- run_query.py
        `-- run_metric_query.py
```

NOTE:

`plan_query.py` is still a required safety layer.
`run_query.py` still calls `plan_query` before executing SQL.
`metric_registry.py` still acts as a consistency layer to prevent metric drift.
Current code added `run_metric_query.py` as the preferred end-to-end path for known HR metrics.

---

## MCP Tools (current code: 7 tools)

### 1. `list_tables`
Lists all tables/views in allowed schemas loaded from metadata.

**Input:**
```json
{ "schema": "reports" }
```

`schema` is optional. Current code allows:
- `dim`
- `fct`
- `reports`

**Current output shape:**
```json
{
  "tables": [
    {
      "full_name": "reports.rpt_headcount",
      "schema": "reports",
      "name": "rpt_headcount",
      "semantic_type": "report",
      "description": "Monthly headcount by dept",
      "grain": ["month", "department_id"],
      "columns_count": 12
    }
  ],
  "count": 1,
  "schema_filter": "reports"
}
```

Use first to discover what exists before picking a metric or writing SQL.

---

### 2. `describe_table`
Returns table-level schema and metadata, enriched from dbt manifest.

Current code returns more than the older version of this doc. Besides columns and description,
it can also return:

- `semantic_type`
- `grain`
- `primary_key`
- `foreign_keys`
- `temporal_logic`
- `warnings`
- `preferred_for`
- `avoid_for`
- `columns_count`

**Input:**
```json
{ "table": "dim.dim_odoo_members" }
```

**Example output:**
```json
{
  "table": "dim.dim_odoo_members",
  "description": "Employee master data",
  "semantic_type": "dimension",
  "grain": ["member_id"],
  "primary_key": ["member_id"],
  "columns": [
    { "name": "member_id", "type": "integer", "description": "Unique member id" },
    { "name": "official_date", "type": "date", "description": "Official start date" }
  ],
  "foreign_keys": [],
  "temporal_logic": ["active_employee"],
  "warnings": [],
  "preferred_for": ["headcount"],
  "avoid_for": [],
  "columns_count": 2
}
```

> **Note:** In the current repo, metadata comes from `dbt/manifest.json`. The older text
> about falling back to Postgres comments no longer matches the current implementation.

---

### 3. `run_query`
Executes a free-form SQL SELECT query against allowed schemas.

IMPORTANT:

`run_query` calls `plan_query` internally before execution.
Only SELECT queries are allowed.

Execution flow in current code:

```text
render bindings for validation
validate_select_only(sql)
plan = plan_query(sql)
choose suggested_sql in smart mode, otherwise planned_sql
enforce LIMIT
execute with psycopg2
normalize result
```

**Input:**
```json
{ "sql": "SELECT division_name, COUNT(*) FROM dim.dim_odoo_members GROUP BY 1", "mode": "smart" }
```

**Current output shape:**
```json
{
  "success": true,
  "data": [
    { "division_name": "Engineering", "count": 42 },
    { "division_name": "HR", "count": 10 }
  ],
  "row_count": 2,
  "executed_sql": "SELECT ... LIMIT 1000",
  "warnings": []
}
```

**Current guardrails enforced in code:**
- AST-based SELECT validation via `sqlglot`
- `plan_query` validation before execution
- LIMIT enforcement in `run_query.py`
- statement timeout at DB session level

**Current constants to be aware of:**
- `run_query.py`: `MAX_ROWS = 1000`, `QUERY_TIMEOUT_MS = 10000`
- `config.py`: `DEFAULT_LIMIT = 1000`, `MAX_LIMIT = 5000`, `FACT_LIMIT = 500`, `QUERY_TIMEOUT_MS = 30000`

This mismatch is important when updating logic: the code currently has two places defining
limits/timeouts.

---

### 4. `get_sample`
Returns N sample rows from a table. Useful for the LLM to understand data shape before querying.

**Input:**
```json
{ "table": "reports.rpt_headcount", "n": 5 }
```

**Current behavior:**
- validates metadata is loaded
- clamps `n` to max 10
- resolves the table via metadata
- runs `SELECT * FROM <table> LIMIT n` through `run_query(..., mode="strict")`

**Output shape:**
```json
{
  "table": "reports.rpt_headcount",
  "columns": ["month", "department", "headcount"],
  "rows": [["2024-06-01", "Engineering", 42]],
  "row_count": 1,
  "note": "Sample only - not representative of full dataset."
}
```

---

### 5. `plan_query`
Validates a SQL query and returns structural analysis before execution. No data is read.

This tool has evolved in current code:

- SQL mode: validates an existing SQL string
- Metric mode: builds SQL from `get_metric` output, auto-resolves joins for `group_by`, then validates the generated SQL

**MCP input schema exposed by `server.py`:**
```json
{ "sql": "SELECT * FROM fct.fct_attendance_daily", "mode": "smart" }
```

**Current SQL-mode output shape:**
```json
{
  "mode": "sql",
  "planned_sql": "SELECT * FROM fct.fct_attendance_daily",
  "suggested_sql": "SELECT * FROM fct.fct_attendance_daily WHERE ...",
  "tables": ["fct.fct_attendance_daily"],
  "warnings": [
    {
      "type": "FACT_NO_FILTER",
      "message": "fct_attendance_daily is a fact table without WHERE",
      "severity": "HIGH"
    }
  ]
}
```

**Metric planning flow now supported in code:**
```text
metric_result from get_metric
-> detect which GROUP BY columns live on base table
-> auto-find related tables for missing columns
-> build JOINs
-> build SELECT / FROM / WHERE / GROUP BY / ORDER BY
-> call plan_query(sql=generated_sql)
```

> **Important current caveat:** `server.py` passes `mode=...` into `plan_query`, but
> `tools/plan_query.py` currently does not accept a `mode` parameter. The MCP `plan_query`
> tool may fail until that mismatch is fixed.

> **LLM workflow:** Always call `plan_query` before `run_query` for any cross-schema
> query or any query touching `fct` tables. For supported business metrics, prefer
> `run_metric_query` instead.

---

### 6. `get_metric`
Returns the canonical definition of a named metric from the metric registry.
Prevents metric drift when the LLM generates SQL outside the `reports` layer.

Current code supports these metrics:

- `headcount`
- `attrition`
- `new_hire`
- `absent_days`
- `tenure`

**Input:**
```json
{
  "metric_name": "headcount",
  "params": { "target_date": "2024-06-30" },
  "group_by": ["division_name", "branch_name"]
}
```

**Current output shape:**
```json
{
  "metric_name": "headcount",
  "metric_type": "point_in_time",
  "description": "Number of active employees at a given point in time.",
  "table": "dim.dim_odoo_members",
  "select_expr": "COUNT(DISTINCT member_id)",
  "where_clause": "(official_date <= %(target_date)s OR official_date IS NULL)AND (end_date IS NULL OR end_date > %(target_date)s)",
  "bindings": { "target_date": "2024-06-30" },
  "missing_params": [],
  "required_joins": [],
  "grain_warnings": [],
  "aggregation": { "function": "COUNT DISTINCT", "column": "member_id" },
  "warnings": [
    "Never use member_status to determine active employees"
  ],
  "constraints": [],
  "usage_hint": "SELECT COUNT(DISTINCT member_id) FROM dim.dim_odoo_members WHERE ..."
}
```

Current code also supports list mode:

```json
{}
```

returns:

```json
{
  "available_metrics": {
    "headcount": "...",
    "attrition": "...",
    "new_hire": "...",
    "absent_days": "...",
    "tenure": "..."
  }
}
```

> **LLM workflow:** When HR asks about a known metric, call `get_metric` first to get
> the canonical logic, or skip straight to `run_metric_query` if the goal is execution.
> Never infer metric logic from column names alone.

---

### 7. `run_metric_query`
This is new in the current codebase and should be documented explicitly.

Purpose:

- execute a named HR metric end-to-end
- avoid hand-writing SQL for known metrics
- centralize metric rendering, SQL planning, and execution in one call

Flow:

```text
get_metric
-> plan_query(metric mode)
-> run_query(strict)
```

**Input:**
```json
{
  "metric_name": "absent_days",
  "params": {
    "start_date": "2024-01-01",
    "end_date": "2024-01-31"
  },
  "group_by": ["division_name"]
}
```

**Current output shape:**
```json
{
  "success": true,
  "data": [
    { "division_name": "Engineering", "value": 18.5 }
  ],
  "row_count": 1,
  "sql": "SELECT dim.dim_odoo_members.division_name, SUM(daily_absent_unit) AS value ...",
  "metric": "absent_days",
  "warnings": []
}
```

Preferred usage:

- `run_metric_query` for supported HR metrics
- `run_query` for truly ad hoc SQL

---

## MCP Resources

Resources provide static metadata that MCP clients can read without calling a tool.

### `dwh://schema-overview`
Returns a summary of exposed schemas and the recommended workflow.

Current code returns:

```json
{
  "project": "HR Data Warehouse",
  "stack": "dbt + PostgreSQL, Kimball model",
  "schemas": {
    "reports": "Pre-built HR reports. Start here.",
    "dim": "Dimension tables: employees, departments, etc.",
    "fct": "Fact tables: attendance, snapshots, etc."
  },
  "workflow": [
    "1. list_tables()",
    "2. run_metric_query()",
    "3. describe_table()",
    "4. get_sample()",
    "5. plan_query()",
    "6. run_query()"
  ]
}
```

This is slightly different from the older wording and should stay aligned with `src/server.py`.

---

## Configuration (`config.py`)

Current code:

```python
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

ALLOWED_SCHEMAS = ["dim", "fct", "reports"]

DEFAULT_LIMIT = 1000
MAX_LIMIT = 5000
FACT_LIMIT = 500

QUERY_TIMEOUT_MS = 30000
```

Additional server config lives in `src/server.py`:

```python
DBT_MANIFEST_PATH = os.getenv("DBT_MANIFEST_PATH", "dbt/manifest.json")
host = os.getenv("MCP_HOST", "0.0.0.0")
port = int(os.getenv("MCP_PORT", 8000))
```

---

## dbt Manifest Integration

When `dbt/manifest.json` is present, `metadata.py` reads it to enrich `describe_table`
and other metadata-driven tool behavior.

**How to keep it updated:**
```bash
# In your dbt project, after any model change:
dbt compile
cp target/manifest.json /path/to/mcp/dbt/manifest.json
```

**What current code extracts:**
- only `model` nodes in allowed schemas
- table-level `description`
- column-level `description`
- `meta.semantic_type`
- `meta.grain`
- `meta.primary_key`
- `meta.foreign_keys`
- `meta.temporal_logic`
- `meta.time_context.reference_date_column`
- `meta.usage.preferred_for`
- `meta.usage.avoid_for`
- `meta.usage.warnings`
- `meta.data_characteristics`
- `meta.synonyms`

Unlike the old version of the doc, the current implementation does not query Postgres
comments as a fallback. Metadata is manifest-driven.

Expected `meta` shape in dbt YAML:

```yaml
models:
  - name: dim_odoo_members
    description: Employee master data
    meta:
      semantic_type: dimension
      grain: [member_id]
      primary_key: member_id
      foreign_keys:
        - column: division_id
          references: dim.dim_division.division_id
      temporal_logic:
        active_employee:
          condition_sql: "(official_date <= :target_date OR official_date IS NULL) AND (end_date IS NULL OR end_date > :target_date)"
      time_context:
        reference_date_column: official_date
      usage:
        preferred_for: ["headcount", "tenure"]
        avoid_for: []
        warnings: ["Do not use member_status as active flag"]
      synonyms:
        division_name: ["phong ban", "bo phan"]
```

---

## Metric Registry (`metric_registry.py`)

A lightweight dictionary that serves as the single source of truth for metric definitions.
This section remains important and should stay detailed.

**Why this matters:**
- `headcount` must always use `COUNT(DISTINCT member_id)`
- `absent_days` must use `SUM(daily_absent_unit)`, not `COUNT(*)`
- `tenure` must use `official_date`, not arbitrary status logic
- without a registry, the same metric can be computed differently across queries

**Current metric set in code:**
- `headcount`
- `attrition`
- `new_hire`
- `absent_days`
- `tenure`

**Current structure pattern:**
```python
METRICS = {
    "headcount": {
        "metric_type": "point_in_time",
        "table": "dim.dim_odoo_members",
        "select_expr": "COUNT(DISTINCT member_id)",
        "where_clause": "(official_date <= {target_date} OR official_date IS NULL)AND (end_date IS NULL OR end_date > {target_date})",
        "required_params": ["target_date"],
        "default_sql": {
            "target_date": "CURRENT_DATE"
        },
        "description": "Number of active employees at a given point in time.",
        "grain": ["division_name", "branch_name"],
        "joins": [],
        "warnings": [
            "Never use member_status to determine active employees"
        ]
    }
}
```

Current design details:

- `metric_type` is either `point_in_time` or `period`
- SQL defaults like `CURRENT_DATE` are injected directly into SQL
- literal user params become psycopg2 bindings
- `render_metric()` returns `select_expr`, `where_clause`, `bindings`, and warnings
- `where_clause` is mandatory business logic, not an optional suggestion
- `resolve_joins_for_grain()` helps determine which joins are needed for requested `group_by` columns

**Usage rule:** When `get_metric` is called, serve directly from this dict.
When a new recurring metric is needed, add it here first before writing SQL.

---

## Guardrails Detail

The older conceptual model is still useful, but the current repo splits guardrails across files:

- `src/guardrails.py` currently has no active implementation
- `src/tools/plan_query.py` performs structural checks and suggested fixes
- `src/tools/run_query.py` performs SELECT validation, limit enforcement, and timeout setup

Current practical flow:

```text
Input SQL
    |
    |- validate SELECT-only with sqlglot
    |- parse tables referenced
    |- warn if fact table has no WHERE
    |- warn if active-employee temporal logic is missing
    |- attempt safe temporal fix via suggested_sql
    |- enforce LIMIT before execution
    `- execute with statement_timeout
```

> **Philosophy:** Fail as safely as the current implementation allows, and keep the
> LLM on the canonical path whenever a known metric already exists.

> **Important caveat:** current `plan_query()` returns warnings and suggested SQL, but
> `run_query.py` checks `plan.get("block")`, which `plan_query.py` does not currently set.
> Treat this as an implementation gap to watch when extending safety logic.

---

## Implementation Notes

### Technology choices
- **Python** with the `mcp` SDK
- **`psycopg2`** for Postgres connection
- **`sqlglot`** for SQL parsing and SQL AST manipulation
- **Transport in current code:** HTTP/SSE using `Starlette`, `SseServerTransport`, and `uvicorn`

### Dependencies (`requirements.txt`)
```text
mcp>=1.0.0
psycopg2-binary>=2.9
sqlglot>=25.0
python-dotenv>=1.0
uvicorn>=0.27.0
starlette>=0.36.0
```

### Running the server
```bash
python src/server.py
```

### Current endpoints

- MCP SSE endpoint: `/mcp`
- MCP message endpoint: `/mcp/messages`
- health check: `/health`

### Client integration note

The old skill text referenced a stdio-style Claude Desktop configuration. That was reasonable
for an earlier design, but the current code in this repo is explicitly an HTTP/SSE server.
If this repo is evolved back to stdio later, update this section again rather than deleting
the architectural explanation above.

---

## Build Order (for LLM implementing this skill)

The original sequence is still mostly right, with one update for metric execution flow:

1. **`config.py`** - DB connection + constants.
2. **`guardrails.py`** - if you revive it, keep pure validation logic here.
3. **`metric_registry.py`** - define all known HR metrics first.
4. **`metadata.py`** - parse `manifest.json` and expose table semantics.
5. **`tools/list_tables.py`** and **`tools/describe_table.py`** - metadata tools.
6. **`tools/get_metric.py`** - expose canonical metric logic.
7. **`tools/plan_query.py`** - validate SQL and support metric-to-SQL planning.
8. **`tools/run_query.py`** - execute validated SQL only.
9. **`tools/run_metric_query.py`** - preferred end-to-end execution path for known metrics.
10. **`server.py`** - wire tools/resources into MCP server and expose HTTP/SSE app.
11. **Test end-to-end** with a real Postgres connection using representative HR questions.

> **Key constraint:** `run_query` must continue calling `plan_query` internally.
> For supported HR metrics, prefer `run_metric_query` over free-form SQL generation.

---

## Known Current Gaps

These are worth keeping in the skill because they affect how future edits should be made:

- `src/guardrails.py` is currently empty, despite older docs describing it as the main safety layer.
- `server.py` passes `mode` into `plan_query`, but `tools/plan_query.py` currently does not accept a `mode` parameter.
- `run_query.py` checks `plan.get("block")`, but `plan_query.py` does not currently produce a `block` field.
- `run_query.py` and `config.py` define separate timeout and limit constants.
- `get_sample.py` imports `run_query` as a top-level module rather than `tools.run_query`; import behavior depends on path setup.
- `README.md` still documents older behavior and should be updated separately from this skill file.

---

## Future Extensions (out of scope for now)

- **LLM agent integration** - point Claude/GPT at this MCP server; no core server redesign needed
- **Full semantic layer** - only if `metric_registry.py` + `reports` layer become insufficient
- **Row-level security** - add `user_id` parameter to tools + filter injection
- **Caching** - cache `list_tables` and `describe_table` responses
- **Write-back tools** - e.g. HR annotations or feedback loops
- **Streaming results** - if row limits are increased later
