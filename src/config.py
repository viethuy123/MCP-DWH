import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import sqlglot
from sqlglot import exp

load_dotenv()
# from config import MAX_LIMIT
# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================
DB_CONFIG: Dict[str, Any] = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

# =============================================================================
# SCHEMA & SECURITY CONTROL
# =============================================================================
# Chỉ cho phép query các schema được định nghĩa trước
ALLOWED_SCHEMAS: List[str] = ["dim", "fct", "reports"]

# =============================================================================
# QUERY LIMITS & TIMEOUT
# =============================================================================
DEFAULT_LIMIT: int = 1000
MAX_LIMIT: int = 5000
FACT_LIMIT: int = 500  # Fact table thường nặng hơn -> limit thấp hơn

QUERY_TIMEOUT_MS: int = 30000  # 30 seconds (Postgres statement_timeout)

# =============================================================================
# SEMANTIC & GUARDRAILS SETTINGS
# =============================================================================
ENABLE_STRICT_MODE: bool = False  # True = block query, False = chỉ cảnh báo

USE_SEMANTIC_TYPE: bool = True    # Ưu tiên semantic_type từ YAML thay vì prefix
FALLBACK_TO_PREFIX: bool = True   # Fallback khi YAML thiếu semantic_type

# Table Type Prefixes (Sử dụng cho fallback logic)
FACT_PREFIXES: List[str] = ["fct_"]
DIM_PREFIXES: List[str] = ["dim_"]
REPORT_PREFIXES: List[str] = ["rpt_"]

TABLE_TYPE_FACT = "fact"
TABLE_TYPE_DIMENSION = "dimension"
TABLE_TYPE_REPORT = "report"
TABLE_TYPE_UNKNOWN = "unknown"

VALID_TABLE_TYPES = {
    TABLE_TYPE_FACT,
    TABLE_TYPE_DIMENSION,
    TABLE_TYPE_REPORT
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def detect_table_type(table_name: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Xác định loại bảng (fact/dimension/report) dựa trên metadata hoặc tiền tố tên bảng.
    """
    # 1. Ưu tiên kiểm tra semantic_type trong YAML metadata
    if USE_SEMANTIC_TYPE and metadata:
        semantic_type = metadata.get("semantic_type")
        if semantic_type in VALID_TABLE_TYPES:
            return semantic_type

    # 2. Fallback kiểm tra tiền tố (Prefix)
    if FALLBACK_TO_PREFIX:
        name_lower = table_name.lower()

        if any(name_lower.startswith(p) for p in FACT_PREFIXES):
            return "fact"
        
        if any(name_lower.startswith(p) for p in DIM_PREFIXES):
            return "dimension"
        
        if any(name_lower.startswith(p) for p in REPORT_PREFIXES):
            return "report"

    return "unknown"


def is_allowed_schema(schema: str) -> bool:
    """Kiểm tra xem schema có nằm trong danh sách cho phép hay không."""
    return schema in ALLOWED_SCHEMAS


def get_query_limit(table_type: str) -> int:
    """Trả về LIMIT phù hợp dựa trên loại bảng."""
    return FACT_LIMIT if table_type == "fact" else DEFAULT_LIMIT


def apply_limit(sql: str, table_type: str = "unknown") -> str:
    limit = get_query_limit(table_type)
    parsed = sqlglot.parse_one(sql)
    existing = parsed.args.get("limit")
    if existing:
        current = int(existing.this.this)
        if current > MAX_LIMIT:
            parsed.set("limit", exp.Limit(this=exp.Literal.number(limit)))
        return parsed.sql()
    return parsed.sql() + f" LIMIT {limit}"


