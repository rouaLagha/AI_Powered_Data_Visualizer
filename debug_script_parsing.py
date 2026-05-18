#!/usr/bin/env python3
"""Debug the script parsing - show what's being parsed."""

import json
from pathlib import Path
import re

# Load test12 metadata
test12_metadata_path = Path(
    "c:/Users/Roua/Desktop/Talan 2025-2026/part2_V3/output/qlik_powerbi_jobs/"
    "qlik_20260518T081201Z_ced09d72/qlik_metadata.json"
)

metadata = json.loads(test12_metadata_path.read_text(encoding="utf-8-sig"))
load_script = metadata.get("load_script", "")

print("=== Analyzing Load Script Structure ===\n")
print(f"Script length: {len(load_script)} chars")
print(f"\nFirst 1000 characters:\n{load_script[:1000]}\n")

# Look for table definitions with different patterns
patterns = [
    (r'(\w[\w\.\-]*?):\s*LOAD', "Pattern 1: TableName: LOAD"),
    (r'(\w[\w\.\-]*?):\s*(?:MAPPING\s+)?LOAD\s+(.*?)(?:FROM|RESIDENT|WHERE|;)', "Pattern 2: LOAD...FROM"),
    (r'(\w[\w]*?)\s*:\s*LOAD\b', "Pattern 3: Simple LOAD"),
    (r'([A-Za-z_]\w*)\s*:\s*LOAD\b', "Pattern 4: Simple table:LOAD"),
]

for pattern, desc in patterns:
    print(f"\n=== {desc} ===")
    matches = list(re.finditer(pattern, load_script, re.IGNORECASE | re.DOTALL))
    print(f"Found {len(matches)} matches")
    for i, m in enumerate(matches[:3], 1):  # Show first 3
        print(f"  {i}. '{m.group(0)[:80]}...'")

# Now extract table definitions manually by looking at the script structure
print("\n=== Manual Analysis ===")
lines = load_script.split('\n')
print(f"Script has {len(lines)} lines")

# Look for lines that define tables
table_lines = [i for i, line in enumerate(lines) if ':' in line and 'LOAD' in line]
print(f"\nLines with ':' and 'LOAD': {len(table_lines)}")
for idx in table_lines[:5]:
    print(f"  Line {idx}: {lines[idx][:80]}")

# Extract actual table names  
print("\n=== Extracted Table Names ===")
table_names = set()
for line in lines:
    if ':' in line and 'LOAD' in line.upper():
        table_name = line.split(':')[0].strip()
        if table_name and not table_name.startswith(('LET', 'SET', '//', '$')):
            table_names.add(table_name)

for name in sorted(table_names):
    print(f"  - {name}")
