from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

import streamlit as st


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

EXPECTED_PYTHON = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
    "python.exe" if os.name == "nt" else "python"
)
RDL_XSD_PATH = ASSETS_DIR / "ReportDefinition.xsd"
TWB_XSD_PATH = ASSETS_DIR / "twb_2026.1.0.xsd"
LOCAL_CONFIG_PATH = BACKEND_DIR / "config" / "llm_config.json"
DEFAULT_CONFIG_PATH = (
    LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else BACKEND_DIR / "config" / "llm_config.example.json"
)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "artifact"


def _visual_output_dir(base_dir_value: str, file_name: str) -> Path:
    base_dir = Path(str(base_dir_value or RDL_TO_TWB_OUTPUT_DIR)).expanduser()
    if not base_dir.is_absolute():
        base_dir = PROJECT_ROOT / base_dir
    rdl_root = RDL_TO_TWB_OUTPUT_DIR.resolve(strict=False)
    base_dir = base_dir.resolve(strict=False)
    try:
        base_dir.relative_to(rdl_root)
    except ValueError:
        base_dir = rdl_root
    if base_dir.name == VISUAL_MAPPING_DIR_NAME:
        return base_dir
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_stem = Path(str(file_name or "uploaded_report.rdl")).stem or "uploaded_report"
    return base_dir / f"{timestamp_utc}_{_safe_name(report_stem)}" / VISUAL_MAPPING_DIR_NAME


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


def _enforce_expected_interpreter() -> None:
    if not EXPECTED_PYTHON.exists():
        return

    current_python = Path(sys.executable).resolve()
    expected_python = EXPECTED_PYTHON.resolve()
    if current_python == expected_python:
        return

    st.error("Wrong Python interpreter detected for this workspace.")
    st.code(
        "\n".join(
            [
                f"Current:  {current_python}",
                f"Expected: {expected_python}",
                "",
                "Start Streamlit with:",
                f"{expected_python} -m streamlit run backend/streamlit_app.py",
            ]
        )
    )
    st.stop()


def main() -> None:
    st.set_page_config(page_title="RDL to TWB", page_icon="📊", layout="centered")
    st.title("RDL -> TWB Converter")
    st.caption("Simple interface to run the LLM multi-agent conversion pipeline")
    _enforce_expected_interpreter()

    uploaded_file = st.file_uploader("Upload .rdl file", type=["rdl"])

    config_path_str = st.text_input(
        "LLM config path",
        value=str(DEFAULT_CONFIG_PATH),
    )

    output_dir_str = st.text_input("Output root folder", value=str(RDL_TO_TWB_OUTPUT_DIR))
    publish_enabled = st.checkbox(
        "Enable publish stage (extract + publish + desktop RPA)",
        value=False,
        help="Disable this for conversion-only tests.",
    )

    if st.button("Convert", type="primary", disabled=uploaded_file is None):
        if uploaded_file is None:
            st.warning("Please upload an .rdl file first.")
            return

        with st.spinner("Running conversion..."):
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_rdl_path = Path(tmp_dir) / uploaded_file.name
                tmp_rdl_path.write_bytes(uploaded_file.getbuffer())
                output_dir = _visual_output_dir(output_dir_str, uploaded_file.name)

                try:
                    result = run_conversion(
                        rdl_path=tmp_rdl_path,
                        rdl_xsd_path=RDL_XSD_PATH,
                        twb_xsd_path=TWB_XSD_PATH,
                        output_dir=output_dir,
                        config_path=Path(config_path_str),
                        publish_enabled=publish_enabled,
                        artifact_mode=os.getenv("RDL_TO_TWB_ARTIFACT_MODE") or "runtime",
                    )
                    if isinstance(result, dict) and isinstance(result.get("twb"), str):
                        result["twb"] = _canonical_visual_workbook_path(result["twb"], output_dir)
                except Exception as exc:
                    st.error(f"Conversion failed: {exc}")
                    return

        st.success("Conversion completed.")

        st.subheader("Pipeline execution")
        st.info("LLM-only pipeline completed.")

        steps = result.get("trace_steps", [])
        if isinstance(steps, list) and steps:
            for index, step in enumerate(steps, start=1):
                st.write(f"{index}. {step}")

        trace_path_str = result.get("pipeline_trace")
        if isinstance(trace_path_str, str) and trace_path_str:
            trace_path = Path(trace_path_str)
            if trace_path.exists():
                try:
                    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
                    file_steps = trace_payload.get("steps", []) if isinstance(trace_payload, dict) else []
                    if isinstance(file_steps, list) and file_steps and not steps:
                        for index, step in enumerate(file_steps, start=1):
                            st.write(f"{index}. {step}")
                except Exception:
                    st.caption("Pipeline trace file exists but could not be rendered.")

        st.subheader("Generated files")
        file_keys = [
            "parsed_rdl",
            "data_model",
            "visual_model",
            "mapping",
            "db_catalog",
            "semantic_generation_report",
            "agent1_raw_response",
            "generated_xml",
            "twb",
            "hyper",
            "twbx",
            "tableau_extract_report",
            "tableau_publish_report",
            "tableau_rpa_publish_report",
            "powerbi_publish_report",
            "published_report_test_report",
            "pipeline_trace",
        ]
        for label in file_keys:
            file_path = result.get(label)
            if not isinstance(file_path, str):
                continue
            path = Path(file_path)
            st.write(f"{label}: {path}")
            if path.exists():
                st.download_button(
                    label=f"Download {path.name}",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime="application/octet-stream",
                    key=f"download-{label}",
                )


if __name__ == "__main__":
    main()
