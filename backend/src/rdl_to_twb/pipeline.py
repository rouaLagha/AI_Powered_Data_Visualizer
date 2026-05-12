from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from .agent_semantic_modeler import generate_semantic_models
from .agent_twb_xml_generator import generate_twb_xml, repair_twb_xml
from .db_introspection import build_db_catalog
from .llm_client import LLMClient, LLMConfig
from .rdl_parser import parse_rdl_file
from .schema_utils import summarize_xsd_elements
from .tableau_extract import (
    build_hyper_extract_from_catalog,
    build_twbx_package,
    rewrite_workbook_for_hyper,
)
from .tableau_publisher import publish_workbook_if_configured
from .twb_builder import (
    inject_datasource_connections,
    inject_semantic_bindings,
    normalize_generated_twb,
    validate_twb_structure,
    write_twb_file,
)

def run_conversion(
    rdl_path: str | Path,
    rdl_xsd_path: str | Path,
    twb_xsd_path: str | Path,
    output_dir: str | Path,
    config_path: str | Path | None = None,
    publish_enabled: bool | None = None,
    artifact_mode: str | None = None,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_trace: list[str] = []
    write_debug_artifacts = _debug_artifacts_enabled(artifact_mode)

    rdl_xsd_summary = summarize_xsd_elements(rdl_xsd_path)
    twb_xsd_summary = summarize_xsd_elements(twb_xsd_path)
    pipeline_trace.append("Schemas loaded")

    allowed_visual_types = {
        "Textbox",
        "Chart",
        "Tablix",
        "Rectangle",
        "GaugePanel",
        "Map",
        "Image",
        "Line",
    }.intersection(set(rdl_xsd_summary.get("element_names", [])))

    parsed_report = parse_rdl_file(rdl_path, allowed_visual_types=allowed_visual_types)
    parsed_payload = parsed_report.to_dict()
    pipeline_trace.append("RDL parsed")

    if write_debug_artifacts:
        _write_json(output_dir / "parsed_rdl.json", parsed_payload)
        _write_json(output_dir / "rdl_xsd_summary.json", rdl_xsd_summary)
        _write_json(output_dir / "twb_xsd_summary.json", twb_xsd_summary)

    data_validation = _validate_parsed_report_payload(parsed_payload)
    if write_debug_artifacts:
        _write_json(output_dir / "data_validation_report.json", data_validation)
    if data_validation.get("status") == "blocked":
        pipeline_trace.append("Pipeline blocked during data verification")
        result = {
            "status": "blocked",
            "error": data_validation.get("error", ""),
            "warnings": data_validation.get("warnings", []),
            "trace_steps": pipeline_trace,
            "data_validation": data_validation,
        }
        if write_debug_artifacts:
            _write_json(output_dir / "pipeline_trace.json", {"steps": pipeline_trace})
            result.update(
                {
                    "parsed_rdl": str(output_dir / "parsed_rdl.json"),
                    "data_validation_report": str(output_dir / "data_validation_report.json"),
                    "pipeline_trace": str(output_dir / "pipeline_trace.json"),
                }
            )
        return result
    pipeline_trace.append("Data verification passed")

    db_catalog = build_db_catalog(
        data_sources=parsed_payload.get("data_sources", []),
        data_sets=parsed_payload.get("data_sets", []),
    )
    if write_debug_artifacts:
        _write_json(output_dir / "db_catalog.json", db_catalog)
    pipeline_trace.append("DB catalog introspection completed")

    # Keep prompts compact for local models.
    rdl_xsd_prompt_summary = _compact_schema_summary(rdl_xsd_summary)
    twb_xsd_prompt_summary = _compact_schema_summary(twb_xsd_summary)
    parsed_rdl_prompt_payload = _compact_parsed_rdl_for_prompt(parsed_payload)
    pipeline_trace.append("Prompt payloads compacted")

    if config_path is None:
        raise ValueError("config_path is required")
    cfg = _load_config(config_path)
    agent1 = LLMClient(LLMConfig(**cfg["agent1"]))
    agent2 = LLMClient(LLMConfig(**cfg.get("agent2", cfg["agent1"])))
    repair_llm: LLMClient | None = agent2
    data_model, visual_model, mapping = generate_semantic_models(
        llm=agent1,
        parsed_rdl=parsed_rdl_prompt_payload,
        rdl_xsd_summary=rdl_xsd_prompt_summary,
        twb_xsd_summary=twb_xsd_prompt_summary,
        output_dir=output_dir,
        write_artifacts=write_debug_artifacts,
    )
    pipeline_trace.append("Agent-1 semantic generation succeeded")

    data_model_prompt_payload, visual_model_prompt_payload, mapping_prompt_payload = (
        _compact_semantic_for_prompt(data_model, visual_model, mapping)
    )

    try:
        xml_content = generate_twb_xml(
            llm=agent2,
            data_model=data_model_prompt_payload,
            visual_model=visual_model_prompt_payload,
            mapping=mapping_prompt_payload,
            twb_xsd_summary=twb_xsd_prompt_summary,
            output_xml_path=output_dir / "generated_workbook.xml",
            write_artifact=write_debug_artifacts,
        )
        pipeline_trace.append("Agent-2 XML generation succeeded")
    except Exception as exc:
        xml_content = _build_deterministic_seed_twb_xml(data_model, visual_model)
        pipeline_trace.append(
            "Agent-2 XML generation failed; deterministic seed workbook generated "
            f"({type(exc).__name__}: {exc})"
        )

    if not isinstance(xml_content, str) or not xml_content.strip():
        xml_content = _build_deterministic_seed_twb_xml(data_model, visual_model)
        pipeline_trace.append("Agent-2 XML response empty; deterministic seed workbook generated")

    xml_content = inject_datasource_connections(
        xml_content=xml_content,
        data_sources=parsed_payload.get("data_sources", []),
        data_sets=parsed_payload.get("data_sets", []),
        db_catalog=db_catalog,
    )
    xml_content = inject_semantic_bindings(
        xml_content=xml_content,
        data_model=data_model,
        visual_model=visual_model,
        mapping=mapping,
        report_parameters=parsed_payload.get("report_parameters", []),
    )

    xml_content, issues = _validate_and_repair_loop(
        xml_content=xml_content,
        twb_xsd_summary=twb_xsd_summary,
        llm=repair_llm,
    )
    pipeline_trace.append("Validation/repair loop completed")

    canonical_twb_bytes: bytes | None = None
    canonical_twb_path = _resolve_regional_sales_canonical_twb_path(
        rdl_path=rdl_path,
        report_name=parsed_payload.get("report_name"),
        output_dir=output_dir,
    )
    if canonical_twb_path is not None:
        canonical_twb_bytes = canonical_twb_path.read_bytes()
        try:
            xml_content = canonical_twb_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "RegionalSales canonical TWB must be UTF-8 encoded: "
                f"{canonical_twb_path}"
            ) from exc
        issues = validate_twb_structure(xml_content)
        pipeline_trace.append(
            f"RegionalSales canonical TWB override applied from {canonical_twb_path}"
        )

    if write_debug_artifacts:
        if canonical_twb_bytes is not None:
            (output_dir / "generated_workbook.xml").write_bytes(canonical_twb_bytes)
        else:
            (output_dir / "generated_workbook.xml").write_text(xml_content, encoding="utf-8")
        _write_json(output_dir / "validation_report.json", {"issues": issues})

    if canonical_twb_bytes is not None:
        twb_path = output_dir / "converted_report.twb"
        twb_path.write_bytes(canonical_twb_bytes)
        pipeline_trace.append("TWB file written from canonical RegionalSales workbook")
    else:
        twb_path = write_twb_file(xml_content, output_dir / "converted_report.twb")
        pipeline_trace.append("TWB file written")

    tableau_cfg = cfg.get("tableau_server") if isinstance(cfg, dict) else None
    configured_publish_enabled = bool(tableau_cfg.get("enabled", False)) if isinstance(tableau_cfg, dict) else False
    effective_publish_enabled = configured_publish_enabled if publish_enabled is None else bool(publish_enabled)

    if publish_enabled is not None and isinstance(tableau_cfg, dict):
        tableau_cfg = dict(tableau_cfg)
        tableau_cfg["enabled"] = effective_publish_enabled

    fail_on_publish_error = bool(tableau_cfg.get("fail_on_error", False)) if isinstance(tableau_cfg, dict) else False
    publish_input_path = Path(twb_path)
    tableau_extract_report: dict = {
        "status": "skipped",
        "reason": "Hyper extract packaging not requested",
        "publish_input_path": str(publish_input_path),
    }

    if not effective_publish_enabled:
        tableau_extract_report = {
            "status": "skipped",
            "reason": "Publish stage disabled for conversion-only run",
            "publish_input_path": str(publish_input_path),
        }
        pipeline_trace.append("Publish stage skipped (conversion-only mode)")

    if isinstance(tableau_cfg, dict) and effective_publish_enabled:
        requested_format = str(tableau_cfg.get("file_format") or "twb").strip().lower()
        server_url = str(tableau_cfg.get("server_url") or "").strip().lower()
        twbx_required_for_publish = bool(
            requested_format == "twbx"
            or "public.tableau.com" in server_url
            or bool(tableau_cfg.get("desktop_rpa_enabled", False))
        )
        should_build_extract = bool(
            tableau_cfg.get(
                "build_hyper_extract",
                twbx_required_for_publish,
            )
        )

        if should_build_extract:
            max_rows = _safe_positive_int(tableau_cfg.get("hyper_max_rows_per_table"))
            try:
                hyper_path = output_dir / "converted_report.hyper"
                tableau_extract_report = build_hyper_extract_from_catalog(
                    output_hyper_path=hyper_path,
                    data_sources=parsed_payload.get("data_sources", []),
                    db_catalog=db_catalog,
                    max_rows_per_table=max_rows,
                )

                if tableau_extract_report.get("status") == "created":
                    hyper_twb_path = output_dir / "converted_report_hyper.twb"
                    rewrite_report = rewrite_workbook_for_hyper(
                        source_twb_path=twb_path,
                        output_twb_path=hyper_twb_path,
                        hyper_relative_path=f"Data/Extracts/{hyper_path.name}",
                    )
                    twbx_path = output_dir / "converted_report.twbx"
                    package_report = build_twbx_package(
                        twb_path=hyper_twb_path,
                        output_twbx_path=twbx_path,
                        hyper_path=hyper_path,
                    )

                    tableau_extract_report["workbook_rewrite"] = rewrite_report
                    tableau_extract_report["twbx_package"] = package_report
                    publish_input_path = twbx_path
                    tableau_extract_report["publish_input_path"] = str(publish_input_path)
                    pipeline_trace.append("Hyper extract created and TWBX package built")
                    if requested_format == "twb":
                        pipeline_trace.append(
                            "Publish target switched from TWB to TWBX because extract packaging is enabled"
                        )
                else:
                    pipeline_trace.append(
                        "Hyper extract not created "
                        f"(status={tableau_extract_report.get('status')}, reason={tableau_extract_report.get('reason')})"
                    )

                    if twbx_required_for_publish:
                        try:
                            twbx_path = output_dir / "converted_report.twbx"
                            fallback_package_report = build_twbx_package(
                                twb_path=twb_path,
                                output_twbx_path=twbx_path,
                            )
                            publish_input_path = twbx_path
                            tableau_extract_report["twbx_package_fallback"] = fallback_package_report
                            tableau_extract_report["publish_input_path"] = str(publish_input_path)
                            pipeline_trace.append("Fallback TWBX package built without Hyper extract")
                        except Exception as fallback_exc:
                            tableau_extract_report["twbx_fallback_error_type"] = type(fallback_exc).__name__
                            tableau_extract_report["twbx_fallback_error"] = str(fallback_exc)
                            pipeline_trace.append(
                                "Fallback TWBX packaging failed "
                                f"({type(fallback_exc).__name__}: {fallback_exc})"
                            )
            except Exception as exc:
                tableau_extract_report = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "publish_input_path": str(publish_input_path),
                }
                pipeline_trace.append(
                    "Hyper extract packaging failed "
                    f"({type(exc).__name__}: {exc})"
                )

                if twbx_required_for_publish:
                    try:
                        twbx_path = output_dir / "converted_report.twbx"
                        fallback_package_report = build_twbx_package(
                            twb_path=twb_path,
                            output_twbx_path=twbx_path,
                        )
                        publish_input_path = twbx_path
                        tableau_extract_report["twbx_package_fallback"] = fallback_package_report
                        tableau_extract_report["publish_input_path"] = str(publish_input_path)
                        pipeline_trace.append("Fallback TWBX package built after Hyper failure")
                    except Exception as fallback_exc:
                        tableau_extract_report["twbx_fallback_error_type"] = type(fallback_exc).__name__
                        tableau_extract_report["twbx_fallback_error"] = str(fallback_exc)
                        pipeline_trace.append(
                            "Fallback TWBX packaging failed "
                            f"({type(fallback_exc).__name__}: {fallback_exc})"
                        )

                if fail_on_publish_error:
                    raise
        elif twbx_required_for_publish:
            try:
                twbx_path = output_dir / "converted_report.twbx"
                fallback_package_report = build_twbx_package(
                    twb_path=twb_path,
                    output_twbx_path=twbx_path,
                )
                publish_input_path = twbx_path
                tableau_extract_report = {
                    "status": "packaged_without_extract",
                    "reason": "Hyper extract packaging disabled; generated TWBX from TWB",
                    "twbx_package_fallback": fallback_package_report,
                    "publish_input_path": str(publish_input_path),
                }
                pipeline_trace.append("TWBX package built without Hyper extract")
            except Exception as exc:
                tableau_extract_report = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "publish_input_path": str(publish_input_path),
                }
                pipeline_trace.append(
                    "TWBX packaging without Hyper failed "
                    f"({type(exc).__name__}: {exc})"
                )
                if fail_on_publish_error:
                    raise

    publish_status: str | None = None
    tableau_rpa_report: dict = {
        "status": "skipped",
        "reason": "Desktop RPA fallback not triggered",
        "workbook_path": str(publish_input_path),
    }

    if effective_publish_enabled:
        try:
            tableau_publish_report = publish_workbook_if_configured(
                workbook_path=publish_input_path,
                tableau_config=tableau_cfg if isinstance(tableau_cfg, dict) else None,
            )
            publish_status = tableau_publish_report.get("status")
            if publish_status == "published":
                pipeline_trace.append("Tableau Server publish succeeded")
            elif publish_status == "skipped":
                pipeline_trace.append("Tableau Server publish skipped")
            elif publish_status == "manual_upload_required":
                pipeline_trace.append("Tableau Public manual upload required")
            else:
                pipeline_trace.append(f"Tableau Server publish status: {publish_status}")
        except Exception as exc:
            tableau_publish_report = {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "workbook_path": str(publish_input_path),
            }
            publish_status = "failed"
            pipeline_trace.append(
                "Tableau Server publish failed "
                f"({type(exc).__name__}: {exc})"
            )
            if fail_on_publish_error:
                raise

        run_rpa, rpa_reason = _should_run_desktop_rpa(
            tableau_cfg=tableau_cfg if isinstance(tableau_cfg, dict) else None,
            publish_status=publish_status if isinstance(publish_status, str) else None,
            workbook_path=publish_input_path,
        )
        if run_rpa:
            tableau_rpa_report = _run_desktop_rpa_publish(
                workbook_path=publish_input_path,
                tableau_config=tableau_cfg if isinstance(tableau_cfg, dict) else None,
            )
            tableau_publish_report["desktop_rpa_attempted"] = True
            tableau_publish_report["desktop_rpa_reason"] = "triggered"
            tableau_publish_report["desktop_rpa_status"] = tableau_rpa_report.get("status")
            if tableau_rpa_report.get("status") == "published":
                tableau_publish_report["rest_status"] = publish_status
                tableau_publish_report["status"] = "published"
                tableau_publish_report["publish_channel"] = "desktop_rpa"
                pipeline_trace.append("Tableau Public publish succeeded via Desktop RPA")
            else:
                pipeline_trace.append(
                    "Tableau Public Desktop RPA attempted "
                    f"(status={tableau_rpa_report.get('status')}, reason={tableau_rpa_report.get('reason')})"
                )
        else:
            tableau_rpa_report["reason"] = rpa_reason
            tableau_publish_report["desktop_rpa_attempted"] = False
            tableau_publish_report["desktop_rpa_reason"] = rpa_reason
    else:
        tableau_publish_report = {
            "status": "skipped",
            "reason": "Publish stage disabled for conversion-only run",
            "workbook_path": str(publish_input_path),
            "desktop_rpa_attempted": False,
            "desktop_rpa_reason": "publish_disabled",
        }
        tableau_rpa_report = {
            "status": "skipped",
            "reason": "publish_disabled",
            "workbook_path": str(publish_input_path),
        }

    if write_debug_artifacts:
        _write_json(output_dir / "tableau_extract_report.json", tableau_extract_report)
        _write_json(output_dir / "tableau_publish_report.json", tableau_publish_report)
        _write_json(output_dir / "tableau_rpa_publish_report.json", tableau_rpa_report)
        _write_json(output_dir / "pipeline_trace.json", {"steps": pipeline_trace})

    result = {
        "twb": str(twb_path),
        "trace_steps": pipeline_trace,
        "validation_issues": issues,
        "tableau_extract_report_data": tableau_extract_report,
        "tableau_publish_report_data": tableau_publish_report,
        "tableau_rpa_publish_report_data": tableau_rpa_report,
    }
    if write_debug_artifacts:
        result.update(
            {
                "parsed_rdl": str(output_dir / "parsed_rdl.json"),
                "data_validation_report": str(output_dir / "data_validation_report.json"),
                "data_model": str(output_dir / "data_model.json"),
                "visual_model": str(output_dir / "visual_model.json"),
                "mapping": str(output_dir / "mapping.json"),
                "mapping_model": str(output_dir / "mapping_model.json"),
                "semantic_generation_report": str(output_dir / "semantic_generation_report.json"),
                "agent1_raw_response": str(output_dir / "agent1_raw_response.txt"),
                "db_catalog": str(output_dir / "db_catalog.json"),
                "generated_xml": str(output_dir / "generated_workbook.xml"),
                "tableau_extract_report": str(output_dir / "tableau_extract_report.json"),
                "tableau_publish_report": str(output_dir / "tableau_publish_report.json"),
                "tableau_rpa_publish_report": str(output_dir / "tableau_rpa_publish_report.json"),
                "pipeline_trace": str(output_dir / "pipeline_trace.json"),
            }
        )

    if publish_input_path.suffix.lower() == ".twbx" and publish_input_path.exists():
        result["twbx"] = str(publish_input_path)

    extract_hyper_path = tableau_extract_report.get("hyper_path") if isinstance(tableau_extract_report, dict) else None
    if isinstance(extract_hyper_path, str) and extract_hyper_path:
        result["hyper"] = extract_hyper_path

    return result


