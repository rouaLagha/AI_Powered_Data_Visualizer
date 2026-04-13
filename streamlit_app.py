from __future__ import annotations

import json
from pathlib import Path
import tempfile

import streamlit as st

from src.rdl_to_twb.pipeline import run_conversion


ROOT_DIR = Path(__file__).resolve().parent
RDL_XSD_PATH = ROOT_DIR / "ReportDefinition.xsd"
TWB_XSD_PATH = ROOT_DIR / "twb_2026.1.0.xsd"
LOCAL_CONFIG_PATH = ROOT_DIR / "config" / "llm_config.json"
DEFAULT_CONFIG_PATH = (
    LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else ROOT_DIR / "config" / "llm_config.example.json"
)


def main() -> None:
    st.set_page_config(page_title="RDL to TWB", page_icon="📊", layout="centered")
    st.title("RDL -> TWB Converter")
    st.caption("Simple interface to run the LLM multi-agent conversion pipeline")

    uploaded_file = st.file_uploader("Upload .rdl file", type=["rdl"])

    config_path_str = st.text_input(
        "LLM config path",
        value=str(DEFAULT_CONFIG_PATH),
    )

    output_dir_str = st.text_input("Output folder", value=str(ROOT_DIR / "output"))

    if st.button("Convert", type="primary", disabled=uploaded_file is None):
        if uploaded_file is None:
            st.warning("Please upload an .rdl file first.")
            return

        with st.spinner("Running conversion..."):
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_rdl_path = Path(tmp_dir) / uploaded_file.name
                tmp_rdl_path.write_bytes(uploaded_file.getbuffer())

                try:
                    result = run_conversion(
                        rdl_path=tmp_rdl_path,
                        rdl_xsd_path=RDL_XSD_PATH,
                        twb_xsd_path=TWB_XSD_PATH,
                        output_dir=Path(output_dir_str),
                        config_path=Path(config_path_str),
                    )
                except Exception as exc:
                    st.error(f"Conversion failed: {exc}")
                    return

        st.success("Conversion completed.")

        st.subheader("Pipeline execution")
        st.info("LLM-only pipeline completed.")

        trace_path_str = result.get("pipeline_trace")
        if isinstance(trace_path_str, str) and trace_path_str:
            trace_path = Path(trace_path_str)
            if trace_path.exists():
                try:
                    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
                    steps = trace_payload.get("steps", []) if isinstance(trace_payload, dict) else []
                    if isinstance(steps, list) and steps:
                        for index, step in enumerate(steps, start=1):
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
