from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rdl_to_twb.tableau_publisher import publish_workbook_if_configured


CONFIG_PATH = ROOT_DIR / "config" / "llm_config.tableau_cloud_sql_assistant.json"
WORKBOOK_PATH = ROOT_DIR / "output" / "validated_semantic_model.twb"
REPORT_PATH = ROOT_DIR / "output" / "tableau_direct_twb_publish_report.json"


def main() -> int:
    if not WORKBOOK_PATH.exists():
        raise FileNotFoundError(f"Workbook file not found: {WORKBOOK_PATH}")
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

    config_payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    tableau_cfg = config_payload.get("tableau_cloud")
    if not isinstance(tableau_cfg, dict):
        raise ValueError("Missing tableau_cloud config.")

    cfg: dict[str, Any] = copy.deepcopy(tableau_cfg)
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cfg["enabled"] = True
    cfg["workbook_name"] = f"{WORKBOOK_PATH.stem}_direct_twb_{timestamp_utc}"
    cfg["publish_mode"] = "create_new"
    cfg["file_format"] = "twb"
    cfg["skip_connection_check"] = True
    cfg["as_job"] = False

    report: dict[str, Any]
    try:
        report = publish_workbook_if_configured(
            workbook_path=WORKBOOK_PATH,
            tableau_config=cfg,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "attempt": "direct_twb_workbook_publish",
            "workbook_path": str(WORKBOOK_PATH),
            "workbook_name": cfg["workbook_name"],
            "skip_connection_check": cfg["skip_connection_check"],
            "error": str(exc),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 1

    report["attempt"] = "direct_twb_workbook_publish"
    report["skip_connection_check"] = cfg["skip_connection_check"]
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
