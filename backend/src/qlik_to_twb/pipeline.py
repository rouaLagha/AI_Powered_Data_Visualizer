from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .mapping_agent import QlikToTableauMappingAgent, build_tableau_mapping_contract
from .qlik_client import (
    QlikEngineApiClient,
    QlikEngineClientConfig,
    create_qlik_engine_client,
    extract_qlik_metadata,
    import_qvf_to_qlik,
)
from src.rdl_to_twb.llm_client import LLMClient, LLMConfig
from .twb_agent import generate_validated_twb


JsonDict = dict[str, Any]


def run_qlik_to_twb(
    qvf_path: str | Path,
    output_dir: str | Path,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    dataprep_cache_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
    request_timeout_seconds: float = 30.0,
    config_path: str | Path | None = None,
    mapping_agent: QlikToTableauMappingAgent | None = None,
    llm_client: LLMClient | None = None,
    twb_llm_client: LLMClient | None = None,
) -> JsonDict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_steps: list[str] = []

    app_id = import_qvf_to_qlik(qvf_path, apps_dir=qlik_apps_dir or None)
    trace_steps.append("QVF imported/openable through Qlik Sense Desktop Apps folder")

    config = QlikEngineClientConfig(
        endpoint_url=qlik_endpoint or "ws://localhost:4848/app",
        apps_dir=qlik_apps_dir,
        dataprep_cache_dir=dataprep_cache_dir,
        user_directory=qlik_user_directory,
        user_id=qlik_user_id,
        session_cookie=qlik_session_cookie,
        request_timeout_seconds=request_timeout_seconds,
    )
    client = qlik_client or create_qlik_engine_client(config=config)
    _require_real_qix_client(client)
    trace_steps.append(f"Qlik Engine API client created ({client.client_name})")

    qlik_metadata = extract_qlik_metadata(client=client, app_id=app_id, qvf_path=qvf_path)
    _require_real_qlik_metadata(qlik_metadata)
    trace_steps.append("QIX metadata extracted")

    metadata_path = output_dir / "qlik_metadata.json"
    _write_json(metadata_path, qlik_metadata)
    trace_steps.append("Qlik metadata written")

    visual_metadata_path = output_dir / "visual_metadata.json"
    _write_json(visual_metadata_path, {"visual_objects": _as_list(qlik_metadata.get("visual_objects"))})
    trace_steps.append("Visual metadata written")

    connection_metadata_path = output_dir / "connection_metadata.json"
    _write_json(
        connection_metadata_path,
        {
            "connections": _as_list(qlik_metadata.get("connections")),
            "warnings": _as_list(qlik_metadata.get("connection_warnings")),
        },
    )
    trace_steps.append("Connection metadata written")

    dataprep_cache_metadata_path = output_dir / "dataprep_cache_metadata.json"
    _write_json(dataprep_cache_metadata_path, dict(qlik_metadata.get("dataprep_cache") or {}))
    trace_steps.append("DataPrep QVD cache metadata written")

    intermediate_model = normalize_qlik_metadata(qlik_metadata)
    intermediate_path = output_dir / "intermediate_model.json"
    _write_json(intermediate_path, intermediate_model)
    trace_steps.append("Intermediate model written")

    if mapping_agent is None:
        mapping_llm = llm_client or _load_mapping_llm(config_path)
        agent = QlikToTableauMappingAgent(llm_client=mapping_llm, model_name=mapping_llm.config.model)
    else:
        agent = mapping_agent
    tableau_mapping = build_tableau_mapping_contract(
        intermediate_model=intermediate_model,
        proposed_mapping=agent.map_model(intermediate_model),
    )
    mapping_path = output_dir / "tableau_mapping.json"
    _write_json(mapping_path, tableau_mapping)
    trace_steps.append("Complete Qlik to Tableau mapping contract written")

    twb_path = output_dir / "generated_report.twb"
    twb_agent_llm = twb_llm_client or (_load_twb_llm(config_path) if config_path is not None else None)
    twb_validation_report = generate_validated_twb(
        intermediate_model=intermediate_model,
        mapping=tableau_mapping,
        output_path=twb_path,
        llm=twb_agent_llm,
    )
    twb_validation_report_path = output_dir / "twb_validation_report.json"
    _write_json(twb_validation_report_path, twb_validation_report)
    trace_steps.append(
        "Tableau workbook draft generated and deterministically validated "
        f"({twb_validation_report.get('draft_source', 'unknown')})"
    )

    trace_path = output_dir / "pipeline_trace.json"
    _write_json(
        trace_path,
        {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "steps": trace_steps,
        },
    )

    return {
        "status": "completed",
        "qvf_path": str(qvf_path),
        "app_id": app_id,
        "client": client.client_name,
        "extraction_mode": client.extraction_mode,
        "qlik_metadata": str(metadata_path),
        "visual_metadata_path": str(visual_metadata_path),
        "connection_metadata_path": str(connection_metadata_path),
        "dataprep_cache_metadata_path": str(dataprep_cache_metadata_path),
        "visual_metadata": _as_list(qlik_metadata.get("visual_objects")),
        "connection_metadata": _as_list(qlik_metadata.get("connections")),
        "connection_warnings": _as_list(qlik_metadata.get("connection_warnings")),
        "dataprep_cache_metadata": dict(qlik_metadata.get("dataprep_cache") or {}),
        "intermediate_model": str(intermediate_path),
        "tableau_mapping": str(mapping_path),
        "twb": str(twb_path),
        "twb_draft_xml": str(twb_validation_report.get("draft_xml_path") or ""),
        "twb_validated_xml": str(twb_validation_report.get("validated_xml_path") or ""),
        "twb_validation_report": str(twb_validation_report_path),
        "pipeline_trace": str(trace_path),
        "summary": {
            "sheet_count": len(intermediate_model.get("sheets", [])),
            "visual_count": sum(len(sheet.get("visuals", [])) for sheet in intermediate_model.get("sheets", [])),
            "master_dimension_count": len(intermediate_model.get("master_dimensions", [])),
            "master_measure_count": len(intermediate_model.get("master_measures", [])),
            "variable_count": len(intermediate_model.get("variables", [])),
            "connection_count": len(intermediate_model.get("connections", [])),
            "dataprep_qvd_table_count": len(_as_list((qlik_metadata.get("dataprep_cache") or {}).get("qvd_tables"))),
            "mapping_agent": tableau_mapping.get("agent", {}),
            "twb_generation": {
                "draft_source": twb_validation_report.get("draft_source", ""),
                "validation_status": twb_validation_report.get("status", ""),
                "deterministic_seed_used": bool(twb_validation_report.get("deterministic_seed_used")),
                "issue_count": len(_as_list(twb_validation_report.get("issues"))),
            },
        },
        "trace_steps": trace_steps,
    }


