from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any


JsonDict = dict[str, Any]


def write_minimal_twb(
    intermediate_model: JsonDict,
    mapping: JsonDict,
    output_path: str | Path,
) -> Path:
    workbook = build_minimal_twb_xml(intermediate_model=intermediate_model, mapping=mapping)
    xml_text = ET.tostring(workbook, encoding="unicode")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + xml_text, encoding="utf-8")
    return output_path


def build_minimal_twb_xml(intermediate_model: JsonDict, mapping: JsonDict) -> ET.Element:
    datasource_name = "qlik_intermediate_model"
    workbook = ET.Element(
        "workbook",
        attrib={
            "original-version": "18.1",
            "source-build": "2026.1.0",
            "source-platform": "win",
            "version": "18.1",
        },
    )

    ET.SubElement(workbook, "preferences")
    _append_datasources(workbook, datasource_name, intermediate_model, mapping)
    worksheet_names = _append_worksheets(workbook, datasource_name, mapping)
    _append_dashboards(workbook, datasource_name, worksheet_names)
    _append_windows(workbook, worksheet_names)
    return workbook


def _append_datasources(
    workbook: ET.Element,
    datasource_name: str,
    intermediate_model: JsonDict,
    mapping: JsonDict,
) -> None:
    datasources = ET.SubElement(workbook, "datasources")
    datasource = ET.SubElement(
        datasources,
        "datasource",
        attrib={
            "caption": "Qlik Intermediate Model",
            "hasconnection": "true",
            "inline": "true",
            "name": datasource_name,
            "version": "18.1",
        },
    )
    tables = _collect_tables(intermediate_model, mapping)
    columns = _collect_columns(tables, intermediate_model, mapping)
    connection_profile = _connection_profile(intermediate_model)

    connection = ET.SubElement(datasource, "connection", attrib={"class": "federated"})
    named_connections = ET.SubElement(connection, "named-connections")
    named_connection = ET.SubElement(
        named_connections,
        "named-connection",
        attrib={"caption": connection_profile["caption"], "name": connection_profile["name"]},
    )
    ET.SubElement(named_connection, "connection", attrib=connection_profile["connection"])

    _append_relation_graph(connection, connection_profile["name"], tables, mapping)

    cols = ET.SubElement(connection, "cols")
    for field in columns:
        ET.SubElement(cols, "map", attrib={"key": field["key"], "value": field["value"]})

    ET.SubElement(datasource, "aliases", attrib={"enabled": "yes"})

    for field in columns:
        ET.SubElement(
            datasource,
            "column",
            attrib={
                "caption": field["caption"],
                "datatype": field["datatype"],
                "name": field["key"],
                "role": field["role"],
                "type": field["type"],
            },
        )


def _append_worksheets(workbook: ET.Element, datasource_name: str, mapping: JsonDict) -> list[str]:
    worksheets = ET.SubElement(workbook, "worksheets")
    worksheet_names: list[str] = []

    for sheet in _as_list(mapping.get("sheets")):
        for visual in _as_list(sheet.get("visuals")):
            worksheet_name = _safe_worksheet_name(
                visual.get("tableau_worksheet_name")
                or visual.get("worksheet_name")
                or f"{sheet.get('tableau_name') or sheet.get('name') or 'Sheet'} - {visual.get('title') or visual.get('visual_title') or 'Visual'}"
            )
            worksheet_names.append(worksheet_name)
            worksheet = ET.SubElement(worksheets, "worksheet", attrib={"name": worksheet_name})
            table = ET.SubElement(worksheet, "table")
            view = ET.SubElement(table, "view")
            view_sources = ET.SubElement(view, "datasources")
            ET.SubElement(
                view_sources,
                "datasource",
                attrib={"caption": "Qlik Intermediate Model", "name": datasource_name},
            )
            deps = ET.SubElement(view, "datasource-dependencies", attrib={"datasource": datasource_name})
            _append_dependency_bindings(deps, visual)
            ET.SubElement(view, "aggregation", attrib={"value": "true"})
            ET.SubElement(table, "style")

            panes = ET.SubElement(table, "panes")
            pane = ET.SubElement(panes, "pane")
            ET.SubElement(pane, "mark", attrib={"class": _mark_class(visual.get("tableau_type"))})

            cols = ET.SubElement(table, "cols")
            rows = ET.SubElement(table, "rows")
            cols.text = _dimension_shelf(datasource_name, visual)
            rows.text = _measure_shelf(datasource_name, visual)

    if not worksheet_names:
        worksheet_names.append("Qlik Metadata")
        worksheet = ET.SubElement(worksheets, "worksheet", attrib={"name": "Qlik Metadata"})
        table = ET.SubElement(worksheet, "table")
        view = ET.SubElement(table, "view")
        ET.SubElement(view, "datasources")
        ET.SubElement(table, "style")
        ET.SubElement(table, "panes")
        ET.SubElement(table, "cols")
        ET.SubElement(table, "rows")

    return worksheet_names