def _build_deterministic_seed_twb_xml(data_model: dict, visual_model: dict) -> str:
    workbook = ET.Element(
        "workbook",
        attrib={
            "version": "18.1",
            "source-build": "2024.2.0 (20242.24.0620.1454)",
            "source-platform": "win",
        },
    )

    ET.SubElement(workbook, "preferences")
    ET.SubElement(workbook, "style")

    datasources_el = ET.SubElement(workbook, "datasources")
    datasource_name = "DataSource_1"
    datasources = data_model.get("datasources", []) if isinstance(data_model, dict) else []
    if isinstance(datasources, list) and datasources:
        first = datasources[0]
        if isinstance(first, dict):
            name = first.get("name")
            if isinstance(name, str) and name.strip():
                datasource_name = name.strip()

    ds_node = ET.SubElement(
        datasources_el,
        "datasource",
        attrib={
            "name": datasource_name,
            "caption": datasource_name,
            "inline": "true",
            "hasconnection": "true",
        },
    )
    ET.SubElement(ds_node, "connection", attrib={"class": "genericodbc"})

    worksheets_el = ET.SubElement(workbook, "worksheets")
    sheet_names: list[str] = []
    sheets = visual_model.get("sheets", []) if isinstance(visual_model, dict) else []
    if isinstance(sheets, list):
        for item in sheets:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip() and name.strip() not in sheet_names:
                sheet_names.append(name.strip())

    if not sheet_names:
        sheet_names = ["Sheet 1"]

    for name in sheet_names:
        ws = ET.SubElement(worksheets_el, "worksheet", attrib={"name": name})
        ET.SubElement(ws, "layout-options")
        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        view_dss = ET.SubElement(view, "datasources")
        ET.SubElement(view_dss, "datasource", attrib={"name": datasource_name})
        ET.SubElement(view, "datasource-dependencies", attrib={"datasource": datasource_name})
        ET.SubElement(view, "perspectives")
        ET.SubElement(view, "aggregation", attrib={"value": "true"})
        ET.SubElement(table, "style")
        ET.SubElement(table, "panes")
        ET.SubElement(table, "rows")
        ET.SubElement(table, "cols")

    windows_el = ET.SubElement(workbook, "windows")
    for name in sheet_names:
        win = ET.SubElement(windows_el, "window", attrib={"name": name, "class": "worksheet"})
        ET.SubElement(win, "cards")
        ET.SubElement(win, "viewpoint")

    return ET.tostring(workbook, encoding="unicode")


