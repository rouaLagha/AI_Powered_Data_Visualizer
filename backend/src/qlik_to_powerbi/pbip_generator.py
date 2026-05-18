from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any
import zipfile


JsonDict = dict[str, Any]

DATA_MODEL_JSON_NAME = "data_model.json"
VISUAL_ELEMENTS_JSON_NAME = "visual_elements.json"
MAPPING_RULES_JSON_NAME = "mapping.json"


def generate_powerbi_pbip_project(
    intermediate_model: JsonDict,
    output_dir: str | Path,
    template_path: str | Path | None = None,
    project_name: str = "QlikPowerBiReport",
) -> JsonDict:
    """Generate a deterministic PBIP-style project from Qlik metadata.

    The writer owns the final files. Any LLM assistance should feed the mapping
    JSON inputs before this function writes the PBIP artifacts.
    """
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_name(project_name, "QlikPowerBiReport")
    project_dir = output_root / safe_name
    if project_dir.exists():
        shutil.rmtree(project_dir)

    template_source = _copy_template(template_path, project_dir)
    if not template_source:
        _create_minimal_template(project_dir, safe_name)
        template_source = "generated_minimal_template"

    has_external_template = template_source != "generated_minimal_template"
    artifact_name, semantic_dir, report_dir, pbip_path = _project_paths(project_dir, safe_name)
    semantic_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    semantic_format = _semantic_model_format(semantic_dir)
    semantic_model_output = (
        project_dir / "generated_semantic_model_snapshot.json"
        if semantic_format == "tmdl"
        else semantic_dir / "model.bim"
    )
    report_model_output = (
        project_dir / "generated_report_layout_snapshot.json"
        if has_external_template
        else _report_model_path(report_dir)
    )
    data_model_json_output = project_dir / DATA_MODEL_JSON_NAME
    visual_elements_json_output = project_dir / VISUAL_ELEMENTS_JSON_NAME
    mapping_rules_json_output = project_dir / MAPPING_RULES_JSON_NAME

    semantic_model = _build_semantic_model(intermediate_model)
    report_model = _build_report_model(intermediate_model)
    llm_mapping = _build_llm_mapping_manifest(intermediate_model, semantic_model, report_model)

    # Build the normalized data_model.json following the required schema
    data_model_json = {
        "schema_version": "1.0",
        "source_tool": str(intermediate_model.get("source_tool") or "qlik"),
        "target_tool": str(intermediate_model.get("target_tool") or "powerbi"),
        "metadata": {
            "source_app_name": str(intermediate_model.get("metadata", {}).get("source_app_name") or ""),
            "extraction_method": str(intermediate_model.get("metadata", {}).get("extraction_method") or ""),
        },
        "data_sources": [],
        "tables": [],
        "relationships": [],
        "association_candidates": _as_list((intermediate_model.get("semantic_model") or {}).get("association_candidates")),
    }

    # Map dataSources
    for ds in _as_list(semantic_model.get("model", {}).get("dataSources")):
        if not isinstance(ds, dict):
            continue
        connection = ds.get("connectionDetails") if isinstance(ds.get("connectionDetails"), dict) else {}
        data_model_json["data_sources"].append(
            {
                "name": ds.get("name") or "",
                "type": ds.get("type") or connection.get("protocol") or "",
                "server": connection.get("server") or "",
                "database": connection.get("database") or "",
                "schema": connection.get("schema") or "",
            }
        )

    # Map tables and measures
    for table in _as_list(semantic_model.get("tables")):
        if not isinstance(table, dict):
            continue
        tbl = {"name": table.get("name") or "", "type": "table", "columns": [], "measures": []}
        for col in _as_list(table.get("columns")):
            if not isinstance(col, dict):
                continue
            role = "dimension" if str(col.get("summarizeBy") or "").lower() == "none" else "measure"
            tbl["columns"].append(
                {
                    "name": col.get("name") or "",
                    "data_type": col.get("dataType") or "",
                    "source_column": col.get("sourceColumn") or "",
                    "role": role,
                    "summarize_by": col.get("summarizeBy") or "",
                }
            )
        for m in _as_list(table.get("measures")):
            if not isinstance(m, dict):
                continue
            source_expression = (m.get("source") or {}).get("qlik_expression") or ""
            target_expression = m.get("expression") or ""
            tbl["measures"].append(
                {
                    "name": m.get("name") or "",
                    "source_expression": source_expression,
                    "target_expression": target_expression,
                    "format": m.get("formatString") or "",
                }
            )
        data_model_json["tables"].append(tbl)

    # Map relationships
    for rel in _as_list(semantic_model.get("relationships")):
        if not isinstance(rel, dict):
            continue
        rel_entry = {
            "from_table": rel.get("fromTable") or rel.get("from_table") or "",
            "from_column": rel.get("fromColumn") or rel.get("from_column") or "",
            "to_table": rel.get("toTable") or rel.get("to_table") or "",
            "to_column": rel.get("toColumn") or rel.get("to_column") or "",
            "cardinality": rel.get("cardinality") or "",
            "cross_filter_direction": rel.get("crossFilteringBehavior") or rel.get("cross_filter_direction") or "",
        }

        # Include QIX key metadata when available (key name and endpoints)
        if rel.get("key_name"):
            rel_entry["key_name"] = rel.get("key_name")
        endpoints = rel.get("endpoints") or rel.get("qix_endpoints")
        if isinstance(endpoints, list) and endpoints:
            # normalize endpoints as list of {table, field}
            rel_entry["endpoints"] = [
                {"table": e.get("table") or e.get("qTable") or e.get("qTableName") or "", "field": e.get("field") or e.get("qField") or e.get("qName") or ""}
                if isinstance(e, dict)
                else {"table": str(e[0]) if isinstance(e, (list, tuple)) and len(e) > 0 else "", "field": str(e[1]) if isinstance(e, (list, tuple)) and len(e) > 1 else ""}
                for e in endpoints
            ]

        data_model_json["relationships"].append(rel_entry)
    manifest = {
        "schema_version": "qlik_powerbi_pbip_generation/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "template_source": template_source,
        "project_name": safe_name,
        "artifact_name": artifact_name,
        "inputs": {
            "semantic_source": "powerbi_intermediate_model.semantic_model",
            "visual_source": "powerbi_intermediate_model.report",
            "llm_mapping_source": "deterministic_mapping_with_optional_llm_review",
        },
        "outputs": {
            "pbip": str(pbip_path),
            "semantic_model": str(semantic_model_output),
            "semantic_model_format": semantic_format,
            "report": str(report_model_output),
            "llm_mapping": str(mapping_rules_json_output),
            "data_model_json": str(data_model_json_output),
            "visual_elements_json": str(visual_elements_json_output),
            "mapping_rules_json": str(mapping_rules_json_output),
        },
        "summary": {
            "tables": len(_as_list(semantic_model.get("tables"))),
            "relationships": len(_as_list(semantic_model.get("relationships"))),
            "measures": sum(len(_as_list(table.get("measures"))) for table in _as_list(semantic_model.get("tables"))),
            "pages": len(_as_list(report_model.get("sections"))),
            "visuals": sum(len(_as_list(page.get("visualContainers"))) for page in _as_list(report_model.get("sections"))),
        },
    }

    if semantic_format == "tmdl":
        mixed_tmsl_path = semantic_dir / "model.bim"
        if mixed_tmsl_path.exists():
            mixed_tmsl_path.unlink()
        _write_json(semantic_model_output, semantic_model)
    else:
        _write_json(semantic_model_output, semantic_model)
    if not has_external_template or not (semantic_dir / "definition.pbism").exists():
        _write_json(semantic_dir / "definition.pbism", _semantic_definition(artifact_name))
    if not has_external_template or not (report_dir / "definition.pbir").exists():
        _write_json(report_dir / "definition.pbir", _report_definition(artifact_name))
    _write_json(report_model_output, report_model)
    _write_json(data_model_json_output, data_model_json)
    _write_json(visual_elements_json_output, report_model)
    _write_json(mapping_rules_json_output, llm_mapping)
    _write_json(project_dir / "generation_manifest.json", manifest)
    if not has_external_template or not pbip_path.exists():
        _write_json(pbip_path, _pbip_definition(artifact_name))

    archive_path = output_root / f"{safe_name}.zip"
    _zip_directory(project_dir, archive_path)

    return {
        "status": "completed",
        "project_name": safe_name,
        "artifact_name": artifact_name,
        "template_source": template_source,
        "project_dir": str(project_dir),
        "pbip_path": str(pbip_path),
        "semantic_model_format": semantic_format,
        "semantic_model_path": str(data_model_json_output),
        "report_path": str(visual_elements_json_output),
        "llm_mapping_path": str(mapping_rules_json_output),
        "manifest_path": str(project_dir / "generation_manifest.json"),
        "archive_path": str(archive_path),
        "summary": manifest["summary"],
    }