def _append_dashboards(workbook: ET.Element, datasource_name: str, worksheet_names: list[str]) -> None:
    dashboards = ET.SubElement(workbook, "dashboards")
    dashboard = ET.SubElement(dashboards, "dashboard", attrib={"name": "Qlik Migrated Dashboard"})
    datasources = ET.SubElement(dashboard, "datasources")
    ET.SubElement(datasources, "datasource", attrib={"caption": "Qlik Intermediate Model", "name": datasource_name})
    zones = ET.SubElement(dashboard, "zones")
    root_zone = ET.SubElement(
        zones,
        "zone",
        attrib={"id": "1", "h": "100000", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"},
    )

    x = 0
    y = 0
    for index, worksheet_name in enumerate(worksheet_names):
        ET.SubElement(
            root_zone,
            "zone",
            attrib={
                "id": str(index + 2),
                "h": "300",
                "name": worksheet_name,
                "type-v2": "worksheet",
                "w": "500",
                "x": str(x),
                "y": str(y),
            },
        )
        if index % 2 == 0:
            x = 500
        else:
            x = 0
            y += 300

    devicelayouts = ET.SubElement(dashboard, "devicelayouts")
    ET.SubElement(devicelayouts, "devicelayout", attrib={"name": "Phone"})


def _append_windows(workbook: ET.Element, worksheet_names: list[str]) -> None:
    windows = ET.SubElement(workbook, "windows")
    dashboard_window = ET.SubElement(windows, "window", attrib={"class": "dashboard", "name": "Qlik Migrated Dashboard"})
    viewpoints = ET.SubElement(dashboard_window, "viewpoints")
    for worksheet_name in worksheet_names:
        ET.SubElement(viewpoints, "viewpoint", attrib={"name": worksheet_name})
    ET.SubElement(dashboard_window, "active", attrib={"id": "-1"})


def _append_relation_graph(
    connection: ET.Element,
    connection_name: str,
    tables: list[JsonDict],
    mapping: JsonDict,
) -> None:
    join_specs = _infer_join_specs(tables, mapping)
    table_lookup = {str(table.get("table_name") or "").strip(): table for table in tables if str(table.get("table_name") or "").strip()}
    relation_tree = _build_join_relation_tree(connection_name, table_lookup, join_specs)
    if relation_tree is not None:
        connection.append(relation_tree)
        return

    visual_tables = _tables_required_by_mapping(tables, mapping)
    visual_lookup = {
        str(table.get("table_name") or "").strip(): table
        for table in visual_tables
        if str(table.get("table_name") or "").strip()
    }
    visual_relation_tree = None
    if len(visual_lookup) > 1:
        visual_join_specs = [
            spec
            for spec in join_specs
            if spec.get("left_table") in visual_lookup and spec.get("right_table") in visual_lookup
        ]
        visual_relation_tree = _build_join_relation_tree(connection_name, visual_lookup, visual_join_specs)
    if visual_relation_tree is not None:
        relation_collection = ET.SubElement(connection, "relation", attrib={"type": "collection"})
        relation_collection.append(visual_relation_tree)
        visual_table_names = set(visual_lookup)
        for table in tables:
            table_name = str(table.get("table_name") or "").strip()
            if not table_name or table_name in visual_table_names:
                continue
            relation_collection.append(_table_relation(connection_name, table_name))
        return

    relation_collection = ET.SubElement(connection, "relation", attrib={"type": "collection"})
    for table in tables:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            continue
        ET.SubElement(
            relation_collection,
            "relation",
            attrib={
                "connection": connection_name,
                "name": table_name,
                "table": f"[{table_name}]",
                "type": "table",
            },
        )


def _build_join_relation_tree(
    connection_name: str,
    table_lookup: dict[str, JsonDict],
    join_specs: list[JsonDict],
) -> ET.Element | None:
    valid_specs = [
        spec
        for spec in join_specs
        if isinstance(spec, dict)
        and spec.get("left_table") in table_lookup
        and spec.get("right_table") in table_lookup
        and spec.get("left_col")
        and spec.get("right_col")
    ]
    if not valid_specs:
        return None

    first = valid_specs[0]
    current = ET.Element("relation", attrib={"type": "join", "join": str(first.get("join") or "left")})
    _append_join_clause(current, first)
    current.append(_table_relation(connection_name, str(first["left_table"])))
    current.append(_table_relation(connection_name, str(first["right_table"])))
    included = {str(first["left_table"]), str(first["right_table"])}

    pending = valid_specs[1:]
    while pending:
        progressed = False
        next_pending = []
        for spec in pending:
            left = str(spec["left_table"])
            right = str(spec["right_table"])
            if left in included and right not in included:
                oriented = spec
                next_table = right
            elif right in included and left not in included:
                oriented = {
                    "join": spec.get("join") or "left",
                    "left_table": right,
                    "left_col": spec.get("right_col") or "",
                    "right_table": left,
                    "right_col": spec.get("left_col") or "",
                }
                next_table = left
            else:
                next_pending.append(spec)
                continue

            parent = ET.Element("relation", attrib={"type": "join", "join": str(oriented.get("join") or "left")})
            _append_join_clause(parent, oriented)
            parent.append(current)
            parent.append(_table_relation(connection_name, next_table))
            current = parent
            included.add(next_table)
            progressed = True

        if not progressed:
            break
        pending = next_pending

    required_tables = set(table_lookup)
    if not required_tables.issubset(included):
        return None
    return current


def _append_join_clause(join_node: ET.Element, spec: JsonDict) -> None:
    clause = ET.SubElement(join_node, "clause", attrib={"type": "join"})
    eq_expr = ET.SubElement(clause, "expression", attrib={"op": "="})
    ET.SubElement(eq_expr, "expression", attrib={"op": f"[{spec['left_table']}].[{spec['left_col']}]"})
    ET.SubElement(eq_expr, "expression", attrib={"op": f"[{spec['right_table']}].[{spec['right_col']}]"})


def _table_relation(connection_name: str, table_name: str) -> ET.Element:
    return ET.Element(
        "relation",
        attrib={
            "connection": connection_name,
            "name": table_name,
            "table": f"[{table_name}]",
            "type": "table",
        },
    )


def _collect_tables(intermediate_model: JsonDict, mapping: JsonDict) -> list[JsonDict]:
    tables: dict[str, JsonDict] = {}

    dataprep_cache = intermediate_model.get("dataprep_cache_metadata")
    dataprep_tables = [item for item in _as_list((dataprep_cache or {}).get("qvd_tables")) if isinstance(item, dict)]

    def add_table(table_name: str, source: str = "") -> JsonDict:
        clean_name = _clean_field_name(table_name or "")
        if not clean_name:
            clean_name = "QlikModel"
        key = clean_name.lower()
        table = tables.setdefault(
            key,
            {
                "table_name": clean_name,
                "source": source,
                "field_names": [],
                "fields": [],
            },
        )
        if source and not table.get("source"):
            table["source"] = source
        return table

    if dataprep_tables:
        for table in dataprep_tables:
            table_name = str(table.get("table_name") or table.get("name") or Path(str(table.get("file_name") or "")).stem or "").strip()
            entry = add_table(table_name, str(table.get("path") or table.get("file_name") or "dataprep_qvd").strip())
            dataprep_fields = [field for field in _as_list(table.get("columns") or table.get("fields")) if isinstance(field, dict)]
            for field in dataprep_fields:
                if not isinstance(field, dict):
                    continue
                if _is_internal_dataprep_field(field):
                    continue
                field_name = _clean_field_name(field.get("name") or "")
                if not field_name or field_name in entry["field_names"]:
                    continue
                data_type = str(field.get("data_type") or field.get("type") or "").strip().lower()
                entry["field_names"].append(field_name)
                entry["fields"].append(
                    {
                        "name": field_name,
                        "datatype": str(field.get("tableau_datatype") or field.get("datatype") or _dataprep_tableau_datatype(data_type)).strip().lower(),
                        "role": str(field.get("role") or _dataprep_role(data_type)).strip().lower(),
                    }
                )
            dataprep_field_names = [
                _clean_field_name(field.get("name") or "")
                for field in dataprep_fields
                if _clean_field_name(field.get("name") or "") and not _is_internal_dataprep_field(field)
            ]
            for field_name in dataprep_field_names:
                if field_name not in entry["field_names"]:
                    entry["field_names"].append(field_name)

    if not tables:
        for table in _as_list(intermediate_model.get("tables")):
            if not isinstance(table, dict):
                continue
            entry = add_table(str(table.get("table_name") or table.get("name") or ""), str(table.get("source") or "").strip())
            for field_name in _as_list(table.get("field_names")):
                clean_field = _clean_field_name(field_name)
                if clean_field and clean_field not in entry["field_names"]:
                    entry["field_names"].append(clean_field)
            for field in _as_list(table.get("fields")):
                if not isinstance(field, dict):
                    continue
                field_name = _clean_field_name(field.get("name") or "")
                if not field_name or field_name in entry["field_names"]:
                    continue
                entry["field_names"].append(field_name)
                entry["fields"].append(
                    {
                        "name": field_name,
                        "datatype": str(field.get("tableau_datatype") or field.get("datatype") or _tableau_datatype(field.get("data_type"))).strip().lower(),
                        "role": str(field.get("role") if field.get("role") in {"dimension", "measure"} else "dimension").strip().lower(),
                    }
                )

        if not tables:
            add_table("QlikModel", "synthetic")

    return sorted(tables.values(), key=lambda item: item["table_name"].lower())


def _tables_required_by_mapping(tables: list[JsonDict], mapping: JsonDict) -> list[JsonDict]:
    required_names: set[str] = set()
    table_by_name = {str(table.get("table_name") or "").strip().lower(): table for table in tables if str(table.get("table_name") or "").strip()}

    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            for table_name in _as_list(visual.get("tables_used")):
                clean = _clean_field_name(table_name)
                if clean:
                    required_names.add(clean.lower())
            for column in _visual_columns(visual):
                table_name = _clean_field_name(column.get("table") or column.get("source_table") or "")
                if table_name:
                    required_names.add(table_name.lower())

    return [table_by_name[name] for name in sorted(required_names) if name in table_by_name]


def _infer_join_specs(tables: list[JsonDict], mapping: JsonDict) -> list[JsonDict]:
    if len(tables) < 2:
        return []

    table_names = [str(table.get("table_name") or "").strip() for table in tables if str(table.get("table_name") or "").strip()]
    table_fields = {name: _table_field_names(table) for name, table in zip(table_names, tables)}
    base_table = _base_table_for_mapping(table_names, mapping)
    if base_table not in table_fields:
        base_table = table_names[0]

    specs: list[JsonDict] = []
    connected = {base_table}
    remaining = [name for name in table_names if name != base_table]

    while remaining:
        progressed = False
        next_remaining = []
        for table_name in remaining:
            match = _best_join_match(table_name, table_fields[table_name], connected, table_fields)
            if not match:
                next_remaining.append(table_name)
                continue
            left_table, left_col, right_col = match
            specs.append(
                {
                    "join": "left",
                    "left_table": left_table,
                    "left_col": left_col,
                    "right_table": table_name,
                    "right_col": right_col,
                }
            )
            connected.add(table_name)
            progressed = True
        if not progressed:
            break
        remaining = next_remaining

    return specs


def _base_table_for_mapping(table_names: list[str], mapping: JsonDict) -> str:
    scores = {name: (100 if name.lower().startswith("fact") else 0) for name in table_names}
    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            for column in _visual_columns(visual):
                table_name = _clean_field_name(column.get("table") or column.get("source_table") or "")
                if not table_name or table_name not in scores:
                    continue
                role = str(column.get("role") or "").strip().lower()
                scores[table_name] += 10 if role == "measure" else 1
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0] if scores else ""


