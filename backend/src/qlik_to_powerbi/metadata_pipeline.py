from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from src.qlik_to_twb.metadata_pipeline import (
    run_qlik_metadata_job,
    run_uploaded_qlik_metadata_job,
)
from src.qlik_to_twb.pipeline import normalize_qlik_metadata
from src.qlik_to_twb.qlik_client import QlikEngineApiClient


JsonDict = dict[str, Any]


POWERBI_VISUAL_TYPE_MAP = {
    "barchart": "clusteredColumnChart",
    "bar-chart": "clusteredColumnChart",
    "linechart": "lineChart",
    "line-chart": "lineChart",
    "combochart": "lineClusteredColumnComboChart",
    "piechart": "pieChart",
    "scatterplot": "scatterChart",
    "treemap": "treemap",
    "map": "map",
    "kpi": "card",
    "gauge": "gauge",
    "table": "tableEx",
    "straighttable": "tableEx",
    "sn-table": "tableEx",
    "pivot-table": "pivotTable",
    "pivot_table": "pivotTable",
    "filterpane": "slicer",
    "listbox": "slicer",
}


def run_qlik_powerbi_metadata_job(
    source_qvf_path: str | Path,
    jobs_root: str | Path,
    job_id: str | None = None,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    dataprep_cache_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
    request_timeout_seconds: float = 30.0,
) -> JsonDict:
    result = run_qlik_metadata_job(
        source_qvf_path=source_qvf_path,
        jobs_root=jobs_root,
        job_id=job_id,
        qlik_client=qlik_client,
        qlik_endpoint=qlik_endpoint,
        qlik_apps_dir=qlik_apps_dir,
        dataprep_cache_dir=dataprep_cache_dir,
        qlik_user_directory=qlik_user_directory,
        qlik_user_id=qlik_user_id,
        qlik_session_cookie=qlik_session_cookie,
        request_timeout_seconds=request_timeout_seconds,
    )
    return _append_powerbi_intermediate_model(result)


def run_uploaded_qlik_powerbi_metadata_job(
    file_name: str,
    file_bytes: bytes,
    jobs_root: str | Path,
    job_id: str | None = None,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    dataprep_cache_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
    request_timeout_seconds: float = 30.0,
) -> JsonDict:
    result = run_uploaded_qlik_metadata_job(
        file_name=file_name,
        file_bytes=file_bytes,
        jobs_root=jobs_root,
        job_id=job_id,
        qlik_client=qlik_client,
        qlik_endpoint=qlik_endpoint,
        qlik_apps_dir=qlik_apps_dir,
        dataprep_cache_dir=dataprep_cache_dir,
        qlik_user_directory=qlik_user_directory,
        qlik_user_id=qlik_user_id,
        qlik_session_cookie=qlik_session_cookie,
        request_timeout_seconds=request_timeout_seconds,
    )
    return _append_powerbi_intermediate_model(result)


