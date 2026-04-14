from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .agent_semantic_modeler import generate_semantic_models
from .agent_twb_xml_generator import generate_twb_xml, repair_twb_xml
from .db_introspection import build_db_catalog
from .llm_client import LLMClient, LLMConfig
from .rdl_parser import parse_rdl_file
from .schema_utils import summarize_xsd_elements
from .twb_builder import (
    inject_datasource_connections,
    inject_semantic_bindings,
    normalize_generated_twb,
    validate_twb_structure,
    write_twb_file,
)


def run_conversion(
    rdl_path: str | Path,
    rdl_xsd_path: str | Path,
    twb_xsd_path: str | Path,
    output_dir: str | Path,
    config_path: str | Path | None = None,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_trace: list[str] = []

    rdl_xsd_summary = summarize_xsd_elements(rdl_xsd_path)
    twb_xsd_summary = summarize_xsd_elements(twb_xsd_path)
    pipeline_trace.append("Schemas loaded")

    allowed_visual_types = {
        "Textbox",
        "Chart",
        "Tablix",
        "Rectangle",
        "GaugePanel",
        "Map",
        "Image",
        "Line",
    }.intersection(set(rdl_xsd_summary.get("element_names", [])))

    parsed_report = parse_rdl_file(rdl_path, allowed_visual_types=allowed_visual_types)
    parsed_payload = parsed_report.to_dict()
    pipeline_trace.append("RDL parsed")

    _write_json(output_dir / "parsed_rdl.json", parsed_payload)
    _write_json(output_dir / "rdl_xsd_summary.json", rdl_xsd_summary)
    _write_json(output_dir / "twb_xsd_summary.json", twb_xsd_summary)

    db_catalog = build_db_catalog(
        data_sources=parsed_payload.get("data_sources", []),
        data_sets=parsed_payload.get("data_sets", []),
    )
    _write_json(output_dir / "db_catalog.json", db_catalog)
    pipeline_trace.append("DB catalog introspection completed")

    # Keep prompts compact for local models while preserving full summaries on disk.
    rdl_xsd_prompt_summary = _compact_schema_summary(rdl_xsd_summary)
    twb_xsd_prompt_summary = _compact_schema_summary(twb_xsd_summary)
    parsed_rdl_prompt_payload = _compact_parsed_rdl_for_prompt(parsed_payload)
    pipeline_trace.append("Prompt payloads compacted")

    if config_path is None:
        raise ValueError("config_path is required")
    cfg = _load_config(config_path)
    agent1 = LLMClient(LLMConfig(**cfg["agent1"]))
    agent2 = LLMClient(LLMConfig(**cfg.get("agent2", cfg["agent1"])))
    repair_llm: LLMClient | None = agent2
    data_model, visual_model, mapping = generate_semantic_models(
        llm=agent1,
        parsed_rdl=parsed_rdl_prompt_payload,
        rdl_xsd_summary=rdl_xsd_prompt_summary,
        twb_xsd_summary=twb_xsd_prompt_summary,
        output_dir=output_dir,
    )
    pipeline_trace.append("Agent-1 semantic generation succeeded")

    data_model_prompt_payload, visual_model_prompt_payload, mapping_prompt_payload = (
        _compact_semantic_for_prompt(data_model, visual_model, mapping)
    )

    try:
        xml_content = generate_twb_xml(
            llm=agent2,
            data_model=data_model_prompt_payload,
            visual_model=visual_model_prompt_payload,
            mapping=mapping_prompt_payload,
            twb_xsd_summary=twb_xsd_prompt_summary,
            output_xml_path=output_dir / "generated_workbook.xml",
        )
        pipeline_trace.append("Agent-2 XML generation succeeded")
    except Exception as exc:
        xml_content = _build_deterministic_seed_twb_xml(data_model, visual_model)
        pipeline_trace.append(
            "Agent-2 XML generation failed; deterministic seed workbook generated "
            f"({type(exc).__name__}: {exc})"
        )

    if not isinstance(xml_content, str) or not xml_content.strip():
        xml_content = _build_deterministic_seed_twb_xml(data_model, visual_model)
        pipeline_trace.append("Agent-2 XML response empty; deterministic seed workbook generated")

    xml_content = inject_datasource_connections(
        xml_content=xml_content,
        data_sources=parsed_payload.get("data_sources", []),
        data_sets=parsed_payload.get("data_sets", []),
        db_catalog=db_catalog,
    )
    xml_content = inject_semantic_bindings(
        xml_content=xml_content,
        data_model=data_model,
        visual_model=visual_model,
        mapping=mapping,
        report_parameters=parsed_payload.get("report_parameters", []),
    )

    xml_content, issues = _validate_and_repair_loop(
        xml_content=xml_content,
        twb_xsd_summary=twb_xsd_summary,
        llm=repair_llm,
    )
    pipeline_trace.append("Validation/repair loop completed")

    (output_dir / "generated_workbook.xml").write_text(xml_content, encoding="utf-8")
    _write_json(output_dir / "validation_report.json", {"issues": issues})

    twb_path = write_twb_file(xml_content, output_dir / "converted_report.twb")
    pipeline_trace.append("TWB file written")
    _write_json(output_dir / "pipeline_trace.json", {"steps": pipeline_trace})

    return {
        "parsed_rdl": str(output_dir / "parsed_rdl.json"),
        "data_model": str(output_dir / "data_model.json"),
        "visual_model": str(output_dir / "visual_model.json"),
        "mapping": str(output_dir / "mapping.json"),
        "semantic_generation_report": str(output_dir / "semantic_generation_report.json"),
        "agent1_raw_response": str(output_dir / "agent1_raw_response.txt"),
        "db_catalog": str(output_dir / "db_catalog.json"),
        "generated_xml": str(output_dir / "generated_workbook.xml"),
        "twb": str(twb_path),
        "pipeline_trace": str(output_dir / "pipeline_trace.json"),
    }


def _build_deterministic_seed_twb_xml(data_model: dict, visual_model: dict) -> str:
    workbook = ET.Element(
        "workbook",
        attrib={
            "version": "18.1",
            "source-build": "2024.2.0 (20242.24.0620.1454)",
            "source-platform": "win",
        },
    )

    ET.SubElement(workbook, "preferences")
    ET.SubElement(workbook, "style")

    datasources_el = ET.SubElement(workbook, "datasources")
    datasource_name = "DataSource_1"
    datasources = data_model.get("datasources", []) if isinstance(data_model, dict) else []
    if isinstance(datasources, list) and datasources:
        first = datasources[0]
        if isinstance(first, dict):
            name = first.get("name")
            if isinstance(name, str) and name.strip():
                datasource_name = name.strip()

    ds_node = ET.SubElement(
        datasources_el,
        "datasource",
        attrib={
            "name": datasource_name,
            "caption": datasource_name,
            "inline": "true",
            "hasconnection": "true",
        },
    )
    ET.SubElement(ds_node, "connection", attrib={"class": "genericodbc"})

    worksheets_el = ET.SubElement(workbook, "worksheets")
    sheet_names: list[str] = []
    sheets = visual_model.get("sheets", []) if isinstance(visual_model, dict) else []
    if isinstance(sheets, list):
        for item in sheets:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip() and name.strip() not in sheet_names:
                sheet_names.append(name.strip())

    if not sheet_names:
        sheet_names = ["Sheet 1"]

    for name in sheet_names:
        ws = ET.SubElement(worksheets_el, "worksheet", attrib={"name": name})
        ET.SubElement(ws, "layout-options")
        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        view_dss = ET.SubElement(view, "datasources")
        ET.SubElement(view_dss, "datasource", attrib={"name": datasource_name})
        ET.SubElement(view, "datasource-dependencies", attrib={"datasource": datasource_name})
        ET.SubElement(view, "perspectives")
        ET.SubElement(view, "aggregation", attrib={"value": "true"})
        ET.SubElement(table, "style")
        ET.SubElement(table, "panes")
        ET.SubElement(table, "rows")
        ET.SubElement(table, "cols")

    windows_el = ET.SubElement(workbook, "windows")
    for name in sheet_names:
        win = ET.SubElement(windows_el, "window", attrib={"name": name, "class": "worksheet"})
        ET.SubElement(win, "cards")
        ET.SubElement(win, "viewpoint")

    return ET.tostring(workbook, encoding="unicode")


def _load_config(config_path: str | Path) -> dict:
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _validate_and_repair_loop(
    xml_content: str,
    twb_xsd_summary: dict,
    llm: LLMClient | None,
) -> tuple[str, list[str]]:
    current = normalize_generated_twb(xml_content)
    issues = validate_twb_structure(current)
    if not issues:
        return current, []

    if llm is None:
        return current, issues

    repaired = repair_twb_xml(
        llm=llm,
        invalid_xml=current,
        issues=issues,
        twb_xsd_summary=_compact_schema_summary(twb_xsd_summary),
    )
    repaired = normalize_generated_twb(repaired)
    repaired_issues = validate_twb_structure(repaired)
    return repaired, repaired_issues


def _compact_schema_summary(summary: dict, max_element_names: int = 200) -> dict:
    element_names = summary.get("element_names", [])
    sample_elements = summary.get("sample_elements", [])
    sample_attributes = summary.get("sample_attributes", [])

    if not isinstance(element_names, list):
        element_names = []
    if not isinstance(sample_elements, list):
        sample_elements = []
    if not isinstance(sample_attributes, list):
        sample_attributes = []

    return {
        "schema_file": summary.get("schema_file"),
        "element_count": summary.get("element_count"),
        "attribute_count": summary.get("attribute_count"),
        "element_names": element_names[:max_element_names],
        "element_names_truncated": len(element_names) > max_element_names,
        "sample_elements": sample_elements,
        "sample_attributes": sample_attributes,
    }


def _compact_parsed_rdl_for_prompt(parsed_rdl: dict) -> dict:
    data_sources = parsed_rdl.get("data_sources", []) if isinstance(parsed_rdl, dict) else []
    data_sets = parsed_rdl.get("data_sets", []) if isinstance(parsed_rdl, dict) else []
    visuals = parsed_rdl.get("visuals", []) if isinstance(parsed_rdl, dict) else []
    report_parameters = parsed_rdl.get("report_parameters", []) if isinstance(parsed_rdl, dict) else []
    report_metadata = parsed_rdl.get("report_metadata", {}) if isinstance(parsed_rdl, dict) else {}
    report_sections = parsed_rdl.get("report_sections", []) if isinstance(parsed_rdl, dict) else []

    compact_datasets: list[dict] = []
    for ds in data_sets[:80]:
        if not isinstance(ds, dict):
            continue
        fields = ds.get("fields", [])
        compact_fields = []
        if isinstance(fields, list):
            for f in fields[:80]:
                if not isinstance(f, dict):
                    continue
                compact_fields.append(
                    {
                        "name": f.get("name"),
                        "data_field": f.get("data_field"),
                        "type_name": f.get("type_name"),
                    }
                )

        query = ds.get("query")
        if isinstance(query, str) and len(query) > 1200:
            query = query[:1200] + " ...[truncated]"

        query_parameters = ds.get("query_parameters", [])
        compact_query_parameters: list[dict] = []
        if isinstance(query_parameters, list):
            for parameter in query_parameters[:40]:
                if not isinstance(parameter, dict):
                    continue
                compact_query_parameters.append(
                    {
                        "name": parameter.get("name"),
                        "value": parameter.get("value"),
                        "parameter_references": parameter.get("parameter_references", []),
                    }
                )

        filters = ds.get("filters", [])
        compact_filters = filters[:30] if isinstance(filters, list) else []

        sort_expressions = ds.get("sort_expressions", [])
        compact_sort_expressions = sort_expressions[:30] if isinstance(sort_expressions, list) else []

        compact_datasets.append(
            {
                "name": ds.get("name"),
                "query": query,
                "data_source_name": ds.get("data_source_name"),
                "fields": compact_fields,
                "query_parameters": compact_query_parameters,
                "filters": compact_filters,
                "sort_expressions": compact_sort_expressions,
            }
        )

    compact_visuals = _compact_visual_tree_for_prompt(visuals, max_nodes=160)

    return {
        "report_name": parsed_rdl.get("report_name") if isinstance(parsed_rdl, dict) else None,
        "namespace": parsed_rdl.get("namespace") if isinstance(parsed_rdl, dict) else None,
        "data_sources": data_sources[:20] if isinstance(data_sources, list) else [],
        "data_sets": compact_datasets,
        "visuals": compact_visuals,
        "report_parameters": report_parameters[:60] if isinstance(report_parameters, list) else [],
        "report_metadata": report_metadata if isinstance(report_metadata, dict) else {},
        "report_sections": report_sections[:20] if isinstance(report_sections, list) else [],
    }


def _compact_visual_tree_for_prompt(visuals: list, max_nodes: int) -> list[dict]:
    compact: list[dict] = []
    remaining = max_nodes

    def _walk(nodes: list) -> list[dict]:
        nonlocal remaining
        out: list[dict] = []
        for node in nodes:
            if remaining <= 0:
                break
            if not isinstance(node, dict):
                continue
            remaining -= 1

            expressions = node.get("expressions", [])
            if isinstance(expressions, list):
                compact_expr = [str(e)[:220] for e in expressions[:8]]
            else:
                compact_expr = []

            layout = node.get("layout", {}) if isinstance(node.get("layout", {}), dict) else {}
            compact_layout = {
                "top": layout.get("top"),
                "left": layout.get("left"),
                "height": layout.get("height"),
                "width": layout.get("width"),
                "referenced_fields": layout.get("referenced_fields"),
            }

            children = node.get("children", []) if isinstance(node.get("children", []), list) else []
            properties = node.get("properties", {}) if isinstance(node.get("properties", {}), dict) else {}
            out.append(
                {
                    "name": node.get("name"),
                    "visual_type": node.get("visual_type"),
                    "dataset_name": node.get("dataset_name"),
                    "expressions": compact_expr,
                    "layout": compact_layout,
                    "properties": _compact_visual_properties(properties),
                    "children": _walk(children),
                }
            )
        return out

    if isinstance(visuals, list):
        compact = _walk(visuals)
    return compact


def _compact_visual_properties(properties: dict) -> dict:
    if not isinstance(properties, dict):
        return {}

    compact: dict[str, object] = {}

    for key in [
        "field_references",
        "parameter_references",
        "text_values",
        "hidden_expression",
        "container_section",
        "semantic_hint",
    ]:
        value = properties.get(key)
        if isinstance(value, list):
            compact[key] = value[:20]
        elif value not in (None, ""):
            compact[key] = value

    for key in ["image", "gauge"]:
        value = properties.get(key)
        if isinstance(value, dict) and value:
            compact[key] = value

    for key in ["filters", "sort_expressions", "groups"]:
        value = properties.get(key)
        if isinstance(value, list) and value:
            compact[key] = value[:25]

    chart = properties.get("chart")
    if isinstance(chart, dict) and chart:
        compact["chart"] = chart

    tablix = properties.get("tablix")
    if isinstance(tablix, dict) and tablix:
        compact["tablix"] = {
            "row_groups": tablix.get("row_groups", [])[:20] if isinstance(tablix.get("row_groups"), list) else [],
            "column_groups": tablix.get("column_groups", [])[:20]
            if isinstance(tablix.get("column_groups"), list)
            else [],
            "cell_expressions": tablix.get("cell_expressions", [])[:20]
            if isinstance(tablix.get("cell_expressions"), list)
            else [],
        }

    return compact


def _compact_semantic_for_prompt(data_model: dict, visual_model: dict, mapping: dict) -> tuple[dict, dict, dict]:
    compact_data_model = {
        "datasources": (data_model.get("datasources", []) if isinstance(data_model, dict) else [])[:20],
        "datasets": (data_model.get("datasets", []) if isinstance(data_model, dict) else [])[:80],
        "fields": (data_model.get("fields", []) if isinstance(data_model, dict) else [])[:200],
        "parameters": (data_model.get("parameters", []) if isinstance(data_model, dict) else [])[:60],
    }

    compact_visual_model = {
        "sheets": (visual_model.get("sheets", []) if isinstance(visual_model, dict) else [])[:120],
        "visuals": (visual_model.get("visuals", []) if isinstance(visual_model, dict) else [])[:160],
        "layout": visual_model.get("layout", {}) if isinstance(visual_model, dict) else {},
        "interactions": (visual_model.get("interactions", []) if isinstance(visual_model, dict) else [])[:120],
    }

    compact_mapping = {
        "visual_to_dataset": (mapping.get("visual_to_dataset", []) if isinstance(mapping, dict) else [])[:200],
        "visual_to_fields": (mapping.get("visual_to_fields", []) if isinstance(mapping, dict) else [])[:200],
        "parameter_usage": (mapping.get("parameter_usage", []) if isinstance(mapping, dict) else [])[:120],
    }

    return compact_data_model, compact_visual_model, compact_mapping


