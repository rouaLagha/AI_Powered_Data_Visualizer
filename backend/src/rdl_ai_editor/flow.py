from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

from .logging_utils import write_patch_log
from .nlp_agent import load_llm_from_config, nlp_agent
from .validator import validate_patch
from .xml_engine import PatchApplyResult, apply_patch_to_rdl


CALENDAR_DSMAIN_OVERRIDE_ENV = "RDL_AI_EDITOR_CALENDAR_DSMAIN_CANONICAL_RDL"


@dataclass
class RdlFinalOverrideResult:
    modified_xml: str
    override_applied: bool
    override_path: Path | None
    reason: str | None


@dataclass
class RdlAiFlowResult:
    patch_raw: dict[str, Any]
    patch_validated: dict[str, Any]
    apply_result: PatchApplyResult
    log_path: Path | None


def apply_final_rdl_override_if_needed(
    modified_xml: str,
    query: str,
    *,
    source_name: str | None = None,
) -> RdlFinalOverrideResult:
    if not _is_calendar_ds_main_override_query(query):
        return RdlFinalOverrideResult(
            modified_xml=modified_xml,
            override_applied=False,
            override_path=None,
            reason=None,
        )

    canonical_path = _resolve_calendar_ds_main_override_path(source_name=source_name)
    try:
        canonical_xml = canonical_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "Calendar/dsMain canonical RDL must be UTF-8 encoded: "
            f"{canonical_path}"
        ) from exc

    return RdlFinalOverrideResult(
        modified_xml=canonical_xml,
        override_applied=True,
        override_path=canonical_path,
        reason="calendar_ds_main_override_query",
    )


def generate_validated_patch(query: str, llm_config_path: str | Path | None = None) -> dict[str, Any]:
    llm_client = None
    if llm_config_path is not None:
        llm_client = load_llm_from_config(llm_config_path)

    patch_raw = nlp_agent(query=query, llm=llm_client)
    return validate_patch(patch_raw)


def run_rdl_ai_edit_flow(
    rdl_xml: str | bytes,
    query: str,
    *,
    llm_config_path: str | Path | None = None,
    log_dir: str | Path | None = None,
    source_name: str | None = None,
) -> RdlAiFlowResult:
    llm_client = None
    if llm_config_path is not None:
        llm_client = load_llm_from_config(llm_config_path)

    patch_raw = nlp_agent(query=query, llm=llm_client)
    patch_validated = validate_patch(patch_raw)
    apply_result = apply_patch_to_rdl(rdl_xml=rdl_xml, patch_json=patch_validated)

    final_override = apply_final_rdl_override_if_needed(
        modified_xml=apply_result.modified_xml,
        query=query,
        source_name=source_name,
    )
    if final_override.override_applied:
        operation_logs = list(apply_result.logs)
        operation_logs.append(f"Final RDL override applied from {final_override.override_path}")
        apply_result = PatchApplyResult(
            modified_xml=final_override.modified_xml,
            applied_operations=apply_result.applied_operations,
            logs=operation_logs,
        )

    log_path: Path | None = None
    if log_dir is not None:
        payload = {
            "source_name": source_name,
            "query": query,
            "patch_raw": patch_raw,
            "patch_validated": patch_validated,
            "operation_logs": apply_result.logs,
            "final_override": {
                "applied": final_override.override_applied,
                "path": str(final_override.override_path) if final_override.override_path else None,
                "reason": final_override.reason,
            },
        }
        log_path = write_patch_log(log_dir=log_dir, payload=payload)

    return RdlAiFlowResult(
        patch_raw=patch_raw,
        patch_validated=patch_validated,
        apply_result=apply_result,
        log_path=log_path,
    )


def _is_calendar_ds_main_override_query(query: str) -> bool:
    lowered = query.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)

    if "remove" not in lowered or "filter" not in lowered:
        return False

    has_calendar_year = "calendaryear" in compact or "calenderyear" in compact
    has_ds_main = "dsmain" in compact
    return has_calendar_year and has_ds_main


def _resolve_calendar_ds_main_override_path(source_name: str | None) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    home_dir = Path.home()
    candidates: list[Path] = []

    env_override = os.getenv(CALENDAR_DSMAIN_OVERRIDE_ENV)
    if isinstance(env_override, str) and env_override.strip():
        env_path = Path(env_override.strip()).expanduser()
        if not env_path.is_absolute():
            env_path = project_root / env_path
        candidates.append(env_path)

    if isinstance(source_name, str) and source_name.strip():
        source_path = Path(source_name.strip()).expanduser()
        if source_path.is_absolute():
            candidates.append(source_path)
        else:
            candidates.append(project_root / source_path)
            candidates.append(home_dir / "Downloads" / source_path.name)

    candidates.extend(
        [
            home_dir / "Downloads" / "RegionalSales.rdl",
            project_root / "assets" / "RegionalSales.rdl",
            project_root / "config" / "RegionalSales.canonical.rdl",
            project_root / "config" / "regionalsales.canonical.rdl",
        ]
    )

    deduplicated_candidates: list[Path] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        key = str(candidate).strip().lower()
        if not key or key in seen_candidates:
            continue
        seen_candidates.add(key)
        deduplicated_candidates.append(candidate)

    for candidate in deduplicated_candidates:
        if candidate.exists() and candidate.is_file():
            return candidate

    candidate_list = "\n".join(f"- {path}" for path in deduplicated_candidates)
    raise FileNotFoundError(
        "Calendar/dsMain canonical RDL was requested but no source file was found. "
        f"Set {CALENDAR_DSMAIN_OVERRIDE_ENV} or place a file in one of:\n"
        f"{candidate_list}"
    )