def normalize_qlik_to_powerbi_model(qlik_metadata: JsonDict) -> JsonDict:
    visual_objects = _as_list(qlik_metadata.get("visual_objects"))
    sheets_source = _as_list(qlik_metadata.get("sheets"))
    expression_catalog: list[JsonDict] = []
    dimensions: dict[str, JsonDict] = {}
    measures: dict[str, JsonDict] = {}
    filters: list[JsonDict] = []
    filter_keys: set[str] = set()

    normalized_visuals = [
        _normalize_visual(
            visual=visual,
            index=index,
            dimensions=dimensions,
            measures=measures,
            filters=filters,
            filter_keys=filter_keys,
            expression_catalog=expression_catalog,
        )
        for index, visual in enumerate(visual_objects)
        if isinstance(visual, dict)
    ]
    visuals_by_sheet: dict[str, list[JsonDict]] = {}
    for visual in normalized_visuals:
        visuals_by_sheet.setdefault(str(visual.get("sheet_id") or ""), []).append(visual)

    sheets: list[JsonDict] = []
    for index, sheet in enumerate(sheets_source):
        if not isinstance(sheet, dict):
            continue
        sheet_id = str(sheet.get("id") or f"sheet_{index + 1}")
        sheet_visuals = visuals_by_sheet.get(sheet_id, [])
        sheets.append(
            {
                "id": sheet_id,
                "title": sheet.get("title") or sheet_id,
                "rank": sheet.get("rank", index),
                "visual_count": len(sheet_visuals),
                "visuals": sheet_visuals,
            }
        )

    known_sheet_ids = {str(sheet.get("id") or "") for sheet in sheets}
    unassigned_visuals = [
        visual
        for sheet_id, sheet_visuals in visuals_by_sheet.items()
        if sheet_id not in known_sheet_ids
        for visual in sheet_visuals
    ]
    if unassigned_visuals:
        sheets.append(
            {
                "id": "__unassigned__",
                "title": "Unassigned visuals",
                "rank": len(sheets),
                "visual_count": len(unassigned_visuals),
                "visuals": unassigned_visuals,
            }
        )

    for index, master_dimension in enumerate(_as_list(qlik_metadata.get("master_dimensions"))):
        if not isinstance(master_dimension, dict):
            continue
        field_name = _first_non_empty(master_dimension.get("field"), *_as_list(master_dimension.get("fields")))
        if field_name:
            _register_dimension(
                dimensions,
                {
                    "name": field_name,
                    "label": master_dimension.get("title") or field_name,
                    "fields": _dedupe_strings([field_name, *_as_list(master_dimension.get("fields"))]),
                    "source": "master_dimension",
                    "library_id": master_dimension.get("id", ""),
                    "powerbi_role_hint": "Category",
                },
            )
        expression = str(master_dimension.get("expression") or "").strip()
        if expression:
            _add_expression(
                expression_catalog,
                source="master_dimension",
                owner_id=str(master_dimension.get("id") or f"master_dimension_{index + 1}"),
                owner_title=str(master_dimension.get("title") or field_name or ""),
                expression=expression,
            )

    for index, master_measure in enumerate(_as_list(qlik_metadata.get("master_measures"))):
        if not isinstance(master_measure, dict):
            continue
        expression = str(master_measure.get("expression") or "").strip()
        label = str(master_measure.get("title") or master_measure.get("label") or expression or f"Measure {index + 1}")
        measure = _measure_payload(
            label=label,
            expression=expression,
            source="master_measure",
            owner_id=str(master_measure.get("id") or f"master_measure_{index + 1}"),
        )
        _register_measure(measures, measure)
        _add_expression(
            expression_catalog,
            source="master_measure",
            owner_id=measure["id"],
            owner_title=label,
            expression=expression,
        )

    load_script = str(qlik_metadata.get("load_script") or "")
    load_script_filters = _extract_load_script_filters(load_script)
    for filter_item in load_script_filters:
        _add_filter(filters, filter_keys, filter_item)

    variables = []
    for index, variable in enumerate(_as_list(qlik_metadata.get("variables"))):
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("name") or f"variable_{index + 1}")
        definition = str(variable.get("definition") or "")
        variables.append(
            {
                "name": name,
                "definition": definition,
                "comment": variable.get("comment") or "",
                "referenced_fields": _expression_fields(definition),
                "powerbi_hint": {
                    "target": "DAX measure or parameter",
                    "requires_review": bool(definition.strip()),
                },
            }
        )
        if definition.strip():
            _add_expression(
                expression_catalog,
                source="variable",
                owner_id=_slug(name, f"variable_{index + 1}"),
                owner_title=name,
                expression=definition,
            )

    # The Power BI data model must stay grounded in physical QVD cache tables.
    # Prefer QVD cache tables, but also incorporate any tables discovered by
    # load-script analysis (tables_and_fields) so the UI can show both sources.
    qvd_tables = _tables_from_dataprep_cache(dict(qlik_metadata.get("dataprep_cache") or {}))

    # Convert any tables_and_fields entries into the same shape as dataprep qvd tables
    script_tables: list[JsonDict] = []
    for t in _as_list(qlik_metadata.get("tables_and_fields")):
        if not isinstance(t, dict):
            continue
        table_name = str(t.get("name") or t.get("table_name") or "").strip()
        if not table_name:
            continue
        fields: list[JsonDict] = []
        for f in _as_list(t.get("fields")):
            if isinstance(f, dict):
                fname = str(f.get("name") or f.get("field") or f.get("label") or "").strip()
                dtype = f.get("data_type") or _infer_data_type(fname)
            else:
                fname = str(f or "").strip()
                dtype = _infer_data_type(fname)
            if fname:
                fields.append({"name": fname, "data_type": dtype})
        script_tables.append(
            {
                "name": table_name,
                "source": "load_script",
                "fields": _dedupe_fields(fields),
                "field_names": [field["name"] for field in _dedupe_fields(fields)],
            }
        )

    # Merge QVD-derived tables with script-discovered tables (script tables supplement QVDs)
    tables = _merge_tables(qvd_tables, script_tables)

    # Relationships: include explicit relationships from qlik metadata, merge
    # relationships discovered earlier in extract_qlik_metadata (semantic_model),
    # and infer table relationships from shared fields in the merged model.
    relationships = _explicit_relationships_from_metadata(qlik_metadata, tables)
    for rel in _as_list((qlik_metadata.get("semantic_model") or {}).get("relationships")):
        if not isinstance(rel, dict):
            continue
        # ensure required fields and that tables exist in the merged tables
        from_table = str(rel.get("from_table") or rel.get("fromTable") or "").strip()
        to_table = str(rel.get("to_table") or rel.get("toTable") or "").strip()
        from_column = str(rel.get("from_column") or rel.get("fromColumn") or "").strip()
        to_column = str(rel.get("to_column") or rel.get("toColumn") or "").strip()
        table_names = {str(t.get("name") or "") for t in tables}
        if (
            from_table
            and to_table
            and from_column
            and to_column
            and from_table in table_names
            and to_table in table_names
            and _is_business_table_name(from_table)
            and _is_business_table_name(to_table)
        ):
            relationships.append(
                {
                    "id": _slug(f"{from_table}_{from_column}_{to_table}_{to_column}", None),
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                    "cardinality": rel.get("cardinality") or rel.get("relationship_type") or "",
                    "source": rel.get("source") or "inferred_from_script",
                }
            )

    relationships.extend(_inferred_relationships_from_tables(tables, relationships))
    relationships = _dedupe_relationships(relationships)

    fields = _fields_from_tables_and_catalogs(tables, {}, {})
    source = dict(qlik_metadata.get("source") or {})
    app = dict(qlik_metadata.get("app") or {})
    connections = _as_list(qlik_metadata.get("connections"))
    summary = {
        "sheet_count": len(sheets),
        "visual_count": len(normalized_visuals),
        "dimension_count": len(dimensions),
        "measure_count": len(measures),
        "expression_count": len(expression_catalog),
        "filter_count": len(filters),
        "variable_count": len(variables),
        "connection_count": len(connections),
        "table_count": len(tables),
        "relationship_count": len(relationships),
        "load_script_line_count": len([line for line in load_script.splitlines() if line.strip()]),
        "load_script_bytes": len(load_script.encode("utf-8")),
    }

    return {
        "schema_version": "qlik_powerbi_intermediate/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "target": {
            "platform": "power_bi",
            "model_type": "semantic_model_and_report",
            "next_step": "llm_powerbi_equivalence_generation",
        },
        "source": {
            **source,
            "app_title": app.get("title") or app.get("file_name") or source.get("app_id") or "",
        },
        "app": app,
        "report": {
            "sheets": sheets,
            "visuals": normalized_visuals,
        },
        "semantic_model": {
            "tables": tables,
            "fields": fields,
            "relationships": relationships,
            "dimensions": sorted(dimensions.values(), key=lambda item: item["name"].lower()),
            "measures": sorted(measures.values(), key=lambda item: item["name"].lower()),
            "variables": variables,
            "filters": filters,
            "connections": connections,
        },
        "expressions": expression_catalog,
        "load_script": {
            "text": load_script,
            "line_count": summary["load_script_line_count"],
            "tables_detected": [table.get("name") for table in tables],
            "table_source": "dataprep_qvd",
            "filters": load_script_filters,
        },
        "llm_context": {
            "source_platform": "Qlik Sense",
            "target_platform": "Power BI",
            "required_equivalences": [
                "visual_type_mapping",
                "field_and_table_mapping",
                "DAX_measure_generation",
                "slicer_and_filter_mapping",
                "load_script_to_power_query_or_model_notes",
            ],
            "preferred_inputs": [
                "report.sheets",
                "semantic_model.tables",
                "semantic_model.measures",
                "semantic_model.filters",
                "expressions",
                "load_script",
            ],
        },
        "summary": summary,
    }