def _best_join_match(
    table_name: str,
    table_fields: set[str],
    connected_tables: set[str],
    all_fields: dict[str, set[str]],
) -> tuple[str, str, str] | None:
    candidates = []
    for connected_table in connected_tables:
        connected_fields = all_fields.get(connected_table, set())
        common = table_fields & connected_fields
        for field in common:
            score = _join_field_score(field, table_name, connected_table)
            if score <= 0:
                continue
            candidates.append((score, connected_table, field, field))
        key_pair = _best_key_pair(connected_fields, table_fields, table_name)
        if key_pair:
            left_col, right_col, score = key_pair
            candidates.append((score, connected_table, left_col, right_col))
    if not candidates:
        return None
    _, connected_table, left_col, right_col = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[0]
    return connected_table, left_col, right_col


def _best_key_pair(
    connected_fields: set[str],
    table_fields: set[str],
    table_name: str,
) -> tuple[str, str, int] | None:
    candidates = []
    primary_keys = sorted(
        [(score, field) for field in table_fields if (score := _primary_key_score(field, table_name)) > 0],
        key=lambda item: (-item[0], item[1]),
    )
    for primary_score, primary_key in primary_keys:
        primary_norm = primary_key.lower()
        for connected_field in connected_fields:
            connected_norm = connected_field.lower()
            score = 0
            if connected_norm == primary_norm:
                score = primary_score + 30
            elif connected_norm.endswith(primary_norm):
                score = primary_score + 10
            elif _table_key_token(table_name) and _table_key_token(table_name) in connected_norm and connected_norm.endswith("key"):
                score = primary_score
            if score > 0:
                candidates.append((score, connected_field, primary_key))
    if not candidates:
        return None
    score, left_col, right_col = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))[0]
    return left_col, right_col, score


