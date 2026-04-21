from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from src.rdl_ai_editor.examples import (
    SAMPLE_MODIFIED_RDL_XML,
    SAMPLE_PATCH_JSON,
    SAMPLE_QUERY,
    SAMPLE_RDL_XML,
)
from src.rdl_ai_editor.flow import apply_final_rdl_override_if_needed
from src.rdl_ai_editor.logging_utils import write_patch_log
from src.rdl_ai_editor.nlp_agent import load_llm_from_config, nlp_agent
from src.rdl_ai_editor.schema import PATCH_SCHEMA
from src.rdl_ai_editor.validator import PatchValidationError, validate_patch
from src.rdl_ai_editor.xml_engine import PatchApplicationError, apply_patch_to_rdl

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.json"
DEFAULT_LOG_DIR = ROOT_DIR / "output" / "rdl_ai_editor_logs"

STATE_SOURCE_XML = "rdl_ai_editor_source_xml"
STATE_SOURCE_FILENAME = "rdl_ai_editor_source_filename"
STATE_QUERY = "rdl_ai_editor_query"
STATE_PATCH_RAW = "rdl_ai_editor_patch_raw"
STATE_PATCH_VALIDATED = "rdl_ai_editor_patch_validated"
STATE_MODIFIED_XML = "rdl_ai_editor_modified_xml"
STATE_OPERATION_LOGS = "rdl_ai_editor_operation_logs"
STATE_LAST_LOG_FILE = "rdl_ai_editor_last_log_file"


st.title("RDL Editor with AI")
st.caption(
    "Independent workflow: RDL + natural language query -> NLP patch JSON -> deterministic XML patch engine -> modified RDL"
)

with st.expander("Architecture and Safety", expanded=False):
    st.markdown(
        """
        - LLM is used only for intent extraction and JSON patch planning.
        - XML is modified only by deterministic rule-based code (XPath + lxml).
        - Existing conversion pipeline is untouched.
        - Every patch run can be logged for audit/debug.
        """
    )

with st.expander("Patch JSON Schema", expanded=False):
    st.json(PATCH_SCHEMA)

uploaded_file = st.file_uploader("Upload an RDL file", type=["rdl", "xml"])
query = st.text_area(
    "Natural language edit query",
    value=st.session_state.get(STATE_QUERY, ""),
    placeholder="Example: Add a filter on Sales for 2022, set title to 'Regional Sales 2022', and change chart main_chart to bar.",
)

use_llm = st.checkbox("Use configured LLM for NLP extraction", value=True)
llm_config_input = st.text_input("LLM config path", value=str(DEFAULT_LLM_CONFIG))
log_dir_input = st.text_input("Patch logs directory", value=str(DEFAULT_LOG_DIR))

if st.button("1) Generate Patch JSON", type="primary"):
    if uploaded_file is None:
        st.error("Please upload an RDL file first.")
    elif not query.strip():
        st.error("Please enter a natural language query.")
    else:
        source_bytes = uploaded_file.getvalue()
        source_xml = source_bytes.decode("utf-8", errors="replace")

        st.session_state[STATE_SOURCE_XML] = source_xml
        st.session_state[STATE_SOURCE_FILENAME] = uploaded_file.name
        st.session_state[STATE_QUERY] = query
        st.session_state.pop(STATE_MODIFIED_XML, None)
        st.session_state.pop(STATE_OPERATION_LOGS, None)

        llm_client = None
        llm_mode = "heuristic"
        if use_llm:
            try:
                llm_client = load_llm_from_config(Path(llm_config_input))
                llm_mode = "llm"
            except Exception as exc:
                st.warning(f"LLM config unavailable, using heuristic NLP fallback. Details: {exc}")

        try:
            patch_raw = nlp_agent(query=query, llm=llm_client)
            patch_validated = validate_patch(patch_raw)

            st.session_state[STATE_PATCH_RAW] = patch_raw
            st.session_state[STATE_PATCH_VALIDATED] = patch_validated

            generation_log = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stage": "generate_patch",
                "llm_mode": llm_mode,
                "source_file": uploaded_file.name,
                "query": query,
                "patch_raw": patch_raw,
                "patch_validated": patch_validated,
            }
            log_path = write_patch_log(log_dir_input, generation_log)
            st.session_state[STATE_LAST_LOG_FILE] = str(log_path)

            st.success("Patch JSON generated and validated.")
        except PatchValidationError as exc:
            st.error("Patch validation failed.")
            for err in exc.errors:
                st.write(f"- {err}")
        except Exception as exc:
            st.error(f"Failed to generate patch JSON: {exc}")

