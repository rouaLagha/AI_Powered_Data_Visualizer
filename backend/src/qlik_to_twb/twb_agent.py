from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any

from src.rdl_to_twb.llm_client import LLMClient
from src.rdl_to_twb.twb_builder import normalize_generated_twb, validate_twb_structure, write_twb_file

from .twb_writer import build_minimal_twb_xml


JsonDict = dict[str, Any]


def generate_validated_twb(
    intermediate_model: JsonDict,
    mapping: JsonDict,
    output_path: str | Path,
    llm: LLMClient | None = None,
    max_validation_rounds: int = 2,
) -> JsonDict:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    deterministic_seed_xml = _deterministic_seed_xml(intermediate_model=intermediate_model, mapping=mapping)
    draft_source = "deterministic_seed"
    draft_error = ""

    if llm is not None:
        try:
            raw_response = llm.chat(
                system_prompt=_twb_xml_system_prompt(),
                user_prompt=_twb_xml_user_prompt(mapping=mapping),
            )
            draft_xml = _repair_required_twb_attributes(_extract_workbook_xml(raw_response))
            draft_source = "llm"
        except Exception as exc:
            draft_xml = _repair_required_twb_attributes(deterministic_seed_xml)
            draft_error = f"{type(exc).__name__}: {exc}"
    else:
        draft_xml = _repair_required_twb_attributes(deterministic_seed_xml)

    draft_xml_path = output_path.with_name(f"{output_path.stem}_draft.xml")
    draft_xml_path.write_text(draft_xml, encoding="utf-8")

    validated_xml, report = validate_and_repair_twb_xml(
        draft_xml=draft_xml,
        deterministic_seed_xml=_repair_required_twb_attributes(deterministic_seed_xml),
        mapping=mapping,
        max_validation_rounds=max_validation_rounds,
    )
    report.update(
        {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "draft_source": draft_source,
            "draft_error": draft_error,
            "draft_xml_path": str(draft_xml_path),
            "twb_path": str(output_path),
        }
    )

    validated_xml_path = output_path.with_name(f"{output_path.stem}_validated.xml")
    validated_xml_path.write_text(validated_xml, encoding="utf-8")
    write_twb_file(validated_xml, output_path)

    report["validated_xml_path"] = str(validated_xml_path)
    return report


def validate_and_repair_twb_xml(
    draft_xml: str,
    deterministic_seed_xml: str,
    mapping: JsonDict,
    max_validation_rounds: int = 2,
) -> tuple[str, JsonDict]:
    attempts = []
    current = str(draft_xml or "").strip()
    source = "draft"
    deterministic_seed_used = False
    max_rounds = max(1, int(max_validation_rounds or 1))
    last_validated_xml = ""
    last_issues: list[str] = []

    for attempt_number in range(1, max_rounds + 1):
        try:
            normalized = _repair_required_twb_attributes(normalize_generated_twb(current))
            normalized = _repair_mapping_field_references(normalized, mapping)
            issues = [
                *validate_twb_structure(normalized),
                *_validate_mapping_alignment(normalized, mapping),
            ]
        except Exception as exc:
            normalized = ""
            issues = [f"XML normalization failed: {type(exc).__name__}: {exc}"]

        attempts.append(
            {
                "attempt": attempt_number,
                "source": source,
                "issues": issues,
                "issue_count": len(issues),
            }
        )
        last_validated_xml = normalized
        last_issues = issues
        if not issues and normalized:
            status = "valid_after_deterministic_repair" if deterministic_seed_used else "valid"
            return normalized, {
                "status": status,
                "issues": [],
                "attempts": attempts,
                "deterministic_seed_used": deterministic_seed_used,
            }

        if deterministic_seed_used:
            break

        current = deterministic_seed_xml
        source = "deterministic_seed"
        deterministic_seed_used = True

    if not last_validated_xml:
        last_validated_xml = _repair_required_twb_attributes(normalize_generated_twb(deterministic_seed_xml))
        last_validated_xml = _repair_mapping_field_references(last_validated_xml, mapping)
        last_issues = [
            *validate_twb_structure(last_validated_xml),
            *_validate_mapping_alignment(last_validated_xml, mapping),
        ]

    status = "valid_after_deterministic_repair" if not last_issues else "repaired_with_residual_issues"
    return last_validated_xml, {
        "status": status,
        "issues": last_issues,
        "attempts": attempts,
        "deterministic_seed_used": deterministic_seed_used,
    }


