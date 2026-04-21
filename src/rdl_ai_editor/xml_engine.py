from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from lxml import etree

from .validator import validate_patch


class PatchApplicationError(RuntimeError):
    pass


GENERIC_CHART_TARGETS = {
    "chart",
    "charts",
    "main_chart",
    "main chart",
    "primary_chart",
    "primary chart",
    "first_chart",
    "first chart",
}


@dataclass
class PatchApplyResult:
    modified_xml: str
    applied_operations: list[dict[str, Any]]
    logs: list[str]


def apply_patch_to_rdl(rdl_xml: str | bytes, patch_json: dict[str, Any]) -> PatchApplyResult:
    validated_patch = validate_patch(patch_json)

    source_bytes = rdl_xml.encode("utf-8") if isinstance(rdl_xml, str) else rdl_xml
    parser = etree.XMLParser(remove_blank_text=False, recover=False)

    try:
        root = etree.fromstring(source_bytes, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise PatchApplicationError(f"Invalid RDL XML input: {exc}") from exc

    namespace_uri = etree.QName(root.tag).namespace
    logs: list[str] = []

    for index, operation in enumerate(validated_patch["operations"]):
        op_name = operation["op"]
        if op_name == "add_filter":
            _apply_add_filter(root, operation, namespace_uri, logs)
        elif op_name == "remove_filter":
            _apply_remove_filter(root, operation, logs)
        elif op_name == "remove_query_condition":
            _apply_remove_query_condition(root, operation, logs)
        elif op_name == "remove_query_parameter":
            _apply_remove_query_parameter(root, operation, logs)
        elif op_name == "remove_report_parameter":
            _apply_remove_report_parameter(root, operation, logs)
        elif op_name == "remove_expression_references":
            _apply_remove_expression_references(root, operation, logs)
        elif op_name == "remove_visual":
            _apply_remove_visual(root, operation, logs)
        elif op_name == "change_chart_type":
            _apply_change_chart_type(root, operation, namespace_uri, logs)
        elif op_name == "update_title":
            _apply_update_text(root, operation, logs, title_alias_mode=True)
        elif op_name == "update_text":
            _apply_update_text(root, operation, logs, title_alias_mode=False)
        else:
            raise PatchApplicationError(f"Unsupported operation at index {index}: {op_name}")

    modified_xml = etree.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return PatchApplyResult(
        modified_xml=modified_xml,
        applied_operations=validated_patch["operations"],
        logs=logs,
    )


def _apply_add_filter(
    root: etree._Element,
    operation: dict[str, Any],
    namespace_uri: str | None,
    logs: list[str],
) -> None:
    dataset = _resolve_dataset(root, operation.get("target_dataset"))
    field = operation["field"]

    field_nodes = dataset.xpath(
        "./*[local-name()='Fields']/*[local-name()='Field' and @Name=$field_name]",
        field_name=field,
    )
    if not field_nodes:
        raise PatchApplicationError(
            f"Cannot add filter: field '{field}' was not found in dataset '{dataset.get('Name')}'."
        )

    filters_nodes = dataset.xpath("./*[local-name()='Filters']")
    if filters_nodes:
        filters_node = filters_nodes[0]
    else:
        filters_node = etree.Element(_qn("Filters", namespace_uri))
        fields_nodes = dataset.xpath("./*[local-name()='Fields']")
        if fields_nodes:
            fields_node = fields_nodes[0]
            dataset.insert(list(dataset).index(fields_node) + 1, filters_node)
        else:
            dataset.append(filters_node)

    filter_node = etree.Element(_qn("Filter", namespace_uri))

    filter_expression = etree.Element(_qn("FilterExpression", namespace_uri))
    filter_expression.text = f"=Fields!{field}.Value"

    operator = etree.Element(_qn("Operator", namespace_uri))
    operator.text = operation["operator"]

    filter_values = etree.Element(_qn("FilterValues", namespace_uri))
    filter_value = etree.Element(_qn("FilterValue", namespace_uri))
    filter_value.text = _to_rdl_filter_value_expression(
        value=operation["value"],
        value_kind=operation.get("value_kind", "literal"),
    )

    filter_values.append(filter_value)
    filter_node.append(filter_expression)
    filter_node.append(operator)
    filter_node.append(filter_values)
    filters_node.append(filter_node)

    logs.append(
        "Added filter on dataset "
        f"{dataset.get('Name')} with field={field}, operator={operation['operator']}, value={operation['value']}"
    )


def _apply_remove_filter(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    dataset = _resolve_dataset(root, operation.get("target_dataset"))
    field = operation["field"]

    filter_nodes = dataset.xpath("./*[local-name()='Filters']/*[local-name()='Filter']")
    if not filter_nodes:
        raise PatchApplicationError(f"No filters were found in dataset '{dataset.get('Name')}'.")

    removed_count = 0
    for filter_node in filter_nodes:
        expression_texts = filter_node.xpath("./*[local-name()='FilterExpression']/text()")
        expression_text = expression_texts[0] if expression_texts else ""
        if field.lower() in expression_text.lower():
            parent = filter_node.getparent()
            if parent is not None:
                parent.remove(filter_node)
                removed_count += 1

    if removed_count == 0:
        raise PatchApplicationError(
            f"No filter referencing field '{field}' was found in dataset '{dataset.get('Name')}'."
        )

    logs.append(f"Removed {removed_count} filter(s) in dataset {dataset.get('Name')} for field={field}")


def _apply_remove_query_condition(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    condition_text = operation["condition_text"]
    target_dataset = operation.get("target_dataset")
    datasets = _resolve_datasets(root, target_dataset)

    removed_count = 0
    for dataset in datasets:
        command_nodes = dataset.xpath("./*[local-name()='Query']/*[local-name()='CommandText']")
        if not command_nodes:
            continue

        command_node = command_nodes[0]
        source_sql = command_node.text or ""
        updated_sql, removed = _remove_sql_condition(source_sql, condition_text)
        if not removed:
            parameter_from_condition = _extract_parameter_name_from_condition_text(condition_text)
            if parameter_from_condition is not None:
                updated_sql, removed = _remove_sql_condition_by_parameter(source_sql, parameter_from_condition)

        if removed:
            command_node.text = updated_sql
            removed_count += 1

    if removed_count == 0:
        dataset_scope = target_dataset or "<all datasets>"
        logs.append(f"No query condition matched for '{condition_text}' in scope {dataset_scope}")
        return

    logs.append(f"Removed query condition '{condition_text}' in {removed_count} dataset(s)")


def _apply_remove_query_parameter(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    parameter = operation["parameter"]
    target_dataset = operation.get("target_dataset")
    target_normalized = _normalize_parameter_name(parameter)
    datasets = _resolve_datasets(root, target_dataset)

    removed_count = 0
    for dataset in datasets:
        query_parameters_nodes = dataset.xpath("./*[local-name()='Query']/*[local-name()='QueryParameters']")
        if not query_parameters_nodes:
            continue

        query_parameters = query_parameters_nodes[0]
        query_parameter_nodes = query_parameters.xpath("./*[local-name()='QueryParameter']")

        for query_parameter in query_parameter_nodes:
            raw_name = query_parameter.get("Name") or ""
            if _normalize_parameter_name(raw_name) == target_normalized:
                query_parameters.remove(query_parameter)
                removed_count += 1

        if not query_parameters.xpath("./*[local-name()='QueryParameter']"):
            parent = query_parameters.getparent()
            if parent is not None:
                parent.remove(query_parameters)

    if removed_count == 0:
        dataset_scope = target_dataset or "<all datasets>"
        logs.append(f"No query parameter matched for '{parameter}' in scope {dataset_scope}")
        return

    logs.append(f"Removed {removed_count} query parameter(s) for parameter={parameter}")


def _apply_remove_report_parameter(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    parameter = operation["parameter"]
    target_normalized = _normalize_parameter_name(parameter)

    report_parameters_nodes = root.xpath("./*[local-name()='ReportParameters']")
    if not report_parameters_nodes:
        logs.append(f"No ReportParameters node found while removing parameter={parameter}")
        _remove_report_parameters_layout_entries(root, target_normalized)
        return

    report_parameters = report_parameters_nodes[0]
    parameter_nodes = report_parameters.xpath("./*[local-name()='ReportParameter']")
    removed_count = 0

    for parameter_node in parameter_nodes:
        raw_name = parameter_node.get("Name") or ""
        if _normalize_parameter_name(raw_name) == target_normalized:
            report_parameters.remove(parameter_node)
            removed_count += 1

    if not report_parameters.xpath("./*[local-name()='ReportParameter']"):
        parent = report_parameters.getparent()
        if parent is not None:
            parent.remove(report_parameters)

    _remove_report_parameters_layout_entries(root, target_normalized)
    if removed_count == 0:
        logs.append(f"No report parameter definition matched for parameter={parameter}")
        return

    logs.append(f"Removed report parameter definition(s) for parameter={parameter}")


def _apply_remove_expression_references(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    parameter = operation["parameter"]
    target_normalized = _normalize_parameter_name(parameter)
    needle = f"parameters!{target_normalized.lower()}.value"

    nodes = root.xpath(
        "//*[text()[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), $needle)]]",
        needle=needle,
    )

    updated_count = 0
    for node in nodes:
        existing = node.text
        if not isinstance(existing, str):
            continue
        if etree.QName(node.tag).localname == "CommandText":
            continue

        replacement = _remove_parameter_reference_from_expression(existing, target_normalized)
        if replacement != existing:
            node.text = replacement
            updated_count += 1

    if updated_count == 0:
        logs.append(f"No expression references found for parameter={parameter}")
        return

    logs.append(f"Removed expression references for parameter={parameter} in {updated_count} node(s)")


def _apply_remove_visual(root: etree._Element, operation: dict[str, Any], logs: list[str]) -> None:
    target = operation["target"]
    visual_type = operation.get("visual_type")
    remove_all_matches = bool(operation.get("remove_all_matches", False))
    target_lower = target.strip().lower()

    nodes: list[etree._Element]
    if visual_type:
        nodes = root.xpath("//*[local-name()=$visual_type and @Name=$target_name]", visual_type=visual_type, target_name=target)
        if not nodes:
            nodes = [
                node
                for node in root.xpath("//*[local-name()=$visual_type]", visual_type=visual_type)
                if (node.get("Name") or "").strip().lower() == target_lower
            ]
    else:
        nodes = root.xpath(
            "//*[@Name=$target_name and ("
            "local-name()='Chart' or "
            "local-name()='Tablix' or "
            "local-name()='Textbox' or "
            "local-name()='Rectangle' or "
            "local-name()='GaugePanel' or "
            "local-name()='Map' or "
            "local-name()='Image' or "
            "local-name()='Line')]",
            target_name=target,
        )
        if not nodes:
            candidate_nodes = root.xpath(
                "//*["
                "local-name()='Chart' or "
                "local-name()='Tablix' or "
                "local-name()='Textbox' or "
                "local-name()='Rectangle' or "
                "local-name()='GaugePanel' or "
                "local-name()='Map' or "
                "local-name()='Image' or "
                "local-name()='Line']"
            )
            nodes = [
                node
                for node in candidate_nodes
                if (node.get("Name") or "").strip().lower() == target_lower
            ]

    if not nodes and _is_generic_chart_target(target):
        nodes = root.xpath("//*[local-name()='Chart']")
        if nodes and (remove_all_matches or len(nodes) == 1):
            nodes = [nodes[0]]

    if not nodes:
        raise PatchApplicationError(f"Visual target '{target}' was not found.")

    if not remove_all_matches:
        nodes = [nodes[0]]

    for node in nodes:
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)

    logs.append(f"Removed {len(nodes)} visual node(s) for target={target}")


def _apply_change_chart_type(
    root: etree._Element,
    operation: dict[str, Any],
    namespace_uri: str | None,
    logs: list[str],
) -> None:
    target = operation["target"]
    to_type = operation["to"]

    chart = _resolve_chart(root, target)

    type_nodes = chart.xpath(".//*[local-name()='ChartSeries']/*[local-name()='Type']")
    if type_nodes:
        for type_node in type_nodes:
            type_node.text = to_type
    else:
        series_nodes = chart.xpath(".//*[local-name()='ChartSeries']")
        if not series_nodes:
            raise PatchApplicationError(f"Chart '{target}' does not contain ChartSeries nodes.")

        for series_node in series_nodes:
            type_node = etree.Element(_qn("Type", namespace_uri))
            type_node.text = to_type
            series_node.insert(0, type_node)

    logs.append(f"Changed chart type for target={target} to {to_type}")


def _apply_update_text(
    root: etree._Element,
    operation: dict[str, Any],
    logs: list[str],
    *,
    title_alias_mode: bool,
) -> None:
    target = operation["target"]
    new_text = operation["text"]

    textbox = _resolve_textbox(root, target, title_alias_mode=title_alias_mode)

    value_nodes = textbox.xpath(".//*[local-name()='TextRun']/*[local-name()='Value']")
    if not value_nodes:
        raise PatchApplicationError(f"Textbox '{target}' does not contain a TextRun/Value node.")

    value_nodes[0].text = new_text
    logs.append(f"Updated text for textbox target={target}")


def _resolve_dataset(root: etree._Element, target_dataset: str | None) -> etree._Element:
    if target_dataset:
        nodes = root.xpath(
            "//*[local-name()='DataSet' and @Name=$dataset_name]",
            dataset_name=target_dataset,
        )
        if not nodes:
            raise PatchApplicationError(f"Dataset '{target_dataset}' was not found.")
        return nodes[0]

    nodes = root.xpath("//*[local-name()='DataSet']")
    if not nodes:
        raise PatchApplicationError("No DataSet node was found in the RDL.")
    return nodes[0]


def _resolve_datasets(root: etree._Element, target_dataset: str | None) -> list[etree._Element]:
    if target_dataset:
        return [_resolve_dataset(root, target_dataset)]

    nodes = root.xpath("//*[local-name()='DataSet']")
    if not nodes:
        raise PatchApplicationError("No DataSet node was found in the RDL.")
    return nodes


def _resolve_chart(root: etree._Element, target: str) -> etree._Element:
    all_charts = root.xpath("//*[local-name()='Chart']")
    if not all_charts:
        raise PatchApplicationError("No Chart node was found in the RDL.")

    exact = root.xpath("//*[local-name()='Chart' and @Name=$target_name]", target_name=target)
    if exact:
        return exact[0]

    target_lower = target.strip().lower()
    case_insensitive = [
        node for node in all_charts if (node.get("Name") or "").strip().lower() == target_lower
    ]
    if len(case_insensitive) == 1:
        return case_insensitive[0]

    fuzzy = [
        node
        for node in all_charts
        if target_lower and target_lower in (node.get("Name") or "").strip().lower()
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]

    if _is_generic_chart_target(target) and len(all_charts) == 1:
        return all_charts[0]

    available = [node.get("Name") for node in all_charts if node.get("Name")]
    available_text = ", ".join(available) if available else "<unnamed charts>"
    raise PatchApplicationError(
        f"Chart target '{target}' was not found. Available chart names: {available_text}"
    )


def _resolve_textbox(root: etree._Element, target: str, *, title_alias_mode: bool) -> etree._Element:
    nodes = root.xpath("//*[local-name()='Textbox' and @Name=$target_name]", target_name=target)
    if nodes:
        return nodes[0]

    if title_alias_mode and target.lower() in {"reporttitle", "main_title", "title"}:
        title_candidates = root.xpath(
            "//*[local-name()='Textbox' and contains(translate(@Name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'title')]"
        )
        if title_candidates:
            return title_candidates[0]

    raise PatchApplicationError(f"Textbox target '{target}' was not found.")


def _to_rdl_filter_value_expression(value: Any, value_kind: str) -> str:
    if value_kind == "parameter":
        return f"=Parameters!{value}.Value"

    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("="):
            return stripped
        escaped = stripped.replace('"', '""')
        return f'="{escaped}"'

    if isinstance(value, bool):
        return f"={str(value)}"

    return f"={value}"


def _qn(tag: str, namespace_uri: str | None) -> str:
    return f"{{{namespace_uri}}}{tag}" if namespace_uri else tag


def _is_generic_chart_target(target: str) -> bool:
    return target.strip().lower() in GENERIC_CHART_TARGETS


def _normalize_parameter_name(name: str) -> str:
    return name.strip().lstrip("@").strip()


def _remove_report_parameters_layout_entries(root: etree._Element, parameter_normalized: str) -> None:
    layout_nodes = root.xpath("./*[local-name()='ReportParametersLayout']")
    if not layout_nodes:
        return

    layout_node = layout_nodes[0]
    cell_defs = layout_node.xpath(".//*[local-name()='CellDefinition']")
    for cell_def in cell_defs:
        parameter_name_nodes = cell_def.xpath("./*[local-name()='ParameterName']")
        if not parameter_name_nodes:
            continue
        parameter_name_node = parameter_name_nodes[0]
        parameter_name = _normalize_parameter_name(parameter_name_node.text or "")
        if parameter_name == parameter_normalized:
            parent = cell_def.getparent()
            if parent is not None:
                parent.remove(cell_def)

    remaining = layout_node.xpath(".//*[local-name()='CellDefinition']")
    if not remaining:
        parent = layout_node.getparent()
        if parent is not None:
            parent.remove(layout_node)


def _remove_sql_condition(command_text: str, condition_text: str) -> tuple[str, bool]:
    condition_pattern = _sql_flexible_text_pattern(condition_text)
    return _remove_sql_predicate(command_text, condition_pattern)


def _remove_sql_condition_by_parameter(command_text: str, parameter: str) -> tuple[str, bool]:
    parameter_pattern = rf"@\s*{re.escape(_normalize_parameter_name(parameter))}\b"
    predicate_pattern = rf"[^\n;]*{parameter_pattern}[^\n;]*"
    return _remove_sql_predicate(command_text, predicate_pattern)


def _remove_sql_predicate(command_text: str, predicate_pattern: str) -> tuple[str, bool]:
    if not command_text.strip():
        return command_text, False

    clause_pattern = re.compile(
        r"(?is)(\bWHERE\b)(.*?)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bUNION\b|;|$)"
    )

    removed_any = False

    def _rewrite_clause(match: re.Match[str]) -> str:
        nonlocal removed_any
        where_keyword = match.group(1)
        body = match.group(2)

        updated_body, removed = _remove_predicate_from_where_body(body, predicate_pattern)
        if removed:
            removed_any = True

        if not updated_body.strip():
            return ""
        return f"{where_keyword} {updated_body.strip()} "

    rewritten = clause_pattern.sub(_rewrite_clause, command_text)
    if not removed_any:
        return command_text, False

    cleaned = _cleanup_sql(rewritten)
    return cleaned, True


def _remove_predicate_from_where_body(where_body: str, predicate_pattern: str) -> tuple[str, bool]:
    body = where_body.strip()
    if not body:
        return where_body, False

    split_parts = re.split(r"(?is)\s+(AND|OR)\s+", body)
    predicates = [part for index, part in enumerate(split_parts) if index % 2 == 0]
    connectors = [part.upper() for index, part in enumerate(split_parts) if index % 2 == 1]

    matcher = re.compile(rf"(?is){predicate_pattern}")

    kept_predicates: list[str] = []
    kept_connectors: list[str] = []
    removed = False

    for index, predicate in enumerate(predicates):
        predicate_text = predicate.strip()
        if not predicate_text:
            continue

        if matcher.search(predicate_text):
            removed = True
            continue

        if kept_predicates:
            connector = connectors[index - 1] if index - 1 < len(connectors) else "AND"
            kept_connectors.append(connector)
        kept_predicates.append(predicate_text)

    if not removed:
        return where_body, False

    if not kept_predicates:
        return "", True

    rebuilt = kept_predicates[0]
    for connector, predicate in zip(kept_connectors, kept_predicates[1:]):
        rebuilt = f"{rebuilt} {connector} {predicate}"

    return rebuilt.strip(), True


def _cleanup_sql(sql: str) -> str:
    sql = re.sub(r"(?is)\bWHERE\b\s*(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bUNION\b|;|$)", "", sql)
    sql = re.sub(r"(?is)\bWHERE\b\s*(AND|OR)\b", "WHERE ", sql)
    sql = re.sub(r"(?is)\b(AND|OR)\b\s*(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bUNION\b|;|$)", "", sql)
    sql = re.sub(r"(?is)(\S)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bUNION\b)", r"\1 ", sql)
    sql = re.sub(r"[ \t]+\n", "\n", sql)
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    return sql.strip()


def _sql_flexible_text_pattern(text: str) -> str:
    escaped = re.escape(text.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    return escaped


def _extract_parameter_name_from_condition_text(condition_text: str) -> str | None:
    match = re.search(r"@([A-Za-z_][A-Za-z0-9_]*)", condition_text)
    if match:
        return match.group(1)

    bracket_match = re.search(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]", condition_text)
    if bracket_match:
        return bracket_match.group(1)

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", condition_text)
    stopwords = {"and", "or", "not", "is", "null", "like", "in", "between"}
    for token in reversed(tokens):
        if token.lower() not in stopwords:
            return token

    return None


def _remove_parameter_reference_from_expression(text: str, parameter: str) -> str:
    parameter_pattern = rf"Parameters!\s*{re.escape(parameter)}\s*\.Value"
    updated = re.sub(parameter_pattern, "", text, flags=re.IGNORECASE)

    updated = re.sub(r"=\s*(&|\+)\s*", "=", updated)
    updated = re.sub(r"\s*(&|\+)\s*($|(?=\)))", "", updated)
    updated = re.sub(r"\s{2,}", " ", updated).strip()

    if text.strip().startswith("="):
        if updated in {"", "="}:
            return '=""'
        if not updated.startswith("="):
            updated = f"={updated}"
        if updated in {"=", "=&", "=+"}:
            return '=""'

    return updated
