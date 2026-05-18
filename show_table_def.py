#!/usr/bin/env python3
"""Show actual structure of table definitions."""

import json
from pathlib import Path

# Load test12 metadata
test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))
load_script = metadata.get("load_script", "")

lines = load_script.split('\n')

# Find first table definition
print("=== Looking for table definitions ===\n")

for i, line in enumerate(lines):
    # Look for pattern: "TableName:" on its own line
    if line.strip().endswith(':') and not line.strip().startswith(('--', '//')):
        table_name = line.strip()[:-1]
        
        # Skip LET, SET, etc.
        if table_name.upper() not in ('LET', 'SET', 'DECLARE', 'IF'):
            print(f"\nFound table definition at line {i}:")
            print(f"  {i}: {line}")
            
            # Show next 5-10 lines
            for j in range(1, 10):
                if i + j < len(lines):
                    print(f"  {i+j}: {lines[i+j]}")
                    
                    # Stop at FROM or ;
                    if 'FROM' in lines[i+j].upper() or ';' in lines[i+j]:
                        break
            
            # Only show first 3 tables to keep output manageable
            break
