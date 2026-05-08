# Project Logic

This file is the quick mental model for the current MCP codebase.
Read this first when you want to understand how the project behaves without opening every Python file.

## What This Project Does

This repo exposes a PostgreSQL data warehouse through MCP for HR analytics.
The server is not just a SQL executor. It also:

- loads dbt semantic metadata
- introspects the live database
- resolves column aliases and synonyms
- plans metric queries before execution
- blocks unsafe SQL
- helps the LLM pick the right table, join, and time filter

## Current Source Of Truth

The project now uses a hybrid metadata model:

1. `dbt/manifest.json`
   - semantic descriptions
   - model-level `meta`
   - column descriptions and column `meta`
   - synonyms defined in dbt YAML
   - foreign keys declared in dbt YAML

2. `dbt/catalog.json`
   - dbt-generated physical column types
   - catalog comments
   - current compiled model shape

3. Live PostgreSQL schema
   - real tables/views that actually exist
   - nullable
   - primary keys
   - database foreign keys
   - table type

The rule is simple:

- DB reality wins for existence
- dbt metadata wins for meaning
- catalog fills in physical column detail

## Metadata Flow

Startup flow:

```text
server.py
  -> init_metadata()
  -> metadata.py loads:
       - manifest
       - catalog if present
       - DB information_schema
  -> merge into MetadataStore
```

What the metadata layer now knows:

- table description
- semantic type: dimension / fact / report
- grain
- primary key
- foreign keys from dbt and from DB
- temporal logic
- usage hints and warnings
- column descriptions, types, nullable flags
- column synonyms and alias resolution

## How Column Resolution Works

The code no longer depends only on physical column names.

Resolution order:

1. exact physical column name
2. column-level synonyms from dbt YAML
3. table-level synonym map
4. fallback search across other tables that expose the column or alias

This matters because dbt names can change over time.
The expected workflow is:

- update dbt YAML
- regenerate manifest and catalog
- MCP picks up the new names automatically

## Metric Flow

Metrics live in `src/metric_registry.py`.

That file defines the canonical metric contract:

- metric name
- metric type
- base table
- select expression
- where clause
- required params
- default SQL params
- grain
- joins
- warnings

Metric execution flow:

```text
get_metric_tool()
  -> resolve metric
  -> render SQL fragments
  -> resolve group_by aliases
  -> attach required joins
  -> plan_query(metric_result=...)
  -> run_query()
```

Important behavior:

- `grain` is advisory, not a hard permission gate
- `group_by` columns can come from the base table or from a joined table
- `job_title` now resolves to `position_name` when the dbt metadata says so
- `position_id` now resolves to `job_id` when the dbt metadata says so

## Query Planning Flow

`src/tools/plan_query.py` has two jobs:

1. validate free-form SQL
2. validate metric-generated SQL before execution

The planner checks:

- SELECT-only safety
- fact table rules
- temporal filter presence
- join safety
- grain mismatch warnings
- missing required filters

The planner tries to fail closed when the query is clearly unsafe.
It should warn when the query is probably valid but semantically risky.

## Join Safety

Join planning is now metadata-driven.

The planner prefers:

1. dbt `meta.foreign_keys`
2. database foreign keys
3. known join paths already present in metadata

Join warnings include cases like:

- no known path
- missing ON clause
- ON clause does not match expected FK path

If metadata does not know the relationship yet, the planner should warn instead of inventing a join.

## Guardrails

Guardrails live across a few modules, not in one place:

- `run_query.py`
  - blocks non-SELECT SQL
  - enforces row limit
  - uses planner before execution

- `plan_query.py`
  - checks semantics and join safety
  - returns `severity` and `block`

- `metric_registry.py`
  - enforces metric contract
  - renders metric SQL consistently

The shared principle is:

- block on hard safety issues
- warn on semantic risk
- never silently guess when the metadata is insufficient

## Key Tools

- `list_tables`
  - discover tables exposed by MCP

- `describe_table`
  - inspect metadata for a specific table

- `get_sample`
  - inspect sample rows to understand shape

- `get_metric`
  - get a canonical metric definition and rendered SQL fragments

- `run_metric_query`
  - end-to-end metric execution

- `plan_query`
  - validate SQL or planned metric SQL

- `run_query`
  - execute validated SQL

## Current Practical Workflow

For a new question:

1. inspect `list_tables`
2. inspect `describe_table`
3. use `get_metric` for known HR metrics
4. use `plan_query` if the query is complex
5. use `run_query` only after planning passes

For a dbt change:

1. update YAML
2. regenerate `manifest.json` and `catalog.json`
3. confirm the live DB still matches
4. reload the server / metadata

## What Still Matters Most

The biggest accuracy drivers in this project are:

- correct dbt YAML
- fresh manifest/catalog
- accurate live schema
- good foreign key metadata
- correct temporal logic
- not overusing grain as a hard block

If those are in sync, the MCP gets much smarter.