def _load_mapping_llm(config_path: str | Path | None) -> LLMClient:
    if config_path is None or not str(config_path).strip():
        raise ValueError("config_path is required for Qlik to Tableau LLM mapping.")

    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    agent_cfg = cfg.get("qlik_mapping_agent") or cfg.get("agent1")
    if not isinstance(agent_cfg, dict):
        raise ValueError("LLM config must include 'qlik_mapping_agent' or 'agent1'.")
    return LLMClient(LLMConfig(**agent_cfg))


def _load_twb_llm(config_path: str | Path | None) -> LLMClient:
    if config_path is None or not str(config_path).strip():
        raise ValueError("config_path is required for Qlik TWB XML generation.")

    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    agent_cfg = (
        cfg.get("qlik_twb_agent")
        or cfg.get("tableau_xml_agent")
        or cfg.get("codex")
        or cfg.get("agent2")
        or cfg.get("qlik_mapping_agent")
        or cfg.get("agent1")
    )
    if not isinstance(agent_cfg, dict):
        raise ValueError("LLM config must include 'qlik_twb_agent', 'agent2', or 'agent1'.")
    return LLMClient(LLMConfig(**agent_cfg))


def _require_real_qix_client(client: QlikEngineApiClient) -> None:
    extraction_mode = str(getattr(client, "extraction_mode", "") or "").strip().lower()
    client_name = str(getattr(client, "client_name", "") or "").strip()
    if extraction_mode != "qix":
        raise ValueError(
            "Qlik conversion requires a real QIX client. "
            f"Received client={client_name or '<unknown>'}, extraction_mode={extraction_mode or '<empty>'}."
        )


