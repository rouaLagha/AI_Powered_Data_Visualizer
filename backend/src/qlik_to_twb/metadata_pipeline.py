from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from .qlik_client import (
    QlikEngineApiClient,
    QlikEngineClientConfig,
    create_qlik_engine_client,
    extract_qlik_metadata,
    import_qvf_to_qlik,
)


JsonDict = dict[str, Any]


def run_qlik_metadata_job(
    source_qvf_path: str | Path,
    jobs_root: str | Path,
    job_id: str | None = None,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
) -> JsonDict:
    """Store a QVF under a job folder and extract real QIX metadata."""
    source = Path(str(source_qvf_path)).expanduser()
    if source.suffix.lower() != ".qvf":
        raise ValueError(f"Qlik source must be a .qvf file: {source_qvf_path}")
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"QVF file not found: {source}")

    safe_job_id = _safe_job_id(job_id or _new_job_id())
    job_dir = Path(jobs_root) / safe_job_id
    upload_dir = job_dir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_qvf_path = upload_dir / _safe_qvf_name(source.name)
    if source.resolve() != stored_qvf_path.resolve():
        shutil.copy2(source, stored_qvf_path)

    return _run_stored_qvf_job(
        stored_qvf_path=stored_qvf_path,
        job_dir=job_dir,
        job_id=safe_job_id,
        qlik_client=qlik_client,
        qlik_endpoint=qlik_endpoint,
        qlik_apps_dir=qlik_apps_dir,
        qlik_user_directory=qlik_user_directory,
        qlik_user_id=qlik_user_id,
        qlik_session_cookie=qlik_session_cookie,
    )


def run_uploaded_qlik_metadata_job(
    file_name: str,
    file_bytes: bytes,
    jobs_root: str | Path,
    job_id: str | None = None,
    qlik_client: QlikEngineApiClient | None = None,
    qlik_endpoint: str = "ws://localhost:4848/app",
    qlik_apps_dir: str = "",
    qlik_user_directory: str = "",
    qlik_user_id: str = "",
    qlik_session_cookie: str = "",
) -> JsonDict:
    """Persist uploaded QVF bytes under a job folder and extract real QIX metadata."""
    if not file_bytes:
        raise ValueError("Uploaded QVF content is empty.")

    safe_job_id = _safe_job_id(job_id or _new_job_id())
    job_dir = Path(jobs_root) / safe_job_id
    upload_dir = job_dir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_qvf_path = upload_dir / _safe_qvf_name(file_name)
    stored_qvf_path.write_bytes(file_bytes)

    return _run_stored_qvf_job(
        stored_qvf_path=stored_qvf_path,
        job_dir=job_dir,
        job_id=safe_job_id,
        qlik_client=qlik_client,
        qlik_endpoint=qlik_endpoint,
        qlik_apps_dir=qlik_apps_dir,
        qlik_user_directory=qlik_user_directory,
        qlik_user_id=qlik_user_id,
        qlik_session_cookie=qlik_session_cookie,
    )


def _run_stored_qvf_job(
    stored_qvf_path: Path,
    job_dir: Path,
    job_id: str,
    qlik_client: QlikEngineApiClient | None,
    qlik_endpoint: str,
    qlik_apps_dir: str,
    qlik_user_directory: str,
    qlik_user_id: str,
    qlik_session_cookie: str,
) -> JsonDict:
    trace_steps: list[str] = [
        "QVF uploaded and stored by backend",
        f"Qlik metadata job created ({job_id})",
    ]

    app_id = import_qvf_to_qlik(stored_qvf_path, apps_dir=qlik_apps_dir or None)
    trace_steps.append(f"QVF imported/openable in Qlik with app_id={app_id}")

    config = QlikEngineClientConfig(
        endpoint_url=qlik_endpoint or "ws://localhost:4848/app",
        apps_dir=qlik_apps_dir,
        user_directory=qlik_user_directory,
        user_id=qlik_user_id,
        session_cookie=qlik_session_cookie,
    )
    client = qlik_client or create_qlik_engine_client(config=config)
    _require_real_qix_client(client)
    trace_steps.append(f"Qlik Engine client ready ({client.client_name})")

    qlik_metadata = extract_qlik_metadata(client=client, app_id=app_id, qvf_path=stored_qvf_path)
    _require_real_qlik_metadata(qlik_metadata)
    trace_steps.append("Qlik app opened and metadata extracted through QIX")

    metadata_path = job_dir / "qlik_metadata.json"
    _write_json(metadata_path, qlik_metadata)
    trace_steps.append("qlik_metadata.json written")

    job_payload: JsonDict = {
        "ok": True,
        "status": "completed",
        "job_id": job_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "qvf_path": str(stored_qvf_path),
        "app_id": app_id,
        "client": client.client_name,
        "extraction_mode": client.extraction_mode,
        "qlik_metadata": str(metadata_path),
        "summary": _metadata_summary(qlik_metadata),
        "trace_steps": trace_steps,
    }
    _write_json(job_dir / "job.json", job_payload)
    return job_payload


def _require_real_qix_client(client: QlikEngineApiClient) -> None:
    extraction_mode = str(getattr(client, "extraction_mode", "") or "").strip().lower()
    if extraction_mode != "qix":
        raise ValueError("Qlik metadata extraction requires a real QIX client.")


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


def _metadata_summary(qlik_metadata: JsonDict) -> JsonDict:
    return {
        "sheet_count": len(_as_list(qlik_metadata.get("sheets"))),
        "visual_count": len(_as_list(qlik_metadata.get("visual_objects"))),
        "master_dimension_count": len(_as_list(qlik_metadata.get("master_dimensions"))),
        "master_measure_count": len(_as_list(qlik_metadata.get("master_measures"))),
        "variable_count": len(_as_list(qlik_metadata.get("variables"))),
        "load_script_bytes": len(str(qlik_metadata.get("load_script") or "").encode("utf-8")),
    }


def _new_job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"qlik_{timestamp}_{uuid4().hex[:8]}"


def _safe_job_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    if not cleaned:
        raise ValueError("job_id is empty after sanitization.")
    return cleaned[:120]


def _safe_qvf_name(value: str) -> str:
    name = Path(value or "uploaded.qvf").name
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).stem).strip("._")
    safe_name = f"{stem or 'uploaded'}.qvf"
    return safe_name[:160]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_json(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real Qlik QVF metadata extraction pipeline.")
    parser.add_argument("--qvf", required=True, help="Path to the source .qvf app")
    parser.add_argument("--jobs-root", default="output/qlik_jobs", help="Root directory for Qlik metadata jobs")
    parser.add_argument("--job-id", default="", help="Optional stable job id")
    parser.add_argument("--qlik-endpoint", default="ws://localhost:4848/app", help="QIX WebSocket endpoint")
    parser.add_argument("--qlik-apps-dir", default="", help="Qlik Sense Desktop Apps directory")
    parser.add_argument("--qlik-user-directory", default="", help="Optional X-Qlik-User directory")
    parser.add_argument("--qlik-user-id", default="", help="Optional X-Qlik-User id")
    parser.add_argument("--qlik-session-cookie", default="", help="Optional Qlik session cookie for secured endpoints")
    args = parser.parse_args()

    result = run_qlik_metadata_job(
        source_qvf_path=args.qvf,
        jobs_root=args.jobs_root,
        job_id=args.job_id or None,
        qlik_endpoint=args.qlik_endpoint,
        qlik_apps_dir=args.qlik_apps_dir,
        qlik_user_directory=args.qlik_user_directory,
        qlik_user_id=args.qlik_user_id,
        qlik_session_cookie=args.qlik_session_cookie,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
