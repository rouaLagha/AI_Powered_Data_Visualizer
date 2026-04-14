from __future__ import annotations

import argparse
from pathlib import Path

from src.rdl_to_twb.pipeline import run_conversion


ROOT_DIR = Path(__file__).resolve().parent
LOCAL_CONFIG_PATH = ROOT_DIR / "config" / "llm_config.json"
DEFAULT_CONFIG_PATH = (
    LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else ROOT_DIR / "config" / "llm_config.example.json"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SSRS RDL to Tableau TWB")
    parser.add_argument("--rdl", required=True, help="Path to source .rdl file")
    parser.add_argument(
        "--rdl-xsd",
        default="ReportDefinition.xsd",
        help="Path to RDL XSD schema",
    )
    parser.add_argument(
        "--twb-xsd",
        default="twb_2026.1.0.xsd",
        help="Path to TWB XSD schema",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to LLM config JSON",
    )

    args = parser.parse_args()

    result = run_conversion(
        rdl_path=Path(args.rdl),
        rdl_xsd_path=Path(args.rdl_xsd),
        twb_xsd_path=Path(args.twb_xsd),
        output_dir=Path(args.output_dir),
        config_path=Path(args.config),
    )

    print("Conversion complete. Files generated:")
    for key, value in result.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
