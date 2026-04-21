from __future__ import annotations

from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET

from .models import ParsedDataSet, ParsedDataSource, ParsedField, ParsedReport, ParsedVisual


FIELD_EXPR_RE = re.compile(r"Fields!([A-Za-z0-9_]+)\.Value", re.IGNORECASE)
PARAM_EXPR_RE = re.compile(r"Parameters!([A-Za-z0-9_]+)\.Value", re.IGNORECASE)
DATASET_EXPR_RE = re.compile(r"DataSetName\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
AGG_EXPR_RE = re.compile(r"\b(sum|avg|average|count|min|max|format|iif)\s*\(", re.IGNORECASE)


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


def _parse_bool(value: str | None) -> bool | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _normalize_connection_key(key: str) -> str:
    return re.sub(r"[\s_\-]+", " ", key.strip().lower())


def _parse_connection_attributes(connection_string: str | None) -> dict[str, str]:
    if not isinstance(connection_string, str):
        return {}

    attributes: dict[str, str] = {}
    for part in connection_string.split(";"):
        token = part.strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        normalized_key = _normalize_connection_key(key)
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        attributes.setdefault(normalized_key, normalized_value)
    return attributes


def _conn_value(attributes: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        candidate = attributes.get(_normalize_connection_key(key))
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _extract_server_and_port(server_value: str | None) -> tuple[str, str]:
    server = (server_value or "").strip()
    if not server:
        return "", ""

    if "," in server:
        host, candidate_port = server.split(",", 1)
        candidate_port = candidate_port.strip()
        if re.fullmatch(r"\d+", candidate_port):
            return host.strip(), candidate_port

    if server.count(":") == 1:
        host, candidate_port = server.split(":", 1)
        candidate_port = candidate_port.strip()
        if re.fullmatch(r"\d+", candidate_port):
            return host.strip(), candidate_port

    return server, ""


def _looks_like_snowflake_data_source(
    provider: str | None,
    connection_string: str | None,
    connection_attributes: dict[str, str],
    data_source_reference: str | None = None,
) -> bool:
    provider_token = (provider or "").strip().lower()
    connection_text = (connection_string or "").strip().lower()
    reference_text = (data_source_reference or "").strip().lower()

    if "snowflake" in provider_token:
        return True
    if "snowflake" in connection_text:
        return True
    if "snowflake" in reference_text:
        return True

    driver = _conn_value(connection_attributes, ("driver",)) or ""
    dsn = _conn_value(connection_attributes, ("dsn", "odbc dsn")) or ""
    if "snowflake" in driver.lower() or "snowflake" in dsn.lower():
        return True

    return any(
        _conn_value(connection_attributes, (key,))
        for key in ("account", "warehouse", "authenticator")
    )


def _infer_provider_class(
    provider: str | None,
    connection_string: str | None,
    connection_attributes: dict[str, str],
    data_source_reference: str | None = None,
) -> str:
    provider_token = (provider or "").strip().lower()

    if _looks_like_snowflake_data_source(
        provider=provider,
        connection_string=connection_string,
        connection_attributes=connection_attributes,
        data_source_reference=data_source_reference,
    ):
        return "snowflake"
    if "sql" in provider_token:
        return "sqlserver"
    if "oracle" in provider_token:
        return "oracle"
    if "postgres" in provider_token:
        return "postgres"
    if "mysql" in provider_token:
        return "mysql"
    return "genericodbc"


def _build_connection_info(
    provider: str | None,
    provider_class: str,
    connection_string: str | None,
    connection_attributes: dict[str, str],
    data_source_reference: str | None,
    user_name: str | None,
) -> dict[str, Any]:
    dsn = _conn_value(connection_attributes, ("dsn", "odbc dsn"))
    account = _conn_value(connection_attributes, ("account",))

    server_hint = _conn_value(
        connection_attributes,
        ("data source", "server", "host", "address", "network address"),
    )
    is_snowflake = provider_class == "snowflake" or _looks_like_snowflake_data_source(
        provider=provider,
        connection_string=connection_string,
        connection_attributes=connection_attributes,
        data_source_reference=data_source_reference,
    )
    if not server_hint and account and is_snowflake:
        server_hint = f"{account}.snowflakecomputing.com"

    server, parsed_port = _extract_server_and_port(server_hint)
    port = _conn_value(connection_attributes, ("port", "tcp port")) or parsed_port

    database = _conn_value(connection_attributes, ("initial catalog", "database", "dbname", "db"))
    schema = _conn_value(connection_attributes, ("schema", "current schema"))
    username = (user_name or "").strip() or _conn_value(
        connection_attributes,
        ("uid", "user id", "user", "username"),
    )
    warehouse = _conn_value(connection_attributes, ("warehouse",))
    role = _conn_value(connection_attributes, ("role",))
    authenticator = _conn_value(connection_attributes, ("authenticator",))

    info: dict[str, Any] = {
        "provider_class": provider_class,
        "cloud_platform": "snowflake" if is_snowflake else None,
        "data_source_reference": data_source_reference,
        "dsn": dsn,
        "account": account,
        "server": server,
        "port": port,
        "database": database,
        "schema": schema,
        "warehouse": warehouse,
        "role": role,
        "authenticator": authenticator,
        "username": username,
        "uses_dsn": bool(dsn),
        "has_password_in_connection_string": bool(_conn_value(connection_attributes, ("pwd", "password"))),
    }

    cleaned: dict[str, Any] = {}
    for key, value in info.items():
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                cleaned[key] = stripped
            continue
        if isinstance(value, bool):
            if value:
                cleaned[key] = value
            continue
        if value is not None:
            cleaned[key] = value

    return cleaned


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
    container_section: str = "",
) -> dict[str, Any]:
    properties: dict[str, Any] = {}

    if isinstance(container_section, str) and container_section.strip():
        properties["container_section"] = container_section.strip()

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
        if _looks_kpi_textbox(expressions, text_values):
            properties["semantic_hint"] = "kpi"

    if visual_type == "Tablix":
        tablix_details = _extract_tablix_details(node, ns)
        if tablix_details:
            properties["tablix"] = tablix_details

    if visual_type == "GaugePanel":
        gauge_value_expressions: list[str] = []
        for value_node in _findall_by_local(node, "Value"):
            if value_node.text is None:
                continue
            text = value_node.text.strip()
            if text and _looks_like_expression(text):
                gauge_value_expressions.append(text)
        if gauge_value_expressions:
            properties["gauge"] = {
                "value_expressions": _unique_ordered(gauge_value_expressions),
            }

    if visual_type == "Image":
        image_source = _text(node, _qn(ns, "Source")) or _find_first_text_by_local(node, {"Source"})
        image_value = _text(node, _qn(ns, "Value")) or _find_first_text_by_local(node, {"Value"})
        mime_type = _text(node, _qn(ns, "MIMEType")) or _find_first_text_by_local(node, {"MIMEType"})
        sizing = _text(node, _qn(ns, "Sizing")) or _find_first_text_by_local(node, {"Sizing"})
        image_payload = {
            "source": image_source,
            "value": image_value,
            "mime_type": mime_type,
            "sizing": sizing,
        }
        image_payload = {k: v for k, v in image_payload.items() if v not in (None, "")}
        if image_payload:
            properties["image"] = image_payload

    if visual_type in {"Chart", "Pie", "Doughnut", "Line", "Area", "Bar", "Scatter", "Shape"}:
        chart_details = _extract_chart_details(node, ns, visual_type)
        if chart_details:
            properties["chart"] = chart_details

    return properties


def _looks_kpi_textbox(expressions: list[str], text_values: list[str]) -> bool:
    if not isinstance(expressions, list):
        expressions = []
    if not isinstance(text_values, list):
        text_values = []

    if any(AGG_EXPR_RE.search(expr or "") for expr in expressions):
        return True

    if any(FIELD_EXPR_RE.search(expr or "") for expr in expressions):
        hint_blob = " ".join(expressions + text_values).lower()
        if any(token in hint_blob for token in ["sales", "quota", "amount", "kpi", "target", "variance", "quantity", "%"]):
            return True

    for value in text_values:
        if not isinstance(value, str):
            continue
        if AGG_EXPR_RE.search(value) or FIELD_EXPR_RE.search(value):
            return True

    return False


def _extract_embedded_visuals(
    node: ET.Element,
    ns: str,
    supported: set[str],
    allowed_visual_types: set[str] | None,
    container_section: str,
    parent_dataset_name: str | None,
    parent_visual_name: str,
) -> list[ParsedVisual]:
    descendant_supported = {"Textbox", "Chart", "GaugePanel", "Image", "Map", "Line"}
    descendant_supported = descendant_supported.intersection(supported)
    if allowed_visual_types:
        descendant_supported = descendant_supported.intersection(allowed_visual_types)
    if not descendant_supported:
        return []

    visuals: list[ParsedVisual] = []
    seen_names: dict[str, int] = {}

    for element in node.iter():
        if element is node:
            continue

        local = _local_name(element.tag)
        if local not in descendant_supported:
            continue

        expressions = _collect_expressions(element)
        if local == "Textbox":
            text_values = _extract_textbox_values(element, ns)
            if not _looks_kpi_textbox(expressions, text_values):
                continue

        child_name = element.attrib.get("Name") if isinstance(element.attrib.get("Name"), str) else ""
        base_name = f"{parent_visual_name}_{child_name.strip() or local}".strip("_")
        if not base_name:
            base_name = f"{parent_visual_name}_{local}"
        lower = base_name.lower()
        seen_names[lower] = seen_names.get(lower, 0) + 1
        resolved_name = base_name if seen_names[lower] == 1 else f"{base_name}_{seen_names[lower]}"

        visual_type = local
        if local == "Chart":
            chart_subtype = _extract_chart_subtype(element)
            if chart_subtype:
                visual_type = chart_subtype

        properties = _extract_visual_properties(
            element,
            ns,
            visual_type,
            expressions,
            container_section=container_section,
        )
        properties["embedded_parent"] = parent_visual_name

        visuals.append(
            ParsedVisual(
                name=resolved_name,
                visual_type=visual_type,
                dataset_name=_extract_dataset_name(element, ns) or parent_dataset_name,
                expressions=expressions,
                layout=_extract_layout(element, ns),
                properties=properties,
            )
        )

    return visuals


def _parse_report_items(
    items_node: ET.Element,
    ns: str,
    allowed_visual_types: set[str] | None = None,
    container_section: str = "Body",
    parent_dataset_name: str | None = None,
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

        source_name = child.attrib.get("Name", local)
        visual_name = source_name if isinstance(source_name, str) and source_name.strip() else local
        section_prefix = container_section.strip()
        if section_prefix and section_prefix.lower() != "body":
            visual_name = f"{section_prefix}_{visual_name}"

        expressions = _collect_expressions(child)
        dataset_name = _extract_dataset_name(child, ns) or parent_dataset_name
        properties = _extract_visual_properties(
            child,
            ns,
            visual_type,
            expressions,
            container_section=container_section,
        )
        if isinstance(source_name, str) and source_name.strip() and source_name.strip() != visual_name:
            properties["source_name"] = source_name.strip()

        visual = ParsedVisual(
            name=visual_name,
            visual_type=visual_type,
            dataset_name=dataset_name,
            expressions=expressions,
            layout=_extract_layout(child, ns),
            properties=properties,
        )

        nested_items = child.find(_qn(ns, "ReportItems"))
        if nested_items is None:
            nested_items = next(iter(_find_children_by_local(child, "ReportItems")), None)
        if nested_items is not None:
            visual.children = _parse_report_items(
                nested_items,
                ns,
                allowed_visual_types=allowed_visual_types,
                container_section=container_section,
                parent_dataset_name=dataset_name,
            )

        if local in {"Tablix", "Rectangle"}:
            embedded_visuals = _extract_embedded_visuals(
                node=child,
                ns=ns,
                supported=supported,
                allowed_visual_types=allowed_visual_types,
                container_section=container_section,
                parent_dataset_name=dataset_name,
                parent_visual_name=visual_name,
            )
            if embedded_visuals:
                existing = {c.name.strip().lower() for c in visual.children if isinstance(c.name, str)}
                for child_visual in embedded_visuals:
                    key = child_visual.name.strip().lower() if isinstance(child_visual.name, str) else ""
                    if key and key in existing:
                        continue
                    if key:
                        existing.add(key)
                    visual.children.append(child_visual)

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
    data_source_nodes = root.findall(f".//{_qn(ns, 'DataSource')}")
    if not data_source_nodes:
        data_source_nodes = _findall_by_local(root, "DataSource")

    for ds in data_source_nodes:
        name = ds.attrib.get("Name", "UnnamedDataSource")
        conn_props = ds.find(_qn(ns, "ConnectionProperties"))
        if conn_props is None:
            conn_props = next(iter(_find_children_by_local(ds, "ConnectionProperties")), None)

        provider = (
            (_text(conn_props, _qn(ns, "DataProvider")) if conn_props is not None else None)
            or (_find_first_text_by_local(conn_props, {"DataProvider"}) if conn_props is not None else None)
        )
        connection_string = (
            (_text(conn_props, _qn(ns, "ConnectString")) if conn_props is not None else None)
            or (_find_first_text_by_local(conn_props, {"ConnectString"}) if conn_props is not None else None)
        )
        integrated_security = (
            (_text(conn_props, _qn(ns, "IntegratedSecurity")) if conn_props is not None else None)
            or (_find_first_text_by_local(conn_props, {"IntegratedSecurity"}) if conn_props is not None else None)
        )
        data_source_reference = _text(ds, _qn(ns, "DataSourceReference")) or _find_first_text_by_local(
            ds,
            {"DataSourceReference"},
        )
        credential_retrieval = _text(ds, _qn(ns, "CredentialRetrieval")) or _find_first_text_by_local(
            ds,
            {"CredentialRetrieval"},
        )
        windows_credentials_text = _text(ds, _qn(ns, "WindowsCredentials"))
        if windows_credentials_text is None:
            windows_credentials_text = _find_first_text_by_local(ds, {"WindowsCredentials"})
        windows_credentials = _parse_bool(windows_credentials_text)
        user_name = _text(ds, _qn(ns, "UserName")) or _find_first_text_by_local(ds, {"UserName"})

        connection_attributes = _parse_connection_attributes(connection_string)
        provider_class = _infer_provider_class(
            provider=provider,
            connection_string=connection_string,
            connection_attributes=connection_attributes,
            data_source_reference=data_source_reference,
        )
        connection_info = _build_connection_info(
            provider=provider,
            provider_class=provider_class,
            connection_string=connection_string,
            connection_attributes=connection_attributes,
            data_source_reference=data_source_reference,
            user_name=user_name,
        )

        security_type = _classify_security_type(
            connection_string=connection_string,
            integrated_security=integrated_security,
            credential_retrieval=credential_retrieval,
            windows_credentials=windows_credentials,
            user_name=user_name,
            provider=provider,
            provider_class=provider_class,
            connection_attributes=connection_attributes,
            data_source_reference=data_source_reference,
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
                data_source_reference=data_source_reference,
                provider_class=provider_class,
                connection_info=connection_info,
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
    body_items_nodes = root.findall(f".//{_qn(ns, 'Body')}/{_qn(ns, 'ReportItems')}")
    if not body_items_nodes:
        for body_node in _findall_by_local(root, "Body"):
            body_items = body_node.find(_qn(ns, "ReportItems"))
            if body_items is None:
                body_items = next(iter(_find_children_by_local(body_node, "ReportItems")), None)
            if body_items is not None:
                body_items_nodes.append(body_items)

    for body_items in body_items_nodes:
        visuals.extend(
            _parse_report_items(
                body_items,
                ns,
                allowed_visual_types=allowed_visual_types,
                container_section="Body",
            )
        )

    for section_local in ["PageHeader", "PageFooter"]:
        section_nodes = root.findall(f".//{_qn(ns, section_local)}")
        if not section_nodes:
            section_nodes = _findall_by_local(root, section_local)

        for section_node in section_nodes:
            section_items = section_node.find(_qn(ns, "ReportItems"))
            if section_items is None:
                section_items = next(iter(_find_children_by_local(section_node, "ReportItems")), None)
            if section_items is None:
                continue

            visuals.extend(
                _parse_report_items(
                    section_items,
                    ns,
                    allowed_visual_types=allowed_visual_types,
                    container_section=section_local,
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
    provider: str | None = None,
    provider_class: str | None = None,
    connection_attributes: dict[str, str] | None = None,
    data_source_reference: str | None = None,
) -> str:
    retrieval = (credential_retrieval or "").strip().lower()
    integrated = (integrated_security or "").strip().lower()
    conn = (connection_string or "").lower()

    attributes = connection_attributes or _parse_connection_attributes(connection_string)
    inferred_provider_class = (provider_class or "").strip().lower()
    is_snowflake = inferred_provider_class == "snowflake" or _looks_like_snowflake_data_source(
        provider=provider,
        connection_string=connection_string,
        connection_attributes=attributes,
        data_source_reference=data_source_reference,
    )

    has_conn_user = bool(_conn_value(attributes, ("uid", "user id", "user", "username"))) or bool(
        re.search(r"(^|;)\s*(?:user id|uid|user|username)\s*=", conn)
    )
    has_conn_password = bool(_conn_value(attributes, ("pwd", "password"))) or bool(
        re.search(r"(^|;)\s*(?:pwd|password)\s*=", conn)
    )
    has_explicit_user = bool(isinstance(user_name, str) and user_name.strip())
    has_authenticator = bool(_conn_value(attributes, ("authenticator",)))

    if retrieval in {"integrated", "windows"}:
        return "username-password" if is_snowflake else "windows"
    if retrieval == "store":
        return "username-password"
    if retrieval == "prompt":
        return "prompt"
    if retrieval == "none":
        if is_snowflake and (has_conn_user or has_conn_password or has_explicit_user or has_authenticator):
            return "username-password"
        return "none"

    if is_snowflake:
        return "username-password"

    if windows_credentials is True:
        return "windows"
    if integrated in {"true", "1", "yes"}:
        return "windows"
    if "integrated security=true" in conn or "integrated security=sspi" in conn or "trusted_connection=true" in conn:
        return "windows"
    if has_conn_user or has_explicit_user:
        return "username-password"

    return "unknown"
