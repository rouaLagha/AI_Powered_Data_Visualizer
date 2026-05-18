#!/usr/bin/env python3
"""Show raw script lines to understand format."""

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

# Find lines with LOAD
print("=== Lines containing both ':' and 'LOAD' ===\n")
count = 0
for i, line in enumerate(lines):
    if ':' in line and 'LOAD' in line.upper():
        print(f"Line {i}: {line}")
        count += 1
        if count >= 10:
            break

if count == 0:
    print("No lines with ':' AND 'LOAD' found. Searching for just 'LOAD':")
    for i, line in enumerate(lines):
        if 'LOAD' in line.upper():
            print(f"Line {i}: {line}")
            count += 1
            if count >= 10:
                break

if count == 0:
    print("\nSearching for just ':':")
    for i, line in enumerate(lines):
        if ':' in line:
            print(f"Line {i}: {line}")
            count += 1
            if count >= 10:
                break

# Show first 100 lines to understand structure
print("\n=== First 30 lines of script ===")
for i, line in enumerate(lines[:30]):
    print(f"{i:3d}: {line}")
