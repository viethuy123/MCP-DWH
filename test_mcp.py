import os
import sys

# fix import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'tools'))

# bỏ src. prefix
from metadata import init_metadata, metadata_store
from tools.run_metric_query import run_metric_query

# =========================
# 1. LOAD METADATA
# =========================

print("\n=== INIT METADATA ===")
init_metadata("dbt/manifest.json")

result = run_metric_query(
    "headcount",
    params={"target_date": "2024-06-30"},
    group_by=["position_name"]
)

print("\n=== RESULT ===")
print(result)