def _require_real_qlik_metadata(qlik_metadata: JsonDict) -> None:
    source = qlik_metadata.get("source") if isinstance(qlik_metadata, dict) else {}
    extraction_mode = str((source or {}).get("extraction_mode") or "").strip().lower()
    if extraction_mode != "qix":
        raise ValueError("Qlik metadata was not extracted through real QIX.")

    has_load_script = bool(str(qlik_metadata.get("load_script") or "").strip())
    has_sheets = bool(_as_list(qlik_metadata.get("sheets")))
    has_visuals = bool(_as_list(qlik_metadata.get("visual_objects")))
    if not (has_load_script or has_sheets or has_visuals):
        raise ValueError("QIX extraction returned no load script, sheets, or visual objects.")


def normalize_qlik_metadata(qlik_metadata: JsonDict) -> JsonDict:
    visual_objects = _as_list(qlik_metadata.get("visual_objects"))
    visual_by_sheet: dict[str, list[JsonDict]] = {}
    fields: dict[str, JsonDict] = {}

    for visual in visual_objects:
        if not isinstance(visual, dict):
            continue
        sheet_id = str(visual.get("sheet_id") or "")
        visual_by_sheet.setdefault(sheet_id, []).append(_normalize_visual(visual, fields))

    sheets = []
    for sheet in _as_list(qlik_metadata.get("sheets")):
        if not isinstance(sheet, dict):
            continue
        sheet_id = str(sheet.get("id") or "")
        sheets.append(
            {
                "id": sheet_id,
                "title": sheet.get("title") or sheet_id or "Sheet",
                "rank": sheet.get("rank", 0),
                "visuals": visual_by_sheet.get(sheet_id, []),
            }
        )

    known_sheet_ids = {str(sheet.get("id") or "") for sheet in sheets if isinstance(sheet, dict)}
    unassigned_visuals = [
        visual
        for sheet_id, visuals in visual_by_sheet.items()
        if sheet_id not in known_sheet_ids
        for visual in visuals
    ]
    if unassigned_visuals:
        sheets.append(
            {
                "id": "__unassigned__",
                "title": "Unassigned visuals",
                "rank": len(sheets),
                "visuals": unassigned_visuals,
            }
        )

    for dimension in _as_list(qlik_metadata.get("master_dimensions")):
        if not isinstance(dimension, dict):
            continue
        _register_field(fields, dimension.get("field"), role="dimension", data_type="string")

    for measure in _as_list(qlik_metadata.get("master_measures")):
        if not isinstance(measure, dict):
            continue
        for field_name in _extract_qlik_expression_fields(measure.get("expression")):
            _register_field(fields, field_name, role="measure", data_type="real")

    tables = _merge_table_metadata(
        _extract_tables_from_load_script(str(qlik_metadata.get("load_script") or "")),
        _extract_tables_from_dataprep_cache(dict(qlik_metadata.get("dataprep_cache") or {})),
    )

    return {
        "source": dict(qlik_metadata.get("source") or {}),
        "tables": tables,
        "fields": sorted(fields.values(), key=lambda item: item["name"].lower()),
        "sheets": sheets,
        "master_dimensions": _as_list(qlik_metadata.get("master_dimensions")),
        "master_measures": _as_list(qlik_metadata.get("master_measures")),
        "variables": _as_list(qlik_metadata.get("variables")),
        "dataprep_cache_metadata": dict(qlik_metadata.get("dataprep_cache") or {}),
        "connection_metadata": _as_list(qlik_metadata.get("connections")),
        "connections": _as_list(qlik_metadata.get("connections")),
        "load_script": qlik_metadata.get("load_script") or "",
    }


