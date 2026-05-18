from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sys


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
ASSETS_DIR = BACKEND_DIR / "assets"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
RDL_TO_TWB_OUTPUT_DIR = OUTPUT_DIR / "rdl_to_twb"
VISUAL_MAPPING_DIR_NAME = "03_visual_mapping"
VISUAL_MAPPING_TWB_NAME = "visual_content_mapped.twb"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from src.rdl_to_twb.pipeline import run_conversion

LOCAL_CONFIG_PATH = BACKEND_DIR / "config" / "llm_config.json"
DEFAULT_CONFIG_PATH = (
    LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else BACKEND_DIR / "config" / "llm_config.example.json"
)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "artifact"


def _default_output_dir(rdl_path: str) -> Path:
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_stem = Path(str(rdl_path or "uploaded_report.rdl")).stem or "uploaded_report"
    return RDL_TO_TWB_OUTPUT_DIR / f"{timestamp_utc}_{_safe_name(report_stem)}" / VISUAL_MAPPING_DIR_NAME


def _canonical_visual_workbook_path(visual_twb_path: str, output_dir: Path) -> str:
    source_path = Path(visual_twb_path)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    source_path = source_path.resolve(strict=True)
    target_path = (output_dir / VISUAL_MAPPING_TWB_NAME).resolve(strict=False)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path != target_path:
        if source_path.parent == target_path.parent:
            if target_path.exists():
                target_path.unlink()
            source_path.replace(target_path)
        else:
            shutil.copy2(source_path, target_path)
    return str(target_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SSRS RDL to Tableau TWB")
    parser.add_argument("--rdl", required=True, help="Path to source .rdl file")
    parser.add_argument(
        "--rdl-xsd",
        default=str(ASSETS_DIR / "ReportDefinition.xsd"),
        help="Path to RDL XSD schema",
    )
    parser.add_argument(
        "--twb-xsd",
        default=str(ASSETS_DIR / "twb_2026.1.0.xsd"),
        help="Path to TWB XSD schema",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Defaults to outputs/rdl_to_twb/<run>/03_visual_mapping.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to LLM config JSON",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Run conversion only and skip extract/publish/RPA stages",
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.rdl)

    result = run_conversion(
        rdl_path=Path(args.rdl),
        rdl_xsd_path=Path(args.rdl_xsd),
        twb_xsd_path=Path(args.twb_xsd),
        output_dir=output_dir,
        config_path=Path(args.config),
        publish_enabled=not args.no_publish,
        artifact_mode=os.getenv("RDL_TO_TWB_ARTIFACT_MODE") or "runtime",
    )
    if isinstance(result, dict) and isinstance(result.get("twb"), str):
        result["twb"] = _canonical_visual_workbook_path(result["twb"], output_dir)

    print("Conversion complete. Files generated:")
    for key, value in result.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
