from __future__ import annotations

import json
import re
from typing import Any


JsonDict = dict[str, Any]

SUPPORTED_TABLEAU_TYPES = {
    "bar_chart",
    "line_chart",
    "kpi",
    "table",
    "text_table",
    "filter",
    "scatter_plot",
    "pie_chart",
    "map",
    "treemap",
    "area_chart",
    "box_plot",
    "histogram",
    "unknown",
}


class QlikToTableauMappingAgent:
    def __init__(
        self,
        llm_client: Any | None = None,
        model_name: str = "gpt-5.3-codex",
    ):
        self.llm_client = llm_client
        self.model_name = model_name

    def map_model(self, intermediate_model: JsonDict) -> JsonDict:
        if self.llm_client is None:
            raise ValueError("Qlik to Tableau mapping requires an LLM client.")

        llm_mapping = self._map_with_llm(intermediate_model)
        try:
            self._validate_llm_mapping(intermediate_model, llm_mapping)
        except ValueError as exc:
            warnings = llm_mapping.setdefault("warnings", [])
            if not isinstance(warnings, list):
                warnings = [str(warnings)]
                llm_mapping["warnings"] = warnings
            warnings.append(f"LLM mapping was completed by deterministic contract enrichment: {exc}")

        llm_mapping.setdefault("agent", {})
        llm_mapping["agent"].update(
            {
                "name": "QlikToTableauMappingAgent",
                "model": self.model_name,
                "mode": "llm",
                "fallback_available": False,
            }
        )
        return build_tableau_mapping_contract(intermediate_model=intermediate_model, proposed_mapping=llm_mapping)

    def _map_with_llm(self, intermediate_model: JsonDict) -> JsonDict:
        system_prompt = (
            "You map real Qlik Sense/QIX metadata into a Tableau workbook mapping. "
            "Return only JSON. Do not invent sheets, visual ids, field names, or source expressions. "
            "Use Tableau-compatible visual types and calculated expressions."
        )
        user_prompt = json.dumps(
            {
                "output_contract": {
                    "agent": {"name": "QlikToTableauMappingAgent", "mode": "llm"},
                    "contract_version": "1.0",
                    "visual_type_map": "object mapping source Qlik visual types to Tableau visual types",
                    "expression_map": [
                        {
                            "visual_id": "source visual id",
                            "source_expression": "original Qlik expression",
                            "tableau_expression": "Tableau expression using table-qualified [Field (Table)] references",
                        }
                    ],
                    "sheets": [
                        {
                            "id": "source sheet id",
                            "name": "source sheet title",
                            "tableau_name": "Tableau dashboard/sheet title",
                            "visuals": [
                                {
                                    "id": "source visual id",
                                    "visual_id": "source visual id",
                                    "title": "source visual title",
                                    "visual_title": "source visual title",
                                    "source_type": "source Qlik type",
                                    "original_qlik_visual_type": "source Qlik type",
                                    "tableau_type": "bar_chart|line_chart|kpi|text_table|filter|unknown",
                                    "mapped_tableau_visual_type": "bar_chart|line_chart|kpi|text_table|filter|unknown",
                                    "tableau_worksheet_name": "Tableau worksheet name",
                                    "tables_used": ["source table names"],
                                    "columns_used": [
                                        {
                                            "column_name": "source column name",
                                            "table": "source table name when known",
                                            "role": "dimension|measure",
                                            "original_qlik_expression": "source expression when applicable",
                                            "translated_tableau_expression": "Tableau expression when applicable",
                                            "qualified_column_name": "Field (SourceTable)",
                                            "tableau_field": "[Field (SourceTable)]",
                                            "tableau_instance_name": "[none:Field__SourceTable:nk] or [sum:Field__SourceTable:qk]",
                                        }
                                    ],
                                    "dimensions": [
                                        {
                                            "label": "source dimension label",
                                            "field": "source field name",
                                            "tableau_field": "[SourceFieldName (SourceTable)]",
                                            "qualified_column_name": "SourceFieldName (SourceTable)",
                                            "tableau_instance_name": "[none:SourceFieldName__SourceTable:nk]",
                                            "role": "dimension",
                                        }
                                    ],
                                    "measures": [
                                        {
                                            "label": "source measure label",
                                            "source_expression": "source Qlik expression",
                                            "original_qlik_expression": "source Qlik expression",
                                            "tableau_expression": "Tableau expression using table-qualified [Field (Table)] references",
                                            "translated_tableau_expression": "Tableau expression",
                                            "role": "measure",
                                        }
                                    ],
                                    "warnings": ["unsupported or heuristic mapping notes"],
                                    "unsupported_mapping_notes": ["unsupported or heuristic mapping notes"],
                                }
                            ],
                        }
                    ],
                },
                "allowed_tableau_types": sorted(SUPPORTED_TABLEAU_TYPES),
                "intermediate_model": intermediate_model,
            },
            ensure_ascii=True,
        )
        content = self.llm_client.chat(system_prompt=system_prompt, user_prompt=user_prompt)
        return _extract_json_object(content)

    def _validate_llm_mapping(self, intermediate_model: JsonDict, mapping: JsonDict) -> None:
        if not isinstance(mapping, dict):
            raise ValueError("LLM mapping response must be a JSON object.")

        sheets = mapping.get("sheets")
        if not isinstance(sheets, list):
            raise ValueError("LLM mapping response must include a sheets array.")

        expected_visual_ids = {
            str(visual.get("id") or "").strip()
            for sheet in _as_list(intermediate_model.get("sheets"))
            if isinstance(sheet, dict)
            for visual in _as_list(sheet.get("visuals"))
            if isinstance(visual, dict) and str(visual.get("id") or "").strip()
        }
        mapped_visual_ids = {
            str(visual.get("id") or visual.get("visual_id") or "").strip()
            for sheet in sheets
            if isinstance(sheet, dict)
            for visual in _as_list(sheet.get("visuals"))
            if isinstance(visual, dict) and str(visual.get("id") or "").strip()
        }

        missing_visual_ids = sorted(expected_visual_ids - mapped_visual_ids)
        if missing_visual_ids:
            raise ValueError(
                "LLM mapping omitted Qlik visual ids: "
                + ", ".join(missing_visual_ids[:10])
                + (" ..." if len(missing_visual_ids) > 10 else "")
            )

        invalid_types = []
        for sheet in sheets:
            if not isinstance(sheet, dict):
                invalid_types.append("<sheet>")
                continue
            for visual in _as_list(sheet.get("visuals")):
                if not isinstance(visual, dict):
                    invalid_types.append("<visual>")
                    continue
                tableau_type = str(visual.get("tableau_type") or visual.get("mapped_tableau_visual_type") or "").strip()
                if tableau_type not in SUPPORTED_TABLEAU_TYPES:
                    invalid_types.append(tableau_type or "<empty>")
        if invalid_types:
            raise ValueError(f"LLM mapping returned unsupported Tableau visual type(s): {', '.join(invalid_types[:10])}.")