def _primary_key_score(field_name: str, table_name: str) -> int:
    normalized = field_name.lower()
    if not normalized.endswith(("key", "id")):
        return 0
    table_token = _table_key_token(table_name)
    if table_token and normalized == f"{table_token}key":
        return 95
    if table_token and normalized == f"{table_token}id":
        return 90
    if normalized in {"id", "key"}:
        return 20
    if normalized.endswith("key"):
        return 70
    return 40


def _join_field_score(field_name: str, table_name: str, connected_table: str) -> int:
    normalized = field_name.lower()
    if normalized == "id":
        return 1
    if normalized.endswith("key"):
        table_token = _table_key_token(table_name)
        connected_token = _table_key_token(connected_table)
        if table_token and normalized == f"{table_token}key":
            return 100
        if connected_token and normalized == f"{connected_token}key":
            return 90
        return 60
    if normalized.endswith("id"):
        return 30
    return 0


def _table_key_token(table_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", table_name or "").lower()
    for prefix in ("dim", "fact", "tbl", "table"):
        if normalized.startswith(prefix) and len(normalized) > len(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized


def _table_field_names(table: JsonDict) -> set[str]:
    fields = {_clean_field_name(field_name) for field_name in _as_list(table.get("field_names")) if _clean_field_name(field_name)}
    for field in _as_list(table.get("fields")):
        if isinstance(field, dict):
            field_name = _clean_field_name(field.get("name") or field.get("field") or "")
            if field_name:
                fields.add(field_name)
    return fields


def _visual_columns(visual: JsonDict) -> list[JsonDict]:
    columns = [item for item in _as_list(visual.get("columns_used")) if isinstance(item, dict)]
    columns.extend(item for item in _as_list(visual.get("dimensions")) if isinstance(item, dict))
    for measure in _as_list(visual.get("measures")):
        if isinstance(measure, dict):
            columns.extend(item for item in _as_list(measure.get("columns")) if isinstance(item, dict))
    return columns


def _collect_columns(tables: list[JsonDict], intermediate_model: JsonDict, mapping: JsonDict) -> list[JsonDict]:
    columns: dict[tuple[str, str], JsonDict] = {}
    strict_dataprep = bool(_as_list((intermediate_model.get("dataprep_cache_metadata") or {}).get("qvd_tables")))
    table_fields: dict[str, set[str]] = {}
    field_to_tables: dict[str, set[str]] = {}
    canonical_table_names: dict[str, str] = {}

    for table in tables:
        table_name_raw = str(table.get("table_name") or "").strip()
        table_name = table_name_raw.lower()
        if not table_name:
            continue
        canonical_table_names.setdefault(table_name, table_name_raw)
        table_fields[table_name] = {str(field_name or "").strip().lower() for field_name in _as_list(table.get("field_names")) if str(field_name or "").strip()}
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip().lower()
            if field_name:
                table_fields[table_name].add(field_name)
                field_to_tables.setdefault(field_name, set()).add(table_name)
        for field_name in table_fields[table_name]:
            field_to_tables.setdefault(field_name, set()).add(table_name)

    def table_has_field(table_name: Any, field_name: Any) -> bool:
        clean_table = str(table_name or "").strip().lower()
        clean_field = str(field_name or "").strip().lower()
        if not clean_field:
            return False
        if clean_table:
            return clean_field in table_fields.get(clean_table, set())
        return any(clean_field in fields for fields in table_fields.values())

    def field_is_unique(field_name: Any) -> bool:
        clean_field = str(field_name or "").strip().lower()
        return len(field_to_tables.get(clean_field, set())) == 1

    def resolve_table_name(table_name: Any, field_name: Any) -> str:
        clean_table = _clean_field_name(table_name or "")
        clean_field = _clean_field_name(field_name or "").lower()
        if clean_table:
            return canonical_table_names.get(clean_table.lower(), clean_table)
        candidates = sorted(field_to_tables.get(clean_field, set()))
        if candidates:
            return canonical_table_names.get(candidates[0], candidates[0])
        return "QlikModel"

    def add_column(table_name: str, field_name: str, datatype: str, role: str) -> None:
        clean_table = _clean_field_name(table_name or "")
        clean_field = _clean_field_name(field_name or "")
        if not clean_field:
            return
        resolved_table = resolve_table_name(clean_table, clean_field)
        table_key = resolved_table.lower()
        field_key = clean_field.lower()
        key = (table_key, field_key)
        role_norm = str(role or "").strip().lower()
        role_norm = role_norm if role_norm in {"dimension", "measure"} else "dimension"
        datatype_norm = _tableau_datatype(datatype)
        if key in columns:
            existing = columns[key]
            if role_norm == "measure":
                existing["datatype"] = datatype_norm if datatype_norm != "string" else "real"
                existing["role"] = "measure"
                existing["type"] = "quantitative"
            return
        display_name = _qualified_column_name(clean_field, resolved_table)
        columns[key] = {
            "caption": clean_field,
            "datatype": datatype_norm,
            "key": f"[{display_name}]",
            "role": role_norm,
            "type": "quantitative" if role_norm == "measure" else "nominal",
            "value": f"[{resolved_table}].[{clean_field}]",
        }

    for table in tables:
        table_name = str(table.get("table_name") or "").strip()
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            add_column(
                table_name,
                field.get("name") or "",
                _tableau_datatype(field.get("datatype") or field.get("data_type")),
                field.get("role") if field.get("role") in {"dimension", "measure"} else "dimension",
            )
        for field_name in _as_list(table.get("field_names")):
            add_column(table_name, field_name, "string", "dimension")

    for sheet in _as_list(mapping.get("sheets")):
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            for column in _as_list(visual.get("columns_used")):
                if not isinstance(column, dict):
                    continue
                if strict_dataprep and not table_has_field(column.get("table") or column.get("source_table") or "", column.get("column_name") or ""):
                    continue
                table_value = column.get("table") or column.get("source_table") or ""
                column_name = column.get("column_name") or ""
                add_column(
                    table_value,
                    column_name,
                    "real" if column.get("role") == "measure" else "string",
                    column.get("role") if column.get("role") in {"dimension", "measure"} else "dimension",
                )
                if strict_dataprep and table_value and field_is_unique(column_name):
                    add_column(
                        "",
                        column_name,
                        "real" if column.get("role") == "measure" else "string",
                        column.get("role") if column.get("role") in {"dimension", "measure"} else "dimension",
                    )
            for dimension in _as_list(visual.get("dimensions")):
                if isinstance(dimension, dict):
                    if strict_dataprep and not table_has_field(dimension.get("table") or "", dimension.get("field") or dimension.get("label") or ""):
                        continue
                    table_value = dimension.get("table") or ""
                    field_value = dimension.get("field") or ""
                    add_column(table_value, field_value, "string", "dimension")
                    if strict_dataprep and table_value and field_is_unique(field_value):
                        add_column("", field_value, "string", "dimension")
            for measure in _as_list(visual.get("measures")):
                if not isinstance(measure, dict):
                    continue
                expression = measure.get("tableau_expression") or measure.get("translated_tableau_expression") or measure.get("source_expression") or ""
                for field_name in _extract_fields_from_tableau_expression(expression):
                    if strict_dataprep and not table_has_field(measure.get("table") or "", field_name):
                        continue
                    add_column(measure.get("table") or "", field_name, "real", "measure")
                    if strict_dataprep and (measure.get("table") or "") and field_is_unique(field_name):
                        add_column("", field_name, "real", "measure")

    return sorted(columns.values(), key=lambda item: (item["caption"].lower(), item["key"]))


def _parse_connection_string(connection_string: Any) -> JsonDict:
    raw = str(connection_string or "").strip()
    if not raw:
        return {}
    if raw.upper().startswith("CUSTOM CONNECT TO"):
        start = raw.find('"')
        end = raw.rfind('"')
        if start >= 0 and end > start:
            raw = raw[start + 1 : end]

    parsed: JsonDict = {}
    for segment in raw.split(";"):
        if not segment or "=" not in segment:
            continue
        key, value = segment.split("=", 1)
        key = key.strip().lower()
        value = value.strip().strip('"')
        if key:
            parsed[key] = value
    return parsed


def _connection_profile(intermediate_model: JsonDict) -> JsonDict:
    connections = [item for item in _as_list(intermediate_model.get("connection_metadata") or intermediate_model.get("connections")) if isinstance(item, dict)]
    primary = next((item for item in connections if not bool(item.get("internal"))), {})
    raw_name = str(primary.get("name") or primary.get("provider") or primary.get("id") or "qlik_intermediate_model").strip()

    parsed_connection = _parse_connection_string(primary.get("connection_string"))
    server = str(parsed_connection.get("host") or parsed_connection.get("server") or primary.get("server") or "").strip()
    port = str(parsed_connection.get("port") or primary.get("port") or "").strip()
    database = str(parsed_connection.get("database") or primary.get("database") or "").strip()

    authentication = str(primary.get("authentication") or "").strip().lower()
    if not authentication:
        if parsed_connection.get("trusted_connection", "").strip().lower() in {"true", "yes"}:
            authentication = "sspi"
        elif parsed_connection.get("integrated security", "").strip().lower() in {"true", "yes"}:
            authentication = "sspi"
        else:
            authentication = "sspi"
    elif authentication in {"log_on_current_user", "trusted_connection", "integrated security"}:
        authentication = "sspi"

    if server and port and "," not in server and "\\" not in server:
        server = f"{server},{port}"

    connection_attributes = {
        "class": "sqlserver",
        "authentication": authentication,
        "minimum-driver-version": "SQL Server Native Client 10.0",
        "odbc-native-protocol": "yes",
    }

    if server:
        connection_attributes["server"] = server
    if database:
        connection_attributes["dbname"] = database

    caption = str(primary.get("name") or primary.get("provider") or "Qlik Intermediate Model").strip() or "Qlik Intermediate Model"
    connection_name = f"sqlserver.{_safe_worksheet_name(raw_name).lower().replace(' ', '_')}"
    return {"name": connection_name, "caption": caption, "connection": connection_attributes}


def _dataprep_tableau_datatype(data_type: str) -> str:
    normalized = str(data_type or "").strip().lower()
    if normalized == "date_or_calendar":
        return "date"
    if normalized in {"key"}:
        return "integer"
    return "string"


def _is_internal_dataprep_field(field: JsonDict) -> bool:
    field_name = _clean_field_name(field.get("name") or "")
    if not field_name:
        return True
    if re.match(r"^Extra_[0-9A-Fa-f-]{12,}$", field_name):
        return True
    return False


def _dataprep_role(data_type: str) -> str:
    # DataPrep QVD headers use measure_candidate for some categorical text fields.
    # Visual expressions are a safer source of truth for promoting fields to measures.
    return "dimension"


def _append_dependency_bindings(deps_node: ET.Element, visual: JsonDict) -> None:
    seen: set[tuple[str, str]] = set()

    def bind(column: JsonDict, role: str = "", datatype: str = "", expression: Any = "") -> None:
        field_name, table_name = _field_and_table_from_contract(column)
        if not field_name:
            return
        role_norm = role or str(column.get("role") or "").strip().lower()
        role_norm = role_norm if role_norm in {"dimension", "measure"} else "dimension"
        datatype_value = _tableau_datatype(datatype or column.get("datatype") or column.get("data_type") or ("real" if role_norm == "measure" else "string"))
        column_ref = _qualified_column_ref(field_name, table_name)
        instance_name = str(column.get("tableau_instance_name") or "").strip()
        if not instance_name:
            instance_name = _column_instance_name(field_name, table_name, role_norm, expression, datatype_value)
        key = (column_ref.lower(), role_norm)
        if key in seen:
            return
        seen.add(key)

        if role_norm == "measure":
            ET.SubElement(
                deps_node,
                "column",
                attrib={
                    "name": column_ref,
                    "role": "measure",
                    "datatype": datatype_value if datatype_value != "string" else "real",
                    "type": "quantitative",
                    "caption": field_name,
                },
            )
            ET.SubElement(
                deps_node,
                "column-instance",
                attrib={
                    "column": column_ref,
                    "derivation": _derivation_from_expression(expression),
                    "name": instance_name,
                    "pivot": "key",
                    "type": "quantitative",
                },
            )
            return

        instance_type = _dimension_type_from_datatype(datatype_value)
        instance_suffix = "qk" if instance_type == "quantitative" else "nk"
        if not str(column.get("tableau_instance_name") or "").strip():
            instance_name = _column_instance_name(field_name, table_name, role_norm, expression, datatype_value, instance_suffix)
        ET.SubElement(
            deps_node,
            "column",
            attrib={
                "name": column_ref,
                "role": "dimension",
                "datatype": datatype_value,
                "type": instance_type,
                "caption": field_name,
            },
        )
        ET.SubElement(
            deps_node,
            "column-instance",
            attrib={"column": column_ref, "derivation": "None", "name": instance_name, "pivot": "key", "type": instance_type},
        )

    for column in _as_list(visual.get("columns_used")):
        if not isinstance(column, dict):
            continue
        bind(
            column,
            column.get("role") or "dimension",
            "real" if column.get("role") == "measure" else "string",
            column.get("tableau_expression") or column.get("translated_tableau_expression") or column.get("original_qlik_expression") or "",
        )

    for dimension in _as_list(visual.get("dimensions")):
        if not isinstance(dimension, dict):
            continue
        bind(dimension, "dimension", "string", dimension.get("tableau_expression") or "")

    for measure in _as_list(visual.get("measures")):
        if not isinstance(measure, dict):
            continue
        expression = measure.get("tableau_expression") or measure.get("translated_tableau_expression") or measure.get("source_expression") or ""
        measure_columns = [item for item in _as_list(measure.get("columns")) if isinstance(item, dict)]
        if measure_columns:
            for column in measure_columns:
                bind(column, "measure", "real", expression)
            continue

        extracted_fields = _extract_fields_from_tableau_expression(expression)
        if extracted_fields:
            for field_name in extracted_fields:
                field, table = _split_qualified_display_field(field_name)
                bind({"column_name": field, "table": table}, "measure", "real", expression)
        else:
            bind({"column_name": measure.get("label") or expression or ""}, "measure", "real", expression)


def _field_and_table_from_contract(column: JsonDict) -> tuple[str, str]:
    field = _clean_field_name(column.get("column_name") or column.get("field") or "")
    table = _clean_field_name(column.get("table") or column.get("source_table") or "")
    if field and table:
        return field, table

    for key in ("tableau_field", "qualified_column_name"):
        parsed_field, parsed_table = _split_qualified_display_field(column.get(key) or "")
        if parsed_field:
            field = field or parsed_field
        if parsed_table:
            table = table or parsed_table
    return field, table or "QlikModel"


def _qualified_column_name(field_name: Any, table_name: Any) -> str:
    field = _clean_field_name(field_name)
    if not field:
        return ""
    table = _clean_field_name(table_name) or "QlikModel"
    return f"{field} ({table})"


def _qualified_column_ref(field_name: Any, table_name: Any) -> str:
    qualified = _qualified_column_name(field_name, table_name)
    return f"[{qualified}]" if qualified else ""


def _instance_name_from_contract(column: JsonDict, role: str, expression: Any = "") -> str:
    existing = str(column.get("tableau_instance_name") or "").strip()
    if existing:
        return existing
    field, table = _field_and_table_from_contract(column)
    datatype = str(column.get("datatype") or column.get("data_type") or "").strip()
    return _column_instance_name(field, table, role, expression, datatype)


def _column_instance_name(
    field_name: Any,
    table_name: Any,
    role: str,
    expression: Any = "",
    datatype: Any = "",
    dimension_suffix: str = "",
) -> str:
    field = _clean_field_name(field_name)
    if not field:
        return ""
    table = _clean_field_name(table_name) or "QlikModel"
    token = re.sub(r"[^A-Za-z0-9_]+", "_", f"{field}__{table}").strip("_") or "Field"
    if role == "measure":
        return f"[{_aggregation_prefix(expression)}:{token}:qk]"
    suffix = dimension_suffix or ("qk" if _dimension_type_from_datatype(datatype) == "quantitative" else "nk")
    return f"[none:{token}:{suffix}]"


def _split_qualified_display_field(value: Any) -> tuple[str, str]:
    text = _clean_field_name(value)
    match = re.match(r"^(.*?)\s+\(([^()]+)\)$", text)
    if match:
        return _clean_field_name(match.group(1)), _clean_field_name(match.group(2))
    return text, ""


def _aggregation_prefix(expression: Any) -> str:
    text = str(expression or "").strip()
    match = re.match(r"([A-Za-z]+)\s*\(", text)
    if not match:
        return "sum"
    normalized = match.group(1).strip().lower()
    return {
        "avg": "avg",
        "average": "avg",
        "count": "cnt",
        "countd": "ctd",
        "min": "min",
        "max": "max",
        "sum": "sum",
    }.get(normalized, "sum")


def _derivation_from_expression(expression: Any) -> str:
    return {
        "avg": "Avg",
        "cnt": "Count",
        "ctd": "CountD",
        "min": "Min",
        "max": "Max",
        "sum": "Sum",
    }.get(_aggregation_prefix(expression), "Sum")


def _dimension_type_from_datatype(datatype: Any) -> str:
    normalized = str(datatype or "").strip().lower()
    if normalized in {"real", "integer", "float", "decimal", "number", "numeric"}:
        return "quantitative"
    if normalized in {"date", "datetime", "timestamp"}:
        return "ordinal"
    return "nominal"


def _dimension_shelf(datasource_name: str, visual: JsonDict) -> str:
    dimensions = [item for item in _as_list(visual.get("dimensions")) if isinstance(item, dict)]
    if dimensions:
        instance_name = _instance_name_from_contract(dimensions[0], "dimension", dimensions[0].get("tableau_expression") or "")
        return f"[{datasource_name}].{instance_name}" if instance_name else ""

    dimension_columns = [
        item
        for item in _as_list(visual.get("columns_used"))
        if isinstance(item, dict) and str(item.get("role") or "").strip().lower() == "dimension"
    ]
    if not dimension_columns:
        return ""
    instance_name = _instance_name_from_contract(dimension_columns[0], "dimension", dimension_columns[0].get("tableau_expression") or "")
    return f"[{datasource_name}].{instance_name}" if instance_name else ""


def _measure_shelf(datasource_name: str, visual: JsonDict) -> str:
    measures = [item for item in _as_list(visual.get("measures")) if isinstance(item, dict)]
    if measures:
        expression = str(measures[0].get("tableau_expression") or measures[0].get("translated_tableau_expression") or "")
        measure_columns = [item for item in _as_list(measures[0].get("columns")) if isinstance(item, dict)]
        if measure_columns:
            instance_name = _instance_name_from_contract(measure_columns[0], "measure", expression)
            return f"[{datasource_name}].{instance_name}" if instance_name else ""
        fields = _extract_fields_from_tableau_expression(expression)
        if fields:
            field, table = _split_qualified_display_field(fields[0])
            instance_name = _column_instance_name(field, table, "measure", expression, "real")
            return f"[{datasource_name}].{instance_name}" if instance_name else ""

    measure_columns = [
        item
        for item in _as_list(visual.get("columns_used"))
        if isinstance(item, dict) and str(item.get("role") or "").strip().lower() == "measure"
    ]
    if not measure_columns:
        return ""
    expression = str(measure_columns[0].get("tableau_expression") or measure_columns[0].get("translated_tableau_expression") or "")
    instance_name = _instance_name_from_contract(measure_columns[0], "measure", expression)
    return f"[{datasource_name}].{instance_name}" if instance_name else ""


def _mark_class(tableau_type: Any) -> str:
    return {
        "bar_chart": "Bar",
        "line_chart": "Line",
        "area_chart": "Area",
        "kpi": "Text",
        "table": "Text",
        "text_table": "Text",
        "filter": "Text",
        "pie_chart": "Pie",
        "scatter_plot": "Circle",
        "map": "Map",
        "treemap": "Square",
        "box_plot": "Circle",
        "histogram": "Bar",
    }.get(str(tableau_type or ""), "Automatic")


def _extract_fields_from_tableau_expression(expression: Any) -> list[str]:
    return [_clean_field_name(match) for match in re.findall(r"\[([^\]]+)\]", str(expression or ""))]


def _tableau_datatype(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"integer", "int", "number", "numeric", "key"}:
        return "integer"
    if normalized in {"float", "decimal", "double", "real", "money"}:
        return "real"
    if normalized in {"date", "datetime", "timestamp"}:
        return "date"
    return "string"


def _safe_worksheet_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"[\[\]*/\\?:]", "_", text)
    return text[:80] or "Worksheet"


def _clean_field_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