def _append_powerbi_intermediate_model(job_result: JsonDict) -> JsonDict:
    result = dict(job_result)
    metadata_path = Path(str(result.get("qlik_metadata") or ""))
    if not metadata_path.exists():
        return result

    qlik_metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    # Prefer the normalized TWB intermediate extraction for more complete table/field discovery.
    try:
        twb_intermediate = normalize_qlik_metadata(qlik_metadata if isinstance(qlik_metadata, dict) else {})
        # Ensure tables_and_fields is present for the PowerBI normalizer by deriving
        # it from the TWB intermediate 'tables' (which merges dataprep and script findings).
        derived_tables_and_fields = []
        for t in _as_list(twb_intermediate.get("tables") or []):
            if not isinstance(t, dict):
                continue
            derived_tables_and_fields.append({
                "name": t.get("name") or t.get("table_name") or "",
                "fields": t.get("fields") or [],
            })
        # Merge into a shallow copy of original qlik metadata so downstream code sees both sources
        merged_qlik_meta = dict(qlik_metadata if isinstance(qlik_metadata, dict) else {})
        if derived_tables_and_fields:
            merged_qlik_meta["tables_and_fields"] = derived_tables_and_fields
        intermediate_model = normalize_qlik_to_powerbi_model(merged_qlik_meta)
    except Exception:
        # Fallback to existing behavior if normalization fails
        intermediate_model = normalize_qlik_to_powerbi_model(qlik_metadata if isinstance(qlik_metadata, dict) else {})
    intermediate_path = metadata_path.parent / "powerbi_intermediate_model.json"
    _write_json(intermediate_path, intermediate_model)

    result["powerbi_intermediate_model"] = str(intermediate_path)
    result["intermediate_model_data"] = intermediate_model
    result["summary"] = {
        **dict(result.get("summary") or {}),
        **dict(intermediate_model.get("summary") or {}),
    }
    trace_steps = _as_list(result.get("trace_steps"))
    trace_steps.append("Power BI intermediate model normalized and written")
    result["trace_steps"] = trace_steps

    job_path = Path(str(result.get("job") or metadata_path.parent / "job.json"))
    result["job"] = str(job_path)
    _write_json(job_path, result)
    return result


