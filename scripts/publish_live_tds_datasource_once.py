from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sql_model_assistant_app import (  # noqa: E402
    _build_live_datasource_tds_for_publish,
    _load_tableau_publish_defaults,
    _preferred_sql_assistant_llm_config_path,
    _tableau_resolve_generated_source_content_path,
    _tableau_resolve_project_id,
    _tableau_timestamped_name,
)


CONFIG_PATH = ROOT_DIR / "config" / "llm_config.tableau_cloud_sql_assistant.json"
SOURCE_TWB_PATH = ROOT_DIR / "output" / "validated_semantic_model.twb"
ARTIFACT_DIR = ROOT_DIR / "output" / "tableau_publish_artifacts"
REPORT_PATH = ROOT_DIR / "output" / "tableau_live_tds_datasource_publish_report.json"


def _load_tableau_config() -> tuple[dict[str, Any], Path]:
    config_path = CONFIG_PATH if CONFIG_PATH.exists() else _preferred_sql_assistant_llm_config_path()
    cfg = _load_tableau_publish_defaults(str(config_path))
    if not cfg:
        raise ValueError(f"No Tableau Cloud config found in {config_path}")
    return cfg, config_path


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> int:
    if not SOURCE_TWB_PATH.exists():
        raise FileNotFoundError(f"TWB file not found: {SOURCE_TWB_PATH}")

    cfg, config_path = _load_tableau_config()
    source_twb_path = _tableau_resolve_generated_source_content_path(
        SOURCE_TWB_PATH,
        "generated_source_twb",
    )
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    datasource_name = _tableau_timestamped_name("validated_semantic_model_live", timestamp_utc)

    tds_path, selected_datasource_name = _build_live_datasource_tds_for_publish(
        source_twb_path=source_twb_path,
        output_dir=ARTIFACT_DIR,
        datasource_name=datasource_name,
        source_datasource_name=str(cfg.get("source_datasource_name") or ""),
    )

    report: dict[str, Any] = {
        "attempt": "publish_live_tds_datasource",
        "requested_source_twb_path": str(SOURCE_TWB_PATH),
        "source_twb_path": str(source_twb_path),
        "config_path": str(config_path),
        "tds_path": str(tds_path),
        "selected_datasource_name": selected_datasource_name,
        "datasource_name": datasource_name,
        "server_url": str(cfg.get("server_url", "")),
        "site_content_url": str(cfg.get("site_content_url", "")),
        "file_format": "tds",
        "created_hyper": False,
        "created_tdsx": False,
    }

    try:
        import tableauserverclient as TSC

        auth_method = str(cfg.get("auth_method") or "username_password").strip().lower()
        if auth_method == "pat":
            auth = TSC.PersonalAccessTokenAuth(
                token_name=str(cfg.get("pat_name", "")),
                personal_access_token=str(cfg.get("pat_secret", "")),
                site_id=str(cfg.get("site_content_url", "")),
            )
        else:
            auth = TSC.TableauAuth(
                username=str(cfg.get("username", "")),
                password=str(cfg.get("password", "")),
                site_id=str(cfg.get("site_content_url", "")),
            )
        server = TSC.Server(str(cfg.get("server_url", "")), use_server_version=True)
        with server.auth.sign_in(auth):
            project_id = str(cfg.get("project_id", "") or "").strip() or _tableau_resolve_project_id(
                server=server,
                tsc_module=TSC,
                project_name=str(cfg.get("project_name", "")),
            )
            datasource_item = TSC.DatasourceItem(project_id=project_id, name=datasource_name)
            published = server.datasources.publish(
                datasource_item,
                str(tds_path),
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
            }
        )
        _write_report(report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