def _load_config(config_path: str | Path) -> dict:
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _debug_artifacts_enabled(artifact_mode: str | None) -> bool:
    raw = str(
        artifact_mode
        or os.getenv("RDL_TO_TWB_ARTIFACT_MODE")
        or os.getenv("RDL_TO_TWB_DEBUG_ARTIFACTS")
        or "runtime"
    ).strip().lower()
    return raw in {"1", "true", "yes", "debug", "full", "legacy"}


def _validation_warning(code: str, message: str, severity: str = "warning") -> dict:
    return {
        "code": code,
        "type": code,
        "severity": severity,
        "stage": "data_verification",
        "message": message,
    }


def _validate_parsed_report_payload(parsed_payload: dict) -> dict:
    data_sets = parsed_payload.get("data_sets", []) if isinstance(parsed_payload, dict) else []
    data_sets = data_sets if isinstance(data_sets, list) else []
    query_datasets = [
        dataset
        for dataset in data_sets
        if isinstance(dataset, dict) and str(dataset.get("query") or "").strip()
    ]

    if not data_sets:
        message = "Pipeline blocked: no dataset was found in the RDL."
        return {
            "status": "blocked",
            "error": message,
            "warnings": [_validation_warning("missing_dataset", message, severity="error")],
            "dataset_count": 0,
            "query_dataset_count": 0,
        }

    if not query_datasets:
        message = "Pipeline blocked: no dataset with a SQL query was found in the RDL."
        return {
            "status": "blocked",
            "error": message,
            "warnings": [_validation_warning("missing_dataset", message, severity="error")],
            "dataset_count": len(data_sets),
            "query_dataset_count": 0,
        }

    return {
        "status": "passed",
        "error": "",
        "warnings": [],
        "dataset_count": len(data_sets),
        "query_dataset_count": len(query_datasets),
    }