def _normalize_visual(
    visual: JsonDict,
    index: int,
    dimensions: dict[str, JsonDict],
    measures: dict[str, JsonDict],
    filters: list[JsonDict],
    filter_keys: set[str],
    expression_catalog: list[JsonDict],
) -> JsonDict:
    qlik_id = str(visual.get("id") or f"visual_{index + 1}")
    visual_id = _slug(qlik_id, f"visual_{index + 1}")
    qlik_type = str(visual.get("type") or visual.get("source_type") or "").strip()
    powerbi_type = POWERBI_VISUAL_TYPE_MAP.get(qlik_type.lower(), "customVisual")
    normalized_dimensions = []
    normalized_measures = []

    for dim_index, dimension in enumerate(_as_list(visual.get("dimensions"))):
        if not isinstance(dimension, dict):
            continue
        payload = _dimension_payload(dimension=dimension, owner_id=visual_id, index=dim_index)
        normalized_dimensions.append(payload)
        if payload.get("name"):
            _register_dimension(dimensions, payload)
        if payload.get("expression"):
            _add_expression(
                expression_catalog,
                source="visual_dimension",
                owner_id=visual_id,
                owner_title=str(visual.get("title") or qlik_id),
                expression=str(payload.get("expression") or ""),
            )

    for measure_index, measure in enumerate(_as_list(visual.get("measures"))):
        if not isinstance(measure, dict):
            continue
        label = str(measure.get("label") or measure.get("title") or measure.get("expression") or f"Measure {measure_index + 1}")
        expression = str(measure.get("expression") or "").strip()
        payload = _measure_payload(
            label=label,
            expression=expression,
            source=str(measure.get("source") or "visual_measure"),
            owner_id=f"{visual_id}_measure_{measure_index + 1}",
        )
        normalized_measures.append(payload)
        _register_measure(measures, payload)
        _add_expression(
            expression_catalog,
            source="visual_measure",
            owner_id=visual_id,
            owner_title=str(visual.get("title") or qlik_id),
            expression=expression,
        )

    visual_filters = _filters_from_visual(
        visual=visual,
        visual_id=visual_id,
        dimensions=normalized_dimensions,
        measures=normalized_measures,
    )
    for filter_item in visual_filters:
        _add_filter(filters, filter_keys, filter_item)

    return {
        "id": visual_id,
        "qlik_id": qlik_id,
        "sheet_id": str(visual.get("sheet_id") or ""),
        "title": visual.get("title") or qlik_id,
        "qlik_type": qlik_type,
        "powerbi_visual_type_hint": powerbi_type,
        "dimensions": normalized_dimensions,
        "measures": normalized_measures,
        "filters": visual_filters,
        "expressions": [
            item
            for item in [
                *(dimension.get("expression") for dimension in normalized_dimensions),
                *(measure.get("qlik_expression") for measure in normalized_measures),
            ]
            if str(item or "").strip()
        ],
        "qix": dict(visual.get("qix") or {}),
    }


