from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONFIG_PATH = ROOT_DIR / "config" / "llm_config.tableau_cloud_sql_assistant.json"
SOURCE_TWB_PATH = OUTPUT_DIR / "validated_semantic_model.twb"
REPORT_PATH = OUTPUT_DIR / "tableau_twb_as_datasource_publish_report.json"


def _load_tableau_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    tableau_cfg = payload.get("tableau_cloud")
    if not isinstance(tableau_cfg, dict):
        raise ValueError("Missing tableau_cloud config.")
    return copy.deepcopy(tableau_cfg)


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def _resolve_project_id(server: Any, tsc_module: Any, project_name: str) -> str:
    projects = [project for project in tsc_module.Pager(server.projects)]
    if not projects:
        raise ValueError("No Tableau projects are available for the authenticated account.")

    requested = str(project_name or "").strip()
    if requested:
        matches = [project for project in projects if str(getattr(project, "name", "")) == requested]
        if not matches:
            requested_lower = requested.lower()
            matches = [
                project
                for project in projects
                if str(getattr(project, "name", "")).lower() == requested_lower
            ]
        if matches:
            return str(matches[0].id)
        raise ValueError(f"Tableau project not found: {requested}")

    default_matches = [p for p in projects if str(getattr(p, "name", "")).strip().lower() == "default"]
    if default_matches:
        return str(default_matches[0].id)
    return str(projects[0].id)


def main() -> int:
    if not SOURCE_TWB_PATH.exists():
        raise FileNotFoundError(f"TWB file not found: {SOURCE_TWB_PATH}")

    cfg = _load_tableau_config()
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    datasource_name = f"{SOURCE_TWB_PATH.stem}_twb_as_datasource_{timestamp_utc}"

    report: dict[str, Any] = {
        "attempt": "publish_twb_file_via_datasource_endpoint",
        "source_twb_path": str(SOURCE_TWB_PATH),
        "datasource_name": datasource_name,
        "server_url": str(cfg.get("server_url", "")),
        "site_content_url": str(cfg.get("site_content_url", "")),
    }

    try:
        import tableauserverclient as TSC

        auth = TSC.TableauAuth(
            username=str(cfg.get("username", "")),
            password=str(cfg.get("password", "")),
            site_id=str(cfg.get("site_content_url", "")),
        )
        server = TSC.Server(str(cfg.get("server_url", "")), use_server_version=True)
        with server.auth.sign_in(auth):
            project_id = str(cfg.get("project_id", "") or "").strip() or _resolve_project_id(
                server=server,
                tsc_module=TSC,
                project_name=str(cfg.get("project_name", "")),
            )
            datasource_item = TSC.DatasourceItem(project_id=project_id, name=datasource_name)
            published = server.datasources.publish(
                datasource_item,
                str(SOURCE_TWB_PATH),
                TSC.Server.PublishMode.CreateNew,
                as_job=False,
            )
        report.update(
            {
                "status": "published",
                "project_id": project_id,
                "datasource_id": getattr(published, "id", None),
                "datasource_content_url": getattr(published, "content_url", None),
                "datasource_webpage_url": getattr(published, "webpage_url", None),
            }
        )
        _write_report(report)
        return 0
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "error": str(exc),
                "expected_reason": (
                    "Tableau datasource publish endpoints do not accept .twb files; "
                    "published datasources require .tds, .tdsx, .hyper, .tde, or .parquet."
                ),
            }
        )
        _write_report(report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