if STATE_PATCH_VALIDATED in st.session_state:
    st.subheader("2) Review Patch Preview")
    st.json(st.session_state[STATE_PATCH_VALIDATED])

    st.write("Planned operations:")
    for idx, operation in enumerate(st.session_state[STATE_PATCH_VALIDATED]["operations"], start=1):
        st.write(f"{idx}. {operation['op']} -> {operation}")

    confirm_apply = st.checkbox("I confirm these operations should be applied to the uploaded RDL")

    if st.button("3) Apply Patch to RDL"):
        if not confirm_apply:
            st.warning("Please confirm before applying the patch.")
        else:
            try:
                result = apply_patch_to_rdl(
                    rdl_xml=st.session_state[STATE_SOURCE_XML],
                    patch_json=st.session_state[STATE_PATCH_VALIDATED],
                )
                final_override = apply_final_rdl_override_if_needed(
                    modified_xml=result.modified_xml,
                    query=st.session_state.get(STATE_QUERY, ""),
                    source_name=st.session_state.get(STATE_SOURCE_FILENAME),
                )

                operation_logs = list(result.logs)
                if final_override.override_applied:
                    operation_logs.append(f"Final RDL override applied from {final_override.override_path}")

                st.session_state[STATE_MODIFIED_XML] = final_override.modified_xml
                st.session_state[STATE_OPERATION_LOGS] = operation_logs

                apply_log = {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "stage": "apply_patch",
                    "source_file": st.session_state.get(STATE_SOURCE_FILENAME),
                    "query": st.session_state.get(STATE_QUERY),
                    "patch_validated": st.session_state.get(STATE_PATCH_VALIDATED),
                    "operation_logs": operation_logs,
                    "final_override": {
                        "applied": final_override.override_applied,
                        "path": str(final_override.override_path) if final_override.override_path else None,
                        "reason": final_override.reason,
                    },
                }
                log_path = write_patch_log(log_dir_input, apply_log)
                st.session_state[STATE_LAST_LOG_FILE] = str(log_path)

                st.success("Patch applied successfully.")
            except PatchApplicationError as exc:
                st.error(f"Patch application failed: {exc}")
            except Exception as exc:
                st.error(f"Unexpected error during patch application: {exc}")

if STATE_OPERATION_LOGS in st.session_state:
    st.subheader("Patch Operation Logs")
    for entry in st.session_state[STATE_OPERATION_LOGS]:
        st.write(f"- {entry}")

if STATE_MODIFIED_XML in st.session_state:
    st.subheader("Modified RDL XML")
    st.text_area(
        "Result XML",
        value=st.session_state[STATE_MODIFIED_XML],
        height=360,
    )

    source_name = st.session_state.get(STATE_SOURCE_FILENAME, "report.rdl")
    output_name = f"{Path(source_name).stem}_ai_modified.rdl"
    st.download_button(
        label="Download Modified RDL",
        data=st.session_state[STATE_MODIFIED_XML].encode("utf-8"),
        file_name=output_name,
        mime="application/xml",
    )

if STATE_LAST_LOG_FILE in st.session_state:
    st.caption(f"Last log file: {st.session_state[STATE_LAST_LOG_FILE]}")

with st.expander("End-to-End Example", expanded=False):
    st.write("Sample RDL snippet")
    st.code(SAMPLE_RDL_XML, language="xml")

    st.write("Sample natural language query")
    st.code(SAMPLE_QUERY, language="text")

    st.write("Sample patch JSON")
    st.json(SAMPLE_PATCH_JSON)

    st.write("Sample modified RDL snippet")
    st.code(SAMPLE_MODIFIED_RDL_XML, language="xml")