def _normalize_visual(visual: JsonDict, fields: dict[str, JsonDict]) -> JsonDict:
    dimensions = []
    for dimension in _as_list(visual.get("dimensions")):
        if not isinstance(dimension, dict):
            continue
        field_name = str(dimension.get("field") or "").strip()
        if field_name:
            _register_field(fields, field_name, role="dimension", data_type=_infer_data_type(field_name))
        for related_field in _as_list(dimension.get("fields")):
            _register_field(fields, related_field, role="dimension", data_type=_infer_data_type(str(related_field)))
        dimensions.append(
            {
                "label": dimension.get("label") or field_name,
                "field": field_name,
                "fields": _as_list(dimension.get("fields")),
                "expression": dimension.get("expression") or "",
                "source": dimension.get("source") or "",
            }
        )

    measures = []
    for measure in _as_list(visual.get("measures")):
        if not isinstance(measure, dict):
            continue
        expression = str(measure.get("expression") or "").strip()
        for field_name in _extract_qlik_expression_fields(expression):
            _register_field(fields, field_name, role="measure", data_type="real")
        measures.append(
            {
                "label": measure.get("label") or expression,
                "expression": expression,
            }
        )

    return {
        "id": visual.get("id", ""),
        "title": visual.get("title") or visual.get("id") or "Visual",
        "type": visual.get("type", ""),
        "source_type": visual.get("source_type") or visual.get("type", ""),
        "qix": dict(visual.get("qix") or {}),
        "data_cache_matches": _as_list(visual.get("data_cache_matches")),
        "best_data_cache_match": dict(visual.get("best_data_cache_match") or {}),
        "dimensions": dimensions,
        "measures": measures,
    }


def _extract_tables_from_load_script(load_script: str) -> list[JsonDict]:
    tables: list[JsonDict] = []
    current: JsonDict | None = None

    for raw_line in str(load_script or "").splitlines():
        line = raw_line.strip()
        table_match = re.match(r"^(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_.]*))\s*:\s*$", line)
        if table_match:
            table_name = str(table_match.group(1) or table_match.group(2) or "").strip()
            if table_name and not table_name.lower().startswith(("set", "__")):
                current = {
                    "name": table_name,
                    "table_name": table_name,
                    "source": "load_script",
                    "fields": [],
                    "field_names": [],
                }
                tables.append(current)
            else:
                current = None
            continue

        if current is None:
            continue

        source_match = re.search(r"\bFROM\s+([A-Za-z0-9_.$\[\]-]+)", line, flags=re.IGNORECASE)
        if source_match and not current.get("source_table"):
            current["source_table"] = source_match.group(1).strip("[]")

        for field_name in _extract_load_script_output_fields(line):
            field = {"name": field_name, "data_type": _infer_data_type(field_name)}
            if field_name not in current["field_names"]:
                current["field_names"].append(field_name)
                current["fields"].append(field)

    return tables


def _extract_tables_from_dataprep_cache(dataprep_cache: JsonDict) -> list[JsonDict]:
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
            if not field_name:
                continue
            fields.append(
                {
                    "name": field_name,
                    "data_type": field.get("data_type") or _infer_data_type(field_name),
                    "no_of_symbols": field.get("no_of_symbols"),
                }
            )
        if not fields:
            fields = [
                {"name": str(field_name), "data_type": _infer_data_type(str(field_name))}
                for field_name in _as_list(table.get("field_names"))
                if str(field_name or "").strip()
            ]
        tables.append(
            {
                "name": table_name,
                "table_name": table_name,
                "source": "dataprep_qvd",
                "file_name": table.get("file_name", ""),
                "path": table.get("path", ""),
                "record_count": table.get("record_count", 0),
                "fields": _dedupe_fields(fields),
                "field_names": [field["name"] for field in _dedupe_fields(fields)],
            }
        )
    return tables


def _merge_table_metadata(*table_groups: list[JsonDict]) -> list[JsonDict]:
    merged: dict[str, JsonDict] = {}
    for table in [item for group in table_groups for item in group]:
        table_name = str(table.get("table_name") or table.get("name") or "").strip()
        if not table_name:
            continue
        key = table_name.lower()
        existing = merged.setdefault(
            key,
            {
                "name": table_name,
                "table_name": table_name,
                "source": table.get("source") or "",
                "fields": [],
                "field_names": [],
            },
        )
        for attr in ("source", "source_table", "file_name", "path", "record_count"):
            if table.get(attr) not in (None, ""):
                existing[attr] = table.get(attr)
        for field in _as_list(table.get("fields")):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("name") or "").strip()
            if not field_name or field_name in existing["field_names"]:
                continue
            existing["field_names"].append(field_name)
            existing["fields"].append(field)
        for field_name in _as_list(table.get("field_names")):
            text = str(field_name or "").strip()
            if text and text not in existing["field_names"]:
                existing["field_names"].append(text)
                existing["fields"].append({"name": text, "data_type": _infer_data_type(text)})
    return sorted(merged.values(), key=lambda item: str(item.get("table_name") or "").lower())