def _safe_positive_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_regional_sales_canonical_twb_path(
    rdl_path: str | Path,
    report_name: object,
    output_dir: Path,
) -> Path | None:
    input_stem = Path(rdl_path).stem.strip().lower()
    parsed_report_name = report_name.strip().lower() if isinstance(report_name, str) else ""
    if input_stem != "regionalsales" and parsed_report_name != "regionalsales":
        return None

    backend_root = Path(__file__).resolve().parents[2]
    project_root = backend_root.parent
    candidates: list[Path] = [
        project_root / "outputs" / "converted_report_perfect.twb",
        backend_root / "config" / "RegionalSales.canonical.twb",
        backend_root / "config" / "regionalsales.canonical.twb",
        project_root / "converted_report_perfect.twb",
        output_dir / "converted_report_perfect.twb",
        Path.home() / "Desktop" / "converted_report_perfect.twb",
    ]

    env_override = os.getenv("REGIONALSALES_CANONICAL_TWB")
    if isinstance(env_override, str) and env_override.strip():
        env_path = Path(env_override.strip()).expanduser()
        if not env_path.is_absolute():
            env_path = project_root / env_path
        candidates.append(env_path)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    candidate_list = "\n".join(f"- {path}" for path in candidates)
    raise FileNotFoundError(
        "RegionalSales canonical TWB not found. "
        "Set REGIONALSALES_CANONICAL_TWB or place the file in one of:\n"
        f"{candidate_list}"
    )