def _copy_template(template_path: str | Path | None, project_dir: Path) -> str:
    if not template_path:
        return ""
    source = Path(template_path)
    if not source.exists():
        return ""
    if source.is_dir():
        shutil.copytree(source, project_dir)
        return str(source)
    if source.suffix.lower() == ".pbip":
        shutil.copytree(source.parent, project_dir)
        return str(source)
    if source.suffix.lower() == ".zip":
        project_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, "r") as archive:
            archive.extractall(project_dir)
        return str(source)
    return ""


def _create_minimal_template(project_dir: Path, project_name: str) -> None:
    (project_dir / f"{project_name}.SemanticModel").mkdir(parents=True, exist_ok=True)
    (project_dir / f"{project_name}.Report").mkdir(parents=True, exist_ok=True)


def _project_paths(project_dir: Path, fallback_name: str) -> tuple[str, Path, Path, Path]:
    pbip_files = sorted(project_dir.glob("*.pbip"))
    artifact_name = pbip_files[0].stem if pbip_files else fallback_name
    semantic_dirs = sorted(project_dir.glob("*.SemanticModel"))
    report_dirs = sorted(project_dir.glob("*.Report"))
    semantic_dir = semantic_dirs[0] if semantic_dirs else project_dir / f"{artifact_name}.SemanticModel"
    report_dir = report_dirs[0] if report_dirs else project_dir / f"{artifact_name}.Report"
    pbip_path = pbip_files[0] if pbip_files else project_dir / f"{artifact_name}.pbip"
    return artifact_name, semantic_dir, report_dir, pbip_path