def build_tableau_mapping_contract(
    intermediate_model: JsonDict,
    proposed_mapping: JsonDict | None = None,
) -> JsonDict:
    """Build the deterministic Qlik-to-Tableau conversion contract written to tableau_mapping.json."""

    proposed_mapping = proposed_mapping if isinstance(proposed_mapping, dict) else {}
    proposed_visuals = _proposed_visuals_by_id(proposed_mapping)
    proposed_sheets = _proposed_sheets_by_id(proposed_mapping)
    tables = _as_table_metadata(intermediate_model)
    field_table_index = _field_table_index(tables)
    visual_qvd_matches = _visual_qvd_matches_by_id(intermediate_model)
    expression_map: list[JsonDict] = []
    visual_type_map: dict[str, str] = {}
    contract_warnings = _string_list(proposed_mapping.get("warnings"))

    contract_sheets = []
    for sheet_index, source_sheet in enumerate(_as_list(intermediate_model.get("sheets"))):
        if not isinstance(source_sheet, dict):
            continue
        sheet_id = str(source_sheet.get("id") or source_sheet.get("sheet_id") or "").strip()
        proposed_sheet = proposed_sheets.get(sheet_id, {})
        sheet_title = str(source_sheet.get("title") or source_sheet.get("name") or sheet_id or f"Sheet {sheet_index + 1}").strip()
        tableau_dashboard_name = str(
            proposed_sheet.get("tableau_dashboard_name")
            or proposed_sheet.get("tableau_name")
            or proposed_sheet.get("name")
            or sheet_title
        ).strip()

        contract_visuals = []
        for visual_index, source_visual in enumerate(_as_list(source_sheet.get("visuals"))):
            if not isinstance(source_visual, dict):
                continue
            visual = _build_visual_contract(
                source_visual=source_visual,
                proposed_visual=proposed_visuals.get(str(source_visual.get("id") or source_visual.get("visual_id") or "").strip(), {}),
                sheet_title=sheet_title,
                tableau_dashboard_name=tableau_dashboard_name,
                visual_index=visual_index,
                field_table_index=field_table_index,
                tables=tables,
                qvd_match=visual_qvd_matches.get(
                    str(source_visual.get("id") or source_visual.get("visual_id") or "").strip(),
                    {},
                ),
            )
            contract_visuals.append(visual)
            if visual["original_qlik_visual_type"]:
                visual_type_map[visual["original_qlik_visual_type"]] = visual["mapped_tableau_visual_type"]
            expression_map.extend(
                {
                    "visual_id": visual["visual_id"],
                    "visual_title": visual["visual_title"],
                    "source_expression": measure["source_expression"],
                    "tableau_expression": measure["tableau_expression"],
                }
                for measure in _as_list(visual.get("measures"))
                if isinstance(measure, dict) and str(measure.get("source_expression") or "").strip()
            )

        contract_sheets.append(
            {
                "id": sheet_id,
                "sheet_id": sheet_id,
                "name": sheet_title,
                "sheet_title": sheet_title,
                "tableau_name": tableau_dashboard_name,
                "tableau_dashboard_name": tableau_dashboard_name,
                "rank": source_sheet.get("rank", sheet_index),
                "visuals": contract_visuals,
            }
        )

    agent = dict(proposed_mapping.get("agent") or {})
    agent.setdefault("name", "QlikToTableauMappingAgent")
    agent["mode"] = "conversion_contract"
    agent["contract_enforced"] = True

    return {
        "contract_version": "1.0",
        "source": dict(intermediate_model.get("source") or {}),
        "agent": agent,
        "tables": tables,
        "fields": _as_list(intermediate_model.get("fields")),
        "visual_type_map": visual_type_map,
        "expression_map": expression_map,
        "sheets": contract_sheets,
        "warnings": contract_warnings,
    }