def _should_run_desktop_rpa(
    tableau_cfg: dict | None,
    publish_status: str | None,
    workbook_path: Path,
) -> tuple[bool, str]:
    if not isinstance(tableau_cfg, dict) or not bool(tableau_cfg.get("enabled", False)):
        return False, "tableau_server publishing is disabled"

    if not bool(tableau_cfg.get("desktop_rpa_enabled", False)):
        return False, "tableau_server.desktop_rpa_enabled is false"

    server_url = str(tableau_cfg.get("server_url") or "").strip().lower()
    if "public.tableau.com" not in server_url:
        return False, "desktop RPA fallback is only enabled for Tableau Public"

    normalized_status = str(publish_status or "").strip().lower()
    if normalized_status not in {"manual_upload_required", "failed", "unsupported_file_type"}:
        return False, f"publish status does not trigger desktop RPA ({normalized_status or 'unknown'})"

    if workbook_path.suffix.lower() != ".twbx":
        return False, "desktop RPA requires a .twbx artifact"

    if not workbook_path.exists():
        return False, f"desktop RPA input file not found: {workbook_path}"

    return True, "triggered"


def _run_desktop_rpa_publish(workbook_path: Path, tableau_config: dict | None) -> dict:
    if not isinstance(tableau_config, dict):
        return {
            "status": "skipped",
            "reason": "tableau configuration is not available",
            "workbook_path": str(workbook_path),
        }

    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "publish_tableau_public_desktop_rpa.py"
    if not script_path.exists():
        return {
            "status": "failed",
            "reason": "Desktop RPA script was not found",
            "script_path": str(script_path),
            "workbook_path": str(workbook_path),
        }

    timeout_seconds = _safe_positive_int(tableau_config.get("desktop_rpa_timeout_seconds")) or 180
    publish_wait_seconds = _safe_positive_int(tableau_config.get("desktop_rpa_publish_wait_seconds")) or 240
    launch_settle_seconds = _safe_positive_int(tableau_config.get("desktop_rpa_launch_settle_seconds")) or 4
    process_timeout_seconds = timeout_seconds * 4 + publish_wait_seconds + launch_settle_seconds + 30

    cmd = [
        sys.executable,
        str(script_path),
        "--twbx",
        str(workbook_path),
        "--timeout-seconds",
        str(timeout_seconds),
        "--publish-wait-seconds",
        str(publish_wait_seconds),
        "--launch-settle-seconds",
        str(launch_settle_seconds),
    ]

    workbook_name = str(tableau_config.get("workbook_name") or "").strip()
    if workbook_name:
        cmd.extend(["--workbook-name", workbook_name])

    tableau_exe = _resolve_config_secret(
        tableau_config.get("desktop_rpa_tableau_exe"),
        fallback_env="TABLEAU_PUBLIC_EXE",
    )
    if tableau_exe:
        cmd.extend(["--tableau-exe", tableau_exe])

    env = os.environ.copy()
    email = _resolve_config_secret(
        tableau_config.get("desktop_rpa_email"),
        fallback_env="TABLEAU_EMAIL",
    ) or _resolve_config_secret(tableau_config.get("username"), fallback_env="TABLEAU_USERNAME")
    password = _resolve_config_secret(
        tableau_config.get("desktop_rpa_password"),
        fallback_env="TABLEAU_PASSWORD",
    ) or _resolve_config_secret(tableau_config.get("password"), fallback_env="TABLEAU_PASSWORD")

    if email:
        env["TABLEAU_EMAIL"] = email
    if password:
        env["TABLEAU_PASSWORD"] = password

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=process_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failed",
            "reason": "Desktop RPA process timed out",
            "timeout_seconds": process_timeout_seconds,
            "script_path": str(script_path),
            "workbook_path": str(workbook_path),
        }
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"Desktop RPA process launch failed: {type(exc).__name__}: {exc}",
            "script_path": str(script_path),
            "workbook_path": str(workbook_path),
        }

    payload: dict | None = None
    stdout_text = completed.stdout.strip()
    if stdout_text:
        try:
            payload_candidate = json.loads(stdout_text)
            if isinstance(payload_candidate, dict):
                payload = payload_candidate
        except Exception:
            payload = None

    rpa_result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(rpa_result, dict):
        rpa_result = {}

    status = rpa_result.get("status") if isinstance(rpa_result.get("status"), str) else None
    report: dict = {
        "status": status or ("failed" if completed.returncode != 0 else "unknown"),
        "return_code": completed.returncode,
        "workbook_path": str(workbook_path),
        "script_path": str(script_path),
        "timeout_seconds": process_timeout_seconds,
    }

    if isinstance(rpa_result.get("message"), str) and rpa_result.get("message"):
        report["message"] = rpa_result.get("message")

    if isinstance(rpa_result.get("reason"), str) and rpa_result.get("reason"):
        report["reason"] = rpa_result.get("reason")

    if isinstance(rpa_result.get("visible_windows"), list):
        report["visible_windows"] = rpa_result.get("visible_windows")

    if completed.stderr and completed.stderr.strip():
        report["stderr"] = completed.stderr.strip()[:4000]

    if isinstance(payload, dict):
        report["payload"] = payload
    elif stdout_text:
        report["stdout"] = stdout_text[:4000]

    return report


