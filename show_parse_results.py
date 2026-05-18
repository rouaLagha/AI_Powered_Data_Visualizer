#!/usr/bin/env python3
"""Show detailed results from script parsing."""

import json
from pathlib import Path
from src.qlik_to_twb.qlik_client import extract_relationships_from_qlik_script

# Load test12 metadata
test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))
load_script = metadata.get("load_script", "")

relationships = extract_relationships_from_qlik_script(load_script, metadata)

print(f"=== Script Parser Results ===\n")
print(f"Total relationships found: {len(relationships)}\n")

# Group by source table
by_source = {}
for rel in relationships:
    from_table = rel['from_table']
    by_source.setdefault(from_table, []).append(rel)

print("Relationships by source table:")
for source_table in sorted(by_source.keys()):
    rels = by_source[source_table]
    print(f"\n{source_table}:")
    for rel in rels:
        print(f"  -> {rel['to_table']} (on field: {rel['from_column']})")

# Show comparison with expected
print("\n\n=== Analysis ===")
print(f"QIX metadata returned: 0 relationships")
print(f"Script parser found: {len(relationships)} relationships")
print(f"Source: qlik_script_parse")
print(f"Cardinality: {relationships[0]['cardinality'] if relationships else 'N/A'}")

# Sample relationships
print(f"\n=== Sample Relationships ===")
for i, rel in enumerate(relationships[:5], 1):
    print(f"{i}. {rel['from_table']}[{rel['from_column']}] -> {rel['to_table']}[{rel['to_column']}]")
