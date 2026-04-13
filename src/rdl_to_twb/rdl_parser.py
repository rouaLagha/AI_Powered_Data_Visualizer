from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from .models import ParsedDataSet, ParsedDataSource, ParsedField, ParsedReport, ParsedVisual


FIELD_EXPR_RE = re.compile(r"Fields!([A-Za-z0-9_]+)\.Value", re.IGNORECASE)
PARAM_EXPR_RE = re.compile(r"Parameters!([A-Za-z0-9_]+)\.Value", re.IGNORECASE)
DATASET_EXPR_RE = re.compile(r"DataSetName\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


def _ns_uri(root_tag: str) -> str:
    if root_tag.startswith("{") and "}" in root_tag:
        return root_tag[1:].split("}", 1)[0]
    return ""


def _qn(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _text(parent: ET.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(path)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _find_children_by_local(parent: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in list(parent) if _local_name(child.tag) == local]


def _find_first_text_by_local(node: ET.Element, local_names: set[str]) -> str | None:
    for element in node.iter():
        if _local_name(element.tag) not in local_names or element.text is None:
            continue
        value = element.text.strip()
        if value:
            return value
    return None


def _findall_by_local(node: ET.Element, local_name: str) -> list[ET.Element]:
    return [element for element in node.iter() if _local_name(element.tag) == local_name]


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _looks_like_expression(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("="):
        return True
    markers = ["Fields!", "Parameters!", "Globals!", "ReportItems!"]
    return any(marker in stripped for marker in markers)


def _collect_expressions(node: ET.Element) -> list[str]:
    expressions: list[str] = []
    for element in node.iter():
        if element.text is None:
            continue
        text = element.text.strip()
        if _looks_like_expression(text):
            expressions.append(text)
    return _unique_ordered(expressions)


def _extract_field_refs(expressions: list[str]) -> list[str]:
    refs: list[str] = []
    for expr in expressions:
        for match in FIELD_EXPR_RE.finditer(expr):
            refs.append(match.group(1))
    return _unique_ordered(refs)


def _extract_param_refs(expressions: list[str]) -> list[str]:
    refs: list[str] = []
    for expr in expressions:
        for match in PARAM_EXPR_RE.finditer(expr):
            refs.append(match.group(1))
    return _unique_ordered(refs)


def _extract_dataset_name(node: ET.Element, ns: str) -> str | None:
    dataset_name = _text(node, _qn(ns, "DataSetName"))
    if dataset_name:
        return dataset_name

    dataset_name = _find_first_text_by_local(node, {"DataSetName"})
    if dataset_name:
        return dataset_name

    for attr_key, attr_value in node.attrib.items():
        if _local_name(attr_key).lower() == "datasetname" and attr_value.strip():
            return attr_value.strip()

    for element in node.iter():
        if element.text is None:
            continue
        match = DATASET_EXPR_RE.search(element.text)
        if match:
            return match.group(1).strip()
    return None


def _extract_layout(node: ET.Element, ns: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ["Top", "Left", "Height", "Width", "ZIndex"]:
        value = _text(node, _qn(ns, key))
        if value is not None:
            result[key.lower()] = value
    return result


def _extract_filters(node: ET.Element, ns: str) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    filter_nodes = node.findall(f".//{_qn(ns, 'Filter')}")
    if not filter_nodes:
        filter_nodes = _findall_by_local(node, "Filter")

    for filter_node in filter_nodes:
        expression = _text(filter_node, _qn(ns, "FilterExpression")) or _find_first_text_by_local(
            filter_node,
            {"FilterExpression"},
        )
        operator = _text(filter_node, _qn(ns, "Operator")) or _find_first_text_by_local(
            filter_node,
            {"Operator"},
        )

        values = [
            value.text.strip()
            for value in filter_node.findall(
                f"{_qn(ns, 'FilterValues')}/{_qn(ns, 'FilterValue')}/{_qn(ns, 'Value')}"
            )
            if value.text and value.text.strip()
        ]
        if not values:
            for value_node in _findall_by_local(filter_node, "FilterValue"):
                if value_node.text and value_node.text.strip():
                    values.append(value_node.text.strip())
                    continue
                candidate = _find_first_text_by_local(value_node, {"Value"})
                if candidate:
                    values.append(candidate)

        data_type = _text(filter_node, _qn(ns, "DataType")) or _find_first_text_by_local(
            filter_node,
            {"DataType"},
        )

        parameter_references = _extract_param_refs(
            [x for x in [expression, *values] if isinstance(x, str)]
        )

        if expression or operator or values or data_type:
            filters.append(
                {
                    "expression": expression,
                    "operator": operator,
                    "values": _unique_ordered(values),
                    "data_type": data_type,
                    "parameter_references": parameter_references,
                }
            )

    return filters


def _extract_sort_expressions(node: ET.Element, ns: str) -> list[dict[str, Any]]:
    sorts: list[dict[str, Any]] = []
    sort_nodes = node.findall(f".//{_qn(ns, 'SortExpression')}")
    if not sort_nodes:
        sort_nodes = _findall_by_local(node, "SortExpression")

    for sort_node in sort_nodes:
        value = _text(sort_node, _qn(ns, "Value")) or _find_first_text_by_local(sort_node, {"Value"})
        direction = _text(sort_node, _qn(ns, "Direction")) or _find_first_text_by_local(
            sort_node,
            {"Direction"},
        )
        if value or direction:
            sorts.append({"value": value, "direction": direction})

    return sorts


def _extract_groups(node: ET.Element, ns: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    group_nodes = node.findall(f".//{_qn(ns, 'Group')}")
    if not group_nodes:
        group_nodes = _findall_by_local(node, "Group")

    for group_node in group_nodes:
        expressions = [
            expr.text.strip()
            for expr in group_node.findall(f"{_qn(ns, 'GroupExpressions')}/{_qn(ns, 'GroupExpression')}")
            if expr.text and expr.text.strip()
        ]
        if not expressions:
            expressions = [
                expr.text.strip()
                for expr in _findall_by_local(group_node, "GroupExpression")
                if expr.text and expr.text.strip()
            ]

        groups.append(
            {
                "name": group_node.attrib.get("Name"),
                "expressions": _unique_ordered(expressions),
                "page_break_at_start": _text(group_node, _qn(ns, "PageBreakAtStart"))
                or _find_first_text_by_local(group_node, {"PageBreakAtStart"}),
                "page_break_at_end": _text(group_node, _qn(ns, "PageBreakAtEnd"))
                or _find_first_text_by_local(group_node, {"PageBreakAtEnd"}),
            }
        )

    # Drop empty shells to keep payload useful for prompting.
    return [
        g
        for g in groups
        if g.get("name") or g.get("expressions") or g.get("page_break_at_start") or g.get("page_break_at_end")
    ]


def _extract_chart_subtype(node: ET.Element) -> str | None:
    known_chart_types = {
        "area",
        "bar",
        "bubble",
        "column",
        "doughnut",
        "donut",
        "funnel",
        "line",
        "pie",
        "point",
        "polar",
        "pyramid",
        "radar",
        "range",
        "scatter",
        "shape",
        "spline",
        "stackedarea",
        "stackedbar",
        "stackedcolumn",
        "stepline",
    }

    subtype_aliases = {
        "pie": "Pie",
        "explodedpie": "Pie",
        "doughnut": "Doughnut",
        "donut": "Doughnut",
        "line": "Line",
        "spline": "Line",
        "stepline": "Line",
        "area": "Area",
        "stackedarea": "Area",
        "bar": "Bar",
        "stackedbar": "Bar",
        "column": "Bar",
        "stackedcolumn": "Bar",
        "scatter": "Scatter",
        "point": "Scatter",
        "bubble": "Scatter",
        "shape": "Shape",
    }

    for tag_name in ["SeriesChartSubtype", "ChartSubtype", "Subtype"]:
        for element in node.iter():
            local = _local_name(element.tag)
            if local != tag_name or element.text is None:
                continue
            value = element.text.strip()
            if not value:
                continue
            canonical = subtype_aliases.get(value.lower())
            if canonical:
                return canonical

    for tag_name in ["SeriesChartType", "ChartType"]:
        for element in node.iter():
            local = _local_name(element.tag)
            if local != tag_name or element.text is None:
                continue
            value = element.text.strip()
            if not value:
                continue
            canonical = subtype_aliases.get(value.lower())
            if canonical:
                return canonical
            return value

    for element in node.iter():
        local = _local_name(element.tag)
        if local != "Type" or element.text is None:
            continue
        value = element.text.strip()
        if not value:
            continue
        canonical = subtype_aliases.get(value.lower())
        if canonical:
            return canonical
        if value.lower() in known_chart_types:
            return value

    return None


def _extract_textbox_values(node: ET.Element, ns: str) -> list[str]:
    values = [
        value.text.strip()
        for value in node.findall(f".//{_qn(ns, 'TextRun')}/{_qn(ns, 'Value')}")
        if value.text and value.text.strip()
    ]
    if not values:
        for text_run in _findall_by_local(node, "TextRun"):
            value = _find_first_text_by_local(text_run, {"Value"})
            if value:
                values.append(value)
    return _unique_ordered(values)


def _extract_chart_details(node: ET.Element, ns: str, visual_type: str) -> dict[str, Any]:
    category_expressions = [
        expr.text.strip()
        for expr in node.findall(f".//{_qn(ns, 'ChartCategoryHierarchy')}//{_qn(ns, 'GroupExpression')}")
        if expr.text and expr.text.strip()
    ]

    category_labels = [
        label.text.strip()
        for label in node.findall(f".//{_qn(ns, 'ChartCategoryHierarchy')}//{_qn(ns, 'Label')}")
        if label.text and label.text.strip()
    ]

    series_names: list[str] = []
    for series in node.findall(f".//{_qn(ns, 'ChartSeries')}"):
        name = series.attrib.get("Name")
        if isinstance(name, str) and name.strip():
            series_names.append(name.strip())
    series_names.extend(
        [
            label.text.strip()
            for label in node.findall(f".//{_qn(ns, 'ChartSeriesHierarchy')}//{_qn(ns, 'Label')}")
            if label.text and label.text.strip()
        ]
    )

    value_expressions: list[str] = []
    for values_node in node.findall(f".//{_qn(ns, 'ChartDataPointValues')}"):
        for child in list(values_node):
            if child.text and child.text.strip() and _looks_like_expression(child.text):
                value_expressions.append(child.text.strip())

    chart_titles = [
        caption.text.strip()
        for caption in node.findall(f".//{_qn(ns, 'ChartTitles')}/{_qn(ns, 'ChartTitle')}/{_qn(ns, 'Caption')}")
        if caption.text and caption.text.strip()
    ]

    payload: dict[str, Any] = {
        "palette": _text(node, _qn(ns, "Palette")) or _find_first_text_by_local(node, {"Palette"}),
        "category_expressions": _unique_ordered(category_expressions),
        "category_labels": _unique_ordered(category_labels),
        "series_names": _unique_ordered(series_names),
        "value_expressions": _unique_ordered(value_expressions),
        "titles": _unique_ordered(chart_titles),
    }
    if visual_type != "Chart":
        payload["resolved_chart_type"] = visual_type

    return {k: v for k, v in payload.items() if v not in (None, [], "")}


def _extract_tablix_member_groups(member: ET.Element, ns: str) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []

    for group in _find_children_by_local(member, "Group"):
        expressions = [
            expr.text.strip()
            for expr in group.findall(f"{_qn(ns, 'GroupExpressions')}/{_qn(ns, 'GroupExpression')}")
            if expr.text and expr.text.strip()
        ]
        if not expressions:
            expressions = [
                expr.text.strip()
                for expr in _findall_by_local(group, "GroupExpression")
                if expr.text and expr.text.strip()
            ]

        groups.append(
            {
                "name": group.attrib.get("Name"),
                "expressions": _unique_ordered(expressions),
            }
        )

    nested_members_container = next(iter(_find_children_by_local(member, "TablixMembers")), None)
    if nested_members_container is not None:
        for nested_member in _find_children_by_local(nested_members_container, "TablixMember"):
            groups.extend(_extract_tablix_member_groups(nested_member, ns))

    return [g for g in groups if g.get("name") or g.get("expressions")]


def _extract_tablix_axis_groups(node: ET.Element, ns: str, hierarchy_local: str) -> list[dict[str, Any]]:
    hierarchy = node.find(_qn(ns, hierarchy_local))
    if hierarchy is None:
        hierarchy = next(iter(_find_children_by_local(node, hierarchy_local)), None)
    if hierarchy is None:
        return []

    members_container = hierarchy.find(_qn(ns, "TablixMembers"))
    if members_container is None:
        members_container = next(iter(_find_children_by_local(hierarchy, "TablixMembers")), None)
    if members_container is None:
        return []

    groups: list[dict[str, Any]] = []
    for member in _find_children_by_local(members_container, "TablixMember"):
        groups.extend(_extract_tablix_member_groups(member, ns))
    return groups


def _extract_tablix_details(node: ET.Element, ns: str) -> dict[str, Any]:
    tablix_body = node.find(_qn(ns, "TablixBody"))
    cell_expressions = _collect_expressions(tablix_body) if tablix_body is not None else []

    details = {
        "row_groups": _extract_tablix_axis_groups(node, ns, "TablixRowHierarchy"),
        "column_groups": _extract_tablix_axis_groups(node, ns, "TablixColumnHierarchy"),
        "cell_expressions": cell_expressions,
    }
    return {k: v for k, v in details.items() if v}


def _extract_query_parameters(dataset_node: ET.Element, ns: str) -> list[dict[str, Any]]:
    query_parameters: list[dict[str, Any]] = []
    query_path = f"{_qn(ns, 'Query')}/{_qn(ns, 'QueryParameters')}/{_qn(ns, 'QueryParameter')}"
    for param in dataset_node.findall(query_path):
        value = _text(param, _qn(ns, "Value")) or _find_first_text_by_local(param, {"Value"})
        parameter_refs = _extract_param_refs([value]) if isinstance(value, str) else []
        query_parameters.append(
            {
                "name": param.attrib.get("Name"),
                "value": value,
                "parameter_references": parameter_refs,
            }
        )

    if query_parameters:
        return query_parameters

    # Namespace-agnostic fallback.
    query_node = next(iter(_find_children_by_local(dataset_node, "Query")), None)
    if query_node is None:
        return []

    query_params_node = next(iter(_find_children_by_local(query_node, "QueryParameters")), None)
    if query_params_node is None:
        return []

    for param in _find_children_by_local(query_params_node, "QueryParameter"):
        value = _find_first_text_by_local(param, {"Value"})
        parameter_refs = _extract_param_refs([value]) if isinstance(value, str) else []
        query_parameters.append(
            {
                "name": param.attrib.get("Name"),
                "value": value,
                "parameter_references": parameter_refs,
            }
        )

    return query_parameters


def _extract_report_sections(root: ET.Element, ns: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []

    section_nodes = root.findall(f".//{_qn(ns, 'ReportSection')}")
    if not section_nodes:
        section_nodes = _findall_by_local(root, "ReportSection")

    for section in section_nodes:
        body = section.find(_qn(ns, "Body"))
        if body is None:
            body = next(iter(_find_children_by_local(section, "Body")), None)

        page = section.find(_qn(ns, "Page"))
        if page is None:
            page = next(iter(_find_children_by_local(section, "Page")), None)

        report_items_node = body.find(_qn(ns, "ReportItems")) if body is not None else None
        if report_items_node is None and body is not None:
            report_items_node = next(iter(_find_children_by_local(body, "ReportItems")), None)

        page_summary = {
            "left_margin": _text(page, _qn(ns, "LeftMargin")) if page is not None else None,
            "right_margin": _text(page, _qn(ns, "RightMargin")) if page is not None else None,
            "top_margin": _text(page, _qn(ns, "TopMargin")) if page is not None else None,
            "bottom_margin": _text(page, _qn(ns, "BottomMargin")) if page is not None else None,
        }

        sections.append(
            {
                "width": _text(section, _qn(ns, "Width")) or _find_first_text_by_local(section, {"Width"}),
                "body_height": _text(body, _qn(ns, "Height")) if body is not None else None,
                "report_items_count": len(list(report_items_node)) if report_items_node is not None else 0,
                "page": {k: v for k, v in page_summary.items() if v is not None},
            }
        )

    return sections


def _extract_report_metadata(root: ET.Element, ns: str, report_sections: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {
        "auto_refresh": _text(root, _qn(ns, "AutoRefresh")) or _find_first_text_by_local(root, {"AutoRefresh"}),
        "language": _text(root, _qn(ns, "Language")) or _find_first_text_by_local(root, {"Language"}),
        "consume_container_whitespace": _text(root, _qn(ns, "ConsumeContainerWhitespace"))
        or _find_first_text_by_local(root, {"ConsumeContainerWhitespace"}),
        "default_font_family": _find_first_text_by_local(root, {"DefaultFontFamily"}),
        "report_sections_count": len(report_sections),
    }

    params_layout = root.find(f".//{_qn(ns, 'ReportParametersLayout')}")
    if params_layout is not None:
        metadata["report_parameters_layout"] = {
            "columns": _text(params_layout, f"{_qn(ns, 'GridLayoutDefinition')}/{_qn(ns, 'NumberOfColumns')}"),
            "rows": _text(params_layout, f"{_qn(ns, 'GridLayoutDefinition')}/{_qn(ns, 'NumberOfRows')}"),
        }

    return {k: v for k, v in metadata.items() if v not in (None, "")}


def _extract_parameter_defaults(param_node: ET.Element, ns: str) -> list[str]:
    values = [
        value.text.strip()
        for value in param_node.findall(
            f"{_qn(ns, 'DefaultValue')}/{_qn(ns, 'Values')}/{_qn(ns, 'Value')}"
        )
        if value.text and value.text.strip()
    ]
    return _unique_ordered(values)


def _extract_parameter_valid_values(param_node: ET.Element, ns: str) -> dict[str, Any]:
    dataset_reference = {
        "dataset_name": _text(
            param_node,
            f"{_qn(ns, 'ValidValues')}/{_qn(ns, 'DataSetReference')}/{_qn(ns, 'DataSetName')}",
        ),
        "value_field": _text(
            param_node,
            f"{_qn(ns, 'ValidValues')}/{_qn(ns, 'DataSetReference')}/{_qn(ns, 'ValueField')}",
        ),
        "label_field": _text(
            param_node,
            f"{_qn(ns, 'ValidValues')}/{_qn(ns, 'DataSetReference')}/{_qn(ns, 'LabelField')}",
        ),
    }

    static_values: list[dict[str, Any]] = []
    for value_node in param_node.findall(
        f"{_qn(ns, 'ValidValues')}/{_qn(ns, 'ParameterValues')}/{_qn(ns, 'ParameterValue')}"
    ):
        static_values.append(
            {
                "value": _text(value_node, _qn(ns, "Value")) or _find_first_text_by_local(value_node, {"Value"}),
                "label": _text(value_node, _qn(ns, "Label")) or _find_first_text_by_local(value_node, {"Label"}),
            }
        )

    payload: dict[str, Any] = {}
    if any(v for v in dataset_reference.values()):
        payload["dataset_reference"] = dataset_reference
    if static_values:
        payload["static_values"] = static_values
    return payload


def _extract_visual_properties(
    node: ET.Element,
    ns: str,
    visual_type: str,
    expressions: list[str],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}

    filters = _extract_filters(node, ns)
    if filters:
        properties["filters"] = filters

    sort_expressions = _extract_sort_expressions(node, ns)
    if sort_expressions:
        properties["sort_expressions"] = sort_expressions

    groups = _extract_groups(node, ns)
    if groups:
        properties["groups"] = groups

    hidden_expression = _text(node, _qn(ns, "Hidden")) or _find_first_text_by_local(node, {"Hidden"})
    if hidden_expression:
        properties["hidden_expression"] = hidden_expression

    parameter_refs = _extract_param_refs(expressions)
    if parameter_refs:
        properties["parameter_references"] = parameter_refs

    if visual_type == "Textbox":
        text_values = _extract_textbox_values(node, ns)
        if text_values:
            properties["text_values"] = text_values

    if visual_type == "Tablix":
        tablix_details = _extract_tablix_details(node, ns)
        if tablix_details:
            properties["tablix"] = tablix_details

    if visual_type in {"Chart", "Pie", "Doughnut", "Line", "Area", "Bar", "Scatter", "Shape"}:
        chart_details = _extract_chart_details(node, ns, visual_type)
        if chart_details:
            properties["chart"] = chart_details

    return properties


def _parse_report_items(
    items_node: ET.Element,
    ns: str,
    allowed_visual_types: set[str] | None = None,
) -> list[ParsedVisual]:
    visuals: list[ParsedVisual] = []
    default_supported = {
        "Textbox",
        "Chart",
        "Tablix",
        "Rectangle",
        "GaugePanel",
        "Map",
        "Image",
        "Line",
    }
    supported = allowed_visual_types.intersection(default_supported) if allowed_visual_types else default_supported

    for child in list(items_node):
        local = _local_name(child.tag)
        if local not in supported:
            continue

        visual_type = local
        if local == "Chart":
            chart_subtype = _extract_chart_subtype(child)
            if chart_subtype:
                visual_type = chart_subtype

        expressions = _collect_expressions(child)
        visual = ParsedVisual(
            name=child.attrib.get("Name", local),
            visual_type=visual_type,
            dataset_name=_extract_dataset_name(child, ns),
            expressions=expressions,
            layout=_extract_layout(child, ns),
            properties=_extract_visual_properties(child, ns, visual_type, expressions),
        )

        nested_items = child.find(_qn(ns, "ReportItems"))
        if nested_items is None:
            nested_items = next(iter(_find_children_by_local(child, "ReportItems")), None)
        if nested_items is not None:
            visual.children = _parse_report_items(
                nested_items,
                ns,
                allowed_visual_types=allowed_visual_types,
            )

        visuals.append(visual)

    return visuals


def parse_rdl_file(
    rdl_path: str | Path,
    allowed_visual_types: set[str] | None = None,
) -> ParsedReport:
    rdl_path = Path(rdl_path)
    tree = ET.parse(rdl_path)
    root = tree.getroot()
    ns = _ns_uri(root.tag)

    report = ParsedReport(
        report_name=rdl_path.stem,
        namespace=ns,
    )

    # Data sources
    for ds in root.findall(f".//{_qn(ns, 'DataSource')}"):
        name = ds.attrib.get("Name", "UnnamedDataSource")
        conn_props = ds.find(_qn(ns, "ConnectionProperties"))
        provider = _text(conn_props, _qn(ns, "DataProvider")) if conn_props is not None else None
        connection_string = _text(conn_props, _qn(ns, "ConnectString")) if conn_props is not None else None
        integrated_security = _text(conn_props, _qn(ns, "IntegratedSecurity")) if conn_props is not None else None
        credential_retrieval = _text(ds, _qn(ns, "CredentialRetrieval"))
        windows_credentials_text = _text(ds, _qn(ns, "WindowsCredentials"))
        if windows_credentials_text is None:
            windows_credentials_text = _find_first_text_by_local(ds, {"WindowsCredentials"})
        windows_credentials = (
            windows_credentials_text.lower() == "true" if isinstance(windows_credentials_text, str) else None
        )
        user_name = _text(ds, _qn(ns, "UserName")) or _find_first_text_by_local(ds, {"UserName"})

        security_type = _classify_security_type(
            connection_string=connection_string,
            integrated_security=integrated_security,
            credential_retrieval=credential_retrieval,
            windows_credentials=windows_credentials,
            user_name=user_name,
        )

        report.data_sources.append(
            ParsedDataSource(
                name=name,
                provider=provider,
                connection_string=connection_string,
                security_type=security_type,
                credential_retrieval=credential_retrieval,
                windows_credentials=windows_credentials,
                user_name=user_name,
            )
        )

    # Data sets
    for dataset_node in root.findall(f".//{_qn(ns, 'DataSet')}"):
        dataset = ParsedDataSet(name=dataset_node.attrib.get("Name", "UnnamedDataSet"))
        dataset.data_source_name = _text(dataset_node, f"{_qn(ns, 'Query')}/{_qn(ns, 'DataSourceName')}")
        dataset.query = _text(dataset_node, f"{_qn(ns, 'Query')}/{_qn(ns, 'CommandText')}")
        dataset.query_parameters = _extract_query_parameters(dataset_node, ns)
        dataset.filters = _extract_filters(dataset_node, ns)
        dataset.sort_expressions = _extract_sort_expressions(dataset_node, ns)

        for field_node in dataset_node.findall(f"{_qn(ns, 'Fields')}/{_qn(ns, 'Field')}"):
            dataset.fields.append(
                ParsedField(
                    name=field_node.attrib.get("Name", "UnnamedField"),
                    source=_text(field_node, _qn(ns, "Value")) or _find_first_text_by_local(field_node, {"Value"}),
                    data_field=_text(field_node, _qn(ns, "DataField"))
                    or _find_first_text_by_local(field_node, {"DataField"}),
                    type_name=_find_first_text_by_local(field_node, {"TypeName"}),
                )
            )
        report.data_sets.append(dataset)

    # Parameters
    for p in root.findall(f".//{_qn(ns, 'ReportParameter')}"):
        report.report_parameters.append(
            {
                "name": p.attrib.get("Name"),
                "type": _text(p, _qn(ns, "DataType")) or _find_first_text_by_local(p, {"DataType"}),
                "nullable": _text(p, _qn(ns, "Nullable")) or _find_first_text_by_local(p, {"Nullable"}),
                "multi_value": _text(p, _qn(ns, "MultiValue"))
                or _find_first_text_by_local(p, {"MultiValue"}),
                "prompt": _text(p, _qn(ns, "Prompt")) or _find_first_text_by_local(p, {"Prompt"}),
                "allow_blank": _text(p, _qn(ns, "AllowBlank"))
                or _find_first_text_by_local(p, {"AllowBlank"}),
                "hidden": _text(p, _qn(ns, "Hidden")) or _find_first_text_by_local(p, {"Hidden"}),
                "prompt_user": _text(p, _qn(ns, "PromptUser"))
                or _find_first_text_by_local(p, {"PromptUser"}),
                "used_in_query": _text(p, _qn(ns, "UsedInQuery"))
                or _find_first_text_by_local(p, {"UsedInQuery"}),
                "default_values": _extract_parameter_defaults(p, ns),
                "valid_values": _extract_parameter_valid_values(p, ns),
            }
        )

    # Visuals from all report sections and nested containers.
    visuals: list[ParsedVisual] = []
    for body_items in root.findall(f".//{_qn(ns, 'Body')}/{_qn(ns, 'ReportItems')}"):
        visuals.extend(
            _parse_report_items(
                body_items,
                ns,
                allowed_visual_types=allowed_visual_types,
            )
        )
    report.visuals = visuals

    # Report-level metadata.
    report.report_sections = _extract_report_sections(root, ns)
    report.report_metadata = _extract_report_metadata(root, ns, report.report_sections)

    # Enrich visuals with explicit field references.
    for visual in report.visuals:
        _attach_referenced_fields(visual)

    return report


def _attach_referenced_fields(visual: ParsedVisual) -> None:
    fields = _extract_field_refs(visual.expressions)
    if fields:
        visual.layout["referenced_fields"] = ",".join(fields)
        visual.properties.setdefault("field_references", fields)

    params = _extract_param_refs(visual.expressions)
    if params:
        visual.properties.setdefault("parameter_references", params)

    for child in visual.children:
        _attach_referenced_fields(child)


def _classify_security_type(
    connection_string: str | None,
    integrated_security: str | None,
    credential_retrieval: str | None,
    windows_credentials: bool | None,
    user_name: str | None,
) -> str:
    retrieval = (credential_retrieval or "").strip().lower()
    integrated = (integrated_security or "").strip().lower()
    conn = (connection_string or "").lower()

    if retrieval in {"integrated", "windows"}:
        return "windows"
    if retrieval == "store":
        return "username-password"
    if retrieval == "prompt":
        return "prompt"
    if retrieval == "none":
        return "none"

    if windows_credentials is True:
        return "windows"
    if integrated in {"true", "1", "yes"}:
        return "windows"
    if "integrated security=true" in conn or "integrated security=sspi" in conn or "trusted_connection=true" in conn:
        return "windows"
    if "user id=" in conn or "uid=" in conn or (user_name and user_name.strip()):
        return "username-password"

    return "unknown"