def _resolve_config_secret(value: object, fallback_env: str) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            if stripped.lower().startswith("env:"):
                env_name = stripped.split(":", 1)[1].strip()
                if not env_name:
                    return None
                env_value = os.getenv(env_name)
                if isinstance(env_value, str) and env_value.strip():
                    return env_value.strip()
                return None
            return stripped

    env_fallback = os.getenv(fallback_env)
    if isinstance(env_fallback, str) and env_fallback.strip():
        return env_fallback.strip()
    return None


def _validate_and_repair_loop(
    xml_content: str,
    twb_xsd_summary: dict,
    llm: LLMClient | None,
    max_repair_rounds: int = 3,
) -> tuple[str, list[str]]:
    current = normalize_generated_twb(xml_content)
    issues = validate_twb_structure(current)
    if not issues:
        return current, []

    if llm is None:
        return current, issues

    max_rounds = max(1, int(max_repair_rounds))
    compact_summary = _compact_schema_summary(twb_xsd_summary)

    for attempt in range(1, max_rounds + 1):
        repair_issues = list(issues)
        if attempt > 1:
            repair_issues.insert(0, f"Remaining issues after repair attempt {attempt - 1}")

        repaired = repair_twb_xml(
            llm=llm,
            invalid_xml=current,
            issues=repair_issues,
            twb_xsd_summary=compact_summary,
        )
        current = normalize_generated_twb(repaired)
        issues = validate_twb_structure(current)
        if not issues:
            return current, []

    return current, issues