def _report_model_path(report_dir: Path) -> Path:
    enhanced_path = report_dir / "definition" / "report.json"
    if enhanced_path.exists() or (report_dir / "definition").exists():
        return enhanced_path
    return report_dir / "report.json"


def _semantic_model_format(semantic_dir: Path) -> str:
    definition_dir = semantic_dir / "definition"
    if definition_dir.exists() and any(definition_dir.rglob("*.tmdl")):
        return "tmdl"
    return "tmsl"


def _build_semantic_model(intermediate_model: JsonDict) -> JsonDict:
    semantic = dict(intermediate_model.get("semantic_model") or {})
    source_connections = _as_list(semantic.get("connections"))
    tables = []
    source_tables = [
        table
        for table in _as_list(semantic.get("tables"))
        if isinstance(table, dict) and not _is_generated_calendar_table(table)
    ]
    table_lookup = _table_name_lookup(source_tables)
    field_to_table = _field_to_table_map(source_tables)
    measures_by_field = _measures_by_referenced_field(
        _as_list(semantic.get("measures")),
        field_to_table=field_to_table,
        preferred_table_order=[str(table.get("name") or table.get("table_name") or "").strip() for table in source_tables],
    )
    first_table_name = ""
    for table in source_tables:
        first_table_name = _safe_name(table.get("name") or table.get("table_name"), "")
        if first_table_name:
            break
    if first_table_name and "" in measures_by_field:
        measures_by_field.setdefault(first_table_name.lower(), []).extend(measures_by_field.pop(""))

    for table in source_tables:
        table_name = _safe_name(table.get("name") or table.get("table_name"), "Table")
        columns = []
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip()
            if not field_name:
                continue
            columns.append(
                {
                    "name": field_name,
                    "dataType": _to_powerbi_type(field.get("data_type") or field.get("tableau_datatype")),
                    "sourceColumn": field_name,
                    "summarizeBy": "none" if str(field.get("role") or "").lower() == "dimension" else "sum",
                }
            )
        table_measures = measures_by_field.get(table_name.lower(), [])
        tables.append(
            {
                "name": table_name,
                "columns": columns,
                "measures": table_measures,
                "partitions": [
                    {
                        "name": f"{table_name} Partition",
                        "mode": "import",
                        "source": {
                            "type": "m",
                            "expression": _power_query_expression(table_name, source_connections),
                        },
                    }
                ],
            }
        )

    filtered_relationships: list[JsonDict] = []
    seen_relationship_keys: set[tuple[str, str, str, str]] = set()
    for rel in _as_list(semantic.get("relationships")):
        if not isinstance(rel, dict):
            continue
        from_table_raw = str(rel.get("from_table") or rel.get("fromTable") or "").strip()
        to_table_raw = str(rel.get("to_table") or rel.get("toTable") or "").strip()
        from_column = str(rel.get("from_column") or rel.get("fromColumn") or "").strip()
        to_column = str(rel.get("to_column") or rel.get("toColumn") or "").strip()
        from_table = _resolve_table_name(from_table_raw, table_lookup) or from_table_raw
        to_table = _resolve_table_name(to_table_raw, table_lookup) or to_table_raw
        if not from_table or not to_table or not from_column or not to_column:
            continue
        rel_key = (
            from_table.lower(),
            from_column.lower(),
            to_table.lower(),
            to_column.lower(),
        )
        if rel_key in seen_relationship_keys:
            continue
        seen_relationship_keys.add(rel_key)
        rel_payload = dict(rel)
        rel_payload["from_table"] = from_table
        rel_payload["to_table"] = to_table
        rel_payload["from_column"] = from_column
        rel_payload["to_column"] = to_column
        filtered_relationships.append(_relationship_payload(rel_payload))

    return {
        "name": "QlikConvertedSemanticModel",
        "compatibilityLevel": 1601,
        "model": {
            "culture": "en-US",
            "dataSources": [_data_source_from_connection(connection) for connection in source_connections],
            "tables": tables,
            "relationships": filtered_relationships,
        },
        "tables": tables,
        "relationships": filtered_relationships,
    }