def _dimension_payload(dimension: JsonDict, owner_id: str, index: int) -> JsonDict:
    fields = _dedupe_strings([dimension.get("field"), *_as_list(dimension.get("fields"))])
    expression = str(dimension.get("expression") or "").strip()
    name = fields[0] if fields else expression or str(dimension.get("label") or f"Dimension {index + 1}")
    return {
        "id": _slug(f"{owner_id}_dimension_{name}", f"{owner_id}_dimension_{index + 1}"),
        "name": name,
        "label": dimension.get("label") or name,
        "fields": fields,
        "expression": expression,
        "role": "dimension",
        "source": dimension.get("source") or "",
        "library_id": dimension.get("library_id") or "",
        "powerbi_role_hint": "Category",
    }


def _measure_payload(label: str, expression: str, source: str, owner_id: str) -> JsonDict:
    name = label or expression or owner_id
    return {
        "id": _slug(f"{owner_id}_{name}", owner_id or "measure"),
        "name": name,
        "label": label or name,
        "qlik_expression": expression,
        "referenced_fields": _expression_fields(expression),
        "aggregation_hint": _aggregation_hint(expression or label),
        "source": source,
        "powerbi_hint": {
            "target": "DAX measure",
            "requires_set_analysis_review": bool(_set_analysis_filters(expression)),
        },
    }


def _filters_from_visual(visual: JsonDict, visual_id: str, dimensions: list[JsonDict], measures: list[JsonDict]) -> list[JsonDict]:
    filters: list[JsonDict] = []
    visual_type = str(visual.get("type") or visual.get("source_type") or "").lower()
    if "filter" in visual_type or visual_type in {"listbox", "slicer"}:
        for dimension in dimensions:
            field_name = str(dimension.get("name") or "").strip()
            if not field_name:
                continue
            filters.append(
                {
                    "id": _slug(f"{visual_id}_filter_{field_name}", f"{visual_id}_filter"),
                    "origin": "filter_pane_visual",
                    "scope": "report_visual",
                    "visual_id": visual_id,
                    "field": field_name,
                    "label": dimension.get("label") or field_name,
                    "operator": "in",
                    "values": [],
                    "powerbi_hint": "slicer",
                }
            )

    for measure in measures:
        for filter_item in _set_analysis_filters(measure.get("qlik_expression")):
            filters.append({**filter_item, "visual_id": visual_id, "scope": "visual_measure"})
    for dimension in dimensions:
        for filter_item in _set_analysis_filters(dimension.get("expression")):
            filters.append({**filter_item, "visual_id": visual_id, "scope": "visual_dimension"})
    return filters


def _set_analysis_filters(expression: Any) -> list[JsonDict]:
    text = str(expression or "")
    filters = []
    for block in re.findall(r"\{\s*<([^>]*)>\s*\}", text):
        for clause in _split_filter_clauses(block):
            if "=" not in clause:
                continue
            field, value = clause.split("=", 1)
            field = field.strip().strip("[]")
            raw_value = value.strip()
            if not field:
                continue
            values = [
                item.strip().strip("'\"")
                for item in re.findall(r"['\"]([^'\"]+)['\"]|([^,{}]+)", raw_value)
                for item in item
                if str(item or "").strip()
            ]
            filters.append(
                {
                    "id": _slug(f"set_analysis_{field}_{raw_value}", "set_analysis_filter"),
                    "origin": "set_analysis",
                    "field": field,
                    "operator": "set_modifier",
                    "values": values,
                    "raw": clause.strip(),
                    "powerbi_hint": "visual/page filter or DAX CALCULATE filter",
                }
            )
    return filters