def _build_visual_contract(
    source_visual: JsonDict,
    proposed_visual: JsonDict,
    sheet_title: str,
    tableau_dashboard_name: str,
    visual_index: int,
    field_table_index: dict[str, str],
    tables: list[JsonDict],
    qvd_match: JsonDict | None = None,
) -> JsonDict:
    visual_id = str(source_visual.get("id") or source_visual.get("visual_id") or "").strip()
    visual_title = str(source_visual.get("title") or source_visual.get("visual_title") or visual_id or f"Visual {visual_index + 1}").strip()
    qlik_type = str(
        source_visual.get("source_type")
        or source_visual.get("original_qlik_visual_type")
        or source_visual.get("type")
        or ""
    ).strip()
    mapped_type = _normalize_tableau_type(
        proposed_visual.get("mapped_tableau_visual_type")
        or proposed_visual.get("tableau_type")
        or _default_tableau_type(qlik_type, source_visual)
    )
    worksheet_name = _safe_tableau_name(
        proposed_visual.get("tableau_worksheet_name")
        or proposed_visual.get("worksheet_name")
        or f"{tableau_dashboard_name or sheet_title} - {visual_title}"
    )

    warnings = _string_list(proposed_visual.get("warnings")) + _string_list(proposed_visual.get("unsupported_mapping_notes"))
    if mapped_type == "unknown":
        warnings.append(f"Unsupported or unknown Qlik visual type: {qlik_type or '<empty>'}.")

    proposed_dimensions = _proposal_items_by_key(proposed_visual.get("dimensions"), ("field", "column_name", "label"))
    dimensions, dimension_columns = _build_dimension_contracts(source_visual, proposed_dimensions, field_table_index, warnings)
    proposed_measures = _proposal_items_by_key(proposed_visual.get("measures"), ("source_expression", "original_qlik_expression", "label"))
    measures, measure_columns = _build_measure_contracts(source_visual, proposed_measures, field_table_index, warnings)

    columns_used = _dedupe_columns([*dimension_columns, *measure_columns])
    table_usage = _table_usage_for_visual(
        qvd_match=qvd_match,
        columns_used=columns_used,
        tables=tables,
        field_table_index=field_table_index,
    )
    tables_used = [item["table_name"] for item in table_usage if item.get("table_name")]
    if not tables_used and columns_used:
        warnings.append("No source table could be inferred for one or more visual columns.")
    if not dimensions and not measures:
        warnings.append("Visual has no extracted dimensions or measures.")

    warnings = _unique_strings(warnings)
    return {
        "id": visual_id,
        "visual_id": visual_id,
        "title": visual_title,
        "visual_title": visual_title,
        "source_type": qlik_type,
        "type": qlik_type,
        "original_qlik_visual_type": qlik_type,
        "tableau_type": mapped_type,
        "mapped_tableau_visual_type": mapped_type,
        "tableau_worksheet_name": worksheet_name,
        "worksheet_name": worksheet_name,
        "tables_used": tables_used,
        "table_usage": table_usage,
        "columns_used": columns_used,
        "dimensions": dimensions,
        "measures": measures,
        "warnings": warnings,
        "unsupported_mapping_notes": warnings,
    }