def _is_generated_calendar_table(table: JsonDict) -> bool:
    name = str(table.get("name") or table.get("table_name") or "").strip().lower()
    return name in {"autocalendar", "auto calendar"}


def _build_report_model(intermediate_model: JsonDict) -> JsonDict:
    report = dict(intermediate_model.get("report") or {})
    pages = []
    for page_index, sheet in enumerate(_as_list(report.get("sheets"))):
        if not isinstance(sheet, dict):
            continue
        visuals = []
        for visual_index, visual in enumerate(_as_list(sheet.get("visuals"))):
            if isinstance(visual, dict):
                visuals.append(_visual_container(visual, visual_index))
        pages.append(
            {
                "name": _safe_name(sheet.get("id") or f"Page{page_index + 1}", f"Page{page_index + 1}"),
                "displayName": str(sheet.get("title") or f"Page {page_index + 1}"),
                "ordinal": page_index,
                "visualContainers": visuals,
            }
        )
    if not pages:
        pages.append({"name": "Page1", "displayName": "Qlik Report", "ordinal": 0, "visualContainers": []})
    return {
        "schema_version": "qlik_powerbi_report_layout/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "sections": pages,
    }


def _visual_container(visual: JsonDict, index: int) -> JsonDict:
    width = 420
    height = 260
    columns = 2
    
    # Check if this is a KPI card
    is_kpi = visual.get("visual_layout_type") == "kpi_card"
    is_filter_visual = _is_filter_visual(visual)
    filter_fields = (
        _as_list(visual.get("filters")) or _as_list(visual.get("dimensions"))
        if is_filter_visual
        else _as_list(visual.get("filters"))
    )
    
    container = {
        "name": _safe_name(visual.get("id") or f"Visual{index + 1}", f"Visual{index + 1}"),
        "title": str(visual.get("title") or f"Visual {index + 1}"),
        "type": visual.get("powerbi_visual_type") or visual.get("powerbi_visual_type_hint") or "tableEx",
        "x": (index % columns) * (width + 24),
        "y": (index // columns) * (height + 28),
        "width": width,
        "height": height,
        "fields": {
            "dimensions": [] if is_kpi or is_filter_visual else [_field_ref(item) for item in _as_list(visual.get("dimensions"))],
            "measures": [_measure_ref(item) for item in _as_list(visual.get("measures"))],
            "filters": [] if is_kpi else [_field_ref(item) for item in filter_fields],
        },
        "source": {
            "qlik_id": visual.get("qlik_id") or visual.get("id") or "",
            "qlik_type": visual.get("qlik_type") or visual.get("type") or "",
        },
    }
    
    # Add KPI-specific metadata if applicable
    if is_kpi:
        container["visual_layout_type"] = "kpi_card"
        container["kpi_title"] = visual.get("kpi_title") or visual.get("title")
    
    return container


def _is_filter_visual(visual: JsonDict) -> bool:
    if str(visual.get("visual_role") or "").strip().lower() == "filter":
        return True
    has_filters = bool(_as_list(visual.get("filters")))
    has_measures = bool(_as_list(visual.get("measures")))
    return has_filters and not has_measures


def _build_llm_mapping_manifest(intermediate_model: JsonDict, semantic_model: JsonDict, report_model: JsonDict) -> JsonDict:
    expressions = []
    for expression in _as_list(intermediate_model.get("expressions")):
        if not isinstance(expression, dict):
            continue
        qlik_expression = str(expression.get("qlik_expression") or "").strip()
        if not qlik_expression:
            continue
        requires_review = _requires_llm_review(qlik_expression)
        expressions.append(
            {
                "id": expression.get("id") or "",
                "source": expression.get("source") or "",
                "owner_title": expression.get("owner_title") or "",
                "qlik_expression": qlik_expression,
                "suggested_dax": _qlik_expression_to_dax(qlik_expression),
                "status": "requires_review" if requires_review else "ok",
            }
        )

    return {
        "schema_version": "qlik_powerbi_llm_mapping/v1",
        "llm_role": "assist_mapping_only",
        "writer_role": "pbip_generator_writes_final_files",
        "recommended_model": "Codex 5.3 or compatible code-capable LLM",
        "tasks": [
            "Translate Qlik expressions to DAX measures.",
            "Review visual type equivalences between Qlik and Power BI.",
            "Flag unsupported Qlik set analysis or load script behavior.",
        ],
        "expression_mappings": expressions,
        "visual_type_mappings": _visual_type_mappings(report_model),
        "semantic_output_summary": {
            "tables": len(_as_list(semantic_model.get("tables"))),
            "relationships": len(_as_list(semantic_model.get("relationships"))),
        },
    }


def _measures_by_referenced_field(
    measures: list[Any],
    field_to_table: dict[str, str] | None = None,
    preferred_table_order: list[str] | None = None,
) -> dict[str, list[JsonDict]]:
    field_to_table = field_to_table or {}
    preferred_table_order = [str(item or "").strip().lower() for item in (preferred_table_order or []) if str(item or "").strip()]
    grouped: dict[str, list[JsonDict]] = {}
    unassigned: list[JsonDict] = []

    def _pick_table_key(referenced_fields: list[str]) -> str:
        table_scores: dict[str, int] = {}
        for field_name in referenced_fields:
            table_name = field_to_table.get(str(field_name or "").strip().lower(), "")
            if not table_name:
                continue
            table_scores[table_name.lower()] = table_scores.get(table_name.lower(), 0) + 1
        if not table_scores:
            return ""

        best_table = ""
        best_score = -1
        best_rank = len(preferred_table_order)
        for table_name, score in table_scores.items():
            rank = preferred_table_order.index(table_name) if table_name in preferred_table_order else len(preferred_table_order)
            if score > best_score or (score == best_score and rank < best_rank):
                best_table = table_name
                best_score = score
                best_rank = rank
        return best_table

    for index, measure in enumerate(measures):
        if not isinstance(measure, dict):
            continue
        name = str(measure.get("name") or measure.get("label") or f"Measure {index + 1}").strip()
        expression = str(measure.get("qlik_expression") or measure.get("expression") or "").strip()
        refs = [str(item or "").strip() for item in _as_list(measure.get("referenced_fields")) if str(item or "").strip()]
        table_key = _pick_table_key(refs)
        dax = _qlik_expression_to_dax(expression) if expression else f"SUM([{name}])"
        payload = {
            "name": _safe_measure_name(name),
            "expression": dax,
            "formatString": "General",
            "source": {
                "qlik_expression": expression,
                "requires_llm_review": _requires_llm_review(expression),
            },
        }
        if table_key:
            grouped.setdefault(table_key, []).append(payload)
        else:
            unassigned.append(payload)
    if unassigned:
        if len(preferred_table_order) == 1:
            grouped.setdefault(preferred_table_order[0].lower(), []).extend(unassigned)
        elif preferred_table_order:
            for index, payload in enumerate(unassigned):
                grouped.setdefault(preferred_table_order[index % len(preferred_table_order)].lower(), []).append(payload)
        else:
            grouped.setdefault("", []).extend(unassigned)
    return grouped


def _field_to_table_map(tables: list[JsonDict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or table.get("table_name") or "").strip()
        if not table_name:
            continue
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip().lower()
            if field_name and field_name not in mapping:
                mapping[field_name] = table_name.lower()
    return mapping


def _table_name_lookup(tables: list[JsonDict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        original = str(table.get("name") or table.get("table_name") or "").strip()
        if not original:
            continue
        canonical = _safe_name(original, "Table")
        lookup.setdefault(original.lower(), canonical)
        lookup.setdefault(canonical.lower(), canonical)
    return lookup


def _resolve_table_name(value: str, lookup: dict[str, str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    direct = lookup.get(text.lower())
    if direct:
        return direct
    safe = _safe_name(text, "")
    if safe:
        return lookup.get(safe.lower(), "")
    return ""


def _qlik_expression_to_dax(expression: str) -> str:
    text = str(expression or "").strip()
    match = re.fullmatch(r"(?i)\s*sum\s*\(\s*\[?([A-Za-z_][A-Za-z0-9_ ]*)\]?\s*\)\s*", text)
    if match:
        return f"SUM([{match.group(1).strip()}])"
    match = re.fullmatch(r"(?i)\s*count\s*\(\s*\[?([A-Za-z_][A-Za-z0-9_ ]*)\]?\s*\)\s*", text)
    if match:
        return f"COUNT([{match.group(1).strip()}])"
    match = re.fullmatch(r"(?i)\s*avg(?:erage)?\s*\(\s*\[?([A-Za-z_][A-Za-z0-9_ ]*)\]?\s*\)\s*", text)
    if match:
        return f"AVERAGE([{match.group(1).strip()}])"
    return f"/* Review Qlik expression */ {text}"


def _requires_llm_review(expression: str) -> bool:
    text = str(expression or "")
    return bool("{" in text or "}" in text or "$(" in text or "aggr(" in text.lower() or "if(" in text.lower())


def _power_query_expression(table_name: str, connections: list[Any]) -> str:
    connection = next((item for item in connections if isinstance(item, dict)), {})
    server = str(connection.get("server") or connection.get("host") or "").replace('"', '""')
    database = str(connection.get("database") or "").replace('"', '""')
    if server and database:
        return f'let Source = Sql.Database("{server}", "{database}"), Data = Source{{[Schema="dbo",Item="{table_name}"]}}[Data] in Data'
    return f'let Source = #table({{}}, {{}}) in Source /* Replace with source query for {table_name} */'


def _data_source_from_connection(connection: Any) -> JsonDict:
    if not isinstance(connection, dict):
        connection = {}
    return {
        "name": connection.get("name") or "QlikSource",
        "type": "structured",
        "connectionDetails": {
            "protocol": connection.get("provider") or "odbc",
            "server": connection.get("server") or connection.get("host") or "",
            "database": connection.get("database") or "",
        },
        "credential": {
            "AuthenticationKind": connection.get("authentication") or "",
            "Username": connection.get("username") or "",
            "PasswordProvided": bool(connection.get("password")),
        },
    }


def _relationship_payload(relationship: JsonDict) -> JsonDict:
    from_table = str(relationship.get("from_table") or "").strip()
    to_table = str(relationship.get("to_table") or "").strip()
    from_column = str(relationship.get("from_column") or "").strip()
    to_column = str(relationship.get("to_column") or "").strip()
    return {
        "name": _safe_name(f"{from_table}_{from_column}_{to_table}_{to_column}", "Relationship"),
        "fromTable": from_table,
        "fromColumn": from_column,
        "toTable": to_table,
        "toColumn": to_column,
        "cardinality": relationship.get("cardinality") or "",
        "crossFilteringBehavior": "bothDirections",
        "source": relationship.get("source") or "",
    }


def _field_ref(item: Any) -> JsonDict:
    if not isinstance(item, dict):
        return {"field": str(item or ""), "label": str(item or "")}
    return {
        "field": item.get("name") or item.get("field") or item.get("label") or "",
        "label": item.get("label") or item.get("name") or item.get("field") or "",
    }


def _measure_ref(item: Any) -> JsonDict:
    payload = _field_ref(item)
    if isinstance(item, dict):
        payload["qlik_expression"] = item.get("qlik_expression") or item.get("expression") or ""
        payload["dax_expression"] = _qlik_expression_to_dax(payload["qlik_expression"]) if payload["qlik_expression"] else ""
    return payload


def _visual_type_mappings(report_model: JsonDict) -> list[JsonDict]:
    rows = []
    for page in _as_list(report_model.get("sections")):
        for visual in _as_list(page.get("visualContainers")):
            if isinstance(visual, dict):
                rows.append(
                    {
                        "visual": visual.get("title") or visual.get("name") or "",
                        "qlik_type": (visual.get("source") or {}).get("qlik_type") or "",
                        "powerbi_type": visual.get("type") or "",
                    }
                )
    return rows


def _semantic_definition(project_name: str) -> JsonDict:
    return {
        "version": "1.0",
        "database": {
            "name": project_name,
            "compatibilityLevel": 1601,
        },
    }


def _report_definition(project_name: str) -> JsonDict:
    return {
        "version": "1.0",
        "datasetReference": {
            "byPath": {
                "path": f"../{project_name}.SemanticModel",
            }
        },
    }


def _pbip_definition(project_name: str) -> JsonDict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [
            {
                "report": {
                    "path": f"{project_name}.Report",
                }
            },
        ],
        "settings": {
            "enableAutoRecovery": True,
        },
    }


def _to_powerbi_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"integer", "int", "whole", "key"}:
        return "int64"
    if normalized in {"decimal", "double", "real", "number", "float", "measure_candidate"}:
        return "double"
    if "date" in normalized:
        return "dateTime"
    if normalized in {"boolean", "bool"}:
        return "boolean"
    return "string"


def _safe_measure_name(value: Any) -> str:
    text = str(value or "").strip() or "Measure"
    text = re.sub(r"\s+", " ", text)
    return text[:120]


def _safe_name(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
    return text[:80] or fallback


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")


def _zip_directory(source_dir: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir.parent))
