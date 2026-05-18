#!/usr/bin/env python3
"""Test the new script-based relationship extraction."""

import json
from pathlib import Path
from src.qlik_to_twb.qlik_client import extract_relationships_from_qlik_script

# Load test12 metadata
test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

print(f"Loading metadata from: {test12_metadata_path}")
if not test12_metadata_path.exists():
    print(f"ERROR: File not found!")
    exit(1)

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))

# Extract load script
load_script = metadata.get("load_script", "")
print(f"\n=== Load Script (first 500 chars) ===")
print(load_script[:500])

# Test the script parser
print("\n=== Extracting relationships from script ===")
relationships = extract_relationships_from_qlik_script(load_script, metadata)

print(f"Found {len(relationships)} relationships:")
for i, rel in enumerate(relationships, 1):
    print(f"\n{i}. {rel['from_table']}[{rel['from_column']}] -> {rel['to_table']}[{rel['to_column']}]")
    print(f"   Source: {rel.get('source', 'unknown')}")
    print(f"   Cardinality: {rel.get('cardinality', 'unknown')}")

# Compare with current QIX extraction (which returns empty for test12)
print("\n=== Comparison ===")
print(f"Script parser found: {len(relationships)} relationships")
print("QIX returned: 0 relationships (empty for test12)")

# Show which tables are actually in the model
qix_payload = metadata.get("get_tables_and_keys", {})
print(f"\n=== Tables in model ===")
for table in json.loads(metadata.get("tables_raw", "[]")):
    if isinstance(table, dict):
        print(f"- {table.get('name') or table.get('qName', '?')}")
