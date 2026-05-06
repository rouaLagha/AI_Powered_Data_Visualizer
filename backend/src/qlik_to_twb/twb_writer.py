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
            "inline": "true",
            "name": datasource_name,
            "version": "18.1",
        },
    )
    connection = ET.SubElement(datasource, "connection", attrib={"class": "textscan"})
    relation = ET.SubElement(connection, "relation", attrib={"name": "QlikModel", "type": "text"})
    relation.text = "SELECT * FROM [QlikModel]"
    ET.SubElement(datasource, "aliases", attrib={"enabled": "yes"})

    for field in _collect_fields(intermediate_model, mapping):
        ET.SubElement(
            datasource,
            "column",
            attrib={
                "caption": field["name"],
                "datatype": field["datatype"],
                "name": f"[{field['name']}]",
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
                f"{sheet.get('tableau_name') or sheet.get('name') or 'Sheet'} - {visual.get('title') or 'Visual'}"
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
            ET.SubElement(view, "datasource-dependencies", attrib={"datasource": datasource_name})
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
    root_zone = ET.SubElement(zones, "zone", attrib={"type-v2": "layout-basic"})

    x = 0
    y = 0
    for index, worksheet_name in enumerate(worksheet_names):
        ET.SubElement(
            root_zone,
            "zone",
            attrib={
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

    for worksheet_name in worksheet_names:
        window = ET.SubElement(windows, "window", attrib={"class": "worksheet", "name": worksheet_name})
        ET.SubElement(window, "cards")
        ET.SubElement(window, "viewpoint")


def _collect_fields(intermediate_model: JsonDict, mapping: JsonDict) -> list[JsonDict]:
    fields: dict[str, JsonDict] = {}
    for field in _as_list(intermediate_model.get("fields")):
        if not isinstance(field, dict):
            continue
        name = _clean_field_name(field.get("name") or "")
        if not name:
            continue
        fields[name] = {
            "name": name,
            "datatype": _tableau_datatype(field.get("data_type")),
            "role": field.get("role") if field.get("role") in {"dimension", "measure"} else "dimension",
            "type": "quantitative" if field.get("role") == "measure" else "nominal",
        }

    for sheet in _as_list(mapping.get("sheets")):
        for visual in _as_list(sheet.get("visuals")):
            for dimension in _as_list(visual.get("dimensions")):
                name = _clean_field_name(dimension.get("field") if isinstance(dimension, dict) else "")
                if name:
                    fields.setdefault(
                        name,
                        {"name": name, "datatype": "string", "role": "dimension", "type": "nominal"},
                    )
            for measure in _as_list(visual.get("measures")):
                expression = measure.get("tableau_expression") if isinstance(measure, dict) else ""
                for name in _extract_fields_from_tableau_expression(expression):
                    fields.setdefault(
                        name,
                        {"name": name, "datatype": "real", "role": "measure", "type": "quantitative"},
                    )

    return sorted(fields.values(), key=lambda item: item["name"].lower())


def _dimension_shelf(datasource_name: str, visual: JsonDict) -> str:
    dimensions = [item for item in _as_list(visual.get("dimensions")) if isinstance(item, dict)]
    if not dimensions:
        return ""
    first = _clean_field_name(dimensions[0].get("field") or "")
    return f"[{datasource_name}].[none:{first}:nk]" if first else ""


def _measure_shelf(datasource_name: str, visual: JsonDict) -> str:
    measures = [item for item in _as_list(visual.get("measures")) if isinstance(item, dict)]
    if not measures:
        return ""
    expression = str(measures[0].get("tableau_expression") or "")
    fields = _extract_fields_from_tableau_expression(expression)
    aggregation = expression.split("(", 1)[0].lower() if "(" in expression else "sum"
    first = fields[0] if fields else _clean_field_name(expression)
    return f"[{datasource_name}].[{aggregation}:{first}:qk]" if first else ""


def _mark_class(tableau_type: Any) -> str:
    return {
        "bar_chart": "Bar",
        "line_chart": "Line",
        "kpi": "Text",
        "table": "Text",
        "filter": "Text",
    }.get(str(tableau_type or ""), "Automatic")


def _extract_fields_from_tableau_expression(expression: Any) -> list[str]:
    return [_clean_field_name(match) for match in re.findall(r"\[([^\]]+)\]", str(expression or ""))]


def _tableau_datatype(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"integer", "int", "number", "numeric"}:
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
