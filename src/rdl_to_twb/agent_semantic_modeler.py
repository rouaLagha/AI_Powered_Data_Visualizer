from __future__ import annotations

import json
from pathlib import Path
import re

from .llm_client import LLMClient
from .prompts import SEMANTIC_SYSTEM_PROMPT, build_semantic_user_prompt


FIELD_EXPR_RE = re.compile(r"Fields!([A-Za-z0-9_]+)\.Value", re.IGNORECASE)


def generate_semantic_models(
    llm: LLMClient,
    parsed_rdl: dict,
    rdl_xsd_summary: dict,
    twb_xsd_summary: dict,
    output_dir: str | Path,
) -> tuple[dict, dict, dict]:
    prompt = build_semantic_user_prompt(parsed_rdl, rdl_xsd_summary, twb_xsd_summary)
    raw = ""
    llm_error: str | None = None
    try:
        raw = llm.chat(SEMANTIC_SYSTEM_PROMPT, prompt)
        parsed_payload = _parse_json_response(raw)
        normalized_payload = _normalize_semantic_payload(parsed_payload)
    except Exception as exc:
        llm_error = f"{type(exc).__name__}: {exc}"
        raw = ""
        normalized_payload = _normalize_semantic_payload({})

    fallback_payload = _build_deterministic_semantic_payload(parsed_rdl)
    payload, fallback_used = _merge_with_fallback(
        llm_payload=normalized_payload,
        fallback_payload=fallback_payload,
        parsed_rdl=parsed_rdl,
    )

    data_model = payload["data_model"]
    visual_model = payload["visual_model"]
    mapping = payload["mapping"]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "agent1_raw_response.txt").write_text(raw, encoding="utf-8")
    (output_dir / "semantic_generation_report.json").write_text(
        json.dumps(
            {
                "llm_error": llm_error,
                "fallback_used": fallback_used,
                "llm_payload_non_empty": {
                    "data_model": _has_data_model_content(normalized_payload["data_model"]),
                    "visual_model": _has_visual_model_content(normalized_payload["visual_model"]),
                    "mapping": _has_mapping_content(normalized_payload["mapping"]),
                },
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    (output_dir / "data_model.json").write_text(
        json.dumps(data_model, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    (output_dir / "visual_model.json").write_text(
        json.dumps(visual_model, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    (output_dir / "mapping.json").write_text(
        json.dumps(mapping, indent=2, ensure_ascii=True), encoding="utf-8"
    )

    return data_model, visual_model, mapping


def _parse_json_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = _strip_code_fences(text)

    if not text:
        raise ValueError("Agent-1 returned empty content; expected JSON object.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        candidate = _extract_outer_json_object(text)
        if candidate is not None:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        preview = text[:400].replace("\n", " ").strip()
        raise ValueError(
            "Agent-1 response is not valid JSON. "
            f"Response preview: {preview}"
        )


def _strip_code_fences(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _extract_outer_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    return text[start : end + 1]


def _normalize_semantic_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}

    data_model = payload.get("data_model", {})
    visual_model = payload.get("visual_model", {})
    mapping = payload.get("mapping", {})

    if not isinstance(data_model, dict):
        data_model = {}
    if not isinstance(visual_model, dict):
        visual_model = {}
    if not isinstance(mapping, dict):
        mapping = {}

    normalized = {
        "data_model": {
            "datasources": _as_list(data_model.get("datasources")),
            "datasets": _as_list(data_model.get("datasets")),
            "fields": _as_list(data_model.get("fields")),
            "parameters": _as_list(data_model.get("parameters")),
        },
        "visual_model": {
            "sheets": _as_list(visual_model.get("sheets")),
            "visuals": _as_list(visual_model.get("visuals")),
            "layout": visual_model.get("layout") if isinstance(visual_model.get("layout"), dict) else {},
            "interactions": _as_list(visual_model.get("interactions")),
        },
        "mapping": {
            "visual_to_dataset": _as_list(mapping.get("visual_to_dataset")),
            "visual_to_fields": _as_list(mapping.get("visual_to_fields")),
            "parameter_usage": _as_list(mapping.get("parameter_usage")),
        },
    }
    return normalized


def _merge_with_fallback(llm_payload: dict, fallback_payload: dict, parsed_rdl: dict) -> tuple[dict, dict]:
    merged = _normalize_semantic_payload(llm_payload)
    fallback = _normalize_semantic_payload(fallback_payload)

    data_input = _has_data_input(parsed_rdl)
    visual_input = _has_visual_input(parsed_rdl)
    param_input = _has_parameter_input(parsed_rdl)

    fallback_used = {
        "data_model": False,
        "visual_model": False,
        "mapping": False,
    }

    if data_input and not _has_data_model_content(merged["data_model"]):
        merged["data_model"] = fallback["data_model"]
        fallback_used["data_model"] = True

    if visual_input and not _has_visual_model_content(merged["visual_model"]):
        merged["visual_model"] = fallback["visual_model"]
        fallback_used["visual_model"] = True

    if (visual_input or param_input) and not _has_mapping_content(merged["mapping"]):
        merged["mapping"] = fallback["mapping"]
        fallback_used["mapping"] = True

    return merged, fallback_used


def _has_data_input(parsed_rdl: dict) -> bool:
    return bool(_as_list(parsed_rdl.get("data_sources")) or _as_list(parsed_rdl.get("data_sets")))


def _has_visual_input(parsed_rdl: dict) -> bool:
    return bool(_as_list(parsed_rdl.get("visuals")))


def _has_parameter_input(parsed_rdl: dict) -> bool:
    return bool(_as_list(parsed_rdl.get("report_parameters")))


def _has_data_model_content(data_model: dict) -> bool:
    return bool(
        _as_list(data_model.get("datasources"))
        or _as_list(data_model.get("datasets"))
        or _as_list(data_model.get("fields"))
        or _as_list(data_model.get("parameters"))
    )


def _has_visual_model_content(visual_model: dict) -> bool:
    return bool(_as_list(visual_model.get("sheets")) or _as_list(visual_model.get("visuals")))


def _has_mapping_content(mapping: dict) -> bool:
    return bool(
        _as_list(mapping.get("visual_to_dataset"))
        or _as_list(mapping.get("visual_to_fields"))
        or _as_list(mapping.get("parameter_usage"))
    )


def _build_deterministic_semantic_payload(parsed_rdl: dict) -> dict:
    data_sources = _as_list(parsed_rdl.get("data_sources"))
    data_sets = _as_list(parsed_rdl.get("data_sets"))
    visuals_tree = _as_list(parsed_rdl.get("visuals"))
    report_parameters = _as_list(parsed_rdl.get("report_parameters"))

    data_model_datasources: list[dict] = []
    for ds in data_sources:
        if not isinstance(ds, dict):
            continue
        data_model_datasources.append(
            {
                "name": ds.get("name"),
                "provider": ds.get("provider"),
                "connection_string": ds.get("connection_string"),
                "security_type": ds.get("security_type"),
            }
        )

    data_model_datasets: list[dict] = []
    data_model_fields: list[dict] = []
    for ds in data_sets:
        if not isinstance(ds, dict):
            continue
        dataset_name = ds.get("name")
        data_model_datasets.append(
            {
                "name": dataset_name,
                "data_source_name": ds.get("data_source_name"),
                "query": ds.get("query"),
            }
        )
        for field in _as_list(ds.get("fields")):
            if not isinstance(field, dict):
                continue
            data_model_fields.append(
                {
                    "dataset_name": dataset_name,
                    "name": field.get("name"),
                    "data_field": field.get("data_field"),
                }
            )

    data_model_parameters: list[dict] = []
    for p in report_parameters:
        if not isinstance(p, dict):
            continue
        data_model_parameters.append(
            {
                "name": p.get("name"),
                "type": p.get("type"),
                "nullable": p.get("nullable"),
                "multi_value": p.get("multi_value"),
            }
        )

    flattened_visuals = _flatten_visuals(visuals_tree)
    visual_sheets: list[dict] = []
    seen_sheet_names: set[str] = set()
    visual_to_dataset: list[dict] = []
    visual_to_fields: list[dict] = []

    for visual in flattened_visuals:
        if not isinstance(visual, dict):
            continue
        v_name = visual.get("name")
        if not isinstance(v_name, str) or not v_name.strip():
            continue
        v_name = v_name.strip()

        dataset_name = _infer_visual_dataset_name(visual, data_sets)
        fields = _extract_visual_field_refs(visual)

        if v_name.lower() not in seen_sheet_names:
            seen_sheet_names.add(v_name.lower())
            visual_sheets.append(
                {
                    "name": v_name,
                    "visual_type": visual.get("visual_type"),
                    "dataset_name": dataset_name,
                }
            )

        if isinstance(dataset_name, str) and dataset_name.strip():
            visual_to_dataset.append(
                {
                    "visual_name": v_name,
                    "dataset_name": dataset_name,
                }
            )
        if fields:
            visual_to_fields.append(
                {
                    "visual_name": v_name,
                    "fields": fields,
                }
            )

    parameter_usage: list[dict] = []
    for p in report_parameters:
        if not isinstance(p, dict):
            continue
        pname = p.get("name")
        if not isinstance(pname, str) or not pname.strip():
            continue
        used_in_datasets = _datasets_using_parameter(pname, data_sets)
        if used_in_datasets:
            parameter_usage.append(
                {
                    "parameter_name": pname,
                    "dataset_names": used_in_datasets,
                }
            )

    return {
        "data_model": {
            "datasources": data_model_datasources,
            "datasets": data_model_datasets,
            "fields": data_model_fields,
            "parameters": data_model_parameters,
        },
        "visual_model": {
            "sheets": visual_sheets,
            "visuals": visuals_tree,
            "layout": {
                "report_name": parsed_rdl.get("report_name"),
                "namespace": parsed_rdl.get("namespace"),
            },
            "interactions": [],
        },
        "mapping": {
            "visual_to_dataset": visual_to_dataset,
            "visual_to_fields": visual_to_fields,
            "parameter_usage": parameter_usage,
        },
    }


def _flatten_visuals(visuals: list) -> list[dict]:
    out: list[dict] = []
    for visual in visuals:
        if not isinstance(visual, dict):
            continue
        out.append(visual)
        children = visual.get("children")
        if isinstance(children, list):
            out.extend(_flatten_visuals(children))
    return out


def _extract_visual_field_refs(visual: dict) -> list[str]:
    refs: list[str] = []
    for expr in _as_list(visual.get("expressions")):
        if not isinstance(expr, str):
            continue
        for match in FIELD_EXPR_RE.finditer(expr):
            name = match.group(1)
            if name not in refs:
                refs.append(name)
    return refs


def _infer_visual_dataset_name(visual: dict, data_sets: list[dict]) -> str | None:
    direct = visual.get("dataset_name")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    refs = set(_extract_visual_field_refs(visual))
    if refs:
        best_name: str | None = None
        best_score = 0
        for ds in data_sets:
            if not isinstance(ds, dict):
                continue
            dname = ds.get("name")
            if not isinstance(dname, str) or not dname.strip():
                continue
            ds_fields: set[str] = set()
            for f in _as_list(ds.get("fields")):
                if not isinstance(f, dict):
                    continue
                for key in ["name", "data_field"]:
                    value = f.get(key)
                    if isinstance(value, str) and value.strip():
                        ds_fields.add(value.strip())
            score = len(refs.intersection(ds_fields))
            if score > best_score:
                best_score = score
                best_name = dname.strip()
        if best_name is not None:
            return best_name

    if len(data_sets) == 1 and isinstance(data_sets[0], dict):
        only = data_sets[0].get("name")
        if isinstance(only, str) and only.strip():
            return only.strip()
    return None


def _datasets_using_parameter(parameter_name: str, data_sets: list[dict]) -> list[str]:
    token = f"@{parameter_name}"
    result: list[str] = []
    for ds in data_sets:
        if not isinstance(ds, dict):
            continue
        query = ds.get("query")
        if not isinstance(query, str):
            continue
        if token.lower() in query.lower():
            dname = ds.get("name")
            if isinstance(dname, str) and dname.strip():
                result.append(dname.strip())
    return result


def _as_list(value) -> list:
    return value if isinstance(value, list) else []