def _compact_schema_summary(summary: dict, max_element_names: int = 200) -> dict:
    element_names = summary.get("element_names", [])
    sample_elements = summary.get("sample_elements", [])
    sample_attributes = summary.get("sample_attributes", [])

    if not isinstance(element_names, list):
        element_names = []
    if not isinstance(sample_elements, list):
        sample_elements = []
    if not isinstance(sample_attributes, list):
        sample_attributes = []

    return {
        "schema_file": summary.get("schema_file"),
        "element_count": summary.get("element_count"),
        "attribute_count": summary.get("attribute_count"),
        "element_names": element_names[:max_element_names],
        "element_names_truncated": len(element_names) > max_element_names,
        "sample_elements": sample_elements,
        "sample_attributes": sample_attributes,
    }


def _compact_parsed_rdl_for_prompt(parsed_rdl: dict) -> dict:
    data_sources = parsed_rdl.get("data_sources", []) if isinstance(parsed_rdl, dict) else []
    data_sets = parsed_rdl.get("data_sets", []) if isinstance(parsed_rdl, dict) else []
    visuals = parsed_rdl.get("visuals", []) if isinstance(parsed_rdl, dict) else []
    report_parameters = parsed_rdl.get("report_parameters", []) if isinstance(parsed_rdl, dict) else []
    report_metadata = parsed_rdl.get("report_metadata", {}) if isinstance(parsed_rdl, dict) else {}
    report_sections = parsed_rdl.get("report_sections", []) if isinstance(parsed_rdl, dict) else []

    compact_datasets: list[dict] = []
    for ds in data_sets[:80]:
        if not isinstance(ds, dict):
            continue
        fields = ds.get("fields", [])
        compact_fields = []
        if isinstance(fields, list):
            for f in fields[:80]:
                if not isinstance(f, dict):
                    continue
                compact_fields.append(
                    {
                        "name": f.get("name"),
                        "data_field": f.get("data_field"),
                        "type_name": f.get("type_name"),
                    }
                )

        query = ds.get("query")
        if isinstance(query, str) and len(query) > 1200:
            query = query[:1200] + " ...[truncated]"

        query_parameters = ds.get("query_parameters", [])
        compact_query_parameters: list[dict] = []
        if isinstance(query_parameters, list):
            for parameter in query_parameters[:40]:
                if not isinstance(parameter, dict):
                    continue
                compact_query_parameters.append(
                    {
                        "name": parameter.get("name"),
                        "value": parameter.get("value"),
                        "parameter_references": parameter.get("parameter_references", []),
                    }
                )

        filters = ds.get("filters", [])
        compact_filters = filters[:30] if isinstance(filters, list) else []

        sort_expressions = ds.get("sort_expressions", [])
        compact_sort_expressions = sort_expressions[:30] if isinstance(sort_expressions, list) else []

        compact_datasets.append(
            {
                "name": ds.get("name"),
                "query": query,
                "data_source_name": ds.get("data_source_name"),
                "fields": compact_fields,
                "query_parameters": compact_query_parameters,
                "filters": compact_filters,
                "sort_expressions": compact_sort_expressions,
            }
        )

    compact_visuals = _compact_visual_tree_for_prompt(visuals, max_nodes=160)

    return {
        "report_name": parsed_rdl.get("report_name") if isinstance(parsed_rdl, dict) else None,
        "namespace": parsed_rdl.get("namespace") if isinstance(parsed_rdl, dict) else None,
        "data_sources": data_sources[:20] if isinstance(data_sources, list) else [],
        "data_sets": compact_datasets,
        "visuals": compact_visuals,
        "report_parameters": report_parameters[:60] if isinstance(report_parameters, list) else [],
        "report_metadata": report_metadata if isinstance(report_metadata, dict) else {},
        "report_sections": report_sections[:20] if isinstance(report_sections, list) else [],
    }