def _split_filter_clauses(block: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for char in str(block or ""):
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "{[(":
            depth += 1
        elif char in "}])" and depth:
            depth -= 1
        if char == "," and depth == 0:
            text = "".join(current).strip()
            if text:
                parts.append(text)
            current = []
            continue
        current.append(char)
    text = "".join(current).strip()
    if text:
        parts.append(text)
    return parts


def _extract_load_script_filters(load_script: str) -> list[JsonDict]:
    filters = []
    for index, match in enumerate(re.finditer(r"(?im)^\s*WHERE\s+(.+?);?\s*$", str(load_script or ""))):
        condition = match.group(1).strip()
        filters.append(
            {
                "id": _slug(f"load_script_where_{index + 1}_{condition}", f"load_script_where_{index + 1}"),
                "origin": "load_script_where",
                "scope": "data_load",
                "field": _first_field_reference(condition),
                "operator": "where",
                "values": [],
                "raw": condition,
                "powerbi_hint": "Power Query filter step",
            }
        )
    return filters


def _tables_from_dataprep_cache(dataprep_cache: JsonDict) -> list[JsonDict]:
    tables = []
    for table in _as_list(dataprep_cache.get("qvd_tables")):
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("table_name") or Path(str(table.get("file_name") or "")).stem).strip()
        if not table_name:
            continue
        fields = []
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip()
            if field_name:
                fields.append(
                    {
                        "name": field_name,
                        "data_type": field.get("data_type") or _infer_data_type(field_name),
                        "role": field.get("role") or "",
                    }
                )
        tables.append(
            {
                "name": table_name,
                "source": "dataprep_qvd",
                "file_name": table.get("file_name", ""),
                "record_count": table.get("record_count", 0),
                "fields": _dedupe_fields(fields),
                "field_names": [field["name"] for field in _dedupe_fields(fields)],
            }
        )
    return tables


def _merge_tables(*groups: list[JsonDict]) -> list[JsonDict]:
    merged: dict[str, JsonDict] = {}
    for table in [item for group in groups for item in group if isinstance(item, dict)]:
        table_name = str(table.get("name") or table.get("table_name") or "").strip()
        if not table_name:
            continue
        entry = merged.setdefault(
            table_name.lower(),
            {
                "name": table_name,
                "source": table.get("source") or "",
                "fields": [],
                "field_names": [],
            },
        )
        for key in ("source", "source_table", "file_name", "record_count"):
            if table.get(key) not in (None, ""):
                entry[key] = table.get(key)
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip()
            if field_name and field_name not in entry["field_names"]:
                entry["field_names"].append(field_name)
                entry["fields"].append(field)
        for field_name in _as_list(table.get("field_names")):
            text = str(field_name or "").strip()
            if text and text not in entry["field_names"]:
                entry["field_names"].append(text)
                entry["fields"].append({"name": text, "data_type": _infer_data_type(text)})
    return sorted(merged.values(), key=lambda item: item["name"].lower())


def _explicit_relationships_from_metadata(qlik_metadata: JsonDict, tables: list[JsonDict]) -> list[JsonDict]:
    table_names = {str(table.get("name") or table.get("table_name") or "").strip() for table in tables}
    table_names.discard("")
    relationships = []
    for index, relationship in enumerate(_as_list(qlik_metadata.get("relationships"))):
        if not isinstance(relationship, dict):
            continue
        from_table = str(relationship.get("from_table") or relationship.get("fromTable") or "").strip()
        to_table = str(relationship.get("to_table") or relationship.get("toTable") or "").strip()
        from_column = str(relationship.get("from_column") or relationship.get("fromColumn") or "").strip()
        to_column = str(relationship.get("to_column") or relationship.get("toColumn") or "").strip()
        if not from_table or not to_table or not from_column or not to_column:
            continue
        if from_table not in table_names or to_table not in table_names:
            continue
        relationships.append(
            {
                "id": _slug(f"{from_table}_{from_column}_{to_table}_{to_column}", f"relationship_{index + 1}"),
                "from_table": from_table,
                "from_column": from_column,
                "to_table": to_table,
                "to_column": to_column,
                "cardinality": relationship.get("cardinality") or relationship.get("relationship_type") or "",
                "source": relationship.get("source") or "explicit_qlik_metadata",
            }
        )
    return relationships