def _build_dimension_contracts(
    source_visual: JsonDict,
    proposed_dimensions: dict[str, JsonDict],
    field_table_index: dict[str, str],
    warnings: list[str],
) -> tuple[list[JsonDict], list[JsonDict]]:
    dimensions = []
    columns = []
    for index, dimension in enumerate(_as_list(source_visual.get("dimensions"))):
        if not isinstance(dimension, dict):
            continue
        field = _clean_field_name(dimension.get("field") or dimension.get("column_name") or "")
        label = str(dimension.get("label") or field or f"Dimension {index + 1}").strip()
        expression = str(dimension.get("expression") or "").strip()
        proposed = proposed_dimensions.get(_proposal_key(field)) or proposed_dimensions.get(_proposal_key(label)) or {}
        tableau_expression = str(proposed.get("tableau_expression") or proposed.get("translated_tableau_expression") or "").strip()
        if expression and not tableau_expression:
            tableau_expression = _translate_qlik_expression(expression, warnings)
        table_name = field_table_index.get(_field_key(field), "")
        qualified = _qualified_column_contract(field, table_name, "dimension")
        tableau_field = _normalize_proposed_tableau_field(proposed.get("tableau_field"), qualified["tableau_field"])
        tableau_expression = _qualify_tableau_expression(tableau_expression, {field: qualified["tableau_field"]})

        dimensions.append(
            {
                "label": label,
                "field": field,
                "column_name": field,
                "table": table_name,
                "qualified_column_name": qualified["qualified_column_name"],
                "tableau_field": tableau_field,
                "tableau_instance_name": qualified["tableau_instance_name"],
                "role": "dimension",
                "original_qlik_expression": expression,
                "tableau_expression": tableau_expression,
                "translated_tableau_expression": tableau_expression,
            }
        )
        if field:
            columns.append(
                {
                    "column_name": field,
                    "table": table_name,
                    "role": "dimension",
                    "source_label": label,
                    "original_qlik_expression": expression,
                    "tableau_expression": tableau_expression,
                    "translated_tableau_expression": tableau_expression,
                    "qualified_column_name": qualified["qualified_column_name"],
                    "tableau_field": tableau_field,
                    "tableau_instance_name": qualified["tableau_instance_name"],
                }
            )
    return dimensions, columns


def _build_measure_contracts(
    source_visual: JsonDict,
    proposed_measures: dict[str, JsonDict],
    field_table_index: dict[str, str],
    warnings: list[str],
) -> tuple[list[JsonDict], list[JsonDict]]:
    measures = []
    columns = []
    for index, measure in enumerate(_as_list(source_visual.get("measures"))):
        if not isinstance(measure, dict):
            continue
        source_expression = str(measure.get("expression") or measure.get("source_expression") or "").strip()
        label = str(measure.get("label") or source_expression or f"Measure {index + 1}").strip()
        proposed = proposed_measures.get(_proposal_key(source_expression)) or proposed_measures.get(_proposal_key(label)) or {}
        tableau_expression = str(proposed.get("tableau_expression") or proposed.get("translated_tableau_expression") or "").strip()
        if source_expression and not tableau_expression:
            tableau_expression = _translate_qlik_expression(source_expression, warnings)
        measure_fields = _fields_from_qlik_expression(source_expression)

        measure_columns = []
        expression_replacements: dict[str, str] = {}
        for field in measure_fields:
            table_name = field_table_index.get(_field_key(field), "")
            qualified = _qualified_column_contract(field, table_name, "measure", tableau_expression)
            expression_replacements[field] = qualified["tableau_field"]
            column = {
                "column_name": field,
                "table": table_name,
                "role": "measure",
                "source_label": label,
                "original_qlik_expression": source_expression,
                "tableau_expression": tableau_expression,
                "translated_tableau_expression": tableau_expression,
                "qualified_column_name": qualified["qualified_column_name"],
                "tableau_field": qualified["tableau_field"],
                "tableau_instance_name": qualified["tableau_instance_name"],
            }
            columns.append(column)
            measure_columns.append(column)
        tableau_expression = _qualify_tableau_expression(tableau_expression, expression_replacements)
        for column in measure_columns:
            column["tableau_expression"] = tableau_expression
            column["translated_tableau_expression"] = tableau_expression
        if source_expression and not measure_fields:
            warnings.append(f"No source column could be inferred from measure expression: {source_expression}.")

        measures.append(
            {
                "label": label,
                "source_expression": source_expression,
                "original_qlik_expression": source_expression,
                "tableau_expression": tableau_expression,
                "translated_tableau_expression": tableau_expression,
                "role": "measure",
                "columns": measure_columns,
            }
        )
    return measures, columns


