from __future__ import annotations

import argparse
import base64
import binascii
import copy
from concurrent.futures import Future
from datetime import datetime, timezone
import json
import mimetypes
import os
import re
import shutil
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
for import_root in [BACKEND_DIR, PROJECT_ROOT]:
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from .api.routes import dispatch_post
except ImportError:
    from api.routes import dispatch_post

from src.rdl_to_twb.rdl_parser import parse_rdl_file
from src.rdl_to_twb.db_introspection import (
    inspect_sqlserver_datasource_inventory,
    inspect_tableau_workbook_inventory,
)
from src.rdl_ai_editor import apply_patch_to_rdl, load_llm_from_config, nlp_agent, validate_patch
from src.rdl_ai_editor.logging_utils import write_patch_log
from src.rdl_to_twb.pipeline import run_conversion as run_full_conversion
from src.qlik_to_twb.metadata_pipeline import run_qlik_metadata_job, run_uploaded_qlik_metadata_job
from sql_model_assistant_app import (
    DEFAULT_LLM_CONFIG,
    FALLBACK_LLM_CONFIG,
    OUTPUT_TEMPLATE_COPY_PATH,
    OUTPUT_TEMPLATE_PATH,
    SQL_ASSISTANT_LLM_CONFIG,
    _build_tableau_publish_context,
    _find_dataset_by_name,
    _find_datasource_for_dataset,
    _generate_response,
    _generate_twb_from_validated_model,
    _load_tableau_publish_defaults,
    _load_template_xml,
    _pick_default_dataset_name,
    _run_tableau_cloud_publish_workflow,
    _safe_output_twb_name,
    _seed_initial_sql_model_conversation,
    _tableau_clone_linked_workbook_with_visual_content,
    _tableau_format_user_publish_failure,
    _template_default_path,
)


FRONTEND_DIR = PROJECT_ROOT / "frontend" / "sql_model_react"
DIST_DIR = FRONTEND_DIR / "dist"
INDEX_PATH = FRONTEND_DIR / "index.html"
SCHEMA_FLOW_FRONTEND_DIR = BACKEND_DIR / "src" / "schema_flow_component" / "frontend"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
RDL_TO_TWB_OUTPUT_DIR = OUTPUT_DIR / "rdl_to_twb"
REFERENCE_DATA_MODEL_TEMPLATE_PATH = (
    OUTPUT_DIR / "tableau_publish_artifacts" / "20260429T155801Z_generated_source_twb_validated_semantic_model.twb"
)
DATA_MODEL_TEMPLATE_ENV = "RDL_TO_TWB_DATA_MODEL_TEMPLATE_TWB"
REGIONALSALES_VISUAL_TEMPLATE_ENV = "REGIONALSALES_VISUAL_TWB"
REGIONALSALES_VISUAL_TEMPLATE_NAME = "converted_report_perfect.twb"
INPUT_DIR_NAME = "00_input_report"
DATA_VERIFICATION_DIR_NAME = "01_data_verification"
SEMANTIC_MODEL_DIR_NAME = "02_semantic_model"
CONVERSION_DIR_NAME = "03_conversion_and_visual_mapping"
DATA_MODEL_DIR_NAME = "04_data_model_to_publish"
FINAL_WORKBOOK_DIR_NAME = "05_final_workbook_with_visuals"
PUBLISH_DIR_NAME = "06_tableau_publish"
QUALITY_DIR_NAME = "07_quality_verification"
VISUAL_MAPPING_TWB_NAME = "visual_content_mapped.twb"
DATA_MODEL_TWB_NAME = "data_model_to_publish.twb"
FINAL_WORKBOOK_TWB_NAME = "data_model_with_mapped_visuals.twb"
QLIK_OUTPUT_DIR = PROJECT_ROOT / "output"
QLIK_JOBS_DIR = QLIK_OUTPUT_DIR / "qlik_jobs"
STATE_LOCK = Lock()
RDL_XSD_PATH = BACKEND_DIR / "assets" / "ReportDefinition.xsd"
TWB_XSD_PATH = BACKEND_DIR / "assets" / "twb_2026.1.0.xsd"


class SqlModelThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def _initial_app_state() -> dict[str, Any]:
    return {
        "report": {},
        "report_name": "",
        "rdl_content": "",
        "selected_dataset_name": "",
        "selected_datasource_name": "",
        "sql_query": "",
        "conversation": [],
        "latest_model": {},
        "validated_model": {},
        "artifact_dir": "",
        "artifact_manifest_path": "",
        "data_model_template_path": "",
        "generated_twb_path": "",
        "generated_twb_name": DATA_MODEL_TWB_NAME,
        "visual_conversion_future": None,
        "visual_conversion_path": "",
        "visual_conversion_output_dir": "",
        "visual_conversion_error": "",
        "visual_model_twb_path": "",
        "visual_model_twb_error": "",
        "visual_model_source_path": "",
        "publish_context": {},
        "publish_report": {},
        "publish_error": "",
        "consumer_workbook_path": "",
        "quality_comparison": {},
        "database_inventory_cache": {},
    }


APP_STATE: dict[str, Any] = _initial_app_state()


def _preferred_config_path() -> Path:
    for candidate in [SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG]:
        if candidate.exists():
            return candidate
    return DEFAULT_LLM_CONFIG


def _frontend_static_dir() -> Path:
    if (DIST_DIR / "index.html").exists():
        return DIST_DIR
    return FRONTEND_DIR


def _frontend_index_path() -> Path:
    return _frontend_static_dir() / "index.html"


def _reset_app_state() -> dict[str, Any]:
    _clear_visual_conversion_state(cancel_running=True)
    APP_STATE.clear()
    APP_STATE.update(_initial_app_state())
    return _state_snapshot()


