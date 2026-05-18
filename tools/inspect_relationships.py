#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# Default path (current active file)
DEFAULT = Path(r"c:\Users\Roua\Desktop\Talan 2025-2026\part2_V4\output\qlik_powerbi_jobs\qlik_20260518T101415Z_6830ebb5\data_model.json")
path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
if not path.exists():
    print(f"ERROR: file not found: {path}")
    sys.exit(1)

data = json.loads(path.read_text(encoding='utf-8-sig'))
sm = data.get('semantic_model', {})
# Build table -> set(field names)
tables = {}
for t in sm.get('tables') or []:
    name = t.get('name')
    raw_fields = t.get('fields') or []
    field_names = set()
    for f in raw_fields:
        if isinstance(f, dict):
            n = f.get('name') or f.get('field')
            if n:
                field_names.add(str(n))
        elif isinstance(f, str):
            field_names.add(f)
    # also include field_names array if present
    for n in (t.get('field_names') or []):
        if n:
            field_names.add(str(n))
    if name:
        tables[name] = field_names

# Also build field list from sm['fields']
fields_index = {}
for f in sm.get('fields') or []:
    name = f.get('name')
    table = f.get('table')
    if name and table:
        fields_index.setdefault(table, set()).add(name)

# Merge indexes
for t, s in fields_index.items():
    tables.setdefault(t, set()).update(s)

rels = sm.get('relationships') or []

print(f"Loaded {len(tables)} tables and {len(rels)} relationships from {path}\n")

suspect = []
enriched = []

# helper to normalize and find suggestions

def normalize_col_name(col):
    if not col:
        return ''
    return col.split('.')[-1].strip()


def find_suggestions(col, table_cols):
    if not col:
        return []
    c_norm = normalize_col_name(col).lower()
    suggestions = []
    for x in sorted(table_cols):
        if x.lower() == c_norm:
            suggestions.insert(0, x)
        elif c_norm in x.lower():
            suggestions.append(x)
        elif x.lower().endswith(c_norm):
            suggestions.append(x)
    return suggestions[:10]


def col_in_table_exact_or_norm(col, table):
    if not col or not table:
        return False
    cols = tables.get(table, set())
    if col in cols:
        return True
    c = normalize_col_name(col).lower()
    for x in cols:
        if x.lower() == c:
            return True
        if '.' in x and x.split('.')[-1].lower() == c:
            return True
    return False

for i, r in enumerate(rels, 1):
    fid = r.get('id')
    src = r.get('source')
    ft = r.get('from_table')
    fc = r.get('from_column')
    tt = r.get('to_table')
    tc = r.get('to_column')

    ft_exists = ft in tables
    tt_exists = tt in tables

    fc_exists = col_in_table_exact_or_norm(fc, ft)
    tc_exists = col_in_table_exact_or_norm(tc, tt)

    status = 'OK' if ft_exists and tt_exists and fc_exists and tc_exists else 'MISMATCH'
    if status == 'MISMATCH':
        suspect.append((fid, src, ft, fc, tt, tc, ft_exists, fc_exists, tt_exists, tc_exists))

    # prepare human readable context
    from_sample = sorted(list(tables.get(ft, [])))[:8] if ft_exists else []
    to_sample = sorted(list(tables.get(tt, [])))[:8] if tt_exists else []

    from_suggestions = find_suggestions(fc, tables.get(ft, set())) if ft_exists else []
    to_suggestions = find_suggestions(tc, tables.get(tt, set())) if tt_exists else []

    human_label = f"{ft}.{normalize_col_name(fc)} -> {tt}.{normalize_col_name(tc)}"

    enriched_rel = dict(r)  # copy original
    enriched_rel['_human_label'] = human_label
    enriched_rel['_from_table_exists'] = ft_exists
    enriched_rel['_to_table_exists'] = tt_exists
    enriched_rel['_from_column_exists'] = fc_exists
    enriched_rel['_to_column_exists'] = tc_exists
    enriched_rel['_from_table_sample_columns'] = from_sample
    enriched_rel['_to_table_sample_columns'] = to_sample
    enriched_rel['_from_column_suggestions'] = from_suggestions
    enriched_rel['_to_column_suggestions'] = to_suggestions

    enriched.append(enriched_rel)

    print(f"{i:02d}. {fid} | {src} | {ft}.{fc} -> {tt}.{tc} | {status}")

# write enriched output next to original
out_path = path.with_name(path.stem + '.readable.json')
out_doc = dict(data)
out_doc.setdefault('semantic_model', {})
out_doc['semantic_model']['relationships_readable'] = enriched
out_path.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False), encoding='utf-8')

print(f"\nWrote enriched relationships to: {out_path}\n")

if suspect:
    print('=== Relations suspectes (détails) ===')
    for s in suspect:
        fid, src, ft, fc, tt, tc, ft_exists, fc_exists, tt_exists, tc_exists = s
        print(f"- id: {fid}")
        print(f"  source: {src}")
        print(f"  from_table exists: {ft_exists}, from_column exists: {fc_exists} ({ft}.{fc})")
        print(f"  to_table exists: {tt_exists}, to_column exists: {tc_exists} ({tt}.{tc})")
        # show suggestions
        if ft_exists and not fc_exists:
            cols = sorted(list(tables.get(ft, [])))[:12]
            print(f"  suggestions in {ft}: {cols}")
        if tt_exists and not tc_exists:
            cols = sorted(list(tables.get(tt, [])))[:12]
            print(f"  suggestions in {tt}: {cols}")
        print()
else:
    print('\nNo suspicious relationships detected.')