def _qualified_column_contract(
    field_name: Any,
    table_name: Any,
    role: str,
    expression: Any = "",
) -> JsonDict:
    field = _clean_field_name(field_name)
    table = _clean_field_name(table_name) or "QlikModel"
    qualified_name = _qualified_column_name(field, table)
    instance_name = _tableau_instance_name(field, table, role, expression)
    return {
        "qualified_column_name": qualified_name,
        "tableau_field": f"[{qualified_name}]" if qualified_name else "",
        "tableau_instance_name": instance_name,
    }


def _qualified_column_name(field_name: Any, table_name: Any) -> str:
    field = _clean_field_name(field_name)
    table = _clean_field_name(table_name) or "QlikModel"
    return f"{field} ({table})" if field else ""


def _tableau_instance_name(field_name: Any, table_name: Any, role: str, expression: Any = "") -> str:
    field = _clean_field_name(field_name)
    if not field:
        return ""
    table = _clean_field_name(table_name) or "QlikModel"
    token = re.sub(r"[^A-Za-z0-9_]+", "_", f"{field}__{table}").strip("_") or "Field"
    if role == "measure":
        return f"[{_aggregation_prefix(expression)}:{token}:qk]"
    return f"[none:{token}:nk]"


def _aggregation_prefix(expression: Any) -> str:
    text = str(expression or "").strip()
    match = re.match(r"([A-Za-z]+)\s*\(", text)
    if not match:
        return "sum"
    normalized = match.group(1).strip().lower()
    return {
        "average": "avg",
        "avg": "avg",
        "count": "cnt",
        "countd": "ctd",
        "min": "min",
        "max": "max",
        "sum": "sum",
    }.get(normalized, "sum")


def _normalize_proposed_tableau_field(proposed_value: Any, fallback: str) -> str:
    proposed = str(proposed_value or "").strip()
    if proposed and _looks_table_qualified_field(proposed):
        return proposed
    return fallback


def _qualify_tableau_expression(expression: Any, replacements: dict[str, str]) -> str:
    text = str(expression or "").strip()
    if not text or not replacements:
        return text
    lookup = {_field_key(field): qualified for field, qualified in replacements.items() if field and qualified}

    def replace(match: re.Match[str]) -> str:
        inner = _clean_field_name(match.group(1))
        if _looks_table_qualified_field(inner):
            return f"[{inner}]"
        return lookup.get(_field_key(inner), f"[{inner}]")

    return re.sub(r"\[([^\]]+)\]", replace, text)


def _looks_table_qualified_field(value: Any) -> bool:
    text = _clean_field_name(value)
    return bool(re.search(r"\s+\([^)]+\)$", text))


