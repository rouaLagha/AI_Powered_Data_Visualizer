from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .mapping_agent import QlikToTableauMappingAgent
from .qlik_client import (
    QlikEngineApiClient,
    QlikEngineClientConfig,
    create_qlik_engine_client,
    extract_qlik_metadata,
    import_qvf_to_qlik,
)
from src.rdl_to_twb.llm_client import LLMClient, LLMConfig
from .twb_writer import write_minimal_twb


JsonDict = dict[str, Any]


def run_qlik_to_twb(
    qvf_path: str | Path,
    output_dir: str | Path,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
    config_path: str | Path | None = None,
    mapping_agent: QlikToTableauMappingAgent | None = None,
    llm_client: LLMClient | None = None,
) -> JsonDict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_steps: list[str] = []

    app_id = import_qvf_to_qlik(qvf_path, apps_dir=qlik_apps_dir or None)
    trace_steps.append("QVF imported/openable through Qlik Sense Desktop Apps folder")

    config = QlikEngineClientConfig(
        endpoint_url=qlik_endpoint or "ws://localhost:4848/app",
        apps_dir=qlik_apps_dir,
        user_directory=qlik_user_directory,
        user_id=qlik_user_id,
        session_cookie=qlik_session_cookie,
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

    intermediate_model = normalize_qlik_metadata(qlik_metadata)
    intermediate_path = output_dir / "intermediate_model.json"
    _write_json(intermediate_path, intermediate_model)
    trace_steps.append("Intermediate model written")

    if mapping_agent is None:
        mapping_llm = llm_client or _load_mapping_llm(config_path)
        agent = QlikToTableauMappingAgent(llm_client=mapping_llm, model_name=mapping_llm.config.model)
    else:
        agent = mapping_agent
    tableau_mapping = agent.map_model(intermediate_model)
    mapping_path = output_dir / "tableau_mapping.json"
    _write_json(mapping_path, tableau_mapping)
    trace_steps.append("Qlik to Tableau LLM mapping completed")

    twb_path = output_dir / "generated_report.twb"
    write_minimal_twb(intermediate_model=intermediate_model, mapping=tableau_mapping, output_path=twb_path)
    trace_steps.append("Tableau workbook generated from QIX metadata and LLM mapping")

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
        "intermediate_model": str(intermediate_path),
        "tableau_mapping": str(mapping_path),
        "twb": str(twb_path),
        "pipeline_trace": str(trace_path),
        "summary": {
            "sheet_count": len(intermediate_model.get("sheets", [])),
            "visual_count": sum(len(sheet.get("visuals", [])) for sheet in intermediate_model.get("sheets", [])),
            "master_dimension_count": len(intermediate_model.get("master_dimensions", [])),
            "master_measure_count": len(intermediate_model.get("master_measures", [])),
            "variable_count": len(intermediate_model.get("variables", [])),
            "mapping_agent": tableau_mapping.get("agent", {}),
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

    for dimension in _as_list(qlik_metadata.get("master_dimensions")):
        if not isinstance(dimension, dict):
            continue
        _register_field(fields, dimension.get("field"), role="dimension", data_type="string")

    for measure in _as_list(qlik_metadata.get("master_measures")):
        if not isinstance(measure, dict):
            continue
        for field_name in _extract_qlik_expression_fields(measure.get("expression")):
            _register_field(fields, field_name, role="measure", data_type="real")

    return {
        "source": dict(qlik_metadata.get("source") or {}),
        "tables": _extract_tables_from_load_script(str(qlik_metadata.get("load_script") or "")),
        "fields": sorted(fields.values(), key=lambda item: item["name"].lower()),
        "sheets": sheets,
        "master_dimensions": _as_list(qlik_metadata.get("master_dimensions")),
        "master_measures": _as_list(qlik_metadata.get("master_measures")),
        "variables": _as_list(qlik_metadata.get("variables")),
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
        dimensions.append(
            {
                "label": dimension.get("label") or field_name,
                "field": field_name,
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
        "dimensions": dimensions,
        "measures": measures,
    }


def _extract_tables_from_load_script(load_script: str) -> list[JsonDict]:
    tables = []
    for match in re.finditer(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$", load_script or ""):
        table_name = match.group(1)
        if table_name.lower().startswith("set"):
            continue
        tables.append({"name": table_name, "source": "load_script"})
    return tables


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
    parser.add_argument("--qlik-user-directory", default="", help="Optional X-Qlik-User directory")
    parser.add_argument("--qlik-user-id", default="", help="Optional X-Qlik-User id")
    parser.add_argument("--qlik-session-cookie", default="", help="Optional Qlik session cookie for secured endpoints")
    parser.add_argument("--config-path", required=True, help="LLM config JSON with qlik_mapping_agent or agent1")
    args = parser.parse_args()

    result = run_qlik_to_twb(
        qvf_path=args.qvf,
        output_dir=args.output_dir,
        qlik_endpoint=args.qlik_endpoint,
        qlik_apps_dir=args.qlik_apps_dir,
        qlik_user_directory=args.qlik_user_directory,
        qlik_user_id=args.qlik_user_id,
        qlik_session_cookie=args.qlik_session_cookie,
        config_path=args.config_path,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