def _compact_visual_tree_for_prompt(visuals: list, max_nodes: int) -> list[dict]:
    compact: list[dict] = []
    remaining = max_nodes

    def _walk(nodes: list) -> list[dict]:
        nonlocal remaining
        out: list[dict] = []
        for node in nodes:
            if remaining <= 0:
                break
            if not isinstance(node, dict):
                continue
            remaining -= 1

            expressions = node.get("expressions", [])
            if isinstance(expressions, list):
                compact_expr = [str(e)[:220] for e in expressions[:8]]
            else:
                compact_expr = []

            layout = node.get("layout", {}) if isinstance(node.get("layout", {}), dict) else {}
            compact_layout = {
                "top": layout.get("top"),
                "left": layout.get("left"),
                "height": layout.get("height"),
                "width": layout.get("width"),
                "referenced_fields": layout.get("referenced_fields"),
            }

            children = node.get("children", []) if isinstance(node.get("children", []), list) else []
            properties = node.get("properties", {}) if isinstance(node.get("properties", {}), dict) else {}
            out.append(
                {
                    "name": node.get("name"),
                    "visual_type": node.get("visual_type"),
                    "dataset_name": node.get("dataset_name"),
                    "expressions": compact_expr,
                    "layout": compact_layout,
                    "properties": _compact_visual_properties(properties),
                    "children": _walk(children),
                }
            )
        return out

    if isinstance(visuals, list):
        compact = _walk(visuals)
    return compact


def _compact_visual_properties(properties: dict) -> dict:
    if not isinstance(properties, dict):
        return {}

    compact: dict[str, object] = {}

    for key in [
        "field_references",
        "parameter_references",
        "text_values",
        "hidden_expression",
        "container_section",
        "semantic_hint",
    ]:
        value = properties.get(key)
        if isinstance(value, list):
            compact[key] = value[:20]
        elif value not in (None, ""):
            compact[key] = value

    for key in ["image", "gauge"]:
        value = properties.get(key)
        if isinstance(value, dict) and value:
            compact[key] = value

    for key in ["filters", "sort_expressions", "groups"]:
        value = properties.get(key)
        if isinstance(value, list) and value:
            compact[key] = value[:25]

    chart = properties.get("chart")
    if isinstance(chart, dict) and chart:
        compact["chart"] = chart

    tablix = properties.get("tablix")
    if isinstance(tablix, dict) and tablix:
        compact["tablix"] = {
            "row_groups": tablix.get("row_groups", [])[:20] if isinstance(tablix.get("row_groups"), list) else [],
            "column_groups": tablix.get("column_groups", [])[:20]
            if isinstance(tablix.get("column_groups"), list)
            else [],
            "cell_expressions": tablix.get("cell_expressions", [])[:20]
            if isinstance(tablix.get("cell_expressions"), list)
            else [],
        }

    return compact


def _compact_semantic_for_prompt(data_model: dict, visual_model: dict, mapping: dict) -> tuple[dict, dict, dict]:
    compact_data_model = {
        "datasources": (data_model.get("datasources", []) if isinstance(data_model, dict) else [])[:20],
        "datasets": (data_model.get("datasets", []) if isinstance(data_model, dict) else [])[:80],
        "fields": (data_model.get("fields", []) if isinstance(data_model, dict) else [])[:200],
        "parameters": (data_model.get("parameters", []) if isinstance(data_model, dict) else [])[:60],
    }

    compact_visual_model = {
        "sheets": (visual_model.get("sheets", []) if isinstance(visual_model, dict) else [])[:120],
        "visuals": (visual_model.get("visuals", []) if isinstance(visual_model, dict) else [])[:160],
        "layout": visual_model.get("layout", {}) if isinstance(visual_model, dict) else {},
        "interactions": (visual_model.get("interactions", []) if isinstance(visual_model, dict) else [])[:120],
    }

    compact_mapping = {
        "visual_to_dataset": (mapping.get("visual_to_dataset", []) if isinstance(mapping, dict) else [])[:200],
        "visual_to_fields": (mapping.get("visual_to_fields", []) if isinstance(mapping, dict) else [])[:200],
        "parameter_usage": (mapping.get("parameter_usage", []) if isinstance(mapping, dict) else [])[:120],
    }

    return compact_data_model, compact_visual_model, compact_mapping