def _as_table_metadata(intermediate_model: JsonDict) -> list[JsonDict]:
    tables = []
    for table in _as_list(intermediate_model.get("tables")):
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or table.get("table_name") or "").strip()
        if not table_name:
            continue
        fields = []
        for field in _as_list(table.get("fields")):
            if isinstance(field, dict):
                field_name = _clean_field_name(field.get("name") or field.get("field") or "")
                if field_name:
                    fields.append({"name": field_name, "data_type": field.get("data_type") or field.get("type") or ""})
            else:
                field_name = _clean_field_name(field)
                if field_name:
                    fields.append({"name": field_name, "data_type": ""})
        if not fields:
            for field_name in _as_list(table.get("field_names")):
                cleaned = _clean_field_name(field_name)
                if cleaned:
                    fields.append({"name": cleaned, "data_type": ""})
        tables.append(
            {
                "table_name": table_name,
                "name": table_name,
                "source": table.get("source") or "",
                "record_count": table.get("record_count"),
                "fields": _dedupe_field_metadata(fields),
                "field_names": [field["name"] for field in _dedupe_field_metadata(fields)],
            }
        )
    return tables


def _visual_qvd_matches_by_id(intermediate_model: JsonDict) -> dict[str, JsonDict]:
    dataprep_metadata = intermediate_model.get("dataprep_cache_metadata") if isinstance(intermediate_model, dict) else {}
    if not isinstance(dataprep_metadata, dict):
        return {}

    matches_by_id: dict[str, JsonDict] = {}
    for match in _as_list(dataprep_metadata.get("visual_table_matches")):
        if not isinstance(match, dict):
            continue
        visual_id = str(match.get("visual_id") or "").strip()
        best_match = match.get("best_match") if isinstance(match.get("best_match"), dict) else {}
        if visual_id and str(best_match.get("table_name") or "").strip():
            matches_by_id[visual_id] = best_match
    return matches_by_id


def _table_usage_for_visual(
    qvd_match: JsonDict | None,
    columns_used: list[JsonDict],
    tables: list[JsonDict],
    field_table_index: dict[str, str],
) -> list[JsonDict]:
    usage: dict[str, JsonDict] = {}
    qvd_match = qvd_match if isinstance(qvd_match, dict) else {}
    if qvd_match.get("table_name"):
        usage[str(qvd_match.get("table_name"))] = {
            "table_name": str(qvd_match.get("table_name")),
            "source": "dataprep_qvd_match",
            "matched_columns": _string_list(qvd_match.get("matched_fields")),
            "match_score": qvd_match.get("score", 0),
        }

    for column in columns_used:
        field = str(column.get("column_name") or "").strip()
        table_name = str(column.get("table") or field_table_index.get(_field_key(field), "")).strip()
        if not table_name:
            continue
        entry = usage.setdefault(
            table_name,
            {"table_name": table_name, "source": _table_source(table_name, tables), "matched_columns": [], "match_score": 0},
        )
        matched_columns = entry.setdefault("matched_columns", [])
        if isinstance(matched_columns, list) and field and field not in matched_columns:
            matched_columns.append(field)

    return sorted(usage.values(), key=lambda item: str(item.get("table_name") or "").lower())