def _twb_xml_system_prompt() -> str:
    return (
        "You are Codex acting as a Tableau .twb XML generation agent. "
        "Generate an initial Tableau workbook XML draft from a validated Qlik-to-Tableau mapping contract. "
        "Return only XML, no Markdown, no explanations, no code fences. "
        "The root element must be <workbook>. Do not include XSD/schema definitions, credentials, secrets, or comments. "
        "Prioritize the datasource block first: define the connection, named-connections, table collection, cols mapping, and field dependencies before worksheets. "
        "Never use unqualified worksheet field references. Use table-qualified fields from the mapping, for example [SalesAmount (FactResellerSales)], not [SalesAmount]. "
        "Worksheet column-instance names must use the mapping tableau_instance_name values, for example [sum:SalesAmount__FactResellerSales:qk]. "
        "When any worksheet uses fields from multiple source tables, define a physical join relation with a valid join clause on shared key fields instead of leaving those tables as unrelated collection entries. "
        "Every dashboard zone must include id, x, y, w, and h attributes. "
        "The outer dashboard root zone must be a layout-basic zone with valid geometry, and each worksheet zone must also have its own id and geometry."
    )


def _twb_xml_user_prompt(mapping: JsonDict) -> str:
    payload = {
        "task": "Generate an initial Tableau .twb XML workbook draft from a validated conversion contract.",
        "hard_requirements": [
            "Root element must be workbook.",
            "Include top-level datasources, worksheets, dashboards, and windows sections.",
            "In the datasource section, define the data source connection information first.",
            "Use a federated connection with named-connections when table metadata is available.",
            "For SQL Server connections, use class='sqlserver' with authentication='sspi', instance-qualified server names (e.g. DESKTOP-GC2J9J1\\SQLEXPRESS), and metadata attributes minimum-driver-version='SQL Server Native Client 10.0' and odbc-native-protocol='yes'.",
            "Represent the source tables as a relation collection and map columns back to their related tables.",
            "Never create unqualified field definitions or worksheet references such as [SalesAmount] when the contract provides table, qualified_column_name, tableau_field, or tableau_instance_name.",
            "Use qualified datasource column names exactly as [Field (Table)] and use each visual column's tableau_instance_name in worksheet rows, columns, and encodings.",
            "When a visual uses columns from multiple tables, create a joined relation tree using shared key fields; do not rely on an unrelated relation collection for that worksheet.",
            "Use every visual's tableau_worksheet_name exactly as a worksheet name.",
            "Place every worksheet in a dashboard zone.",
            "Every <zone> element in <dashboards> must include id, x, y, w, and h attributes.",
            "Use a layout-basic root dashboard zone with valid geometry before worksheet zones.",
            "Use columns_used/dimensions/measures from the contract only; do not invent source fields.",
            "Do not include password, api key, token, secret, XSD/schema-definition, or connection-pooling elements.",
            "Return raw XML only.",
        ],
        "mapping_contract": mapping,
    }
    return json.dumps(payload, ensure_ascii=True)


def _deterministic_seed_xml(intermediate_model: JsonDict, mapping: JsonDict) -> str:
    workbook = build_minimal_twb_xml(intermediate_model=intermediate_model, mapping=mapping)
    return ET.tostring(workbook, encoding="unicode")


