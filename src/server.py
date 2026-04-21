import os
import sys
import json

# đảm bảo src/ trong Python path
sys.path.insert(0, os.path.dirname(__file__))

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, Resource, ResourceContents
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

from metadata import init_metadata
from tools.list_tables import list_tables
from tools.describe_table import describe_table
from tools.get_sample import get_sample
from tools.get_metric import get_metric_tool
from tools.plan_query import plan_query
from tools.run_query import run_query
from tools.run_metric_query import run_metric_query

DBT_MANIFEST_PATH = os.getenv("DBT_MANIFEST_PATH", "dbt/manifest.json")

# =============================================================================
# INIT
# =============================================================================

server = Server("postgres-hr")
init_metadata(DBT_MANIFEST_PATH)

# =============================================================================
# TOOLS
# =============================================================================

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_tables",
            description=(
                "List all available tables in the data warehouse. "
                "Use first to discover what tables exist. "
                "Filter by schema: 'dim', 'fct', or 'reports'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "schema": {
                        "type": "string",
                        "enum": ["dim", "fct", "reports"],
                        "description": "Filter by schema. Omit for all."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="describe_table",
            description=(
                "Get full schema and metadata for a table. "
                "Returns columns, descriptions, grain, foreign keys, temporal logic, warnings. "
                "Call before writing any query on an unfamiliar table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "e.g. 'dim.dim_odoo_members' or 'dim_odoo_members'"
                    }
                },
                "required": ["table"]
            }
        ),
        Tool(
            name="get_sample",
            description=(
                "Get sample rows from a table to understand data shape. "
                "Use before writing queries to verify column names and formats."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "e.g. 'dim.dim_odoo_members'"
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of rows, default 5, max 10.",
                        "default": 5
                    }
                },
                "required": ["table"]
            }
        ),
        Tool(
            name="get_metric",
            description=(
                "Get canonical definition for a named HR metric. "
                "Always call before querying headcount, attrition, new_hire, absent_days, tenure. "
                "Omit metric_name to list all available metrics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "enum": ["headcount", "attrition", "new_hire", "absent_days", "tenure"],
                        "description": "Omit to list all metrics."
                    },
                    "params": {
                        "type": "object",
                        "description": "e.g. {'target_date': '2024-06-30'} or {'start_date': '...', 'end_date': '...'}"
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to GROUP BY — resolves required joins."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="run_metric_query",
            description=(
                "Execute a named HR metric query end-to-end. "
                "Handles get_metric → build SQL → validate → execute in one call. "
                "Use for headcount, attrition, new_hire, absent_days, tenure."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "enum": ["headcount", "attrition", "new_hire", "absent_days", "tenure"]
                    },
                    "params": {
                        "type": "object",
                        "description": "Date params. If omitted, defaults to current date/month."
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "e.g. ['division_name', 'branch_name']"
                    }
                },
                "required": ["metric_name"]
            }
        ),
        Tool(
            name="plan_query",
            description=(
                "Validate a SQL query before execution. "
                "Checks temporal filters, fact table rules, join key issues. "
                "Returns warnings and suggested fix. "
                "Call before run_query for fct tables or cross-schema queries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL SELECT query to validate."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["smart", "strict"],
                        "default": "smart"
                    }
                },
                "required": ["sql"]
            }
        ),
        Tool(
            name="run_query",
            description=(
                "Execute a free-form SQL SELECT query. "
                "Runs plan_query validation automatically before execution. "
                "SELECT only. Max 1000 rows."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL SELECT query to execute."
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["smart", "strict"],
                        "default": "smart"
                    }
                },
                "required": ["sql"]
            }
        ),
    ]


# =============================================================================
# TOOL HANDLER
# =============================================================================

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(
        type="text",
        text=json.dumps(result, ensure_ascii=False, indent=2, default=str)
    )]


def _dispatch(name: str, args: dict) -> dict:
    if name == "list_tables":
        return list_tables(schema=args.get("schema"))

    elif name == "describe_table":
        return describe_table(table=args["table"])

    elif name == "get_sample":
        return get_sample(table=args["table"], n=args.get("n", 5))

    elif name == "get_metric":
        return get_metric_tool(
            metric_name=args.get("metric_name"),
            params=args.get("params"),
            group_by=args.get("group_by"),
        )

    elif name == "run_metric_query":
        return run_metric_query(
            metric_name=args["metric_name"],
            params=args.get("params"),
            group_by=args.get("group_by"),
        )

    elif name == "plan_query":
        return plan_query(sql=args["sql"], mode=args.get("mode", "smart"))

    elif name == "run_query":
        return run_query(sql=args["sql"], mode=args.get("mode", "smart"))

    else:
        return {"error": f"Unknown tool: {name}"}


# =============================================================================
# RESOURCES
# =============================================================================

@server.list_resources()
async def handle_list_resources():
    return [
        Resource(
            uri="dwh://schema-overview",
            name="DWH Schema Overview",
            description="Schemas, tables, and recommended workflow.",
            mimeType="application/json"
        )
    ]


@server.read_resource()
async def handle_read_resource(uri: str):
    if uri == "dwh://schema-overview":
        overview = {
            "project": "HR Data Warehouse",
            "stack":   "dbt + PostgreSQL, Kimball model",
            "schemas": {
                "reports": "Pre-built HR reports. Start here.",
                "dim":     "Dimension tables: employees, departments, etc.",
                "fct":     "Fact tables: attendance, snapshots, etc.",
            },
            "workflow": [
                "1. list_tables()           — discover tables",
                "2. run_metric_query()      — for known metrics (headcount, attrition...)",
                "3. describe_table()        — understand schema",
                "4. get_sample()            — verify data shape",
                "5. plan_query()            — validate free-form SQL",
                "6. run_query()             — execute free-form SQL",
            ]
        }
        return ResourceContents(
            uri=uri,
            mimeType="application/json",
            text=json.dumps(overview, ensure_ascii=False, indent=2)
        )
    return ResourceContents(uri=uri, mimeType="text/plain", text="Not found")


# =============================================================================
# STARLETTE APP
# =============================================================================

def create_app():
    sse = SseServerTransport("/mcp/messages")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1],
                server.create_initialization_options()
            )

    async def health(request):
        return JSONResponse({"status": "ok", "server": "postgres-hr-mcp"})

    return Starlette(
        routes=[
            Route("/mcp",           endpoint=handle_sse),
            Mount("/mcp/messages",  app=sse.handle_post_message),
            Route("/health",        endpoint=health),
        ]
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", 8000))
    print(f"[MCP] postgres-hr starting on {host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")