def _field_table_index(tables: list[JsonDict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for table in tables:
        table_name = str(table.get("table_name") or table.get("name") or "").strip()
        if not table_name:
            continue
        for field in _as_list(table.get("fields")):
            field_name = field.get("name") if isinstance(field, dict) else field
            key = _field_key(field_name)
            if key:
                index.setdefault(key, table_name)
        for field_name in _as_list(table.get("field_names")):
            key = _field_key(field_name)
            if key:
                index.setdefault(key, table_name)
    return index


def _table_source(table_name: str, tables: list[JsonDict]) -> str:
    for table in tables:
        if str(table.get("table_name") or table.get("name") or "").strip().lower() == table_name.lower():
            return str(table.get("source") or "")
    return ""


def _default_tableau_type(qlik_type: str, visual: JsonDict) -> str:
    normalized = str(qlik_type or "").strip().lower()
    if normalized in {"barchart", "bar-chart", "bar"}:
        return "bar_chart"
    if normalized in {"linechart", "line-chart", "line"}:
        return "line_chart"
    if normalized in {"kpi", "gauge"}:
        return "kpi"
    if normalized in {"table", "straighttable", "pivot-table", "pivot", "sn-table"}:
        return "text_table"
    if normalized in {"filterpane", "filter", "listbox"}:
        return "filter"
    if normalized in {"scatterplot", "scatter"}:
        return "scatter_plot"
    if normalized in {"piechart", "pie"}:
        return "pie_chart"
    if normalized == "map":
        return "map"
    if normalized == "treemap":
        return "treemap"
    if normalized == "histogram":
        return "histogram"
    if normalized == "boxplot":
        return "box_plot"
    if normalized in {"area", "areachart"}:
        return "area_chart"
    if _as_list(visual.get("measures")) and not _as_list(visual.get("dimensions")):
        return "kpi"
    if _as_list(visual.get("dimensions")) and _as_list(visual.get("measures")):
        return "bar_chart"
    return "unknown"


def _normalize_tableau_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "bar": "bar_chart",
        "line": "line_chart",
        "kpi_card": "kpi",
        "crosstab": "text_table",
        "text": "text_table",
        "tableau_table": "text_table",
        "scatter": "scatter_plot",
        "pie": "pie_chart",
        "boxplot": "box_plot",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in SUPPORTED_TABLEAU_TYPES else "unknown"


def _translate_qlik_expression(expression: Any, warnings: list[str]) -> str:
    text = str(expression or "").strip()
    if not text:
        return ""

    aggregation_map = {
        "sum": "SUM",
        "avg": "AVG",
        "average": "AVG",
        "count": "COUNT",
        "min": "MIN",
        "max": "MAX",
    }

    def replace_aggregation(match: re.Match[str]) -> str:
        function_name = aggregation_map.get(match.group(1).lower(), match.group(1).upper())
        field_name = _clean_field_name(match.group(2) or match.group(3) or "")
        return f"{function_name}([{field_name}])" if field_name else match.group(0)

    translated = re.sub(
        r"\b(sum|avg|average|count|min|max)\s*\(\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_.]*))\s*\)",
        replace_aggregation,
        text,
        flags=re.IGNORECASE,
    )
    if translated == text:
        fields = _fields_from_qlik_expression(text)
        if len(fields) == 1 and text in {fields[0], f"[{fields[0]}]"}:
            translated = f"[{fields[0]}]"
        elif fields:
            translated = text
            warnings.append(f"Expression kept with heuristic translation: {text}.")
        else:
            warnings.append(f"Expression could not be translated automatically: {text}.")
    return translated


def _fields_from_qlik_expression(expression: Any) -> list[str]:
    text = str(expression or "")
    fields: list[str] = []
    for match in re.finditer(r"\[([^\]]+)\]", text):
        _append_unique(fields, _clean_field_name(match.group(1)))
    for match in re.finditer(
        r"\b(?:sum|avg|average|count|min|max)\s*\(\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_.]*))\s*\)",
        text,
        flags=re.IGNORECASE,
    ):
        _append_unique(fields, _clean_field_name(match.group(1) or match.group(2) or ""))
    if not fields and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", text.strip()):
        _append_unique(fields, text.strip())
    return fields


def _proposed_visuals_by_id(mapping: JsonDict) -> dict[str, JsonDict]:
    visuals: dict[str, JsonDict] = {}
    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            visual_id = str(visual.get("visual_id") or visual.get("id") or "").strip()
            if visual_id:
                visuals[visual_id] = visual
    return visuals


def _proposed_sheets_by_id(mapping: JsonDict) -> dict[str, JsonDict]:
    sheets: dict[str, JsonDict] = {}
    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        sheet_id = str(sheet.get("sheet_id") or sheet.get("id") or "").strip()
        if sheet_id:
            sheets[sheet_id] = sheet
    return sheets


def _proposal_items_by_key(value: Any, keys: tuple[str, ...]) -> dict[str, JsonDict]:
    items: dict[str, JsonDict] = {}
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        for key in keys:
            proposal_key = _proposal_key(item.get(key))
            if proposal_key:
                items.setdefault(proposal_key, item)
    return items


def _proposal_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _field_key(value: Any) -> str:
    return _clean_field_name(value).lower()


def _clean_field_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.strip()


def _dedupe_columns(columns: list[JsonDict]) -> list[JsonDict]:
    deduped = []
    seen = set()
    for column in columns:
        key = (
            str(column.get("column_name") or "").lower(),
            str(column.get("table") or column.get("source_table") or "").lower(),
            str(column.get("role") or "").lower(),
            str(column.get("original_qlik_expression") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(column)
    return deduped


def _dedupe_field_metadata(fields: list[JsonDict]) -> list[JsonDict]:
    deduped = []
    seen = set()
    for field in fields:
        name = str(field.get("name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        deduped.append(field)
    return deduped


def _safe_tableau_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"[\[\]*/\\?:]", "_", text)
    return text[:80] or "Worksheet"


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _unique_strings(values: list[str]) -> list[str]:
    output = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_json_object(content: str) -> JsonDict:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object from the mapping agent.")
    return payload