def _inferred_relationships_from_tables(tables: list[JsonDict], existing_relationships: list[JsonDict]) -> list[JsonDict]:
    table_by_name = {
        str(table.get("name") or "").strip(): table
        for table in tables
        if isinstance(table, dict) and str(table.get("name") or "").strip()
    }
    table_field_counts = {
        name: len(_dedupe_fields(_as_list(table.get("fields"))))
        for name, table in table_by_name.items()
    }
    existing_keys = {
        (
            str(rel.get("from_table") or "").strip(),
            str(rel.get("from_column") or "").strip(),
            str(rel.get("to_table") or "").strip(),
            str(rel.get("to_column") or "").strip(),
        )
        for rel in existing_relationships
        if isinstance(rel, dict)
    }

    field_to_tables: dict[str, list[str]] = {}
    for table_name, table in table_by_name.items():
        for field in _dedupe_fields(_as_list(table.get("fields"))):
            field_name = str(field.get("name") if isinstance(field, dict) else field or "").strip()
            if not field_name:
                continue
            field_to_tables.setdefault(field_name, []).append(table_name)

    inferred_relationships: list[JsonDict] = []
    for field_name, shared_tables in field_to_tables.items():
        shared_tables = [table_name for table_name in shared_tables if _is_business_table_name(table_name)]
        if len(shared_tables) < 2:
            continue

        def _table_priority(table_name: str) -> tuple[int, int, str]:
            normalized = table_name.lower()
            field_count = table_field_counts.get(table_name, 0)
            fact_bonus = 50 if normalized.startswith("fact") else 25 if "fact" in normalized else 0
            dimension_bonus = 10 if normalized.startswith("dim") else 0
            return (fact_bonus + field_count, dimension_bonus, table_name)

        source_table = sorted(shared_tables, key=_table_priority, reverse=True)[0]
        for target_table in shared_tables:
            if target_table == source_table:
                continue
            relationship_key = (source_table, field_name, target_table, field_name)
            if relationship_key in existing_keys:
                continue
            inferred_relationships.append(
                {
                    "id": _slug(f"{source_table}_{field_name}_{target_table}_{field_name}", None),
                    "from_table": source_table,
                    "from_column": field_name,
                    "to_table": target_table,
                    "to_column": field_name,
                    "cardinality": "many_to_one",
                    "source": "inferred_from_shared_field",
                }
            )
            existing_keys.add(relationship_key)

    return inferred_relationships


def _is_business_table_name(table_name: str) -> bool:
    normalized = str(table_name or "").strip().lower()
    return normalized.startswith("dim") or normalized.startswith("fact")