def _extract_load_script_output_fields(line: str) -> list[str]:
    cleaned = str(line or "").strip().rstrip(",;")
    if not cleaned or cleaned.startswith(("//", "///")):
        return []
    if re.match(r"^(LOAD|SELECT|FROM|WHERE|RESIDENT|DROP|TAG|SET|LET|FOR|NEXT|IF|ENDIF|DO|LOOP)\b", cleaned, flags=re.IGNORECASE):
        return []

    alias_match = re.search(r"\bAS\s+(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_.]*))", cleaned, flags=re.IGNORECASE)
    if alias_match:
        return [alias_match.group(1) or alias_match.group(2)]

    bracket_fields = re.findall(r"\[([^\]]+)\]", cleaned)
    if bracket_fields:
        return [bracket_fields[0]]

    bare_match = re.match(r"([A-Za-z_][A-Za-z0-9_.]*)", cleaned)
    if bare_match and "(" not in cleaned:
        return [bare_match.group(1)]
    return []


def _dedupe_fields(fields: list[JsonDict]) -> list[JsonDict]:
    output = []
    seen = set()
    for field in fields:
        field_name = str(field.get("name") or "").strip()
        key = field_name.lower()
        if not field_name or key in seen:
            continue
        seen.add(key)
        output.append(field)
    return output


def _extract_qlik_expression_fields(expression: Any) -> list[str]:
    text = str(expression or "")
    fields = []
    for match in re.finditer(r"\b(?:sum|avg|average|count)\s*\(\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))\s*\)", text, flags=re.IGNORECASE):
        field_name = (match.group(1) or match.group(2) or "").strip()
        if field_name and field_name not in fields:
            fields.append(field_name)
    return fields


def _register_field(
    fields: dict[str, JsonDict],
    name: Any,
    role: str,
    data_type: str,
) -> None:
    field_name = str(name or "").strip()
    if not field_name:
        return
    existing = fields.get(field_name)
    if existing:
        if role == "measure":
            existing["role"] = "measure"
            existing["data_type"] = data_type
        return
    fields[field_name] = {
        "name": field_name,
        "role": role,
        "data_type": data_type,
    }


def _infer_data_type(field_name: str) -> str:
    normalized = field_name.lower()
    if normalized == "datekey" or (normalized.endswith("key") and "date" not in normalized):
        return "integer"
    if "date" in normalized:
        return "date"
    if normalized.endswith("year") or "number" in normalized:
        return "integer"
    return "string"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_json(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a Qlik Sense QVF app to a Tableau TWB through real QIX and LLM mapping.")
    parser.add_argument("--qvf", required=True, help="Path to the source .qvf app")
    parser.add_argument("--output-dir", default="output", help="Output directory for generated artifacts")
    parser.add_argument("--qlik-endpoint", default="ws://localhost:4848/app", help="QIX WebSocket endpoint")
    parser.add_argument("--qlik-apps-dir", default="", help="Qlik Sense Desktop Apps directory")
    parser.add_argument("--dataprep-cache-dir", default="", help="Optional Qlik DataPrepAppCache directory")
    parser.add_argument("--qlik-user-directory", default="", help="Optional X-Qlik-User directory")
    parser.add_argument("--qlik-user-id", default="", help="Optional X-Qlik-User id")
    parser.add_argument("--qlik-session-cookie", default="", help="Optional Qlik session cookie for secured endpoints")
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0, help="QIX request timeout in seconds")
    parser.add_argument("--config-path", required=True, help="LLM config JSON with qlik_mapping_agent or agent1")
    args = parser.parse_args()

    result = run_qlik_to_twb(
        qvf_path=args.qvf,
        output_dir=args.output_dir,
        qlik_endpoint=args.qlik_endpoint,
        qlik_apps_dir=args.qlik_apps_dir,
        dataprep_cache_dir=args.dataprep_cache_dir,
        qlik_user_directory=args.qlik_user_directory,
        qlik_user_id=args.qlik_user_id,
        qlik_session_cookie=args.qlik_session_cookie,
        request_timeout_seconds=args.request_timeout_seconds,
        config_path=args.config_path,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
