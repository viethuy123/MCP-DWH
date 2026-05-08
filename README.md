# postgres-hr-mcp

MCP (Model Context Protocol) server exposing a PostgreSQL Data Warehouse (dbt + Kimball)
for HR self-serve analytics — no SQL required.

---

## Prerequisites

- Python 3.11+
- PostgreSQL DWH running (local or remote)
- dbt project with `target/manifest.json` available
- MCP client (e.g. Claude Desktop)

---

## Project Structure

```
postgres-hr-mcp/
├── README.md
├── SKILL.md                        ← architecture spec + build guide for LLMs
├── PROJECT_LOGIC.md                ← current runtime logic and reading map
├── requirements.txt
├── .env                            ← DB credentials (git-ignored)
├── .env.example
├── dbt/
│   └── manifest.json               ← copied from dbt target/, see step 3 below
└── src/
    ├── server.py                   ← MCP entry point
    ├── config.py                   ← DB connection + constants
    ├── guardrails.py               ← SQL validation (SELECT-only, schema whitelist)
    ├── metadata.py                 ← Postgres info_schema + dbt manifest reader
    └── tools/
        ├── __init__.py
        ├── list_tables.py
        ├── describe_table.py
        ├── run_query.py
        └── get_sample.py
```

---

## Recommended Reading Order

1. `PROJECT_LOGIC.md` for the current runtime behavior
2. `SKILL.md` for the broader architecture and build context
3. `README.md` for setup and day-to-day usage

---

## Setup

### Step 1 — Clone & install dependencies

```bash
git clone <your-repo-url>
cd postgres-hr-mcp

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Step 2 — Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
PG_HOST=localhost
PG_PORT=5432
PG_DBNAME=your_db
PG_USER=your_user
PG_PASSWORD=your_password
```

### Step 3 — Copy dbt manifest

After any `dbt run` or `dbt compile` in your dbt project:

```bash
cp /path/to/your-dbt-project/target/manifest.json ./dbt/manifest.json
```

> If `manifest.json` is absent, the server still works — table/column descriptions
> will just be `null` until you add dbt `.yml` descriptions and copy the manifest.

### Step 4 — Run the server

```bash
python src/server.py
```

Expected output:
```
[MCP] postgres-hr server started
[MCP] Connected to PostgreSQL: your_db@localhost:5432
[MCP] Exposed schemas: reports, dim, fct
[MCP] Tools registered: list_tables, describe_table, run_query, get_sample
```

---

## Connect to Claude Desktop

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "postgres-hr": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/src/server.py"],
      "env": {
        "PG_PASSWORD": "your_password"
      }
    }
  }
}
```

Restart Claude Desktop. You should see `postgres-hr` appear in the tools list.

---

## Verify the server works

Run these in order to confirm each layer is working:

```
# 1. List all available tables
list_tables()

# 2. Inspect a specific table
describe_table("reports.rpt_headcount")

# 3. Preview sample data
get_sample("reports.rpt_headcount", 5)

# 4. Simple query
run_query("SELECT * FROM reports.rpt_headcount LIMIT 10")

# 5. Cross-schema query (reports + dim)
run_query("""
  SELECT r.month, r.department, r.headcount, d.manager_name
  FROM reports.rpt_headcount r
  JOIN dim.dim_department d USING (department_id)
  ORDER BY r.month DESC
""")
```

---

## Sample HR Questions (for LLM testing)

Use these to verify the full MCP + LLM flow end-to-end:

| Question | Expected tool calls |
|---|---|
| "Headcount theo phòng ban tháng này là bao nhiêu?" | `list_tables` → `run_query` |
| "Có bao nhiêu nhân viên được tuyển trong Q1?" | `describe_table` → `run_query` |
| "Phòng nào có tỉ lệ nghỉ việc cao nhất 6 tháng qua?" | `describe_table` → `run_query` |
| "Nhân viên nào có thâm niên trên 5 năm còn đang làm việc?" | `describe_table` → `run_query` |
| "So sánh headcount tháng này vs tháng trước theo từng team" | `run_query` |

---

## Guardrails summary

The server enforces these rules on every `run_query` call:

| Rule | Detail |
|---|---|
| SELECT only | `INSERT`, `UPDATE`, `DELETE`, `DROP`, etc. are blocked |
| Schema whitelist | Only `reports`, `dim`, `fct` are accessible |
| Row limit | Max 1000 rows returned (configurable in `config.py`) |
| Timeout | 30 seconds per query (configurable in `config.py`) |
| Single statement | Semicolon-chained queries are rejected |

---

## Updating dbt descriptions

When you add or update column descriptions in your dbt `.yml` files:

```bash
# In dbt project
dbt compile

# Copy fresh manifest
cp target/manifest.json /path/to/postgres-hr-mcp/dbt/manifest.json
```

No server restart needed — manifest is read per request.

---

## Troubleshooting

**`Connection refused` on startup**
→ Check `PG_HOST`, `PG_PORT` in `.env`. Confirm Postgres is running.

**Tables not showing up in `list_tables`**
→ Confirm the dbt models have been run (`dbt run`) and materialized in `reports`, `dim`, or `fct` schema.

**`Permission denied` on query**
→ Ensure the DB user has `SELECT` privilege on the exposed schemas:
```sql
GRANT USAGE ON SCHEMA reports, dim, fct TO your_user;
GRANT SELECT ON ALL TABLES IN SCHEMA reports TO your_user;
GRANT SELECT ON ALL TABLES IN SCHEMA dim TO your_user;
GRANT SELECT ON ALL TABLES IN SCHEMA fct TO your_user;
```

**Descriptions showing `null`**
→ `manifest.json` is missing or stale. Re-run `dbt compile` and copy the file.

**Query blocked by guardrails unexpectedly**
→ Check `src/guardrails.py`. The validator fails closed — if unsure, it rejects.
Open an issue with the exact SQL that was blocked.