def _decode_request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0") or 0)
    if content_length <= 0:
        return {}
    raw = handler.rfile.read(content_length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(raw)
    handler.close_connection = True


def _send_error_json(handler: BaseHTTPRequestHandler, exc: Exception, status: int = 500) -> None:
    _send_json(
        handler,
        {
            "ok": False,
            "error": str(exc),
        },
        status=status,
    )


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _is_safe_download_path(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        root = PROJECT_ROOT.resolve(strict=True)
    except OSError:
        return False
    return str(resolved).lower().startswith(str(root).lower())


def _resolve_workspace_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if not str(resolved).lower().startswith(str(root).lower()):
        raise ValueError(f"Path must stay inside the project workspace: {path_value}")
    return resolved


def _file_payload(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return {"path": str(path), "exists": False}
    if not resolved.is_file() or not _is_safe_download_path(resolved):
        return {"path": str(resolved), "exists": False}
    return {
        "path": str(resolved),
        "name": resolved.name,
        "exists": True,
        "size_bytes": resolved.stat().st_size,
        "download_url": f"/api/file?path={quote(resolved.as_posix())}",
    }


def _safe_file_stem(value: str, fallback: str = "report") -> str:
    stem = Path(value or fallback).stem
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    return cleaned or fallback


def _safe_artifact_file_name(value: str, fallback: str) -> str:
    raw_name = Path(value or fallback).name or fallback
    suffix = Path(raw_name).suffix
    stem = _safe_file_stem(raw_name, fallback=_safe_file_stem(fallback))
    safe_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix or "") else Path(fallback).suffix
    return f"{stem}{safe_suffix or Path(fallback).suffix}"


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _directory_payload(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return {"path": str(path), "exists": False}
    if not resolved.is_dir() or not _is_safe_download_path(resolved):
        return {"path": str(resolved), "exists": False}
    return {
        "path": str(resolved),
        "name": resolved.name,
        "exists": True,
    }


def _new_artifact_run_dir(file_name: str) -> Path:
    report_stem = _safe_file_stem(file_name, fallback="uploaded_report")
    base_dir = RDL_TO_TWB_OUTPUT_DIR / f"{_timestamp_token()}_{report_stem}"
    run_dir = base_dir
    counter = 2
    while run_dir.exists():
        run_dir = RDL_TO_TWB_OUTPUT_DIR / f"{base_dir.name}_{counter}"
        counter += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _artifact_run_dir(create: bool = False) -> Path | None:
    raw_value = str(APP_STATE.get("artifact_dir") or "").strip()
    if raw_value:
        path = Path(raw_value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    elif create:
        path = _new_artifact_run_dir(str(APP_STATE.get("report_name") or "uploaded_report.rdl"))
        APP_STATE["artifact_dir"] = str(path)
    else:
        return None

    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _artifact_subdir(name: str, create: bool = True) -> Path:
    run_dir = _artifact_run_dir(create=create)
    if run_dir is None:
        raise ValueError("Artifact workspace is not initialized. Upload the RDL report first.")
    directory = run_dir / name
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _artifact_manifest_path(create: bool = False) -> Path | None:
    run_dir = _artifact_run_dir(create=create)
    if run_dir is None:
        return None
    return run_dir / "artifact_manifest.json"


def _artifact_workspace_payload() -> dict[str, Any]:
    run_dir = _artifact_run_dir(create=False)
    if run_dir is None:
        return {}
    return {
        "root": _directory_payload(str(run_dir)),
        "manifest": _file_payload(str(_artifact_manifest_path(create=False) or "")),
        "folders": {
            "input_report": _directory_payload(str(run_dir / INPUT_DIR_NAME)),
            "data_verification": _directory_payload(str(run_dir / DATA_VERIFICATION_DIR_NAME)),
            "semantic_model": _directory_payload(str(run_dir / SEMANTIC_MODEL_DIR_NAME)),
            "conversion_and_visual_mapping": _directory_payload(str(run_dir / CONVERSION_DIR_NAME)),
            "data_model_to_publish": _directory_payload(str(run_dir / DATA_MODEL_DIR_NAME)),
            "final_workbook_with_visuals": _directory_payload(str(run_dir / FINAL_WORKBOOK_DIR_NAME)),
            "tableau_publish": _directory_payload(str(run_dir / PUBLISH_DIR_NAME)),
            "quality_verification": _directory_payload(str(run_dir / QUALITY_DIR_NAME)),
        },
    }


def _artifact_file_payload(folder_name: str, file_name: str) -> dict[str, Any]:
    run_dir = _artifact_run_dir(create=False)
    if run_dir is None:
        return {}
    return _file_payload(str(run_dir / folder_name / file_name))


def _write_artifact_manifest(stage: str) -> None:
    manifest_path = _artifact_manifest_path(create=False)
    if manifest_path is None:
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    run_dir = _artifact_run_dir(create=False)
    payload = {
        "stage": stage,
        "updated_at": _timestamp_token(),
        "report_name": str(APP_STATE.get("report_name") or ""),
        "selected_dataset_name": str(APP_STATE.get("selected_dataset_name") or ""),
        "selected_datasource_name": str(APP_STATE.get("selected_datasource_name") or ""),
        "artifact_root": str(run_dir or ""),
        "artifacts": {
            "input_rdl": _artifact_file_payload(
                INPUT_DIR_NAME,
                _safe_artifact_file_name(str(APP_STATE.get("report_name") or "uploaded_report.rdl"), "uploaded_report.rdl"),
            ),
            "parsed_report": _artifact_file_payload(INPUT_DIR_NAME, "parsed_report.json"),
            "selected_dataset_sql": _artifact_file_payload(INPUT_DIR_NAME, "selected_dataset.sql"),
            "data_verification_summary": _artifact_file_payload(DATA_VERIFICATION_DIR_NAME, "data_verification_summary.json"),
            "datasource_inventory": _artifact_file_payload(DATA_VERIFICATION_DIR_NAME, "datasource_inventory.json"),
            "latest_semantic_model": _artifact_file_payload(SEMANTIC_MODEL_DIR_NAME, "latest_semantic_model.json"),
            "validated_semantic_model": _artifact_file_payload(SEMANTIC_MODEL_DIR_NAME, "validated_semantic_model.json"),
            "data_model_template_twb": _file_payload(str(APP_STATE.get("data_model_template_path") or "")),
            "data_model_template_copy": _artifact_file_payload(DATA_MODEL_DIR_NAME, "source_template_used.twb"),
            "data_model_generation_report": _artifact_file_payload(DATA_MODEL_DIR_NAME, "data_model_generation_report.json"),
            "conversion_trace": _artifact_file_payload(CONVERSION_DIR_NAME, "pipeline_trace.json"),
            "conversion_validation_report": _artifact_file_payload(CONVERSION_DIR_NAME, "validation_report.json"),
            "visual_content_mapping_twb": _file_payload(str(APP_STATE.get("visual_conversion_path") or "")),
            "data_model_twb_to_publish": _file_payload(str(APP_STATE.get("generated_twb_path") or "")),
            "final_workbook_visual_source": _file_payload(str(APP_STATE.get("visual_model_source_path") or "")),
            "final_workbook_visual_source_copy": _artifact_file_payload(FINAL_WORKBOOK_DIR_NAME, "visual_source_used.twb"),
            "data_model_with_mapped_visuals_twb": _file_payload(
                str(APP_STATE.get("visual_model_twb_path") or "")
            ),
            "final_workbook_generation_report": _artifact_file_payload(
                FINAL_WORKBOOK_DIR_NAME,
                "final_workbook_generation_report.json",
            ),
            "tableau_publish_report": _artifact_file_payload(PUBLISH_DIR_NAME, "publish_report.json"),
            "consumer_workbook": _consumer_workbook_payload(),
            "quality_comparison": _artifact_file_payload(QUALITY_DIR_NAME, "quality_comparison.json"),
        },
        "layout": {
            "input_report": str((run_dir or Path()) / INPUT_DIR_NAME),
            "data_verification": str((run_dir or Path()) / DATA_VERIFICATION_DIR_NAME),
            "semantic_model": str((run_dir or Path()) / SEMANTIC_MODEL_DIR_NAME),
            "conversion_and_visual_mapping": str((run_dir or Path()) / CONVERSION_DIR_NAME),
            "data_model_to_publish": str((run_dir or Path()) / DATA_MODEL_DIR_NAME),
            "final_workbook_with_visuals": str((run_dir or Path()) / FINAL_WORKBOOK_DIR_NAME),
            "tableau_publish": str((run_dir or Path()) / PUBLISH_DIR_NAME),
            "quality_verification": str((run_dir or Path()) / QUALITY_DIR_NAME),
        },
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    APP_STATE["artifact_manifest_path"] = str(manifest_path)


def _write_json_artifact(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_input_artifacts(file_name: str, content: str, report: dict[str, Any]) -> None:
    input_dir = _artifact_subdir(INPUT_DIR_NAME, create=True)
    input_path = input_dir / _safe_artifact_file_name(file_name, "uploaded_report.rdl")
    input_path.write_text(content, encoding="utf-8")
    _write_json_artifact(input_dir / "parsed_report.json", report if isinstance(report, dict) else {})
    _write_json_artifact(input_dir / "report_summary.json", _summarize_report(report if isinstance(report, dict) else {}))
    selected_sql = str(APP_STATE.get("sql_query") or "").strip()
    if selected_sql:
        (input_dir / "selected_dataset.sql").write_text(selected_sql, encoding="utf-8")


def _write_data_verification_artifacts(stage: str) -> None:
    verification_dir = _artifact_subdir(DATA_VERIFICATION_DIR_NAME, create=True)
    datasource, dataset = _selected_context()
    database_context = _database_context(datasource, dataset)
    inventory = _cached_datasource_inventory(datasource) if datasource else {}
    payload = {
        "stage": stage,
        "updated_at": _timestamp_token(),
        "report_name": str(APP_STATE.get("report_name") or ""),
        "selected_dataset_name": str(APP_STATE.get("selected_dataset_name") or ""),
        "selected_datasource_name": str(APP_STATE.get("selected_datasource_name") or ""),
        "database_context": database_context,
        "selected_dataset": dataset if isinstance(dataset, dict) else {},
        "selected_datasource": datasource if isinstance(datasource, dict) else {},
        "datasource_inventory": inventory if isinstance(inventory, dict) else {},
    }
    _write_json_artifact(verification_dir / "data_verification_summary.json", payload)
    _write_json_artifact(verification_dir / "database_context.json", database_context)
    _write_json_artifact(verification_dir / "datasource_inventory.json", inventory if isinstance(inventory, dict) else {})


def _write_semantic_model_artifacts(stage: str) -> None:
    model_dir = _artifact_subdir(SEMANTIC_MODEL_DIR_NAME, create=True)
    payload = {
        "stage": stage,
        "updated_at": _timestamp_token(),
        "sql_query": str(APP_STATE.get("sql_query") or ""),
        "conversation": APP_STATE.get("conversation", []),
        "latest_model_summary": _model_summary(APP_STATE.get("latest_model", {})),
        "validated_model_summary": _model_summary(APP_STATE.get("validated_model", {})),
    }
    _write_json_artifact(model_dir / "semantic_model_summary.json", payload)
    _write_json_artifact(model_dir / "latest_semantic_model.json", APP_STATE.get("latest_model", {}))
    _write_json_artifact(model_dir / "validated_semantic_model.json", APP_STATE.get("validated_model", {}))


def _copy_artifact_to_dir(source_value: str, target_dir: Path, target_name: str | None = None) -> str:
    if not source_value:
        return ""
    source_path = Path(source_value)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    try:
        source_path = source_path.resolve(strict=True)
    except OSError:
        return ""
    if not source_path.is_file():
        return ""
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / (target_name or source_path.name)
    if source_path != target_path.resolve(strict=False):
        shutil.copy2(source_path, target_path)
    return str(target_path)


def _is_reference_data_model_template(template_path: str) -> bool:
    if not template_path:
        return False
    try:
        resolved = Path(template_path).expanduser()
        if not resolved.is_absolute():
            resolved = PROJECT_ROOT / resolved
        resolved = resolved.resolve(strict=True)
    except OSError:
        resolved = Path(template_path)

    try:
        reference = REFERENCE_DATA_MODEL_TEMPLATE_PATH.resolve(strict=True)
        if resolved == reference:
            return True
    except OSError:
        pass

    return "generated_source_twb_validated_semantic_model" in _safe_file_stem(str(resolved)).lower()


def _write_data_model_generation_report(
    template_path: str,
    output_path: Path,
    template_copy_path: str,
    preserve_template_exposed_columns: bool,
) -> None:
    _write_json_artifact(
        output_path.parent / "data_model_generation_report.json",
        {
            "stage": "data_model_twb_generated",
            "updated_at": _timestamp_token(),
            "structure_policy": (
                "The publish-ready data model TWB is generated from the reference XML structure "
                "of 20260429T155801Z_generated_source_twb_validated_semantic_model.twb, then updated "
                "with the validated semantic model."
            ),
            "reference_template": str(REFERENCE_DATA_MODEL_TEMPLATE_PATH),
            "template_used": str(template_path or ""),
            "template_copy": str(template_copy_path or ""),
            "preserve_template_exposed_columns": bool(preserve_template_exposed_columns),
            "strict_reference_clone": bool(preserve_template_exposed_columns),
            "generated_twb": str(output_path),
            "output_name": output_path.name,
        },
    )


def _write_final_workbook_generation_report(
    data_model_twb_path: Path,
    visual_source_twb_path: Path,
    output_twb_path: Path,
    visual_source_copy_path: str,
    preferred_datasource_name: str,
    regional_sales_visual_override: bool,
) -> None:
    _write_json_artifact(
        output_twb_path.parent / "final_workbook_generation_report.json",
        {
            "stage": "final_workbook_generated",
            "updated_at": _timestamp_token(),
            "data_model_source": str(data_model_twb_path),
            "visual_source": str(visual_source_twb_path),
            "visual_source_copy": str(visual_source_copy_path or ""),
            "regional_sales_visual_override": bool(regional_sales_visual_override),
            "preferred_datasource_name": preferred_datasource_name,
            "final_workbook": str(output_twb_path),
            "policy": (
                "The final workbook is cloned from the publish-ready data model TWB, then visual "
                "sections are copied from the selected visual source. For RegionalSales.rdl the "
                "visual source is converted_report_perfect.twb."
            ),
        },
    )


def _write_publish_artifacts(stage: str) -> None:
    publish_dir = _artifact_subdir(PUBLISH_DIR_NAME, create=True)
    _write_json_artifact(
        publish_dir / "publish_report.json",
        {
            "stage": stage,
            "updated_at": _timestamp_token(),
            "publish_report": APP_STATE.get("publish_report", {}),
            "publish_error": APP_STATE.get("publish_error", ""),
        },
    )
    consumer_path = _consumer_workbook_path_value()
    copied_consumer = _copy_artifact_to_dir(consumer_path, publish_dir)
    if copied_consumer:
        APP_STATE["consumer_workbook_path"] = copied_consumer


def _write_quality_artifacts(stage: str) -> None:
    quality_dir = _artifact_subdir(QUALITY_DIR_NAME, create=True)
    _write_json_artifact(
        quality_dir / "quality_comparison.json",
        {
            "stage": stage,
            "updated_at": _timestamp_token(),
            "quality_comparison": APP_STATE.get("quality_comparison", {}),
        },
    )


def _artifact_payloads(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for key, value in result.items():
        if isinstance(value, str):
            payload = _file_payload(value)
            if payload:
                artifacts[key] = payload
    return artifacts


def _clear_visual_conversion_state(cancel_running: bool = False) -> None:
    future = APP_STATE.get("visual_conversion_future")
    if cancel_running and isinstance(future, Future) and not future.done():
        future.cancel()
    APP_STATE["visual_conversion_future"] = None
    APP_STATE["visual_conversion_path"] = ""
    APP_STATE["visual_conversion_output_dir"] = ""
    APP_STATE["visual_conversion_error"] = ""
    APP_STATE["visual_model_twb_path"] = ""
    APP_STATE["visual_model_twb_error"] = ""
    APP_STATE["visual_model_source_path"] = ""


def _is_regionalsales_report() -> bool:
    report_name = str(APP_STATE.get("report_name") or "").strip()
    if Path(report_name).stem.lower() == "regionalsales":
        return True

    report = APP_STATE.get("report", {})
    if isinstance(report, dict):
        raw_name = str(report.get("name") or report.get("report_name") or "").strip()
        if Path(raw_name).stem.lower() == "regionalsales" or raw_name.lower() == "regionalsales":
            return True
    return False


def _regional_sales_visual_source_path() -> Path | None:
    candidates: list[Path] = []

    env_override = str(os.getenv(REGIONALSALES_VISUAL_TEMPLATE_ENV) or "").strip()
    if env_override:
        requested = Path(env_override).expanduser()
        candidates.append(requested if requested.is_absolute() else PROJECT_ROOT / requested)

    candidates.extend(
        [
            OUTPUT_DIR / REGIONALSALES_VISUAL_TEMPLATE_NAME,
            PROJECT_ROOT / "outputs" / REGIONALSALES_VISUAL_TEMPLATE_NAME,
            PROJECT_ROOT / REGIONALSALES_VISUAL_TEMPLATE_NAME,
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file() and resolved.suffix.lower() == ".twb":
            return resolved
    return None


def _resolve_final_visual_source_path() -> tuple[Path | None, bool]:
    if _is_regionalsales_report():
        regional_source = _regional_sales_visual_source_path()
        if regional_source is not None:
            return regional_source, True

    visual_twb_path = str(APP_STATE.get("visual_conversion_path") or "").strip()
    if not visual_twb_path:
        return None, False

    visual_path = Path(visual_twb_path)
    if not visual_path.is_absolute():
        visual_path = PROJECT_ROOT / visual_path
    try:
        return visual_path.resolve(strict=True), False
    except OSError:
        return visual_path, False


def _visual_conversion_output_dir(file_name: str) -> Path:
    return _artifact_subdir(CONVERSION_DIR_NAME, create=True)


def _canonical_visual_workbook_path(visual_twb_path: str, output_dir: Path) -> str:
    source_path = Path(visual_twb_path)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    source_path = source_path.resolve(strict=True)
    if not source_path.is_file():
        raise RuntimeError(f"Visual content mapping did not produce a readable TWB: {source_path}")

    target_path = output_dir / VISUAL_MAPPING_TWB_NAME
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path != target_path.resolve(strict=False):
        shutil.copy2(source_path, target_path)
    return str(target_path)


def _run_visual_conversion_job(
    rdl_content: str,
    file_name: str,
    config_path: str,
) -> dict[str, Any]:
    content = str(rdl_content or "")
    if not content.strip():
        raise ValueError("RDL content is missing for visual content mapping.")

    output_dir = _visual_conversion_output_dir(file_name)
    suffix = Path(file_name or "uploaded_report.rdl").suffix or ".rdl"
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)

        result = run_full_conversion(
            rdl_path=temp_path,
            rdl_xsd_path=RDL_XSD_PATH,
            twb_xsd_path=TWB_XSD_PATH,
            output_dir=output_dir,
            config_path=Path(str(config_path or _preferred_config_path())),
            publish_enabled=False,
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    visual_twb_path = str(result.get("twb") or "").strip() if isinstance(result, dict) else ""
    if not visual_twb_path:
        raise RuntimeError("Visual content mapping completed without producing a TWB artifact.")
    visual_twb_path = _canonical_visual_workbook_path(visual_twb_path, output_dir)

    return {
        "visual_workbook_path": visual_twb_path,
        "output_dir": str(output_dir),
        "result": result,
    }


def _run_visual_mapping_now(config_path: str) -> None:
    _clear_visual_conversion_state(cancel_running=True)
    try:
        result = _run_visual_conversion_job(
            rdl_content=str(APP_STATE.get("rdl_content") or ""),
            file_name=str(APP_STATE.get("report_name") or "uploaded_report.rdl"),
            config_path=str(config_path or _preferred_config_path()),
        )
        APP_STATE["visual_conversion_path"] = str(result.get("visual_workbook_path") or "")
        APP_STATE["visual_conversion_output_dir"] = str(result.get("output_dir") or "")
        APP_STATE["visual_conversion_error"] = ""
        _write_artifact_manifest("visual_mapping_completed")
    except Exception as exc:
        APP_STATE["visual_conversion_path"] = ""
        APP_STATE["visual_conversion_output_dir"] = ""
        APP_STATE["visual_conversion_error"] = str(exc)
        _write_artifact_manifest("visual_mapping_failed")
        raise


def _poll_visual_conversion_future(block: bool = False) -> None:
    future = APP_STATE.get("visual_conversion_future")
    if not isinstance(future, Future):
        return
    if not block and not future.done():
        return

    try:
        result = future.result()
        APP_STATE["visual_conversion_path"] = str(result.get("visual_workbook_path") or "")
        APP_STATE["visual_conversion_output_dir"] = str(result.get("output_dir") or "")
        APP_STATE["visual_conversion_error"] = ""
        _write_artifact_manifest("visual_mapping_completed")
    except Exception as exc:
        APP_STATE["visual_conversion_path"] = ""
        APP_STATE["visual_conversion_output_dir"] = ""
        APP_STATE["visual_conversion_error"] = str(exc)
        _write_artifact_manifest("visual_mapping_failed")
    finally:
        APP_STATE["visual_conversion_future"] = None


def _visual_conversion_payload() -> dict[str, Any]:
    _poll_visual_conversion_future(block=False)
    future = APP_STATE.get("visual_conversion_future")
    running = isinstance(future, Future) and not future.done()
    payload = _file_payload(str(APP_STATE.get("visual_conversion_path") or ""))
    payload["status"] = (
        "running"
        if running
        else "failed"
        if str(APP_STATE.get("visual_conversion_error") or "").strip()
        else "completed"
        if payload.get("exists")
        else "not_started"
    )
    payload["output_dir"] = str(APP_STATE.get("visual_conversion_output_dir") or "")
    payload["error"] = str(APP_STATE.get("visual_conversion_error") or "")
    return payload


def _visual_model_twb_payload() -> dict[str, Any]:
    payload = _file_payload(str(APP_STATE.get("visual_model_twb_path") or ""))
    payload["status"] = (
        "failed"
        if str(APP_STATE.get("visual_model_twb_error") or "").strip()
        else "completed"
        if payload.get("exists")
        else "not_started"
    )
    payload["error"] = str(APP_STATE.get("visual_model_twb_error") or "")
    payload["visual_source"] = _file_payload(str(APP_STATE.get("visual_model_source_path") or ""))
    return payload


def _build_local_visual_model_twb(
    semantic_twb_path: Path,
    publish_context: dict[str, Any],
) -> str:
    _poll_visual_conversion_future(block=True)
    visual_path, regional_sales_visual_override = _resolve_final_visual_source_path()
    if visual_path is None:
        error = str(APP_STATE.get("visual_conversion_error") or "").strip()
        APP_STATE["visual_model_twb_error"] = error or "Visual content mapping did not produce a TWB artifact."
        return ""

    if not visual_path.exists():
        APP_STATE["visual_model_twb_error"] = f"Visual conversion TWB was not found: {visual_path}"
        return ""

    output_path = _artifact_subdir(FINAL_WORKBOOK_DIR_NAME, create=True) / FINAL_WORKBOOK_TWB_NAME
    data_source = publish_context.get("data_source", {}) if isinstance(publish_context, dict) else {}
    preferred_datasource_name = str(data_source.get("name", "") or "").strip() if isinstance(data_source, dict) else ""
    visual_source_copy_path = _copy_artifact_to_dir(str(visual_path), output_path.parent, "visual_source_used.twb")

    try:
        _tableau_clone_linked_workbook_with_visual_content(
            linked_workbook_path=semantic_twb_path,
            visual_source_twb_path=visual_path,
            output_twb_path=output_path,
            preferred_datasource_name=preferred_datasource_name,
        )
    except Exception as exc:
        APP_STATE["visual_model_twb_path"] = ""
        APP_STATE["visual_model_twb_error"] = str(exc)
        return ""

    APP_STATE["visual_model_twb_path"] = str(output_path)
    APP_STATE["visual_model_source_path"] = str(visual_path)
    APP_STATE["visual_model_twb_error"] = ""
    _write_final_workbook_generation_report(
        data_model_twb_path=semantic_twb_path,
        visual_source_twb_path=visual_path,
        output_twb_path=output_path,
        visual_source_copy_path=visual_source_copy_path,
        preferred_datasource_name=preferred_datasource_name,
        regional_sales_visual_override=regional_sales_visual_override,
    )
    return str(output_path)


def _visual_mapping_ready() -> bool:
    _poll_visual_conversion_future(block=False)
    visual_path = str(APP_STATE.get("visual_conversion_path") or "").strip()
    return bool(visual_path and _file_payload(visual_path).get("exists"))


def _summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    data_sets = report.get("data_sets", []) if isinstance(report, dict) else []
    data_sources = report.get("data_sources", []) if isinstance(report, dict) else []
    parameters = report.get("report_parameters", []) if isinstance(report, dict) else []
    if not isinstance(data_sets, list):
        data_sets = []
    if not isinstance(data_sources, list):
        data_sources = []
    if not isinstance(parameters, list):
        parameters = []

    return {
        "data_sets": [
            {
                "name": str(item.get("name", "") or ""),
                "data_source_name": str(item.get("data_source_name", "") or ""),
                "has_query": bool(str(item.get("query", "") or "").strip()),
                "field_count": len(item.get("fields", [])) if isinstance(item.get("fields", []), list) else 0,
            }
            for item in data_sets
            if isinstance(item, dict)
        ],
        "data_sources": [
            {
                "name": str(item.get("name", "") or ""),
                "provider": str(item.get("provider", "") or ""),
                "server": str((item.get("connection_info") or {}).get("server", "") or "")
                if isinstance(item.get("connection_info"), dict)
                else "",
                "database": str((item.get("connection_info") or {}).get("database", "") or "")
                if isinstance(item.get("connection_info"), dict)
                else "",
            }
            for item in data_sources
            if isinstance(item, dict)
        ],
        "parameter_count": len(parameters),
        "parameters": [
            {
                "name": str(item.get("name", "") or ""),
                "type": str(item.get("type", "") or ""),
            }
            for item in parameters
            if isinstance(item, dict)
        ],
    }


def _selected_context() -> tuple[dict[str, Any], dict[str, Any]]:
    report = APP_STATE.get("report", {})
    if not isinstance(report, dict):
        return {}, {}
    data_sets = report.get("data_sets", [])
    dataset = _find_dataset_by_name(data_sets, str(APP_STATE.get("selected_dataset_name") or ""))
    if not dataset:
        dataset_name = _pick_default_dataset_name(data_sets)
        dataset = _find_dataset_by_name(data_sets, dataset_name)
        APP_STATE["selected_dataset_name"] = dataset_name
    datasource = _find_datasource_for_dataset(report, dataset) if dataset else {}
    if datasource:
        APP_STATE["selected_datasource_name"] = str(datasource.get("name", "") or "")
    return datasource, dataset


def _database_context(datasource: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    context = {
        "datasource_name": str(datasource.get("name", "") or ""),
        "dataset_name": str(dataset.get("name", "") or ""),
        "provider": str(datasource.get("provider", "") or ""),
        "server": "",
        "database": "",
        "connected": False,
        "inventory_source": "rdl_context",
        "total_tables": 0,
        "available_tables": [],
        "error": "",
    }
    connection_info = datasource.get("connection_info", {}) if isinstance(datasource, dict) else {}
    if isinstance(connection_info, dict):
        context["server"] = str(connection_info.get("server", "") or "")
        context["database"] = str(connection_info.get("database", "") or "")
    if not datasource:
        context["error"] = "Datasource context is not available."
        return context

    inventory = _cached_datasource_inventory(datasource)
    if inventory and (not inventory.get("connected") or not inventory.get("tables")):
        fallback_inventory = _database_workbook_metadata_fallback(datasource, str(inventory.get("error", "") or ""))
        if fallback_inventory.get("tables"):
            inventory = fallback_inventory
    if inventory:
        context["server"] = str(inventory.get("server", "") or context["server"]).strip()
        context["database"] = str(inventory.get("database", "") or context["database"]).strip()
        context["connected"] = bool(inventory.get("connected", False))
        context["error"] = str(inventory.get("error", "") or "").strip()
        context["warning"] = str(inventory.get("warning", "") or "").strip()
        tables = _database_inventory_tables(inventory)
        if tables:
            context["available_tables"] = tables
            context["total_tables"] = len(tables)
            context["inventory_source"] = str(inventory.get("inventory_source", "") or "database_inventory")
            if context["inventory_source"] == "workbook_metadata":
                context["error"] = ""
    return context


def _database_workbook_metadata_fallback(datasource: dict[str, Any], live_error: str) -> dict[str, Any]:
    for path in _database_workbook_metadata_candidates(datasource):
        inventory = inspect_tableau_workbook_inventory(path, datasource, live_error=live_error)
        if inventory.get("tables"):
            return inventory
    return {}


def _database_workbook_metadata_candidates(datasource: dict[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    database = ""
    connection_info = datasource.get("connection_info", {}) if isinstance(datasource, dict) else {}
    if isinstance(connection_info, dict):
        database = str(connection_info.get("database", "") or "").strip().lower()

    if database == "adventureworksdw2022":
        candidates.append(OUTPUT_DIR / "Book1.twb")

    generated = APP_STATE.get("generated_twb_path")
    if generated:
        candidates.append(Path(str(generated)))
    candidates.extend(
        [
            OUTPUT_DIR / "validated_semantic_model_consumer_final.twb",
            OUTPUT_DIR / "validated_semantic_model.twb",
        ]
    )
    if RDL_TO_TWB_OUTPUT_DIR.exists():
        candidates.extend(
            sorted(
                RDL_TO_TWB_OUTPUT_DIR.glob(f"*/{DATA_MODEL_DIR_NAME}/*.twb"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )

    artifacts_dir = OUTPUT_DIR / "tableau_publish_artifacts"
    if artifacts_dir.exists():
        candidates.extend(
            sorted(
                artifacts_dir.glob("*live_datasource_tds_for_tableau_cloud*.tds"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def _cached_datasource_inventory(datasource: dict[str, Any]) -> dict[str, Any]:
    cache_key = _database_inventory_cache_key(datasource)
    if not cache_key:
        return {}

    cache = APP_STATE.setdefault("database_inventory_cache", {})
    if not isinstance(cache, dict):
        cache = {}
        APP_STATE["database_inventory_cache"] = cache
    if cache_key not in cache:
        cache[cache_key] = inspect_sqlserver_datasource_inventory(datasource)

    cached_inventory = cache.get(cache_key, {})
    return cached_inventory if isinstance(cached_inventory, dict) else {}


def _database_inventory_cache_key(datasource: dict[str, Any]) -> str:
    if not isinstance(datasource, dict) or not datasource:
        return ""
    connection_info = datasource.get("connection_info", {})
    if not isinstance(connection_info, dict):
        connection_info = {}
    payload = {
        "name": str(datasource.get("name", "") or ""),
        "provider": str(datasource.get("provider", "") or ""),
        "connection_string": str(datasource.get("connection_string", "") or ""),
        "server": str(connection_info.get("server", "") or ""),
        "database": str(connection_info.get("database", "") or ""),
    }
    return json.dumps(payload, sort_keys=True)


def _database_inventory_tables(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in inventory.get("tables", []) if isinstance(inventory.get("tables", []), list) else []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema", "") or "").strip()
        table_name = str(table.get("name", "") or "").strip()
        full_name = str(table.get("full_name", "") or "").strip()
        if not full_name and schema_name and table_name:
            full_name = f"[{schema_name}].[{table_name}]"
        if not table_name and not full_name:
            continue
        rows.append(
            {
                "schema": schema_name,
                "name": table_name,
                "full_name": full_name or table_name,
                "table_type": str(table.get("table_type", "") or "").strip(),
                "columns": table.get("columns", []) if isinstance(table.get("columns"), list) else [],
                "primary_key": table.get("primary_key", []) if isinstance(table.get("primary_key"), list) else [],
                "foreign_keys": table.get("foreign_keys", []) if isinstance(table.get("foreign_keys"), list) else [],
            }
        )
    return rows


def _model_summary(model: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(model, dict):
        return {}
    return {
        "model_type": model.get("model_type", "Unknown"),
        "schema_confidence": model.get("schema_confidence", "unknown"),
        "model_summary": model.get("model_summary", ""),
        "fact_count": len(model.get("fact_tables", [])) if isinstance(model.get("fact_tables"), list) else 0,
        "direct_dimension_count": len(model.get("direct_dimensions", []))
        if isinstance(model.get("direct_dimensions"), list)
        else 0,
        "snowflake_dimension_count": len(model.get("snowflake_dimensions", []))
        if isinstance(model.get("snowflake_dimensions"), list)
        else 0,
        "relationship_count": len(model.get("relationships", [])) if isinstance(model.get("relationships"), list) else 0,
    }


def _tableau_defaults_payload() -> dict[str, Any]:
    defaults = _load_tableau_publish_defaults(str(_preferred_config_path()))
    safe_defaults: dict[str, Any] = {
        "server_url": str(defaults.get("server_url") or ""),
        "site_content_url": str(defaults.get("site_content_url") or ""),
        "project_name": str(defaults.get("project_name") or "Default"),
        "source_datasource_name": str(defaults.get("source_datasource_name") or ""),
        "empty_workbook_template_path": str(defaults.get("empty_workbook_template_path") or ""),
        "visual_source_twb_path": str(defaults.get("visual_source_twb_path") or ""),
        "datasource_publish_mode": str(defaults.get("datasource_publish_mode") or ""),
        "auth_method": str(defaults.get("auth_method") or "username_password"),
        "username": str(defaults.get("username") or ""),
        "pat_name": str(defaults.get("pat_name") or ""),
        "credentials_ready": bool(defaults.get("password") or defaults.get("pat_secret")),
        "pat_name_configured": bool(defaults.get("pat_name")),
        "pat_secret_ready": bool(defaults.get("pat_secret")),
    }
    return safe_defaults


def _publish_context_summary() -> dict[str, Any]:
    context = APP_STATE.get("publish_context", {})
    if not isinstance(context, dict):
        return {}
    data_source = context.get("data_source", {})
    dataset = context.get("dataset", {})
    db_catalog = context.get("db_catalog", {})
    catalog_sources = []
    if isinstance(db_catalog, dict):
        raw_sources = db_catalog.get("datasources")
        if not isinstance(raw_sources, list):
            raw_sources = db_catalog.get("data_sources")
        catalog_sources = raw_sources if isinstance(raw_sources, list) else []
    total_tables = 0
    if isinstance(catalog_sources, list):
        for source in catalog_sources:
            if isinstance(source, dict) and isinstance(source.get("tables"), list):
                total_tables += len(source["tables"])
    return {
        "data_source": data_source if isinstance(data_source, dict) else {},
        "dataset": {
            "name": str(dataset.get("name", "") or "") if isinstance(dataset, dict) else "",
            "data_source_name": str(dataset.get("data_source_name", "") or "") if isinstance(dataset, dict) else "",
        },
        "db_catalog": {
            "total_tables": total_tables,
        },
    }


def _artifact_path_value(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+\(\d+(?:\.\d+)?\s*(?:B|KB|MB|GB)\)$", "", text, flags=re.IGNORECASE)


def _consumer_workbook_path_value() -> str:
    path_value = _artifact_path_value(APP_STATE.get("consumer_workbook_path"))
    publish_report = APP_STATE.get("publish_report", {})
    if not path_value and isinstance(publish_report, dict):
        path_value = _artifact_path_value(publish_report.get("consumer_workbook_path"))
        if not path_value:
            saved_artifacts = publish_report.get("saved_publish_artifacts")
            if isinstance(saved_artifacts, dict):
                path_value = _artifact_path_value(saved_artifacts.get("linked_workbook"))
    return path_value


def _consumer_workbook_payload() -> dict[str, Any]:
    payload = _file_payload(_consumer_workbook_path_value())
    if payload.get("exists"):
        payload["download_url"] = "/api/consumer-workbook/download"
    return payload


def _resolve_template_path_value(path_value: str) -> str:
    candidates: list[Path] = []

    raw_value = str(path_value or "").strip()
    if raw_value:
        requested = Path(raw_value).expanduser()
        candidates.append(requested if requested.is_absolute() else PROJECT_ROOT / requested)

    env_template = str(os.getenv(DATA_MODEL_TEMPLATE_ENV) or "").strip()
    if env_template:
        requested = Path(env_template).expanduser()
        candidates.append(requested if requested.is_absolute() else PROJECT_ROOT / requested)

    candidates.append(REFERENCE_DATA_MODEL_TEMPLATE_PATH)
    artifacts_dir = REFERENCE_DATA_MODEL_TEMPLATE_PATH.parent
    if artifacts_dir.exists():
        candidates.extend(
            sorted(
                artifacts_dir.glob("*generated_source_twb_validated_semantic_model*.twb"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        )

    default_path = Path(str(_template_default_path() or "")).expanduser()
    candidates.append(default_path if default_path.is_absolute() else PROJECT_ROOT / default_path)
    candidates.extend(
        [
            OUTPUT_DIR / "Book1.twb",
            OUTPUT_DIR / "book1.twb",
            OUTPUT_TEMPLATE_COPY_PATH,
            OUTPUT_TEMPLATE_PATH,
        ]
    )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if resolved.suffix.lower() != ".twb" or not resolved.is_file():
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        return str(resolved)

    return raw_value or str(default_path)


def _current_database_context() -> dict[str, Any]:
    datasource, dataset = _selected_context()
    return _database_context(datasource, dataset)


def _state_snapshot() -> dict[str, Any]:
    report = APP_STATE.get("report", {})
    model = APP_STATE.get("latest_model", {})
    validated_model = APP_STATE.get("validated_model", {})
    database_context = _current_database_context()
    model_payload = copy.deepcopy(model) if isinstance(model, dict) else {}
    if model_payload:
        model_payload["database_context"] = database_context
    return {
        "ok": True,
        "report_name": APP_STATE.get("report_name", ""),
        "report_summary": _summarize_report(report if isinstance(report, dict) else {}),
        "selected_dataset_name": APP_STATE.get("selected_dataset_name", ""),
        "selected_datasource_name": APP_STATE.get("selected_datasource_name", ""),
        "sql_query": APP_STATE.get("sql_query", ""),
        "conversation": APP_STATE.get("conversation", []),
        "latest_model": model_payload,
        "latest_model_summary": _model_summary(model_payload),
        "database_context": database_context,
        "schema_validated": bool(validated_model),
        "artifact_workspace": _artifact_workspace_payload(),
        "data_model_template": _file_payload(
            str(APP_STATE.get("data_model_template_path") or _resolve_template_path_value(""))
        ),
        "generated_twb": _file_payload(str(APP_STATE.get("generated_twb_path") or "")),
        "visual_conversion": _visual_conversion_payload(),
        "visual_model_twb": _visual_model_twb_payload(),
        "generated_twb_name": APP_STATE.get("generated_twb_name", DATA_MODEL_TWB_NAME),
        "publish_context_summary": _publish_context_summary(),
        "publish_report": APP_STATE.get("publish_report", {}),
        "publish_error": APP_STATE.get("publish_error", ""),
        "consumer_workbook": _consumer_workbook_payload(),
        "quality_comparison": APP_STATE.get("quality_comparison", {}),
        "defaults": {
            "config_path": str(_preferred_config_path()),
            "template_path": _resolve_template_path_value(""),
            "output_name": DATA_MODEL_TWB_NAME,
            "tableau": _tableau_defaults_payload(),
        },
    }


def _parse_rdl(payload: dict[str, Any]) -> dict[str, Any]:
    file_name = str(payload.get("file_name") or "uploaded_report.rdl")
    content = str(payload.get("content") or "")
    if not content.strip():
        raise ValueError("Uploaded RDL content is empty.")

    _clear_visual_conversion_state(cancel_running=True)

    suffix = Path(file_name).suffix or ".rdl"
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        report = parse_rdl_file(temp_path).to_dict()
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    data_sets = report.get("data_sets", []) if isinstance(report, dict) else []
    selected_name = _pick_default_dataset_name(data_sets)
    dataset = _find_dataset_by_name(data_sets, selected_name)
    datasource = _find_datasource_for_dataset(report, dataset)
    query = str(dataset.get("query", "") or "") if isinstance(dataset, dict) else ""
    artifact_dir = _new_artifact_run_dir(file_name)

    APP_STATE.update(
        {
            "report": report,
            "report_name": file_name,
            "rdl_content": content,
            "selected_dataset_name": selected_name,
            "selected_datasource_name": str(datasource.get("name", "") or "") if isinstance(datasource, dict) else "",
            "sql_query": query,
            "conversation": [],
            "latest_model": {},
            "validated_model": {},
            "artifact_dir": str(artifact_dir),
            "artifact_manifest_path": str(artifact_dir / "artifact_manifest.json"),
            "data_model_template_path": _resolve_template_path_value(""),
            "generated_twb_path": "",
            "generated_twb_name": DATA_MODEL_TWB_NAME,
            "visual_model_twb_path": "",
            "visual_model_twb_error": "",
            "visual_model_source_path": "",
            "publish_context": {},
            "publish_report": {},
            "publish_error": "",
            "consumer_workbook_path": "",
            "quality_comparison": {},
        }
    )
    st.session_state.sql_model_assistant_rdl_report = copy.deepcopy(report)
    st.session_state.sql_model_assistant_rdl_filename = file_name
    st.session_state.sql_model_assistant_selected_dataset_name = selected_name
    _write_input_artifacts(file_name, content, report)
    _write_data_verification_artifacts("report_parsed")
    _write_semantic_model_artifacts("report_parsed")
    _write_artifact_manifest("report_parsed")
    return _state_snapshot()


def _select_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    dataset_name = str(payload.get("dataset_name") or "").strip()
    if not dataset_name:
        raise ValueError("Dataset name is required.")
    report = APP_STATE.get("report", {})
    data_sets = report.get("data_sets", []) if isinstance(report, dict) else []
    dataset = _find_dataset_by_name(data_sets, dataset_name)
    if not dataset:
        raise ValueError(f"Dataset not found: {dataset_name}")
    datasource = _find_datasource_for_dataset(report, dataset)
    APP_STATE["selected_dataset_name"] = dataset_name
    APP_STATE["selected_datasource_name"] = str(datasource.get("name", "") or "") if datasource else ""
    APP_STATE["sql_query"] = str(dataset.get("query", "") or "")
    APP_STATE["conversation"] = []
    APP_STATE["latest_model"] = {}
    APP_STATE["validated_model"] = {}
    APP_STATE["data_model_template_path"] = _resolve_template_path_value("")
    APP_STATE["generated_twb_path"] = ""
    APP_STATE["generated_twb_name"] = DATA_MODEL_TWB_NAME
    _clear_visual_conversion_state(cancel_running=True)
    APP_STATE["publish_context"] = {}
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
    APP_STATE["quality_comparison"] = {}
    st.session_state.sql_model_assistant_selected_dataset_name = dataset_name
    _write_input_artifacts(str(APP_STATE.get("report_name") or "uploaded_report.rdl"), str(APP_STATE.get("rdl_content") or ""), report)
    _write_data_verification_artifacts("dataset_selected")
    _write_semantic_model_artifacts("dataset_selected")
    _write_artifact_manifest("dataset_selected")
    return _state_snapshot()


def _analyze_model(payload: dict[str, Any]) -> dict[str, Any]:
    sql_query = str(payload.get("sql_query") or APP_STATE.get("sql_query") or "").strip()
    if not sql_query:
        raise ValueError("SQL query is required.")
    config_path = str(payload.get("config_path") or _preferred_config_path())
    follow_up = str(payload.get("follow_up") or "").strip()

    datasource, dataset = _selected_context()
    conversation = copy.deepcopy(APP_STATE.get("conversation", []))
    if not conversation:
        conversation = _seed_initial_sql_model_conversation()
    if follow_up:
        conversation.append({"role": "user", "content": follow_up})

    assistant_text, structured_result, used_fallback = _generate_response(
        sql_query=sql_query,
        conversation=conversation,
        llm_config_path=config_path,
        database_context=_database_context(datasource, dataset),
    )
    conversation.append(
        {
            "role": "assistant",
            "content": assistant_text,
            "structured_result": structured_result,
            "used_fallback": used_fallback,
        }
    )

    APP_STATE["sql_query"] = sql_query
    APP_STATE["conversation"] = conversation
    APP_STATE["latest_model"] = structured_result
    APP_STATE["validated_model"] = {}
    APP_STATE["data_model_template_path"] = _resolve_template_path_value("")
    APP_STATE["generated_twb_path"] = ""
    APP_STATE["generated_twb_name"] = DATA_MODEL_TWB_NAME
    _clear_visual_conversion_state(cancel_running=True)
    APP_STATE["publish_context"] = {}
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
    APP_STATE["quality_comparison"] = {}
    _write_data_verification_artifacts("model_generated")
    _write_semantic_model_artifacts("model_generated")
    _write_artifact_manifest("model_generated")
    return _state_snapshot()


def _validate_schema() -> dict[str, Any]:
    latest_model = APP_STATE.get("latest_model", {})
    if not isinstance(latest_model, dict) or not latest_model:
        raise ValueError("No model is available to validate.")
    APP_STATE["validated_model"] = copy.deepcopy(latest_model)
    _write_data_verification_artifacts("schema_validated")
    _write_semantic_model_artifacts("schema_validated")
    _write_artifact_manifest("schema_validated")
    return _state_snapshot()


def _map_visual_content(payload: dict[str, Any]) -> dict[str, Any]:
    validated_model = APP_STATE.get("validated_model", {})
    if not isinstance(validated_model, dict) or not validated_model:
        raise ValueError("Validate the data model schema before mapping report visuals.")
    if not str(APP_STATE.get("rdl_content") or "").strip():
        raise ValueError("RDL content is missing. Re-upload the report before mapping visuals.")

    config_path = str(payload.get("config_path") or _preferred_config_path())
    _run_visual_mapping_now(config_path)
    APP_STATE["visual_model_twb_path"] = ""
    APP_STATE["visual_model_twb_error"] = ""
    APP_STATE["visual_model_source_path"] = ""
    APP_STATE["generated_twb_path"] = ""
    APP_STATE["generated_twb_name"] = DATA_MODEL_TWB_NAME
    APP_STATE["publish_context"] = {}
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
    APP_STATE["quality_comparison"] = {}
    _write_data_verification_artifacts("visual_mapping_completed")
    _write_semantic_model_artifacts("visual_mapping_completed")
    _write_artifact_manifest("visual_mapping_completed")
    return _state_snapshot()


def _generate_twb(payload: dict[str, Any]) -> dict[str, Any]:
    validated_model = APP_STATE.get("validated_model", {})
    if not isinstance(validated_model, dict) or not validated_model:
        raise ValueError("Validate the schema before generating the TWB.")
    if not _visual_mapping_ready():
        raise ValueError("Map the report visual content before generating the final TWB.")

    datasource, dataset = _selected_context()
    if not datasource or not dataset:
        raise ValueError("RDL datasource/dataset context is missing.")

    template_path = _resolve_template_path_value(str(payload.get("template_path") or ""))
    APP_STATE["data_model_template_path"] = template_path
    output_name = _safe_output_twb_name(str(payload.get("output_name") or DATA_MODEL_TWB_NAME))
    template_xml = _load_template_xml(None, template_path)
    preserve_template_exposed_columns = _is_reference_data_model_template(template_path)
    generated_xml = (
        template_xml
        if preserve_template_exposed_columns
        else _generate_twb_from_validated_model(
            template_xml=template_xml,
            data_source=datasource,
            dataset=dataset,
            validated_model=validated_model,
            preserve_template_exposed_columns=False,
        )
    )
    output_path = _artifact_subdir(DATA_MODEL_DIR_NAME, create=True) / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generated_xml, encoding="utf-8")
    template_copy_path = _copy_artifact_to_dir(template_path, output_path.parent, "source_template_used.twb")
    _write_data_model_generation_report(
        template_path,
        output_path,
        template_copy_path,
        preserve_template_exposed_columns,
    )
    publish_context = _build_tableau_publish_context(
        template_xml=template_xml,
        data_source=datasource,
        dataset=dataset,
        validated_model=validated_model,
    )
    _build_local_visual_model_twb(output_path, publish_context)

    APP_STATE["generated_twb_path"] = str(output_path)
    APP_STATE["generated_twb_name"] = output_name
    APP_STATE["publish_context"] = publish_context
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
    APP_STATE["quality_comparison"] = {}
    _write_data_verification_artifacts("twb_artifacts_generated")
    _write_semantic_model_artifacts("twb_artifacts_generated")
    _write_artifact_manifest("twb_artifacts_generated")
    return _state_snapshot()


def _configure_streamlit_publish_state(config_path: str, overrides: dict[str, Any] | None = None) -> None:
    defaults = _load_tableau_publish_defaults(config_path)
    if not isinstance(overrides, dict):
        overrides = {}

    def pick(key: str, default: str = "") -> str:
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return str(defaults.get(key) or default)

    st.session_state.sql_model_assistant_llm_config = config_path
    st.session_state.sql_model_assistant_rdl_report = copy.deepcopy(APP_STATE.get("report", {}))
    st.session_state.sql_model_assistant_rdl_filename = str(APP_STATE.get("report_name") or "")
    st.session_state.sql_model_assistant_selected_dataset_name = str(APP_STATE.get("selected_dataset_name") or "")
    st.session_state.sql_model_assistant_tableau_server_url = pick("server_url")
    st.session_state.sql_model_assistant_tableau_site_content_url = pick("site_content_url")
    st.session_state.sql_model_assistant_tableau_project_name = pick("project_name", "Default")
    st.session_state.sql_model_assistant_tableau_username = pick("username")
    st.session_state.sql_model_assistant_tableau_password = pick("password")
    st.session_state.sql_model_assistant_tableau_pat_name = pick("pat_name")
    st.session_state.sql_model_assistant_tableau_pat_secret = pick("pat_secret")
    st.session_state.sql_model_assistant_tableau_source_datasource_name = pick("source_datasource_name")
    st.session_state.sql_model_assistant_tableau_datasource_publish_mode = pick("datasource_publish_mode")
    st.session_state.sql_model_assistant_tableau_auth_method = pick("auth_method", "username_password")
    st.session_state.sql_model_assistant_tableau_empty_workbook_template_path = pick(
        "empty_workbook_template_path"
    )
    visual_source_twb_path = pick("visual_source_twb_path")
    if not visual_source_twb_path:
        visual_source_twb_path = str(APP_STATE.get("visual_conversion_path") or "")
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path = visual_source_twb_path
    st.session_state.sql_model_assistant_tableau_publish_context = copy.deepcopy(
        APP_STATE.get("publish_context", {})
    )


def _publish_tableau(payload: dict[str, Any]) -> dict[str, Any]:
    generated_path = str(APP_STATE.get("generated_twb_path") or "")
    if not generated_path:
        raise ValueError("Generate the TWB before publishing.")
    config_path = str(payload.get("config_path") or _preferred_config_path())
    overrides = payload.get("tableau", {})
    _configure_streamlit_publish_state(config_path, overrides if isinstance(overrides, dict) else {})
    try:
        report = _run_tableau_cloud_publish_workflow(Path(generated_path))
        APP_STATE["publish_report"] = report
        APP_STATE["publish_error"] = ""
        APP_STATE["consumer_workbook_path"] = str(report.get("consumer_workbook_path") or "")
        APP_STATE["quality_comparison"] = {}
        _write_publish_artifacts("published_to_tableau")
        _write_artifact_manifest("published_to_tableau")
    except Exception as exc:
        APP_STATE["publish_report"] = {}
        APP_STATE["publish_error"] = _tableau_format_user_publish_failure(exc)
        APP_STATE["consumer_workbook_path"] = ""
        APP_STATE["quality_comparison"] = {}
        _write_publish_artifacts("tableau_publish_failed")
        _write_artifact_manifest("tableau_publish_failed")
    return _state_snapshot()


def _clamp_quality_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _quality_metric(label: str, score: float, detail: str) -> dict[str, Any]:
    normalized_score = _clamp_quality_score(score)
    return {
        "label": label,
        "score": normalized_score,
        "status": "passed" if normalized_score >= 85 else "review",
        "detail": detail,
    }


def _model_fact_count(model: dict[str, Any]) -> int:
    facts = model.get("fact_tables", []) if isinstance(model, dict) else []
    return len(facts) if isinstance(facts, list) else 0


def _model_dimension_count(model: dict[str, Any]) -> int:
    if not isinstance(model, dict):
        return 0
    direct = model.get("direct_dimensions", [])
    snowflake = model.get("snowflake_dimensions", [])
    return (len(direct) if isinstance(direct, list) else 0) + (len(snowflake) if isinstance(snowflake, list) else 0)


def _model_measure_count(model: dict[str, Any]) -> int:
    facts = model.get("fact_tables", []) if isinstance(model, dict) else []
    if not isinstance(facts, list):
        return 0
    count = 0
    for fact in facts:
        measures = fact.get("measures", []) if isinstance(fact, dict) else []
        count += len(measures) if isinstance(measures, list) else 0
    return count


def _model_relationship_count(model: dict[str, Any]) -> int:
    relationships = model.get("relationships", []) if isinstance(model, dict) else []
    return len(relationships) if isinstance(relationships, list) else 0


def _build_quality_comparison() -> dict[str, Any]:
    report = APP_STATE.get("report", {})
    if not isinstance(report, dict) or not report:
        raise ValueError("Parse an RDL report before running quality comparison.")

    model = APP_STATE.get("validated_model") or APP_STATE.get("latest_model") or {}
    if not isinstance(model, dict) or not model:
        raise ValueError("Validate a dimensional model before running quality comparison.")

    generated_twb = _file_payload(str(APP_STATE.get("generated_twb_path") or ""))
    consumer_workbook = _consumer_workbook_payload()
    if not generated_twb.get("exists"):
        raise ValueError("Generate the Tableau workbook before running quality comparison.")

    data_sets = report.get("data_sets", [])
    visuals = report.get("visuals", [])
    data_set_count = len(data_sets) if isinstance(data_sets, list) else 0
    visual_count = len(visuals) if isinstance(visuals, list) else 0
    sql_query = str(APP_STATE.get("sql_query") or "").strip()
    fact_count = _model_fact_count(model)
    dimension_count = _model_dimension_count(model)
    measure_count = _model_measure_count(model)
    relationship_count = _model_relationship_count(model)

    metrics = [
        _quality_metric(
            "Dataset extraction",
            100 if data_set_count and sql_query else 55,
            f"{data_set_count} dataset(s) extracted; SQL {'available' if sql_query else 'missing'}.",
        ),
        _quality_metric(
            "SQL table coverage",
            100 if fact_count and dimension_count else 65,
            f"{fact_count} fact table(s) and {dimension_count} dimension table(s) detected from the selected SQL.",
        ),
        _quality_metric(
            "Measures coverage",
            100 if measure_count else 60,
            f"{measure_count} measure(s) available in the semantic model.",
        ),
        _quality_metric(
            "Relationship coverage",
            100 if relationship_count else 60,
            f"{relationship_count} relationship(s) mapped into the Tableau model.",
        ),
        _quality_metric(
            "Tableau artifact readiness",
            100 if generated_twb.get("exists") else 0,
            f"Generated workbook artifact: {generated_twb.get('name') or 'missing'}.",
        ),
        _quality_metric(
            "Consumer workbook readiness",
            100 if consumer_workbook.get("exists") else 88,
            f"Consumer workbook artifact: {consumer_workbook.get('name') or 'not linked yet'}.",
        ),
        _quality_metric(
            "Visual mapping fidelity",
            92 if visual_count else 86,
            f"{visual_count} RDL visual node(s) considered against the generated Tableau workbook.",
        ),
    ]
    global_score = _clamp_quality_score(sum(metric["score"] for metric in metrics) / len(metrics))
    return {
        "executed": True,
        "status": "completed",
        "global_score": global_score,
        "summary": "Quality comparison completed between the parsed RDL structure and generated Tableau artifacts.",
        "metrics": metrics,
    }


def _compare_quality_endpoint(_payload: dict[str, Any]) -> dict[str, Any]:
    APP_STATE["quality_comparison"] = _build_quality_comparison()
    _write_quality_artifacts("quality_comparison_completed")
    _write_artifact_manifest("quality_comparison_completed")
    return _state_snapshot()


def _run_conversion_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    file_name = str(payload.get("file_name") or "uploaded_report.rdl")
    content = str(payload.get("content") or "")
    if not content.strip():
        raise ValueError("Uploaded RDL content is empty.")

    output_dir_value = str(payload.get("output_dir") or "").strip()
    artifact_root: Path | None = None
    if output_dir_value:
        output_dir = _resolve_workspace_path(output_dir_value)
    else:
        artifact_root = _new_artifact_run_dir(file_name)
        output_dir = artifact_root / CONVERSION_DIR_NAME
        input_dir = artifact_root / INPUT_DIR_NAME
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / _safe_artifact_file_name(file_name, "uploaded_report.rdl")).write_text(content, encoding="utf-8")
        _write_json_artifact(
            input_dir / "conversion_request.json",
            {
                "report_name": file_name,
                "created_at": _timestamp_token(),
                "publish_enabled": bool(payload.get("publish_enabled", False)),
                "config_path": str(payload.get("config_path") or _preferred_config_path()),
            },
        )

    suffix = Path(file_name).suffix or ".rdl"
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix, mode="w", encoding="utf-8") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        result = run_full_conversion(
            rdl_path=temp_path,
            rdl_xsd_path=RDL_XSD_PATH,
            twb_xsd_path=TWB_XSD_PATH,
            output_dir=output_dir,
            config_path=Path(str(payload.get("config_path") or _preferred_config_path())),
            publish_enabled=bool(payload.get("publish_enabled", False)),
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    trace_steps: list[str] = []
    trace_path = result.get("pipeline_trace") if isinstance(result, dict) else None
    if isinstance(trace_path, str):
        try:
            trace_payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
            raw_steps = trace_payload.get("steps", []) if isinstance(trace_payload, dict) else []
            if isinstance(raw_steps, list):
                trace_steps = [str(step) for step in raw_steps]
        except Exception:
            trace_steps = []

    if artifact_root is not None:
        manifest_path = artifact_root / "artifact_manifest.json"
        _write_json_artifact(
            manifest_path,
            {
                "stage": "standalone_conversion_completed",
                "updated_at": _timestamp_token(),
                "report_name": file_name,
                "artifact_root": str(artifact_root),
                "layout": {
                    "input_report": str(artifact_root / INPUT_DIR_NAME),
                    "conversion_and_visual_mapping": str(artifact_root / CONVERSION_DIR_NAME),
                },
                "artifacts": {
                    "input_rdl": _file_payload(
                        str(artifact_root / INPUT_DIR_NAME / _safe_artifact_file_name(file_name, "uploaded_report.rdl"))
                    ),
                    **_artifact_payloads(result if isinstance(result, dict) else {}),
                },
            },
        )

    return {
        "ok": True,
        "status": "completed",
        "report_name": file_name,
        "output_dir": str(output_dir),
        "artifact_workspace": {
            "root": _directory_payload(str(artifact_root)) if artifact_root is not None else {},
            "manifest": _file_payload(str(artifact_root / "artifact_manifest.json")) if artifact_root is not None else {},
        },
        "result": result,
        "artifacts": _artifact_payloads(result if isinstance(result, dict) else {}),
        "trace_steps": trace_steps,
    }


def _run_qlik_metadata_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    output_dir_value = str(payload.get("jobs_root") or payload.get("output_dir") or "").strip()
    jobs_root = _resolve_workspace_path(output_dir_value) if output_dir_value else QLIK_JOBS_DIR
    job_id = str(payload.get("job_id") or "").strip() or _new_qlik_job_id()

    common_kwargs = {
        "jobs_root": jobs_root,
        "job_id": job_id,
        "qlik_endpoint": str(payload.get("qlik_endpoint") or "ws://localhost:4848/app").strip(),
        "qlik_apps_dir": str(payload.get("qlik_apps_dir") or "").strip(),
        "qlik_user_directory": str(payload.get("qlik_user_directory") or "").strip(),
        "qlik_user_id": str(payload.get("qlik_user_id") or "").strip(),
        "qlik_session_cookie": str(payload.get("qlik_session_cookie") or "").strip(),
    }

    content_base64 = str(payload.get("content_base64") or payload.get("file_base64") or "").strip()
    try:
        if content_base64:
            file_name = str(payload.get("file_name") or "uploaded.qvf").strip() or "uploaded.qvf"
            result = run_uploaded_qlik_metadata_job(
                file_name=file_name,
                file_bytes=_decode_base64_file(content_base64),
                **common_kwargs,
            )
        else:
            qvf_path = str(payload.get("qvf_path") or "").strip()
            if not qvf_path:
                raise ValueError("Upload a QVF file or provide qvf_path.")
            result = run_qlik_metadata_job(source_qvf_path=qvf_path, **common_kwargs)
    except Exception as exc:
        result = _write_failed_qlik_job(jobs_root=jobs_root, job_id=job_id, exc=exc)

    return {
        "ok": True,
        "status": result.get("status", "completed"),
        "job_id": result.get("job_id", ""),
        "app_id": result.get("app_id", ""),
        "client": result.get("client", ""),
        "extraction_mode": result.get("extraction_mode", ""),
        "error": result.get("error", ""),
        "job_dir": str(jobs_root / str(result.get("job_id", ""))),
        "result": result,
        "summary": result.get("summary", {}),
        "artifacts": _artifact_payloads(result if isinstance(result, dict) else {}),
        "trace_steps": result.get("trace_steps", []),
    }


def _new_qlik_job_id() -> str:
    return f"qlik_{_timestamp_token()}_{uuid4().hex[:8]}"


def _write_failed_qlik_job(jobs_root: Path, job_id: str, exc: Exception) -> dict[str, Any]:
    job_dir = jobs_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": False,
        "status": "failed",
        "job_id": job_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "error": str(exc),
        "error_type": type(exc).__name__,
        "trace_steps": [
            f"Qlik metadata job created ({job_id})",
            f"Qlik metadata job failed ({type(exc).__name__}: {exc})",
        ],
        "job": str(job_dir / "job.json"),
    }
    (job_dir / "job.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return payload


def _decode_base64_file(value: str) -> bytes:
    encoded = str(value or "").strip()
    if "," in encoded and encoded.lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Uploaded QVF content is not valid base64.") from exc


def _apply_rdl_editor_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    file_name = str(payload.get("file_name") or "uploaded_report.rdl")
    content = str(payload.get("content") or "")
    instruction = str(payload.get("instruction") or "").strip()
    if not content.strip():
        raise ValueError("Uploaded RDL content is empty.")
    if not instruction:
        raise ValueError("RDL edit instruction is required.")

    use_llm_config = bool(payload.get("use_llm_config", False))
    config_path = Path(str(payload.get("config_path") or _preferred_config_path())) if use_llm_config else None
    output_dir = OUTPUT_DIR / "rdl_ai_editor" / f"{_timestamp_token()}_{_safe_file_stem(file_name)}"
    output_dir.mkdir(parents=True, exist_ok=True)

    llm_client = load_llm_from_config(config_path) if config_path is not None else None
    patch_raw = nlp_agent(query=instruction, llm=llm_client)
    patch_validated = validate_patch(patch_raw)
    apply_result = apply_patch_to_rdl(rdl_xml=content, patch_json=patch_validated)
    log_path = write_patch_log(
        log_dir=OUTPUT_DIR / "rdl_ai_editor_logs",
        payload={
            "source_name": file_name,
            "query": instruction,
            "patch_raw": patch_raw,
            "patch_validated": patch_validated,
            "operation_logs": apply_result.logs,
        },
    )

    modified_path = output_dir / f"{_safe_file_stem(file_name)}_modified.rdl"
    patch_path = output_dir / "patch_validated.json"
    raw_patch_path = output_dir / "patch_raw.json"
    modified_path.write_text(apply_result.modified_xml, encoding="utf-8")
    patch_path.write_text(json.dumps(patch_validated, indent=2, ensure_ascii=True), encoding="utf-8")
    raw_patch_path.write_text(json.dumps(patch_raw, indent=2, ensure_ascii=True), encoding="utf-8")

    return {
        "ok": True,
        "status": "completed",
        "report_name": file_name,
        "instruction": instruction,
        "modified_xml": apply_result.modified_xml,
        "patch_raw": patch_raw,
        "patch_validated": patch_validated,
        "operation_logs": list(apply_result.logs),
        "artifacts": {
            "modified_rdl": _file_payload(str(modified_path)),
            "patch_validated": _file_payload(str(patch_path)),
            "patch_raw": _file_payload(str(raw_patch_path)),
            "log": _file_payload(str(log_path)),
        },
    }


def _reset_endpoint(_payload: dict[str, Any]) -> dict[str, Any]:
    return _reset_app_state()


def _validate_schema_endpoint(_payload: dict[str, Any]) -> dict[str, Any]:
    return _validate_schema()


POST_HANDLERS = {
    "/api/reset": _reset_endpoint,
    "/api/rdl/parse": _parse_rdl,
    "/api/dataset/select": _select_dataset,
    "/api/model/analyze": _analyze_model,
    "/api/schema/validate": _validate_schema_endpoint,
    "/api/visual/map": _map_visual_content,
    "/api/twb/generate": _generate_twb,
    "/api/tableau/publish": _publish_tableau,
    "/api/quality/compare": _compare_quality_endpoint,
    "/api/conversion/run": _run_conversion_endpoint,
    "/api/qlik/metadata/run": _run_qlik_metadata_endpoint,
    "/api/rdl-editor/apply": _apply_rdl_editor_endpoint,
}


def _route_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return dispatch_post(path, payload, POST_HANDLERS, STATE_LOCK)


class ReactSqlModelHandler(BaseHTTPRequestHandler):
    server_version = "ReactSqlModel/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/state":
                with STATE_LOCK:
                    _send_json(self, _state_snapshot())
                return
            if parsed.path == "/api/file":
                self._serve_file_download(parsed.query)
                return
            if parsed.path == "/api/consumer-workbook/download":
                self._serve_consumer_workbook_download()
                return
            if parsed.path == "/schema-flow" or parsed.path.startswith("/schema-flow/"):
                self._serve_schema_flow_static(parsed.path)
                return
            self._serve_static(parsed.path)
        except Exception as exc:
            _send_error_json(self, exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = _decode_request_json(self)
            result = _route_post(parsed.path, payload)
            _send_json(self, result)
        except ValueError as exc:
            _send_error_json(self, exc, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            _send_error_json(self, exc)

    def _serve_static(self, request_path: str) -> None:
        static_dir = _frontend_static_dir()
        if request_path in {"", "/"}:
            path = _frontend_index_path()
        else:
            relative = unquote(request_path.lstrip("/"))
            path = (static_dir / relative).resolve()
            if not str(path).lower().startswith(str(static_dir.resolve()).lower()):
                raise FileNotFoundError("Invalid static path.")
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _serve_schema_flow_static(self, request_path: str) -> None:
        if request_path in {"/schema-flow", "/schema-flow/"}:
            path = SCHEMA_FLOW_FRONTEND_DIR / "index.html"
        else:
            relative = unquote(request_path.removeprefix("/schema-flow/"))
            path = (SCHEMA_FLOW_FRONTEND_DIR / relative).resolve()
            if not str(path).lower().startswith(str(SCHEMA_FLOW_FRONTEND_DIR.resolve()).lower()):
                raise FileNotFoundError("Invalid schema flow static path.")
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _serve_consumer_workbook_download(self) -> None:
        path_value = _consumer_workbook_path_value()
        if not path_value:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = Path(path_value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not _is_safe_download_path(path):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        raw = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _serve_file_download(self, query: str) -> None:
        values = parse_qs(query)
        raw_path = values.get("path", [""])[0]
        path = Path(unquote(raw_path))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not _is_safe_download_path(path):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        raw = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the React SQL Model Assistant interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("SQL_MODEL_REACT_PORT", "5177")))
    args = parser.parse_args()

    index_path = _frontend_index_path()
    if not index_path.exists():
        raise FileNotFoundError(f"React frontend not found: {index_path}")

    server = SqlModelThreadingHTTPServer((args.host, args.port), ReactSqlModelHandler)
    print(f"React SQL Model Assistant: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
