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
from src.qlik_to_twb.qlik_client import (
    QlikEngineApiClient,
    extract_relationships_from_qlik_script,
    validate_and_convert_associations_to_relationships,
)
from src.qlik_to_powerbi.pbip_generator import generate_powerbi_pbip_project


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
    pbip_template_path: str = "",
    generate_pbip: bool = True,
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
    return _append_powerbi_intermediate_model(result, pbip_template_path=pbip_template_path, generate_pbip=generate_pbip)


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
    pbip_template_path: str = "",
    generate_pbip: bool = True,
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
    return _append_powerbi_intermediate_model(result, pbip_template_path=pbip_template_path, generate_pbip=generate_pbip)


def generate_powerbi_report_from_metadata_job(
    job_dir: str | Path,
    pbip_template_path: str = "",
) -> JsonDict:
    """Generate Power BI PBIP artifacts from an already extracted metadata job."""
    job_dir = Path(job_dir)
    job_path = job_dir / "job.json"
    if not job_path.exists():
        raise FileNotFoundError(f"Qlik Power BI job report not found: {job_path}")
    result = json.loads(job_path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError(f"Qlik Power BI job report is not a JSON object: {job_path}")
    if str(result.get("status") or "").lower() == "failed":
        raise ValueError(result.get("error") or "Cannot generate Power BI report from a failed metadata job.")
    return _append_powerbi_intermediate_model(result, pbip_template_path=pbip_template_path, generate_pbip=True)


def normalize_qlik_to_powerbi_model(qlik_metadata: JsonDict) -> JsonDict:
    visual_objects = _as_list(qlik_metadata.get("visual_objects"))
    sheets_source = _as_list(qlik_metadata.get("sheets"))
    expression_catalog: list[JsonDict] = []
    dimensions: dict[str, JsonDict] = {}
    measures: dict[str, JsonDict] = {}
    filters: list[JsonDict] = []
    filter_keys: set[str] = set()

    normalized_visuals_all = [
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
    child_visual_ids = {
        str(child_id or "").strip().lower()
        for visual in normalized_visuals_all
        for child_id in _as_list((visual.get("qix") or {}).get("child_ids"))
        if str(child_id or "").strip()
    }
    normalized_visuals = [
        visual
        for visual in normalized_visuals_all
        if not _is_embedded_child_visual(visual, child_visual_ids)
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

    # Relationships are only materialized when they are explicit or validated.
    # Qlik shared-field links stay as association candidates until business validation.
    relationships = _explicit_relationships_from_metadata(qlik_metadata, tables)
    relationships = _filter_valid_relationships(relationships)

    association_candidates = _association_candidates_from_metadata(qlik_metadata)
    if not association_candidates:
        load_script = str(qlik_metadata.get("load_script") or "").strip()
        if load_script:
            association_candidates = extract_relationships_from_qlik_script(load_script, qlik_metadata)
    association_candidates = _dedupe_association_candidates(
        _filter_association_candidates(association_candidates, tables)
    )

    validated_associations = _validated_associations_from_metadata(qlik_metadata)
    if validated_associations:
        table_metadata = _relationship_table_metadata(tables)
        for rel in validate_and_convert_associations_to_relationships(validated_associations, table_metadata):
            if _is_valid_relationship(rel):
                relationships.append({
                    "id": _slug(f"{rel['from_table']}_{rel['from_column']}_{rel['to_table']}_{rel['to_column']}", None),
                    **rel,
                })
    
    table_name_lookup = {
        str(t.get("name") or "").strip().lower(): str(t.get("name") or "").strip()
        for t in tables
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    }
    for rel in _as_list((qlik_metadata.get("semantic_model") or {}).get("relationships")):
        if not isinstance(rel, dict):
            continue
        if not _is_valid_relationship(rel):
            continue
        # ensure required fields and that tables exist in the merged tables
        from_table = str(rel.get("from_table") or rel.get("fromTable") or "").strip()
        to_table = str(rel.get("to_table") or rel.get("toTable") or "").strip()
        from_column = str(rel.get("from_column") or rel.get("fromColumn") or "").strip()
        to_column = str(rel.get("to_column") or rel.get("toColumn") or "").strip()
        resolved_from = table_name_lookup.get(from_table.lower(), from_table)
        resolved_to = table_name_lookup.get(to_table.lower(), to_table)
        if (
            resolved_from
            and resolved_to
            and from_column
            and to_column
            and resolved_from.lower() in table_name_lookup
            and resolved_to.lower() in table_name_lookup
            and not _is_technical_relationship(
                {
                    "from_table": resolved_from,
                    "from_column": from_column,
                    "to_table": resolved_to,
                    "to_column": to_column,
                }
            )
        ):
            relationships.append(
                {
                    "id": _slug(f"{resolved_from}_{from_column}_{resolved_to}_{to_column}", None),
                    "from_table": resolved_from,
                    "from_column": from_column,
                    "to_table": resolved_to,
                    "to_column": to_column,
                    "cardinality": rel.get("cardinality") or rel.get("relationship_type") or "",
                    "source": rel.get("source") or "inferred_from_script",
                }
            )

    # By default, do NOT perform heuristic fallback inference that can invent
    # relationships. Allow inference only when qlik_metadata explicitly opts in
    # via 'allow_inference': True (for backward compatibility/testing).
    allow_inference = bool((qlik_metadata or {}).get("allow_inference"))
    if allow_inference:
        inferred_relationships = _inferred_relationships_from_tables(tables, relationships)
        relationships.extend(inferred_relationships)

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
        "association_candidate_count": len(association_candidates),
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
            "association_candidates": association_candidates,
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

def _append_powerbi_intermediate_model(job_result: JsonDict, pbip_template_path: str = "", generate_pbip: bool = True) -> JsonDict:
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
    data_model_path = metadata_path.parent / "data_model.json"
    data_model_payload = {
        "source": dict(intermediate_model.get("source") or {}),
        "semantic_model": dict(intermediate_model.get("semantic_model") or {}),
        "summary": dict(intermediate_model.get("summary") or {}),
    }
    _write_json(data_model_path, data_model_payload)

    result["powerbi_intermediate_model"] = str(data_model_path)
    result["intermediate_model_data"] = intermediate_model
    result["summary"] = {
        **dict(result.get("summary") or {}),
        **dict(intermediate_model.get("summary") or {}),
    }
    trace_steps = _as_list(result.get("trace_steps"))
    trace_steps.append("Power BI intermediate model normalized and written")

    if not generate_pbip:
        result["trace_steps"] = trace_steps
        job_path = Path(str(result.get("job") or metadata_path.parent / "job.json"))
        result["job"] = str(job_path)
        _write_json(job_path, result)
        return result

    pbip_output_dir = metadata_path.parent / "powerbi_pbip_project"
    pbip_result = generate_powerbi_pbip_project(
        intermediate_model=intermediate_model,
        output_dir=pbip_output_dir,
        template_path=pbip_template_path or None,
        project_name=_slug(str(intermediate_model.get("source", {}).get("app_title") or result.get("app_id") or "QlikPowerBiReport"), "QlikPowerBiReport"),
    )
    result["powerbi_pbip_generation"] = pbip_result
    result["powerbi_pbip_project"] = pbip_result.get("project_dir", "")
    result["powerbi_pbip_file"] = pbip_result.get("pbip_path", "")
    result["powerbi_pbip_archive"] = pbip_result.get("archive_path", "")
    result["powerbi_pbip_manifest"] = pbip_result.get("manifest_path", "")
    result["powerbi_semantic_model"] = pbip_result.get("semantic_model_path", "")
    result["powerbi_report_model"] = pbip_result.get("report_path", "")
    result["powerbi_llm_mapping"] = pbip_result.get("llm_mapping_path", "")
    result["summary"] = {
        **dict(result.get("summary") or {}),
        "pbip_visual_count": int(dict(pbip_result.get("summary") or {}).get("visuals") or 0),
        "pbip_page_count": int(dict(pbip_result.get("summary") or {}).get("pages") or 0),
    }
    trace_steps.append("Power BI PBIP project generated from template metadata")
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

    raw_measures: list[JsonDict] = []
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
        raw_measures.append(payload)
    normalized_measures = _dedupe_visual_measures(raw_measures)
    for payload in normalized_measures:
        _register_measure(measures, payload)
        _add_expression(
            expression_catalog,
            source="visual_measure",
            owner_id=visual_id,
            owner_title=str(visual.get("title") or qlik_id),
            expression=str(payload.get("qlik_expression") or ""),
        )

    visual_filters = _filters_from_visual(
        visual=visual,
        visual_id=visual_id,
        dimensions=normalized_dimensions,
        measures=normalized_measures,
    )
    for filter_item in visual_filters:
        _add_filter(filters, filter_keys, filter_item)

    visual_role = _classify_visual_role(
        visual=visual,
        powerbi_type=powerbi_type,
        dimensions=normalized_dimensions,
        measures=normalized_measures,
        filters=visual_filters,
    )

    base_visual = {
        "id": visual_id,
        "qlik_id": qlik_id,
        "sheet_id": str(visual.get("sheet_id") or ""),
        "title": visual.get("title") or qlik_id,
        "qlik_type": qlik_type,
        "powerbi_visual_type_hint": powerbi_type,
        "visual_role": visual_role,
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

    # For KPI visuals, add special structure with kpi_title separate from measures
    if visual_role == "kpi":
        kpi_title = visual.get("title") or qlik_id
        base_visual.update({
            "visual_layout_type": "kpi_card",
            "kpi_title": kpi_title,
            # For KPI cards, dimensions/filters should be empty
            "dimensions": [],
            "filters": [],
        })

    return base_visual


def _is_embedded_child_visual(visual: JsonDict, child_visual_ids: set[str]) -> bool:
    visual_id = str(visual.get("qlik_id") or visual.get("id") or "").strip().lower()
    if not visual_id or visual_id not in child_visual_ids:
        return False
    if str(visual.get("sheet_id") or "").strip():
        return False
    qix = visual.get("qix") if isinstance(visual.get("qix"), dict) else {}
    return not _as_list(qix.get("child_ids"))


def _classify_visual_role(
    visual: JsonDict,
    powerbi_type: str,
    dimensions: list[JsonDict],
    measures: list[JsonDict],
    filters: list[JsonDict],
) -> str:
    qix = visual.get("qix") if isinstance(visual.get("qix"), dict) else {}
    qtype = str(visual.get("type") or visual.get("source_type") or qix.get("qType") or "").strip().lower()
    has_measure = any(str(measure.get("qlik_expression") or measure.get("name") or "").strip() for measure in measures if isinstance(measure, dict))
    has_dimension = any(str(dimension.get("name") or "").strip() for dimension in dimensions if isinstance(dimension, dict))
    dimension_sources = {str(dimension.get("source") or "").strip().lower() for dimension in dimensions if isinstance(dimension, dict)}

    if filters and not has_measure:
        return "filter"
    if has_dimension and not has_measure and dimension_sources and all(source.startswith("list_") for source in dimension_sources):
        return "filter"
    if not has_dimension and has_measure and len(measures) == 1:
        return "kpi"
    if qtype in {"kpi", "gauge"} and has_measure:
        return "kpi"
    return "chart"


def _dedupe_visual_measures(items: list[JsonDict]) -> list[JsonDict]:
    deduped: list[JsonDict] = []
    seen: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip().lower()
        expression = str(item.get("qlik_expression") or "").strip().lower()
        key = expression or label
        if not key:
            continue
        if key in seen:
            existing_index = seen[key]
            existing = deduped[existing_index]
            if not str(existing.get("qlik_expression") or "").strip() and expression:
                merged = dict(existing)
                merged.update({k: v for k, v in item.items() if v not in (None, "", [])})
                deduped[existing_index] = merged
            continue
        if not expression and label:
            if any(str(existing.get("label") or existing.get("name") or "").strip().lower() == label for existing in deduped):
                continue
        if expression and label:
            label_match = next(
                (
                    index
                    for index, existing in enumerate(deduped)
                    if str(existing.get("label") or existing.get("name") or "").strip().lower() == label
                    and not str(existing.get("qlik_expression") or "").strip()
                ),
                None,
            )
            if label_match is not None:
                merged = dict(deduped[label_match])
                merged.update({k: v for k, v in item.items() if v not in (None, "", [])})
                deduped[label_match] = merged
                seen[key] = label_match
                continue
        seen[key] = len(deduped)
        deduped.append(item)
    return deduped


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
    dimension_sources = {str(dimension.get("source") or "").strip().lower() for dimension in dimensions if isinstance(dimension, dict)}
    is_selector_shape = bool(dimensions) and not measures and dimension_sources and all(
        source.startswith("list_") for source in dimension_sources
    )
    if "filter" in visual_type or is_selector_shape:
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
                no_of_symbols = _safe_int(field.get("no_of_symbols"))
                fields.append(
                    {
                        "name": field_name,
                        "data_type": field.get("data_type") or _infer_data_type(field_name),
                        "role": field.get("role") or "",
                        "no_of_symbols": no_of_symbols,
                        "distinct_count": no_of_symbols,
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
        if not _is_valid_relationship(relationship):
            continue
        from_table = str(relationship.get("from_table") or relationship.get("fromTable") or "").strip()
        to_table = str(relationship.get("to_table") or relationship.get("toTable") or "").strip()
        from_column = str(relationship.get("from_column") or relationship.get("fromColumn") or "").strip()
        to_column = str(relationship.get("to_column") or relationship.get("toColumn") or "").strip()
        if not from_table or not to_table or not from_column or not to_column:
            continue
        if from_table not in table_names or to_table not in table_names:
            continue
        if _is_technical_relationship(
            {
                "from_table": from_table,
                "from_column": from_column,
                "to_table": to_table,
                "to_column": to_column,
            }
        ):
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


def _association_candidates_from_metadata(qlik_metadata: JsonDict) -> list[JsonDict]:
    candidates: list[JsonDict] = []
    for item in _as_list(qlik_metadata.get("association_candidates")):
        if isinstance(item, dict):
            candidates.append(item)
    for item in _as_list(qlik_metadata.get("relationships")):
        if isinstance(item, dict):
            candidate = _association_candidate_from_unvalidated_relationship(item)
            if candidate:
                candidates.append(candidate)
    semantic_model = qlik_metadata.get("semantic_model")
    if isinstance(semantic_model, dict):
        for item in _as_list(semantic_model.get("association_candidates")):
            if isinstance(item, dict):
                candidates.append(item)
        for item in _as_list(semantic_model.get("relationships")):
            if isinstance(item, dict) and item.get("association_type") == "qlik_association":
                candidates.append(item)
            elif isinstance(item, dict):
                candidate = _association_candidate_from_unvalidated_relationship(item)
                if candidate:
                    candidates.append(candidate)
    return candidates


def _association_candidate_from_unvalidated_relationship(relationship: JsonDict) -> JsonDict:
    source = str(relationship.get("source") or "").strip().lower()
    if source not in {"qix_get_tables_and_keys", "qlik_script_parse", "qlik_script_parse_unvalidated"}:
        return {}
    from_table = str(relationship.get("from_table") or relationship.get("fromTable") or "").strip()
    to_table = str(relationship.get("to_table") or relationship.get("toTable") or "").strip()
    from_column = str(relationship.get("from_column") or relationship.get("fromColumn") or "").strip()
    to_column = str(relationship.get("to_column") or relationship.get("toColumn") or "").strip()
    field = from_column if from_column.lower() == to_column.lower() else (relationship.get("key_name") or from_column or to_column)
    if not field or not from_table or not to_table:
        return {}
    candidate = {
        "field": field,
        "tables": [from_table, to_table],
        "association_type": "qlik_association",
        "cardinality": "unknown",
        "requires_validation": True,
        "source": source,
    }
    endpoints = relationship.get("endpoints") or relationship.get("qix_endpoints")
    if isinstance(endpoints, list):
        candidate["endpoints"] = endpoints
    return candidate


def _validated_associations_from_metadata(qlik_metadata: JsonDict) -> list[JsonDict]:
    candidates: list[JsonDict] = []
    for key in ("validated_associations", "relationship_validations"):
        for item in _as_list(qlik_metadata.get(key)):
            if isinstance(item, dict):
                candidates.append(item)
    semantic_model = qlik_metadata.get("semantic_model")
    if isinstance(semantic_model, dict):
        for key in ("validated_associations", "relationship_validations"):
            for item in _as_list(semantic_model.get(key)):
                if isinstance(item, dict):
                    candidates.append(item)
    return [
        item
        for item in candidates
        if bool(item.get("validated") is True or item.get("requires_validation") is False)
    ]


def _filter_association_candidates(candidates: list[JsonDict], tables: list[JsonDict]) -> list[JsonDict]:
    table_lookup = {
        str(table.get("name") or table.get("table_name") or "").strip().lower(): str(table.get("name") or table.get("table_name") or "").strip()
        for table in tables
        if isinstance(table, dict) and str(table.get("name") or table.get("table_name") or "").strip()
    }
    filtered: list[JsonDict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        field = str(candidate.get("field") or candidate.get("key_name") or "").strip()
        if _is_technical_name(field):
            continue
        tables_value = candidate.get("tables")
        tables_list = [str(table or "").strip() for table in tables_value] if isinstance(tables_value, list) else []
        if not tables_list and isinstance(candidate.get("endpoints"), list):
            tables_list = [
                str(endpoint.get("table") or "").strip()
                for endpoint in candidate.get("endpoints")
                if isinstance(endpoint, dict)
            ]
        resolved_tables = []
        for table_name in tables_list:
            resolved = table_lookup.get(table_name.lower(), table_name)
            if not resolved or _is_technical_name(resolved):
                continue
            if resolved.lower() not in {table.lower() for table in resolved_tables}:
                resolved_tables.append(resolved)
        if len(resolved_tables) < 2:
            continue
        clean_candidate = dict(candidate)
        clean_candidate["field"] = field
        clean_candidate["tables"] = sorted(resolved_tables, key=lambda item: item.lower())
        clean_candidate["association_type"] = clean_candidate.get("association_type") or "qlik_association"
        clean_candidate["requires_validation"] = True
        clean_candidate.update(_candidate_cardinality_payload(clean_candidate["tables"], field))
        clean_candidate["cardinality"] = clean_candidate.get("cardinality") or "unknown"
        if isinstance(clean_candidate.get("endpoints"), list):
            clean_candidate["endpoints"] = [
                endpoint
                for endpoint in clean_candidate["endpoints"]
                if isinstance(endpoint, dict)
                and not _is_technical_name(str(endpoint.get("table") or ""))
                and not _is_technical_name(str(endpoint.get("field") or ""))
            ]
        filtered.append(clean_candidate)
    return filtered


def _dedupe_association_candidates(candidates: list[JsonDict]) -> list[JsonDict]:
    deduped: list[JsonDict] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for candidate in candidates:
        field = str(candidate.get("field") or "").strip().lower()
        tables = tuple(sorted(str(table or "").strip().lower() for table in _as_list(candidate.get("tables")) if str(table or "").strip()))
        if not field or len(tables) < 2:
            continue
        key = (field, tables)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _filter_valid_relationships(relationships: list[JsonDict]) -> list[JsonDict]:
    return [rel for rel in relationships if _is_valid_relationship(rel) and not _is_technical_relationship(rel)]


def _is_valid_relationship(relationship: JsonDict) -> bool:
    if not isinstance(relationship, dict):
        return False
    if relationship.get("association_type") == "qlik_association":
        return False
    if relationship.get("requires_validation") is True:
        return False
    source = str(relationship.get("source") or "").strip().lower()
    if source in {"qix_get_tables_and_keys", "qlik_script_parse", "qlik_script_parse_unvalidated"}:
        return False
    return True


def _is_technical_relationship(relationship: JsonDict) -> bool:
    return any(
        _is_technical_name(relationship.get(key))
        for key in ("from_table", "to_table", "from_column", "to_column")
    )


def _is_technical_name(value: Any) -> bool:
    return str(value or "").strip().startswith("__")


def _relationship_table_metadata(tables: list[JsonDict]) -> dict[str, JsonDict]:
    metadata: dict[str, JsonDict] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or table.get("table_name") or "").strip()
        if not table_name:
            continue
        field_stats: dict[str, JsonDict] = {}
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or field.get("field") or "").strip()
            if not field_name:
                continue
            distinct_count = field.get("distinct_count")
            if distinct_count is None:
                distinct_count = field.get("no_of_symbols")
            if distinct_count is not None:
                field_stats[field_name.lower()] = {
                    "distinct_count": _safe_int(distinct_count),
                    "no_of_symbols": _safe_int(distinct_count),
                }
        metadata[table_name] = {
            "record_count": _safe_int(table.get("record_count")),
            "fields": _as_list(table.get("fields")),
            "field_stats": field_stats,
        }
    return metadata


def _cardinality_for_relationship(
    from_table: str,
    to_table: str,
    from_field: str,
    to_field: str,
    table_metadata: dict[str, JsonDict],
) -> str:
    rule_cardinality, _rule_name = _business_cardinality_rule(from_table, to_table, from_field)
    if rule_cardinality:
        return rule_cardinality

    from_meta = table_metadata.get(from_table, {})
    to_meta = table_metadata.get(to_table, {})
    from_rows = _safe_int(from_meta.get("record_count"))
    to_rows = _safe_int(to_meta.get("record_count"))
    from_distinct = _distinct_count_for_field(from_meta, from_field)
    to_distinct = _distinct_count_for_field(to_meta, to_field)
    if not from_rows or not to_rows or from_distinct is None or to_distinct is None:
        return "unknown"
    from_has_duplicates = from_rows > from_distinct
    to_has_duplicates = to_rows > to_distinct
    if not from_has_duplicates and not to_has_duplicates:
        return "one_to_one"
    if from_has_duplicates and not to_has_duplicates:
        return "many_to_one"
    if not from_has_duplicates and to_has_duplicates:
        return "one_to_many"
    return "many_to_many"


def _candidate_cardinality_payload(tables: list[str], field_name: str) -> JsonDict:
    if len(tables) != 2:
        return {}
    orientation = _business_relationship_orientation(tables[0], tables[1], field_name)
    if not orientation:
        return {}
    from_table, to_table, cardinality, rule = orientation
    return {
        "from_table": from_table,
        "from_column": field_name,
        "to_table": to_table,
        "to_column": field_name,
        "cardinality": cardinality,
        "cardinality_rule": rule,
    }


def _business_relationship_orientation(
    table_a: str,
    table_b: str,
    shared_field: str = "",
) -> tuple[str, str, str, str] | None:
    a_role = _table_business_role(table_a)
    b_role = _table_business_role(table_b)
    if a_role == "fact" and b_role == "dim":
        return table_a, table_b, "many_to_one", "fact_to_dim"
    if a_role == "dim" and b_role == "fact":
        return table_b, table_a, "many_to_one", "fact_to_dim"
    if a_role == "dim" and b_role == "dim":
        target = _general_dimension_for_field(table_a, table_b, shared_field)
        if target == table_a:
            return table_b, table_a, "many_to_one", "detail_dim_to_general_dim"
        if target == table_b:
            return table_a, table_b, "many_to_one", "detail_dim_to_general_dim"
    return None


def _business_cardinality_rule(from_table: str, to_table: str, shared_field: str = "") -> tuple[str, str]:
    from_role = _table_business_role(from_table)
    to_role = _table_business_role(to_table)
    if from_role == "fact" and to_role == "dim":
        return "many_to_one", "fact_to_dim"
    if from_role == "dim" and to_role == "fact":
        return "one_to_many", "fact_to_dim_reversed"
    if from_role == "dim" and to_role == "dim":
        target = _general_dimension_for_field(from_table, to_table, shared_field)
        if target == to_table:
            return "many_to_one", "detail_dim_to_general_dim"
        if target == from_table:
            return "one_to_many", "detail_dim_to_general_dim_reversed"
    return "", ""


def _table_business_role(table_name: str) -> str:
    normalized = str(table_name or "").strip().lower()
    if normalized.startswith("fact") or "_fact" in normalized or "fact" in normalized:
        return "fact"
    if normalized.startswith("dim") or "_dim" in normalized or "dimension" in normalized:
        return "dim"
    return ""


def _general_dimension_for_field(table_a: str, table_b: str, shared_field: str) -> str:
    field_base = _dimension_key_base(shared_field)
    if not field_base:
        return ""
    a_score = _dimension_field_match_score(table_a, field_base)
    b_score = _dimension_field_match_score(table_b, field_base)
    if a_score > b_score:
        return table_a
    if b_score > a_score:
        return table_b
    return ""


def _dimension_key_base(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    for suffix in ("alternatekey", "key", "id", "code"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _dimension_field_match_score(table_name: str, field_base: str) -> int:
    table_base = re.sub(r"[^a-z0-9]+", "", str(table_name or "").strip().lower())
    if table_base.startswith("dim"):
        table_base = table_base[3:]
    if not table_base or not field_base:
        return 0
    if table_base == field_base:
        return 100 + len(table_base)
    if field_base.endswith(table_base):
        return 60 + len(table_base)
    if table_base.endswith(field_base):
        return 40 + len(field_base)
    if field_base in table_base or table_base in field_base:
        return 10 + min(len(field_base), len(table_base))
    return 0


def _distinct_count_for_field(table_metadata: JsonDict, field_name: str) -> int | None:
    normalized = str(field_name or "").strip().lower()
    if not normalized:
        return None
    field_stats = table_metadata.get("field_stats")
    if isinstance(field_stats, dict):
        stats = field_stats.get(normalized)
        if isinstance(stats, dict):
            value = stats.get("distinct_count")
            if value is None:
                value = stats.get("no_of_symbols")
            if value is not None:
                return _safe_int(value)
    return None


def _inferred_relationships_from_tables(tables: list[JsonDict], existing_relationships: list[JsonDict]) -> list[JsonDict]:
    table_by_name = {
        str(table.get("name") or "").strip(): table
        for table in tables
        if isinstance(table, dict) and str(table.get("name") or "").strip()
    }
    table_metadata = _relationship_table_metadata(tables)
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
                    "cardinality": _cardinality_for_relationship(source_table, target_table, field_name, field_name, table_metadata),
                    "source": "inferred_from_shared_field",
                }
            )
            existing_keys.add(relationship_key)

    # Fallback inference for key-like columns that do not share the exact same
    # text (e.g., CustomerKey vs CustomerAlternateKey).
    table_key_fields: dict[str, list[str]] = {}
    for table_name, table in table_by_name.items():
        keys: list[str] = []
        for field in _dedupe_fields(_as_list(table.get("fields"))):
            field_name = str(field.get("name") if isinstance(field, dict) else field or "").strip()
            if field_name and _field_looks_like_key(field_name):
                keys.append(field_name)
        if keys:
            table_key_fields[table_name] = keys

    table_names = list(table_key_fields.keys())

    def _table_priority(table_name: str) -> tuple[int, int, str]:
        normalized = table_name.lower()
        field_count = table_field_counts.get(table_name, 0)
        fact_bonus = 50 if normalized.startswith("fact") else 25 if "fact" in normalized else 0
        dimension_bonus = 10 if normalized.startswith("dim") else 0
        return (fact_bonus + field_count, dimension_bonus, table_name)

    for source_table in table_names:
        source_keys = table_key_fields.get(source_table, [])
        if not source_keys:
            continue
        for target_table in table_names:
            if target_table == source_table:
                continue
            preferred_table = sorted((source_table, target_table), key=_table_priority, reverse=True)[0]
            if source_table != preferred_table:
                continue
            target_keys = table_key_fields.get(target_table, [])
            if not target_keys:
                continue

            for source_key in source_keys:
                source_key_bases = _field_key_bases(source_key)
                for target_key in target_keys:
                    target_key_bases = _field_key_bases(target_key)
                    if not source_key_bases or not target_key_bases:
                        continue
                    if not _key_bases_match(source_key_bases, target_key_bases, target_table):
                        continue

                    relationship_key = (source_table, source_key, target_table, target_key)
                    if relationship_key in existing_keys:
                        continue

                    inferred_relationships.append(
                        {
                            "id": _slug(f"{source_table}_{source_key}_{target_table}_{target_key}", None),
                            "from_table": source_table,
                            "from_column": source_key,
                            "to_table": target_table,
                            "to_column": target_key,
                            "cardinality": _cardinality_for_relationship(source_table, target_table, source_key, target_key, table_metadata),
                            "source": "inferred_from_key_semantics",
                        }
                    )
                    existing_keys.add(relationship_key)
                    break
                else:
                    continue
                break

    return inferred_relationships


def _field_looks_like_key(field_name: str) -> bool:
    clean = str(field_name or "").strip().lower()
    return "key" in clean


def _field_key_bases(field_name: str) -> set[str]:
    text = str(field_name or "").strip()
    if not text:
        return set()

    # Strip table-qualified prefixes and separators while preserving key tokens.
    candidates = [text]
    if "." in text:
        candidates.append(text.split(".")[-1])
    if "-" in text:
        candidates.extend(part for part in text.split("-") if part)

    bases: set[str] = set()
    for candidate in candidates:
        compact = re.sub(r"[^A-Za-z0-9]+", "", candidate).lower()
        compact = compact.replace("alternate", "")
        if compact.endswith("key") and len(compact) > 3:
            bases.add(compact[:-3])
        # Capture patterns embedded in longer names.
        for match in re.finditer(r"([a-z0-9]+)key", compact):
            base = str(match.group(1) or "").strip()
            if base:
                bases.add(base)
    return {base for base in bases if base}


def _key_bases_match(source_bases: set[str], target_bases: set[str], target_table: str) -> bool:
    if source_bases.intersection(target_bases):
        return True

    # Date dimensions often expose one canonical date key while facts may carry
    # role-playing keys (OrderDateKey, DueDateKey, ShipDateKey).
    target_name = str(target_table or "").strip().lower()
    if "date" in target_name:
        return any(base.endswith("date") for base in source_bases)

    return False


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
        if isinstance(field, dict):
            field_name = str(field.get("name") or "").strip()
        else:
            field_name = str(field or "").strip()
        key = field_name.lower()
        if field_name and key not in seen:
            seen.add(key)
            if isinstance(field, dict):
                output.append(field)
            else:
                output.append({"name": field_name})
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


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
