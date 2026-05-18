from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def write_patch_log(log_dir: str | Path, payload: dict[str, Any]) -> Path:
    target_dir = Path(log_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = target_dir / f"rdl_patch_{timestamp}.json"

    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return log_path