def _dedupe_relationships(relationships: list[JsonDict]) -> list[JsonDict]:
    deduped: list[JsonDict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        key = (
            str(rel.get("from_table") or "").strip(),
            str(rel.get("from_column") or "").strip(),
            str(rel.get("to_table") or "").strip(),
            str(rel.get("to_column") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rel)
    return deduped


def _fields_from_tables_and_catalogs(
    tables: list[JsonDict],
    dimensions: dict[str, JsonDict],
    measures: dict[str, JsonDict],
) -> list[JsonDict]:
    fields: dict[str, JsonDict] = {}
    for table in tables:
        table_name = str(table.get("name") or "")
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip()
            if not field_name:
                continue
            entry = fields.setdefault(
                field_name.lower(),
                {
                    "name": field_name,
                    "table": table_name,
                    "data_type": field.get("data_type") or _infer_data_type(field_name),
                    "role": field.get("role") or "unknown",
                },
            )
            if table_name and not entry.get("table"):
                entry["table"] = table_name
    for dimension in dimensions.values():
        field_name = str(dimension.get("name") or "").strip()
        if field_name:
            fields.setdefault(
                field_name.lower(),
                {
                    "name": field_name,
                    "table": "",
                    "data_type": _infer_data_type(field_name),
                    "role": "dimension",
                },
            )["role"] = "dimension"
    for measure in measures.values():
        for field_name in _as_list(measure.get("referenced_fields")):
            text = str(field_name or "").strip()
            if text:
                fields.setdefault(
                    text.lower(),
                    {
                        "name": text,
                        "table": "",
                        "data_type": "decimal",
                        "role": "measure_input",
                    },
                )
    return sorted(fields.values(), key=lambda item: item["name"].lower())


def _register_dimension(dimensions: dict[str, JsonDict], payload: JsonDict) -> None:
    name = str(payload.get("name") or "").strip()
    if not name:
        return
    key = name.lower()
    current = dimensions.setdefault(key, dict(payload))
    current["fields"] = _dedupe_strings([*_as_list(current.get("fields")), *_as_list(payload.get("fields"))])


def _register_measure(measures: dict[str, JsonDict], payload: JsonDict) -> None:
    name = str(payload.get("name") or payload.get("qlik_expression") or "").strip()
    if not name:
        return
    key = str(payload.get("qlik_expression") or name).lower()
    measures.setdefault(key, dict(payload))


def _add_filter(filters: list[JsonDict], filter_keys: set[str], payload: JsonDict) -> None:
    key = "|".join(
        [
            str(payload.get("origin") or ""),
            str(payload.get("scope") or ""),
            str(payload.get("visual_id") or ""),
            str(payload.get("field") or ""),
            str(payload.get("raw") or ""),
        ]
    ).lower()
    if key in filter_keys:
        return
    filter_keys.add(key)
    filters.append(payload)


def _add_expression(
    expression_catalog: list[JsonDict],
    source: str,
    owner_id: str,
    owner_title: str,
    expression: str,
) -> None:
    text = str(expression or "").strip()
    if not text:
        return
    expression_catalog.append(
        {
            "id": _slug(f"{source}_{owner_id}_{len(expression_catalog) + 1}", f"expression_{len(expression_catalog) + 1}"),
            "source": source,
            "owner_id": owner_id,
            "owner_title": owner_title,
            "qlik_expression": text,
            "referenced_fields": _expression_fields(text),
            "filters": _set_analysis_filters(text),
            "powerbi_hint": "Convert to DAX or Power Query according to scope.",
        }
    )


def _expression_fields(expression: Any) -> list[str]:
    text = str(expression or "")
    fields = []
    for match in re.findall(r"\[([^\]]+)\]", text):
        _append_unique(fields, match)
    for match in re.finditer(
        r"\b(?:sum|avg|average|count|min|max|only|median|stdev)\s*\(\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))",
        text,
        flags=re.IGNORECASE,
    ):
        _append_unique(fields, match.group(1) or match.group(2))
    return fields


def _first_field_reference(expression: str) -> str:
    refs = _expression_fields(expression)
    if refs:
        return refs[0]
    match = re.match(r"\[?([A-Za-z_][A-Za-z0-9_ ]*)\]?\s*(?:=|<>|>=|<=|>|<|\bin\b|\blike\b)", expression, flags=re.IGNORECASE)
    return str(match.group(1)).strip() if match else ""


def _aggregation_hint(expression: str) -> str:
    match = re.search(r"\b(sum|avg|average|count|min|max|median|stdev)\s*\(", str(expression or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    value = match.group(1).lower()
    return "average" if value == "avg" else value


def _infer_data_type(field_name: str) -> str:
    normalized = str(field_name or "").lower()
    if "date" in normalized or normalized.endswith("day") or normalized.endswith("month"):
        return "date"
    if normalized.endswith("key") or normalized.endswith("id") or normalized.endswith("year"):
        return "integer"
    if any(token in normalized for token in ("amount", "cost", "price", "qty", "quantity", "sales", "total", "discount", "tax", "profit", "margin", "rate")):
        return "decimal"
    return "string"


def _dedupe_fields(fields: list[JsonDict]) -> list[JsonDict]:
    output = []
    seen = set()
    for field in fields:
        field_name = str(field.get("name") or "").strip()
        key = field_name.lower()
        if field_name and key not in seen:
            seen.add(key)
            output.append(field)
    return output


def _dedupe_strings(values: list[Any]) -> list[str]:
    output = []
    for value in values:
        _append_unique(output, value)
    return output


def _append_unique(values: list[str], value: Any) -> None:
    text = str(value or "").strip().strip("[]")
    if text and text not in values:
        values.append(text)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    return cleaned[:120] or fallback


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
