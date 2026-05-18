#!/usr/bin/env python3
"""Debug the improved script parsing - show what's being extracted."""

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

print("=== Testing Improved Script Parser ===\n")

# Add detailed tracing - let me modify the extraction function inline to see what's happening
import re

lines = load_script.split('\n')
print(f"Script has {len(lines)} lines\n")

# Manually trace the parsing
table_fields = {}

i = 0
while i < len(lines):
    line = lines[i].strip()
    
    # Look for table definition
    if ':' in line and line.upper().find('LOAD') > line.find(':'):
        table_name_match = re.match(r'^(\w[\w\.\-]*?)\s*:', line)
        if table_name_match:
            table_name = table_name_match.group(1).strip()
            
            if table_name.upper() not in ('LET', 'SET', 'DECLARE', 'IF', 'ENDIF') and not table_name.startswith('$'):
                print(f"Found table: {table_name}")
                
                # Extract load clause
                load_pos = line.upper().find('LOAD')
                current_load_line = line[load_pos + 4:].strip()
                
                # Read next lines
                j = i + 1
                load_lines_count = 0
                while j < len(lines) and load_lines_count < 20:
                    next_line = lines[j].strip()
                    
                    if next_line.upper().startswith(('FROM', 'RESIDENT', 'WHERE')):
                        break
                    
                    if next_line == ';':
                        break
                    
                    if not next_line or next_line.startswith('--'):
                        j += 1
                        continue
                    
                    current_load_line += ' ' + next_line
                    load_lines_count += 1
                    j += 1
                
                # Parse fields
                print(f"  Load clause: {current_load_line[:100]}...")
                
                field_parts = re.split(r',(?=\s*[A-Za-z_\[])', current_load_line)
                fields = []
                for part in field_parts[:5]:  # Show first 5
                    part = part.strip()
                    if part:
                        print(f"    Field part: {part}")
                        
                        # Try AS pattern
                        as_match = re.search(r'(\w[\w\.\-]*?)\s+AS\s+\[?([^\],]+)\]?', part, re.IGNORECASE)
                        if as_match:
                            print(f"      -> Extracted (AS): {as_match.group(2)}")
                            fields.append(as_match.group(2).strip())
                        else:
                            field_match = re.match(r'(\w[\w\.\-]*)', part)
                            if field_match:
                                fname = field_match.group(1).strip()
                                if fname.upper() not in ('FROM', 'RESIDENT', 'WHERE', 'LOAD'):
                                    print(f"      -> Extracted: {fname}")
                                    fields.append(fname)
                
                table_fields[table_name] = set(fields)
                print()
    
    i += 1
    
    if len(table_fields) >= 5:  # Stop after 5 tables to keep output manageable
        break

print(f"\nTotal tables extracted: {len(table_fields)}")
for table, fields in sorted(table_fields.items()):
    print(f"  {table}: {len(fields)} fields - {', '.join(list(fields)[:3])}...")
