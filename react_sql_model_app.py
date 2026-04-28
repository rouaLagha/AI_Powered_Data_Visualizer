from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import streamlit as st

from src.rdl_to_twb.rdl_parser import parse_rdl_file
from sql_model_assistant_app import (
    DEFAULT_LLM_CONFIG,
    FALLBACK_LLM_CONFIG,
    OUTPUT_TEMPLATE_COPY_PATH,
    OUTPUT_TEMPLATE_PATH,
    ROOT_DIR,
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
    _tableau_format_user_publish_failure,
    _template_default_path,
)


FRONTEND_DIR = ROOT_DIR / "frontend" / "sql_model_react"
DIST_DIR = FRONTEND_DIR / "dist"
INDEX_PATH = FRONTEND_DIR / "index.html"
SCHEMA_FLOW_FRONTEND_DIR = ROOT_DIR / "src" / "schema_flow_component" / "frontend"
OUTPUT_DIR = ROOT_DIR / "output"
STATE_LOCK = Lock()


class SqlModelThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _initial_app_state() -> dict[str, Any]:
    return {
        "report": {},
        "report_name": "",
        "selected_dataset_name": "",
        "selected_datasource_name": "",
        "sql_query": "",
        "conversation": [],
        "latest_model": {},
        "validated_model": {},
        "generated_twb_path": "",
        "generated_twb_name": "validated_semantic_model.twb",
        "publish_context": {},
        "publish_report": {},
        "publish_error": "",
        "consumer_workbook_path": "",
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
        root = ROOT_DIR.resolve(strict=True)
    except OSError:
        return False
    return str(resolved).lower().startswith(str(root).lower())


def _file_payload(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT_DIR / path
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
        "credentials_ready": bool(defaults.get("password") or defaults.get("pat_secret")),
        "pat_name_configured": bool(defaults.get("pat_name")),
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


def _consumer_workbook_payload() -> dict[str, Any]:
    path_value = str(APP_STATE.get("consumer_workbook_path") or "").strip()
    publish_report = APP_STATE.get("publish_report", {})
    if not path_value and isinstance(publish_report, dict):
        path_value = str(publish_report.get("consumer_workbook_path") or "").strip()
        if not path_value:
            saved_artifacts = publish_report.get("saved_publish_artifacts")
            if isinstance(saved_artifacts, dict):
                path_value = str(saved_artifacts.get("linked_workbook") or "").strip()
    return _file_payload(path_value)


def _state_snapshot() -> dict[str, Any]:
    report = APP_STATE.get("report", {})
    model = APP_STATE.get("latest_model", {})
    validated_model = APP_STATE.get("validated_model", {})
    return {
        "ok": True,
        "report_name": APP_STATE.get("report_name", ""),
        "report_summary": _summarize_report(report if isinstance(report, dict) else {}),
        "selected_dataset_name": APP_STATE.get("selected_dataset_name", ""),
        "selected_datasource_name": APP_STATE.get("selected_datasource_name", ""),
        "sql_query": APP_STATE.get("sql_query", ""),
        "conversation": APP_STATE.get("conversation", []),
        "latest_model": model if isinstance(model, dict) else {},
        "latest_model_summary": _model_summary(model if isinstance(model, dict) else {}),
        "schema_validated": bool(validated_model),
        "generated_twb": _file_payload(str(APP_STATE.get("generated_twb_path") or "")),
        "generated_twb_name": APP_STATE.get("generated_twb_name", "validated_semantic_model.twb"),
        "publish_context_summary": _publish_context_summary(),
        "publish_report": APP_STATE.get("publish_report", {}),
        "publish_error": APP_STATE.get("publish_error", ""),
        "consumer_workbook": _consumer_workbook_payload(),
        "defaults": {
            "config_path": str(_preferred_config_path()),
            "template_path": _template_default_path(),
            "output_name": "validated_semantic_model.twb",
            "tableau": _tableau_defaults_payload(),
        },
    }


def _parse_rdl(payload: dict[str, Any]) -> dict[str, Any]:
    file_name = str(payload.get("file_name") or "uploaded_report.rdl")
    content = str(payload.get("content") or "")
    if not content.strip():
        raise ValueError("Uploaded RDL content is empty.")

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

    APP_STATE.update(
        {
            "report": report,
            "report_name": file_name,
            "selected_dataset_name": selected_name,
            "selected_datasource_name": str(datasource.get("name", "") or "") if isinstance(datasource, dict) else "",
            "sql_query": query,
            "conversation": [],
            "latest_model": {},
            "validated_model": {},
            "generated_twb_path": "",
            "publish_context": {},
            "publish_report": {},
            "publish_error": "",
            "consumer_workbook_path": "",
        }
    )
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
    APP_STATE["generated_twb_path"] = ""
    APP_STATE["publish_context"] = {}
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
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
    elif follow_up:
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
    APP_STATE["generated_twb_path"] = ""
    APP_STATE["publish_context"] = {}
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
    return _state_snapshot()


def _validate_schema() -> dict[str, Any]:
    latest_model = APP_STATE.get("latest_model", {})
    if not isinstance(latest_model, dict) or not latest_model:
        raise ValueError("No model is available to validate.")
    APP_STATE["validated_model"] = copy.deepcopy(latest_model)
    return _state_snapshot()


def _generate_twb(payload: dict[str, Any]) -> dict[str, Any]:
    validated_model = APP_STATE.get("validated_model", {})
    if not isinstance(validated_model, dict) or not validated_model:
        raise ValueError("Validate the schema before generating the TWB.")

    datasource, dataset = _selected_context()
    if not datasource or not dataset:
        raise ValueError("RDL datasource/dataset context is missing.")

    template_path = str(payload.get("template_path") or _template_default_path())
    output_name = _safe_output_twb_name(str(payload.get("output_name") or "validated_semantic_model.twb"))
    template_xml = _load_template_xml(None, template_path)
    generated_xml = _generate_twb_from_validated_model(
        template_xml=template_xml,
        data_source=datasource,
        dataset=dataset,
        validated_model=validated_model,
    )
    output_path = OUTPUT_DIR / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generated_xml, encoding="utf-8")
    publish_context = _build_tableau_publish_context(
        template_xml=template_xml,
        data_source=datasource,
        dataset=dataset,
        validated_model=validated_model,
    )

    APP_STATE["generated_twb_path"] = str(output_path)
    APP_STATE["generated_twb_name"] = output_name
    APP_STATE["publish_context"] = publish_context
    APP_STATE["publish_report"] = {}
    APP_STATE["publish_error"] = ""
    APP_STATE["consumer_workbook_path"] = ""
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
    st.session_state.sql_model_assistant_tableau_server_url = pick("server_url")
    st.session_state.sql_model_assistant_tableau_site_content_url = pick("site_content_url")
    st.session_state.sql_model_assistant_tableau_project_name = pick("project_name", "Default")
    st.session_state.sql_model_assistant_tableau_username = pick("username")
    st.session_state.sql_model_assistant_tableau_password = pick("password")
    st.session_state.sql_model_assistant_tableau_source_datasource_name = pick("source_datasource_name")
    st.session_state.sql_model_assistant_tableau_datasource_publish_mode = pick("datasource_publish_mode")
    st.session_state.sql_model_assistant_tableau_auth_method = pick("auth_method", "username_password")
    st.session_state.sql_model_assistant_tableau_empty_workbook_template_path = pick(
        "empty_workbook_template_path"
    )
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path = pick("visual_source_twb_path")
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
    except Exception as exc:
        APP_STATE["publish_report"] = {}
        APP_STATE["publish_error"] = _tableau_format_user_publish_failure(exc)
        APP_STATE["consumer_workbook_path"] = ""
    return _state_snapshot()


def _route_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with STATE_LOCK:
        if path == "/api/reset":
            return _reset_app_state()
        if path == "/api/rdl/parse":
            return _parse_rdl(payload)
        if path == "/api/dataset/select":
            return _select_dataset(payload)
        if path == "/api/model/analyze":
            return _analyze_model(payload)
        if path == "/api/schema/validate":
            return _validate_schema()
        if path == "/api/twb/generate":
            return _generate_twb(payload)
        if path == "/api/tableau/publish":
            return _publish_tableau(payload)
    raise ValueError(f"Unknown endpoint: {path}")


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

    def _serve_file_download(self, query: str) -> None:
        values = parse_qs(query)
        raw_path = values.get("path", [""])[0]
        path = Path(unquote(raw_path))
        if not path.is_absolute():
            path = ROOT_DIR / path
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