def _validate_mapping_alignment(xml_content: str, mapping: JsonDict) -> list[str]:
    issues: list[str] = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        return [f"XML parse error during mapping alignment validation: {exc}"]

    worksheet_names = {
        str(element.attrib.get("name") or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "worksheet" and str(element.attrib.get("name") or "").strip()
    }
    expected_names = {
        str(visual.get("tableau_worksheet_name") or visual.get("worksheet_name") or "").strip()
        for sheet in _as_list(mapping.get("sheets"))
        if isinstance(sheet, dict)
        for visual in _as_list(sheet.get("visuals"))
        if isinstance(visual, dict) and str(visual.get("tableau_worksheet_name") or visual.get("worksheet_name") or "").strip()
    }

    for worksheet_name in sorted(expected_names - worksheet_names):
        issues.append(f"Missing worksheet required by mapping contract: {worksheet_name}")
    issues.extend(_validate_dashboard_zone_geometry(root))
    issues.extend(_validate_worksheet_field_qualification(root, mapping))
    issues.extend(_validate_multitable_visual_relations(root, mapping))
    return issues


def _repair_mapping_field_references(xml_content: str, mapping: JsonDict) -> str:
    try:
        root = ET.fromstring(str(xml_content or "").strip())
    except ET.ParseError:
        return str(xml_content or "").strip()

    replacements_by_worksheet = _field_replacements_by_worksheet(mapping)
    if not replacements_by_worksheet:
        return ET.tostring(root, encoding="unicode")

    for worksheet in [el for el in root.iter() if _local_name(el.tag) == "worksheet"]:
        worksheet_name = str(worksheet.attrib.get("name") or "").strip()
        replacements = replacements_by_worksheet.get(worksheet_name) or replacements_by_worksheet.get("*")
        if not replacements:
            continue

        field_replacements = replacements["fields"]
        instance_replacements = replacements["instances"]
        for element in worksheet.iter():
            local = _local_name(element.tag)
            if local == "column":
                name = str(element.attrib.get("name") or "").strip()
                if name in field_replacements:
                    element.attrib["name"] = field_replacements[name]
            elif local == "column-instance":
                column_ref = str(element.attrib.get("column") or "").strip()
                instance_ref = str(element.attrib.get("name") or "").strip()
                if column_ref in field_replacements:
                    element.attrib["column"] = field_replacements[column_ref]
                if instance_ref in instance_replacements:
                    element.attrib["name"] = instance_replacements[instance_ref]

            for attr_name, attr_value in list(element.attrib.items()):
                if attr_name not in {"column", "name"}:
                    continue
                repaired = _replace_known_instance_refs(str(attr_value or ""), instance_replacements)
                if repaired != attr_value:
                    element.attrib[attr_name] = repaired

            if element.text:
                element.text = _replace_known_instance_refs(element.text, instance_replacements)

    return ET.tostring(root, encoding="unicode")


def _field_replacements_by_worksheet(mapping: JsonDict) -> dict[str, JsonDict]:
    output: dict[str, JsonDict] = {}
    all_fields: dict[str, str] = {}
    all_instances: dict[str, str] = {}

    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            worksheet_name = str(visual.get("tableau_worksheet_name") or visual.get("worksheet_name") or "").strip()
            if not worksheet_name:
                continue
            entry = output.setdefault(worksheet_name, {"fields": {}, "instances": {}})
            for column in _visual_contract_columns(visual):
                field, table = _field_table_from_mapping_column(column)
                if not field or not table:
                    continue
                role = str(column.get("role") or "").strip().lower()
                role = role if role in {"dimension", "measure"} else "dimension"
                qualified_field = str(column.get("tableau_field") or f"[{field} ({table})]").strip()
                instance_name = str(column.get("tableau_instance_name") or _default_instance_name(field, table, role)).strip()
                display_name = f"{field} ({table})"
                token = _instance_token(field, table)
                field_aliases = {f"[{field}]", _bracket(display_name)}
                instance_aliases = {
                    _default_instance_name(field, "", role),
                    _default_instance_name(display_name, "", role),
                    f"[sum:{field}:qk]",
                    f"[sum:{display_name}:qk]",
                    f"[none:{field}:nk]",
                    f"[none:{display_name}:nk]",
                    f"[sum:{token}:qk]",
                    f"[none:{token}:nk]",
                }
                for alias in field_aliases:
                    if alias != qualified_field:
                        entry["fields"][alias] = qualified_field
                        all_fields[alias] = qualified_field
                for alias in instance_aliases:
                    if alias != instance_name:
                        entry["instances"][alias] = instance_name
                        all_instances[alias] = instance_name

    if all_fields or all_instances:
        output["*"] = {"fields": all_fields, "instances": all_instances}
    return output


def _replace_known_instance_refs(value: str, replacements: dict[str, str]) -> str:
    repaired = str(value or "")
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if old and new:
            repaired = repaired.replace(old, new)
    return repaired


def _field_table_from_mapping_column(column: JsonDict) -> tuple[str, str]:
    field = _clean_bracketed(column.get("column_name") or column.get("field") or "")
    table = _clean_bracketed(column.get("table") or column.get("source_table") or "")
    if field and table:
        return field, table
    parsed_field, parsed_table = _split_qualified_display_field(
        column.get("tableau_field") or column.get("qualified_column_name") or ""
    )
    return field or parsed_field, table or parsed_table


def _default_instance_name(field: Any, table: Any, role: str) -> str:
    clean_field = _clean_bracketed(field)
    clean_table = _clean_bracketed(table)
    if not clean_field:
        return ""
    token = _instance_token(clean_field, clean_table) if clean_table else clean_field
    if role == "measure":
        return f"[sum:{token}:qk]"
    return f"[none:{token}:nk]"


def _instance_token(field: Any, table: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", f"{_clean_bracketed(field)}__{_clean_bracketed(table)}").strip("_")


def _bracket(value: Any) -> str:
    text = _clean_bracketed(value)
    return f"[{text}]" if text else ""


def _validate_dashboard_zone_geometry(root: ET.Element) -> list[str]:
    issues: list[str] = []
    required = ("id", "x", "y", "w", "h")
    for dashboard in [el for el in root.iter() if _local_name(el.tag) == "dashboard"]:
        dashboard_name = str(dashboard.attrib.get("name") or "<dashboard>")
        for zone in [el for el in dashboard.iter() if _local_name(el.tag) == "zone"]:
            missing = [attr for attr in required if not str(zone.attrib.get(attr) or "").strip()]
            if missing:
                issues.append(
                    f"Dashboard zone in {dashboard_name} is missing required attribute(s): {', '.join(missing)}."
                )
    return issues


def _validate_worksheet_field_qualification(root: ET.Element, mapping: JsonDict) -> list[str]:
    expected = _qualified_fields_from_mapping(mapping)
    if not expected:
        return []

    issues: list[str] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "map":
            issue = _unqualified_field_issue(str(element.attrib.get("key") or ""), expected, "<datasource>")
            if issue:
                issues.append(issue)
        elif local == "column":
            issue = _unqualified_field_issue(str(element.attrib.get("name") or ""), expected, "<datasource>")
            if issue:
                issues.append(issue)
        elif local == "column-instance":
            issue = _unqualified_field_issue(str(element.attrib.get("column") or ""), expected, "<datasource>")
            if issue:
                issues.append(issue)
            instance_issue = _unqualified_instance_issue(str(element.attrib.get("name") or ""), expected, "<datasource>")
            if instance_issue:
                issues.append(instance_issue)

    for worksheet in [el for el in root.iter() if _local_name(el.tag) == "worksheet"]:
        worksheet_name = str(worksheet.attrib.get("name") or "<worksheet>")
        for deps in [el for el in worksheet.iter() if _local_name(el.tag) == "datasource-dependencies"]:
            for child in list(deps):
                local = _local_name(child.tag)
                if local == "column":
                    name = str(child.attrib.get("name") or "").strip()
                    issue = _unqualified_field_issue(name, expected, worksheet_name)
                    if issue:
                        issues.append(issue)
                elif local == "column-instance":
                    column_name = str(child.attrib.get("column") or "").strip()
                    instance_name = str(child.attrib.get("name") or "").strip()
                    issue = _unqualified_field_issue(column_name, expected, worksheet_name)
                    if issue:
                        issues.append(issue)
                    instance_issue = _unqualified_instance_issue(instance_name, expected, worksheet_name)
                    if instance_issue:
                        issues.append(instance_issue)

        for table_child in [el for el in worksheet.iter() if _local_name(el.tag) in {"rows", "cols"}]:
            text = str(table_child.text or "")
            for instance_name in re.findall(r"\[[^\]]+\]\.(\[[^\]]+\])", text):
                instance_issue = _unqualified_instance_issue(instance_name, expected, worksheet_name)
                if instance_issue:
                    issues.append(instance_issue)

        for element in worksheet.iter():
            column_attr = str(element.attrib.get("column") or "")
            for instance_name in re.findall(r"\[[^\]]+\]\.(\[[^\]]+\])", column_attr):
                instance_issue = _unqualified_instance_issue(instance_name, expected, worksheet_name)
                if instance_issue:
                    issues.append(instance_issue)

    return _unique_strings(issues)


def _qualified_fields_from_mapping(mapping: JsonDict) -> dict[str, JsonDict]:
    expected: dict[str, JsonDict] = {}
    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            for column in _visual_contract_columns(visual):
                field = _clean_bracketed(column.get("column_name") or column.get("field") or "")
                table = _clean_bracketed(column.get("table") or column.get("source_table") or "")
                if not field:
                    parsed_field, parsed_table = _split_qualified_display_field(
                        column.get("tableau_field") or column.get("qualified_column_name") or ""
                    )
                    field = parsed_field
                    table = table or parsed_table
                if not field or not table:
                    continue
                role = str(column.get("role") or "").strip().lower()
                qualified_field = str(column.get("tableau_field") or f"[{field} ({table})]").strip()
                instance_name = str(column.get("tableau_instance_name") or "").strip()
                token = re.sub(r"[^A-Za-z0-9_]+", "_", f"{field}__{table}").strip("_")
                field_key = field.lower()
                entry = expected.setdefault(
                    field_key,
                    {"qualified_fields": set(), "tokens": set(), "roles": set()},
                )
                entry["qualified_fields"].add(qualified_field)
                if instance_name:
                    entry["tokens"].add(instance_name)
                if token:
                    if role == "measure":
                        entry["tokens"].add(f"[sum:{token}:qk]")
                    else:
                        entry["tokens"].add(f"[none:{token}:nk]")
                if role:
                    entry["roles"].add(role)
    return expected


def _validate_multitable_visual_relations(root: ET.Element, mapping: JsonDict) -> list[str]:
    multitable_visuals = []
    for sheet in _as_list(mapping.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        for visual in _as_list(sheet.get("visuals")):
            if not isinstance(visual, dict):
                continue
            tables = _visual_source_tables(visual)
            if len(tables) > 1:
                multitable_visuals.append((str(visual.get("tableau_worksheet_name") or visual.get("worksheet_name") or ""), tables))

    if not multitable_visuals:
        return []
    has_join_relation = any(
        _local_name(element.tag) == "relation" and str(element.attrib.get("type") or "").strip().lower() == "join"
        for element in root.iter()
    )
    if has_join_relation:
        return []

    return [
        "Multi-table visual "
        + (worksheet_name or "<worksheet>")
        + " uses "
        + ", ".join(tables)
        + " but the datasource has no physical join relation; Tableau may query the wrong relationOp."
        for worksheet_name, tables in multitable_visuals
    ]


def _visual_source_tables(visual: JsonDict) -> list[str]:
    tables = []
    for table_name in _as_list(visual.get("tables_used")):
        text = _clean_bracketed(table_name)
        if text and text not in tables:
            tables.append(text)
    for column in _visual_contract_columns(visual):
        table_name = _clean_bracketed(column.get("table") or column.get("source_table") or "")
        if table_name and table_name not in tables:
            tables.append(table_name)
    return tables


def _visual_contract_columns(visual: JsonDict) -> list[JsonDict]:
    columns = [item for item in _as_list(visual.get("columns_used")) if isinstance(item, dict)]
    columns.extend(item for item in _as_list(visual.get("dimensions")) if isinstance(item, dict))
    for measure in _as_list(visual.get("measures")):
        if not isinstance(measure, dict):
            continue
        columns.extend(item for item in _as_list(measure.get("columns")) if isinstance(item, dict))
    return columns


def _unqualified_field_issue(field_ref: str, expected: dict[str, JsonDict], worksheet_name: str) -> str:
    field_name = _clean_bracketed(field_ref)
    if not field_name:
        return ""
    parsed_field, parsed_table = _split_qualified_display_field(field_name)
    if parsed_table:
        return ""
    entry = expected.get(parsed_field.lower())
    if not entry:
        return ""
    qualified = sorted(entry.get("qualified_fields") or [])
    return (
        f"Worksheet {worksheet_name} uses unqualified field {field_ref}; "
        f"use {qualified[0] if qualified else '<qualified field>'} from the mapping contract."
    )


def _unqualified_instance_issue(instance_ref: str, expected: dict[str, JsonDict], worksheet_name: str) -> str:
    text = _clean_bracketed(instance_ref)
    if not text:
        return ""
    parts = text.split(":")
    if len(parts) < 3:
        return ""
    token = parts[1]
    if "__" in token:
        return ""
    parsed_field, parsed_table = _split_qualified_display_field(token)
    if parsed_table and parsed_field:
        entry = expected.get(parsed_field.lower())
    else:
        entry = expected.get(token.lower())
    if not entry:
        return ""
    qualified_instances = sorted(entry.get("tokens") or [])
    return (
        f"Worksheet {worksheet_name} uses unqualified column-instance {instance_ref}; "
        f"use {qualified_instances[0] if qualified_instances else '<qualified instance>'} from the mapping contract."
    )


def _split_qualified_display_field(value: Any) -> tuple[str, str]:
    text = _clean_bracketed(value)
    match = re.match(r"^(.*?)\s+\(([^()]+)\)$", text)
    if match:
        return _clean_bracketed(match.group(1)), _clean_bracketed(match.group(2))
    return text, ""


def _clean_bracketed(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text


def _extract_workbook_xml(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("TWB XML agent returned empty content.")
    if text.startswith("```"):
        text = _strip_code_fences(text).strip()

    candidate = _find_parseable_workbook_fragment(text)
    if candidate:
        return candidate

    start = text.find("<workbook")
    end = text.rfind("</workbook>")
    if start >= 0 and end >= start:
        return text[start : end + len("</workbook>")].strip()
    return text


def _repair_required_twb_attributes(xml_content: str) -> str:
    try:
        root = ET.fromstring(str(xml_content or "").strip())
    except ET.ParseError:
        return str(xml_content or "").strip()

    if _local_name(root.tag) != "workbook":
        return ET.tostring(root, encoding="unicode")

    for dashboard in [el for el in root.iter() if _local_name(el.tag) == "dashboard"]:
        zones = _find_direct_child(dashboard, "zones")
        if zones is None:
            continue

        zone_nodes = [child for child in list(zones) if _local_name(child.tag) == "zone"]
        if not zone_nodes:
            continue

        next_zone_id = _next_zone_id(root)
        def repair_zone(zone: ET.Element, depth: int, sibling_index: int) -> None:
            nonlocal next_zone_id
            zone_id = zone.attrib.get("id")
            if not isinstance(zone_id, str) or not zone_id.strip():
                zone.attrib["id"] = str(next_zone_id)
                next_zone_id += 1

            if depth == 0:
                defaults = {"x": "0", "y": "0", "w": "100000", "h": "100000"}
            elif depth == 1:
                defaults = {
                    "x": str((sibling_index % 2) * 500),
                    "y": str((sibling_index // 2) * 300),
                    "w": "500",
                    "h": "300",
                }
            else:
                defaults = {"x": "0", "y": "0", "w": "500", "h": "300"}

            for attr, default_value in defaults.items():
                if not str(zone.attrib.get(attr) or "").strip():
                    zone.attrib[attr] = default_value

            child_zones = [child for child in list(zone) if _local_name(child.tag) == "zone"]
            for child_index, child_zone in enumerate(child_zones):
                repair_zone(child_zone, depth + 1, child_index)

        for root_index, root_zone in enumerate(zone_nodes):
            if root_index == 0:
                root_zone.attrib.setdefault("type-v2", "layout-basic")
                repair_zone(root_zone, 0, root_index)
            else:
                repair_zone(root_zone, 1, root_index - 1)

    return ET.tostring(root, encoding="unicode")


def _next_zone_id(root: ET.Element) -> int:
    zone_ids = []
    for zone in root.iter():
        if _local_name(zone.tag) != "zone":
            continue
        try:
            zone_ids.append(int(str(zone.attrib.get("id") or "").strip()))
        except ValueError:
            continue
    return (max(zone_ids) + 1) if zone_ids else 1


def _find_direct_child(parent: ET.Element, tag_name: str) -> ET.Element | None:
    for child in list(parent):
        if _local_name(child.tag) == tag_name:
            return child
    return None


def _find_parseable_workbook_fragment(text: str) -> str:
    best = ""
    search_from = 0
    while True:
        start = text.find("<workbook", search_from)
        if start < 0:
            break
        end_search_from = start
        while True:
            end = text.find("</workbook>", end_search_from)
            if end < 0:
                break
            candidate = text[start : end + len("</workbook>")].strip()
            if _is_workbook_xml(candidate) and len(candidate) > len(best):
                best = candidate
            end_search_from = end + len("</workbook>")
        search_from = start + len("<workbook")
    return best


def _is_workbook_xml(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    return _local_name(root.tag) == "workbook"


def _strip_code_fences(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _local_name(tag: Any) -> str:
    text = str(tag or "")
    return text.split("}", 1)[-1] if "}" in text else text


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_strings(values: list[str]) -> list[str]:
    output = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output
