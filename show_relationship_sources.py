#!/usr/bin/env python3
"""Show detailed breakdown of relationship sources."""

import json
from pathlib import Path
from src.qlik_to_powerbi.metadata_pipeline import normalize_qlik_to_powerbi_model

test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))
intermediate_model = normalize_qlik_to_powerbi_model(metadata)
relationships = intermediate_model.get('semantic_model', {}).get('relationships', [])

print("=== Detailed Breakdown ===\n")

# Group by source
by_source = {}
for rel in relationships:
    source = rel.get('source', 'unknown')
    by_source.setdefault(source, []).append(rel)

print(f"Total relationships: {len(relationships)}\n")

for source in sorted(by_source.keys()):
    rels = by_source[source]
    print(f"\n{source}: {len(rels)} relationships")
    
    # Show sample
    for i, rel in enumerate(rels[:3], 1):
        print(f"  {i}. {rel['from_table']}[{rel['from_column']}] -> {rel['to_table']}[{rel['to_column']}]")
    
    if len(rels) > 3:
        print(f"  ... and {len(rels) - 3} more")

# Identify which are the REAL extracted ones from script
script_rels = by_source.get('qlik_script_parse', [])
print(f"\n\n=== REAL Relationships (from script parsing) ===")
print(f"Count: {len(script_rels)}")

if script_rels:
    print("\nSample REAL relationships:")
    for i, rel in enumerate(script_rels[:10], 1):
        print(f"{i}. {rel['from_table']}[{rel['from_column']}] -> {rel['to_table']}[{rel['to_column']}]")
