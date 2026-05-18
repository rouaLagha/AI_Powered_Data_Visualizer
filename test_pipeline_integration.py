#!/usr/bin/env python3
"""Test complete pipeline with new script parsing."""

import sys
import json
from pathlib import Path
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Import pipeline
from src.qlik_to_powerbi.metadata_pipeline import normalize_qlik_to_powerbi_model

# Load test12 metadata
test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

print(f"Loading metadata from: {test12_metadata_path}\n")

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))

# Run the normalized model generation
print("=== Running normalize_qlik_to_powerbi_model ===\n")

try:
    intermediate_model = normalize_qlik_to_powerbi_model(metadata)
    
    relationships = intermediate_model.get('semantic_model', {}).get('relationships', [])
    
    print(f"Success!")
    print(f"\nTotal relationships in output: {len(relationships)}")
    
    # Show relationships by source
    by_source = {}
    for rel in relationships:
        source = rel.get('source', 'unknown')
        by_source.setdefault(source, []).append(rel)
    
    print(f"\nRelationships by source:")
    for source in sorted(by_source.keys()):
        print(f"  {source}: {len(by_source[source])}")
    
    # Show sample relationships
    print(f"\n=== Sample Relationships ===")
    for i, rel in enumerate(relationships[:10], 1):
        print(f"{i}. {rel['from_table']}[{rel['from_column']}] -> {rel['to_table']}[{rel['to_column']}] ({rel.get('source', '?')})")
    
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
