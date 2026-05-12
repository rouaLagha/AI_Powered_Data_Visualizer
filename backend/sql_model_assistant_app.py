# from __future__ import annotations

# import copy
# import hashlib
# import html
# import json
# import os
# import shutil
# from datetime import datetime, timezone
# from pathlib import Path
# import re
# import tempfile
# import time
# from typing import Any
# from urllib.parse import urlparse
# import xml.etree.ElementTree as ET
# import zipfile

# import streamlit as st

# from src.rdl_ai_editor.nlp_agent import load_llm_from_config
# from src.rdl_to_twb.db_introspection import build_db_catalog
# from src.rdl_to_twb.rdl_parser import parse_rdl_file
# from src.rdl_to_twb.tableau_extract import build_hyper_extract_from_catalog
# from src.rdl_to_twb.twb_builder import inject_datasource_connections


# ROOT_DIR = Path(__file__).resolve().parent
# SQL_ASSISTANT_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.tableau_cloud_sql_assistant.json"
# DEFAULT_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.json"
# FALLBACK_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.example.json"
# OUTPUT_TEMPLATE_PATH = ROOT_DIR / "output" / "template_semantic_model.twb"
# DEFAULT_TEMPLATE_PATH = ROOT_DIR / "Semantic_model.twb"
# FALLBACK_TEMPLATE_PATH = Path.home() / "Desktop" / "Semantic_model.twb"

# SYSTEM_PROMPT = """You are SQL Model Assistant.
# You read a SQL query and the conversation history, then return the latest dimensional model.
# Output JSON only.

# Return this shape:
# {
#   "assistant_response": "short explanation",
#   "model": {
#     "model_type": "Star | Snowflake | Hybrid | Unknown",
#     "schema_confidence": "high | medium | low",
#     "model_summary": "short plain-language summary",
#     "fact_tables": [
#       {
#         "name": "string",
#         "measures": ["string"],
#         "foreign_keys": ["string"]
#       }
#     ],
#     "direct_dimensions": [
#       {
#         "name": "string",
#         "physical_table": "string",
#         "alias": "string",
#         "semantic_role": "string",
#         "attributes": ["string"],
#         "natural_key": "string"
#       }
#     ],
#     "snowflake_dimensions": [
#       {
#         "name": "string",
#         "physical_table": "string",
#         "alias": "string",
#         "semantic_role": "string",
#         "attributes": ["string"],
#         "natural_key": "string"
#       }
#     ],
#     "relationships": [
#       {
#         "from_table": "string",
#         "to_table": "string",
#         "relationship_type": "fact_to_dimension | dimension_to_dimension",
#         "cardinality": "string",
#         "join_condition": "string"
#       }
#     ],
#     "review_notes": ["string"],
#     "assumptions": ["string"],
#     "warnings": ["string"]
#   }
# }

# Rules:
# - Apply the latest user correction when possible.
# - Return the full current model, not only the delta.
# - Preserve real SQL join paths in join_condition.
# - Use fact-side column names for fact foreign keys.
# - Distinguish fact-to-dimension and dimension-to-dimension relationships.
# - Keep direct_dimensions separate from snowflake_dimensions.
# - Keep role-playing dimensions as distinct semantic instances (same physical table, different alias/role).
# - Use empty strings or empty lists instead of null.
# """

# FACT_HINTS = (
#     "fact",
#     "fct",
#     "sales",
#     "order",
#     "orders",
#     "transaction",
#     "invoice",
#     "payment",
#     "event",
#     "line",
# )
# DIM_HINTS = (
#     "dim",
#     "date",
#     "calendar",
#     "customer",
#     "product",
#     "region",
#     "store",
#     "location",
#     "employee",
#     "lookup",
#     "reference",
# )
# AGG_FUNCS = ("sum", "count", "avg", "min", "max")
# UNIQUE_KEY_SUFFIX_HINTS = ("id", "key", "code", "number", "num")
# NON_KEY_COLUMN_HINTS = (
#     "name",
#     "description",
#     "label",
#     "comment",
#     "amount",
#     "total",
#     "qty",
#     "quantity",
#     "price",
#     "cost",
#     "revenue",
#     "sales",
#     "metric",
#     "value",
# )
# TABLEAU_TRANSIENT_RETRY_DELAYS_SECONDS = (5, 15)
# TABLEAU_RECOVERY_LOOKUP_DELAYS_SECONDS = (0, 10, 30)
# TABLEAU_MAINTENANCE_RETRY_DELAYS_SECONDS = (5, 15)


# def main() -> None:
#     st.set_page_config(page_title="SQL Model Assistant", page_icon=":mag:", layout="wide")
#     st.title("SQL Model Assistant")
#     st.caption(
#         "Upload an RDL report, extract SQL, validate the schema, then update a semantic_model.twb template."
#     )
#     st.info(
#         "For the full multipage app, launch `streamlit_app.py`. "
#         "That keeps `RDL -> TWB Converter` as the main page and shows this assistant as a secondary page."
#     )

#     _init_state()

#     with st.sidebar:
#         st.subheader("Settings")
#         st.session_state.sql_model_assistant_llm_config = st.text_input(
#             "LLM config path",
#             value=st.session_state.sql_model_assistant_llm_config,
#         )
#         _sync_tableau_settings_from_config_if_needed()
#         st.caption("Uses the existing LLM config if available, otherwise falls back to local heuristics.")
#         _render_tableau_publish_settings()
#         if st.button("Reset Conversation"):
#             _reset_state()
#             st.rerun()

#     if st.session_state.sql_model_assistant_sql_query:
#         with st.expander("Current SQL Query", expanded=False):
#             st.code(st.session_state.sql_model_assistant_sql_query, language="sql")
#     else:
#         _render_rdl_intake()

#     if st.session_state.sql_model_assistant_messages:
#         st.markdown("### Chat")
#         for message in st.session_state.sql_model_assistant_messages:
#             with st.chat_message(message["role"]):
#                 st.write(message["content"])
#                 if message["role"] == "assistant":
#                     if message.get("used_fallback"):
#                         st.caption("SQL-evidence mode used for this response.")
#                     _render_structured_result(message.get("structured_result", {}))

#     if st.session_state.sql_model_assistant_sql_query:
#         latest_model = _latest_assistant_model()
#         if latest_model:
#             _render_validation_and_twb_tools(latest_model)

#         follow_up = st.chat_input(
#             "Send a correction, for example: Customer to Sales should be one-to-many."
#         )
#         if follow_up:
#             _handle_follow_up(follow_up.strip())
#             st.rerun()


# def _init_state() -> None:
#     st.session_state.setdefault("sql_model_assistant_pending_sql", "")
#     st.session_state.setdefault("sql_model_assistant_sql_query", "")
#     st.session_state.setdefault("sql_model_assistant_messages", [])
#     st.session_state.setdefault("sql_model_assistant_rdl_report", {})
#     st.session_state.setdefault("sql_model_assistant_rdl_filename", "")
#     st.session_state.setdefault("sql_model_assistant_selected_dataset_name", "")
#     st.session_state.setdefault("sql_model_assistant_selected_datasource_name", "")
#     st.session_state.setdefault("sql_model_assistant_schema_validated", False)
#     st.session_state.setdefault("sql_model_assistant_validated_model", {})
#     st.session_state.setdefault("sql_model_assistant_generated_twb", "")
#     st.session_state.setdefault("sql_model_assistant_generated_twb_name", "validated_semantic_model.twb")
#     st.session_state.setdefault("sql_model_assistant_template_path", _template_default_path())
#     st.session_state.setdefault(
#         "sql_model_assistant_llm_config",
#         str(_preferred_sql_assistant_llm_config_path()),
#     )
#     # Upgrade old sessions that still point to the generic config.
#     current_cfg_value = str(st.session_state.sql_model_assistant_llm_config or "").strip()
#     generic_default_value = str(DEFAULT_LLM_CONFIG)
#     generic_default_resolved = str(DEFAULT_LLM_CONFIG.resolve()) if DEFAULT_LLM_CONFIG.exists() else generic_default_value
#     if SQL_ASSISTANT_LLM_CONFIG.exists() and current_cfg_value in {generic_default_value, generic_default_resolved}:
#         st.session_state.sql_model_assistant_llm_config = str(SQL_ASSISTANT_LLM_CONFIG)

#     tableau_defaults = _load_tableau_publish_defaults(st.session_state.sql_model_assistant_llm_config)
#     st.session_state.setdefault("sql_model_assistant_tableau_auto_publish", True)
#     st.session_state.setdefault("sql_model_assistant_tableau_publish_extract", True)
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_server_url",
#         str(
#             tableau_defaults.get("server_url")
#             or os.getenv("TABLEAU_SERVER_URL")
#             or ""
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_site_content_url",
#         str(
#             tableau_defaults.get("site_content_url")
#             or os.getenv("TABLEAU_SITE_CONTENT_URL")
#             or ""
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_project_name",
#         str(
#             tableau_defaults.get("project_name")
#             or os.getenv("TABLEAU_PROJECT_NAME")
#             or "Default"
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_project_id",
#         str(
#             tableau_defaults.get("project_id")
#             or os.getenv("TABLEAU_PROJECT_ID")
#             or ""
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_username",
#         str(
#             tableau_defaults.get("username")
#             or os.getenv("TABLEAU_USERNAME")
#             or ""
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_password",
#         str(
#             tableau_defaults.get("password")
#             or os.getenv("TABLEAU_PASSWORD")
#             or ""
#         ),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_source_datasource_name",
#         str(tableau_defaults.get("source_datasource_name") or ""),
#     )
#     st.session_state.setdefault(
#         "sql_model_assistant_tableau_loaded_config_path",
#         "",
#     )
#     st.session_state.setdefault("sql_model_assistant_tableau_last_publish_report", {})
#     st.session_state.setdefault("sql_model_assistant_tableau_last_publish_error", "")
#     st.session_state.setdefault("sql_model_assistant_tableau_publish_context", {})


# def _reset_state() -> None:
#     st.session_state.sql_model_assistant_pending_sql = ""
#     st.session_state.sql_model_assistant_sql_query = ""
#     st.session_state.sql_model_assistant_messages = []
#     st.session_state.sql_model_assistant_rdl_report = {}
#     st.session_state.sql_model_assistant_rdl_filename = ""
#     st.session_state.sql_model_assistant_selected_dataset_name = ""
#     st.session_state.sql_model_assistant_selected_datasource_name = ""
#     st.session_state.sql_model_assistant_template_path = _template_default_path()
#     _clear_validation_outputs()


# def _clear_validation_outputs() -> None:
#     st.session_state.sql_model_assistant_schema_validated = False
#     st.session_state.sql_model_assistant_validated_model = {}
#     st.session_state.sql_model_assistant_generated_twb = ""
#     st.session_state.sql_model_assistant_generated_twb_name = "validated_semantic_model.twb"
#     st.session_state.sql_model_assistant_tableau_last_publish_report = {}
#     st.session_state.sql_model_assistant_tableau_last_publish_error = ""
#     st.session_state.sql_model_assistant_tableau_publish_context = {}


# def _start_conversation(sql_query: str) -> None:
#     st.session_state.sql_model_assistant_sql_query = sql_query
#     st.session_state.sql_model_assistant_messages = [
#         {
#             "role": "user",
#             "content": "Analyze this SQL query and produce the current dimensional model.",
#         }
#     ]
#     _clear_validation_outputs()
#     _append_assistant_response()


# def _handle_follow_up(message: str) -> None:
#     if not message:
#         return
#     _clear_validation_outputs()
#     st.session_state.sql_model_assistant_messages.append(
#         {
#             "role": "user",
#             "content": message,
#         }
#     )
#     _append_assistant_response()


# def _append_assistant_response() -> None:
#     sql_query = st.session_state.sql_model_assistant_sql_query
#     conversation = st.session_state.sql_model_assistant_messages
#     llm_config_path = st.session_state.sql_model_assistant_llm_config

#     with st.spinner("Building dimensional model..."):
#         assistant_text, structured_result, used_fallback = _generate_response(
#             sql_query=sql_query,
#             conversation=conversation,
#             llm_config_path=llm_config_path,
#         )

#     st.session_state.sql_model_assistant_messages.append(
#         {
#             "role": "assistant",
#             "content": assistant_text,
#             "structured_result": structured_result,
#             "used_fallback": used_fallback,
#         }
#     )


# def _template_default_path() -> str:
#     if OUTPUT_TEMPLATE_PATH.exists():
#         return str(OUTPUT_TEMPLATE_PATH)
#     if DEFAULT_TEMPLATE_PATH.exists():
#         return str(DEFAULT_TEMPLATE_PATH)
#     if FALLBACK_TEMPLATE_PATH.exists():
#         return str(FALLBACK_TEMPLATE_PATH)
#     return str(OUTPUT_TEMPLATE_PATH)


# def _preferred_sql_assistant_llm_config_path() -> Path:
#     for candidate in [SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG]:
#         if candidate.exists():
#             return candidate
#     return SQL_ASSISTANT_LLM_CONFIG


# def _render_tableau_publish_settings() -> None:
#     st.markdown("---")
#     st.subheader("Tableau Cloud Publish")
#     st.session_state.sql_model_assistant_tableau_auto_publish = st.checkbox(
#         "Auto publish after TWB generation",
#         value=bool(st.session_state.sql_model_assistant_tableau_auto_publish),
#         key="sql_model_assistant_tableau_auto_publish_input",
#     )
#     st.session_state.sql_model_assistant_tableau_publish_extract = st.checkbox(
#         "Publish datasource as Extract",
#         value=bool(st.session_state.sql_model_assistant_tableau_publish_extract),
#         key="sql_model_assistant_tableau_publish_extract_input",
#         help="When enabled, build a .hyper extract and publish the datasource as .tdsx instead of live.",
#     )
#     st.session_state.sql_model_assistant_tableau_server_url = st.text_input(
#         "Server URL",
#         value=st.session_state.sql_model_assistant_tableau_server_url,
#         key="sql_model_assistant_tableau_server_url_input",
#         help="Example: https://<pod>.online.tableau.com",
#     )
#     st.session_state.sql_model_assistant_tableau_site_content_url = st.text_input(
#         "Site Content URL (URI)",
#         value=st.session_state.sql_model_assistant_tableau_site_content_url,
#         key="sql_model_assistant_tableau_site_content_url_input",
#         help="Example: your-site-content-url",
#     )
#     st.session_state.sql_model_assistant_tableau_project_id = st.text_input(
#         "Project ID (optional)",
#         value=st.session_state.sql_model_assistant_tableau_project_id,
#         key="sql_model_assistant_tableau_project_id_input",
#         help="Recommended when your account cannot publish to the default project.",
#     )
#     st.session_state.sql_model_assistant_tableau_project_name = st.text_input(
#         "Project Name",
#         value=st.session_state.sql_model_assistant_tableau_project_name,
#         key="sql_model_assistant_tableau_project_name_input",
#     )
#     st.session_state.sql_model_assistant_tableau_username = st.text_input(
#         "Username",
#         value=st.session_state.sql_model_assistant_tableau_username,
#         key="sql_model_assistant_tableau_username_input",
#     )
#     st.session_state.sql_model_assistant_tableau_password = st.text_input(
#         "Password",
#         value=st.session_state.sql_model_assistant_tableau_password,
#         type="password",
#         key="sql_model_assistant_tableau_password_input",
#     )
#     st.session_state.sql_model_assistant_tableau_source_datasource_name = st.text_input(
#         "Source Datasource Name (optional)",
#         value=st.session_state.sql_model_assistant_tableau_source_datasource_name,
#         key="sql_model_assistant_tableau_source_ds_name_input",
#         help="If empty, the first non-Parameters datasource in the generated TWB is used.",
#     )


# def _sync_tableau_settings_from_config_if_needed() -> None:
#     current_config_path = str(st.session_state.sql_model_assistant_llm_config or "").strip()
#     if not current_config_path:
#         current_config_path = str(_preferred_sql_assistant_llm_config_path())
#         st.session_state.sql_model_assistant_llm_config = current_config_path
#     loaded_config_path = str(st.session_state.get("sql_model_assistant_tableau_loaded_config_path", "") or "").strip()
#     defaults = _load_tableau_publish_defaults(current_config_path)
#     current_server = str(st.session_state.get("sql_model_assistant_tableau_server_url", "") or "").strip().lower()
#     defaults_server = str(defaults.get("server_url") or "").strip().lower()
#     stale_public_server = (
#         current_server == "https://public.tableau.com"
#         and defaults_server
#         and defaults_server != "https://public.tableau.com"
#     )

#     if current_config_path == loaded_config_path and not stale_public_server:
#         return

#     if defaults:
#         if defaults.get("server_url"):
#             st.session_state.sql_model_assistant_tableau_server_url = str(defaults["server_url"])
#             st.session_state.sql_model_assistant_tableau_server_url_input = str(defaults["server_url"])
#         if defaults.get("site_content_url"):
#             st.session_state.sql_model_assistant_tableau_site_content_url = str(defaults["site_content_url"])
#             st.session_state.sql_model_assistant_tableau_site_content_url_input = str(defaults["site_content_url"])
#         if defaults.get("project_id"):
#             st.session_state.sql_model_assistant_tableau_project_id = str(defaults["project_id"])
#             st.session_state.sql_model_assistant_tableau_project_id_input = str(defaults["project_id"])
#         if defaults.get("project_name"):
#             st.session_state.sql_model_assistant_tableau_project_name = str(defaults["project_name"])
#             st.session_state.sql_model_assistant_tableau_project_name_input = str(defaults["project_name"])
#         if defaults.get("username"):
#             st.session_state.sql_model_assistant_tableau_username = str(defaults["username"])
#             st.session_state.sql_model_assistant_tableau_username_input = str(defaults["username"])
#         if defaults.get("password"):
#             st.session_state.sql_model_assistant_tableau_password = str(defaults["password"])
#             st.session_state.sql_model_assistant_tableau_password_input = str(defaults["password"])
#         if defaults.get("source_datasource_name"):
#             st.session_state.sql_model_assistant_tableau_source_datasource_name = str(
#                 defaults["source_datasource_name"]
#             )
#             st.session_state.sql_model_assistant_tableau_source_ds_name_input = str(
#                 defaults["source_datasource_name"]
#             )

#     st.session_state.sql_model_assistant_tableau_loaded_config_path = current_config_path


# def _load_tableau_publish_defaults(config_path: str) -> dict[str, str]:
#     defaults: dict[str, str] = {}
#     raw_path = Path(str(config_path or "")).expanduser()
#     candidates = [raw_path]
#     if not raw_path.is_absolute():
#         candidates.append(ROOT_DIR / raw_path)

#     resolved_path: Path | None = None
#     for candidate in candidates:
#         if candidate.exists():
#             resolved_path = candidate
#             break
#     if resolved_path is None:
#         return defaults

#     try:
#         payload = json.loads(resolved_path.read_text(encoding="utf-8"))
#     except Exception:
#         return defaults

#     tableau_cfg = None
#     if isinstance(payload, dict):
#         # Prefer a Cloud-specific key name, but keep backward compatibility.
#         candidate = payload.get("tableau_cloud")
#         if isinstance(candidate, dict):
#             tableau_cfg = candidate
#         else:
#             legacy_candidate = payload.get("tableau_server")
#             if isinstance(legacy_candidate, dict):
#                 tableau_cfg = legacy_candidate
#     if not isinstance(tableau_cfg, dict):
#         return defaults

#     for key in ["server_url", "site_content_url", "project_id", "project_name", "source_datasource_name"]:
#         value = tableau_cfg.get(key)
#         if isinstance(value, str) and value.strip():
#             defaults[key] = value.strip()

#     username = _resolve_config_secret(tableau_cfg.get("username"), fallback_env="TABLEAU_USERNAME")
#     if username:
#         defaults["username"] = username

#     password = _resolve_config_secret(tableau_cfg.get("password"), fallback_env="TABLEAU_PASSWORD")
#     if password:
#         defaults["password"] = password

#     return defaults


# def _resolve_config_secret(value: object, fallback_env: str) -> str | None:
#     if isinstance(value, str):
#         stripped = value.strip()
#         if stripped:
#             if stripped.lower().startswith("env:"):
#                 env_name = stripped.split(":", 1)[1].strip()
#                 if not env_name:
#                     return None
#                 env_value = os.getenv(env_name)
#                 if isinstance(env_value, str) and env_value.strip():
#                     return env_value.strip()
#                 return None
#             return stripped

#     env_fallback = os.getenv(fallback_env)
#     if isinstance(env_fallback, str) and env_fallback.strip():
#         return env_fallback.strip()
#     return None


# def _render_rdl_intake() -> None:
#     st.markdown("### First Step")
#     uploaded_rdl = st.file_uploader(
#         "Upload report (.rdl)",
#         type=["rdl"],
#         key="sql_model_assistant_rdl_upload",
#     )

#     action_col, clear_col = st.columns(2)
#     with action_col:
#         if st.button("Extract SQL from RDL", type="primary", key="extract_sql_from_rdl"):
#             if uploaded_rdl is None:
#                 st.warning("Please upload an RDL file first.")
#             else:
#                 try:
#                     report = _parse_uploaded_rdl(uploaded_rdl)
#                 except Exception as exc:
#                     st.error(f"Failed to parse RDL: {exc}")
#                 else:
#                     st.session_state.sql_model_assistant_rdl_report = report
#                     st.session_state.sql_model_assistant_rdl_filename = str(
#                         getattr(uploaded_rdl, "name", "uploaded_report.rdl")
#                     )
#                     st.session_state.sql_model_assistant_selected_dataset_name = _pick_default_dataset_name(
#                         report.get("data_sets", [])
#                     )
#                     datasource, _dataset = _resolve_current_rdl_context()
#                     st.session_state.sql_model_assistant_selected_datasource_name = str(
#                         datasource.get("name", "") if datasource else ""
#                     )
#                     _clear_validation_outputs()
#                     st.rerun()

#     with clear_col:
#         if st.button("Clear RDL Selection", key="clear_rdl_selection"):
#             st.session_state.sql_model_assistant_rdl_report = {}
#             st.session_state.sql_model_assistant_rdl_filename = ""
#             st.session_state.sql_model_assistant_selected_dataset_name = ""
#             st.session_state.sql_model_assistant_selected_datasource_name = ""
#             st.session_state.sql_model_assistant_pending_sql = ""
#             _clear_validation_outputs()
#             st.rerun()

#     report = st.session_state.sql_model_assistant_rdl_report
#     if not isinstance(report, dict) or not report:
#         st.caption("Upload an RDL file, then extract SQL from one dataset.")
#         return

#     data_sets = report.get("data_sets", [])
#     if not isinstance(data_sets, list) or not data_sets:
#         st.error("No datasets were found in this RDL file.")
#         return

#     dataset_names = [
#         str(dataset.get("name", "")).strip()
#         for dataset in data_sets
#         if isinstance(dataset, dict) and str(dataset.get("name", "")).strip()
#     ]
#     dataset_names = _unique(dataset_names)
#     if not dataset_names:
#         st.error("The RDL datasets do not contain valid dataset names.")
#         return

#     current_name = str(st.session_state.sql_model_assistant_selected_dataset_name or "").strip()
#     if current_name not in dataset_names:
#         current_name = dataset_names[0]
#         st.session_state.sql_model_assistant_selected_dataset_name = current_name

#     selected_name = st.selectbox(
#         "Select dataset/query to analyze",
#         options=dataset_names,
#         index=dataset_names.index(current_name),
#     )
#     st.session_state.sql_model_assistant_selected_dataset_name = selected_name

#     dataset = _find_dataset_by_name(data_sets, selected_name)
#     if not dataset:
#         st.error("Could not resolve the selected dataset.")
#         return

#     datasource = _find_datasource_for_dataset(report, dataset)
#     st.session_state.sql_model_assistant_selected_datasource_name = str(
#         datasource.get("name", "") if datasource else ""
#     )

#     query = str(dataset.get("query", "") or "").strip()
#     if query:
#         st.code(query, language="sql")
#     else:
#         st.warning("The selected dataset does not contain a SQL CommandText.")

#     with st.expander("RDL datasource details", expanded=False):
#         if datasource:
#             info = datasource.get("connection_info", {}) if isinstance(datasource, dict) else {}
#             st.write({
#                 "name": datasource.get("name", ""),
#                 "provider": datasource.get("provider", ""),
#                 "provider_class": datasource.get("provider_class", ""),
#                 "server": info.get("server", "") if isinstance(info, dict) else "",
#                 "database": info.get("database", "") if isinstance(info, dict) else "",
#                 "security_type": datasource.get("security_type", ""),
#             })
#         else:
#             st.caption("No datasource metadata found for this dataset.")

#     if st.button("Analyze Extracted SQL", type="primary", key="analyze_rdl_sql"):
#         if not query:
#             st.warning("The selected dataset has no SQL query to analyze.")
#             return
#         _start_conversation(query)
#         st.rerun()


# def _parse_uploaded_rdl(uploaded_rdl: Any) -> dict[str, Any]:
#     payload = uploaded_rdl.getvalue()
#     if not payload:
#         raise ValueError("Uploaded RDL file is empty.")

#     file_name = str(getattr(uploaded_rdl, "name", "uploaded_report.rdl"))
#     suffix = Path(file_name).suffix or ".rdl"
#     temp_path: Path | None = None

#     try:
#         with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
#             handle.write(payload)
#             temp_path = Path(handle.name)

#         parsed_report = parse_rdl_file(temp_path)
#         return parsed_report.to_dict()
#     finally:
#         if temp_path is not None and temp_path.exists():
#             temp_path.unlink(missing_ok=True)


# def _pick_default_dataset_name(data_sets: Any) -> str:
#     if not isinstance(data_sets, list):
#         return ""

#     first_named = ""
#     for dataset in data_sets:
#         if not isinstance(dataset, dict):
#             continue
#         name = str(dataset.get("name", "") or "").strip()
#         if not name:
#             continue
#         if not first_named:
#             first_named = name
#         query = str(dataset.get("query", "") or "").strip()
#         if query:
#             return name
#     return first_named


# def _find_dataset_by_name(data_sets: Any, dataset_name: str) -> dict[str, Any]:
#     if not isinstance(data_sets, list):
#         return {}
#     target = str(dataset_name or "").strip()
#     if not target:
#         return {}
#     for dataset in data_sets:
#         if not isinstance(dataset, dict):
#             continue
#         if str(dataset.get("name", "") or "").strip() == target:
#             return dataset
#     return {}


# def _find_datasource_for_dataset(report: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
#     data_sources = report.get("data_sources", []) if isinstance(report, dict) else []
#     if not isinstance(data_sources, list):
#         return {}

#     data_source_name = str(dataset.get("data_source_name", "") or "").strip() if isinstance(dataset, dict) else ""
#     if data_source_name:
#         for source in data_sources:
#             if not isinstance(source, dict):
#                 continue
#             if str(source.get("name", "") or "").strip() == data_source_name:
#                 return source

#     for source in data_sources:
#         if isinstance(source, dict):
#             return source
#     return {}


# def _resolve_current_rdl_context() -> tuple[dict[str, Any], dict[str, Any]]:
#     report = st.session_state.sql_model_assistant_rdl_report
#     if not isinstance(report, dict) or not report:
#         return {}, {}

#     data_sets = report.get("data_sets", [])
#     selected_name = str(st.session_state.sql_model_assistant_selected_dataset_name or "").strip()
#     dataset = _find_dataset_by_name(data_sets, selected_name)
#     if not dataset:
#         fallback_name = _pick_default_dataset_name(data_sets)
#         dataset = _find_dataset_by_name(data_sets, fallback_name)
#         if fallback_name:
#             st.session_state.sql_model_assistant_selected_dataset_name = fallback_name

#     datasource = _find_datasource_for_dataset(report, dataset) if dataset else {}
#     return datasource, dataset


# def _latest_assistant_model() -> dict[str, Any]:
#     for message in reversed(st.session_state.sql_model_assistant_messages):
#         if message.get("role") != "assistant":
#             continue
#         structured_result = message.get("structured_result")
#         if isinstance(structured_result, dict) and structured_result:
#             return structured_result
#     return {}


# def _render_validation_and_twb_tools(latest_model: dict[str, Any]) -> None:
#     st.markdown("### Validation & Template Update")
#     if st.button("Validate Schema", type="primary", key="validate_star_schema"):
#         st.session_state.sql_model_assistant_schema_validated = True
#         st.session_state.sql_model_assistant_validated_model = copy.deepcopy(latest_model)
#         st.success("Schema validated.")

#     if not st.session_state.sql_model_assistant_schema_validated:
#         return

#     st.success("Validated model locked for template update.")

#     datasource, dataset = _resolve_current_rdl_context()
#     if not datasource or not dataset:
#         st.error("RDL context is missing. Re-upload the report and analyze a dataset before generating TWB.")
#         return

#     template_upload = st.file_uploader(
#         "Optional: upload the semantic model template (.twb)",
#         type=["twb"],
#         key="sql_model_assistant_template_upload",
#     )
#     template_path = st.text_input(
#         "Template path (.twb)",
#         value=st.session_state.sql_model_assistant_template_path,
#         key="sql_model_assistant_template_path_input",
#     )
#     st.session_state.sql_model_assistant_template_path = template_path

#     output_name = st.text_input(
#         "Output TWB filename",
#         value=st.session_state.sql_model_assistant_generated_twb_name,
#         key="sql_model_assistant_output_twb_name",
#     )

#     if st.button("Update Template TWB", type="primary", key="update_template_twb"):
#         try:
#             template_xml = _load_template_xml(template_upload, template_path)
#             validated_model = st.session_state.sql_model_assistant_validated_model
#             generated_xml = _generate_twb_from_validated_model(
#                 template_xml=template_xml,
#                 data_source=datasource,
#                 dataset=dataset,
#                 validated_model=validated_model,
#             )
#             st.session_state.sql_model_assistant_tableau_publish_context = _build_tableau_publish_context(
#                 template_xml=template_xml,
#                 data_source=datasource,
#                 dataset=dataset,
#                 validated_model=validated_model,
#             )
#             safe_name = _safe_output_twb_name(output_name)
#             st.session_state.sql_model_assistant_generated_twb = generated_xml
#             st.session_state.sql_model_assistant_generated_twb_name = safe_name

#             output_path = ROOT_DIR / "output" / safe_name
#             output_path.parent.mkdir(parents=True, exist_ok=True)
#             output_path.write_text(generated_xml, encoding="utf-8")
#             st.success(f"Generated TWB saved to: {output_path}")

#             if st.session_state.sql_model_assistant_tableau_auto_publish:
#                 try:
#                     with st.spinner(
#                         "Publishing validated semantic model to Tableau Cloud "
#                         "(datasource with timestamp + linked workbook)..."
#                     ):
#                         publish_report = _run_tableau_cloud_publish_workflow(output_path)
#                     st.session_state.sql_model_assistant_tableau_last_publish_report = publish_report
#                     st.session_state.sql_model_assistant_tableau_last_publish_error = ""
#                     st.success(
#                         "Tableau Cloud publish succeeded: "
#                         f"datasource '{publish_report.get('datasource_name', '-')}', "
#                         f"workbook '{publish_report.get('workbook_name', '-')}'."
#                     )
#                 except Exception as publish_exc:
#                     formatted_error = _tableau_format_publish_error(publish_exc)
#                     st.session_state.sql_model_assistant_tableau_last_publish_error = formatted_error
#                     st.error(f"Tableau Cloud publish failed: {formatted_error}")
#         except Exception as exc:
#             st.error(f"Failed to build TWB: {exc}")

#     generated_twb = st.session_state.sql_model_assistant_generated_twb
#     generated_twb_path = ROOT_DIR / "output" / st.session_state.sql_model_assistant_generated_twb_name
#     if generated_twb:
#         st.download_button(
#             "Download Generated TWB",
#             data=generated_twb.encode("utf-8"),
#             file_name=st.session_state.sql_model_assistant_generated_twb_name,
#             mime="application/xml",
#             key="download_generated_twb",
#         )
#         if st.button("Publish to Tableau Cloud Now", key="publish_generated_twb_now"):
#             try:
#                 with st.spinner(
#                     "Publishing validated semantic model to Tableau Cloud "
#                     "(datasource with timestamp + linked workbook)..."
#                 ):
#                     publish_report = _run_tableau_cloud_publish_workflow(generated_twb_path)
#                 st.session_state.sql_model_assistant_tableau_last_publish_report = publish_report
#                 st.session_state.sql_model_assistant_tableau_last_publish_error = ""
#                 st.success(
#                     "Tableau Cloud publish succeeded: "
#                     f"datasource '{publish_report.get('datasource_name', '-')}', "
#                     f"workbook '{publish_report.get('workbook_name', '-')}'."
#                 )
#             except Exception as exc:
#                 formatted_error = _tableau_format_publish_error(exc)
#                 st.session_state.sql_model_assistant_tableau_last_publish_error = formatted_error
#                 st.error(f"Tableau Cloud publish failed: {formatted_error}")

#     publish_report = st.session_state.sql_model_assistant_tableau_last_publish_report
#     publish_error = str(st.session_state.sql_model_assistant_tableau_last_publish_error or "").strip()
#     if publish_report:
#         st.markdown("#### Tableau Cloud Publish Report")
#         st.json(publish_report)
#     elif publish_error:
#         st.error(f"Last Tableau Cloud publish error: {publish_error}")


# def _safe_output_twb_name(value: str) -> str:
#     name = Path(str(value or "validated_semantic_model.twb").strip()).name or "validated_semantic_model.twb"
#     if not name.lower().endswith(".twb"):
#         name = f"{name}.twb"
#     return name


# def _run_tableau_cloud_publish_workflow(generated_twb_path: Path) -> dict[str, Any]:
#     if not generated_twb_path.exists():
#         raise FileNotFoundError(f"Generated TWB file not found: {generated_twb_path}")

#     cfg_defaults = _load_tableau_publish_defaults(str(st.session_state.sql_model_assistant_llm_config or ""))

#     raw_server_url = str(st.session_state.sql_model_assistant_tableau_server_url or "").strip()
#     server_url = _tableau_normalize_server_url(raw_server_url)
#     site_content_url = str(st.session_state.sql_model_assistant_tableau_site_content_url or "").strip()
#     project_id = str(st.session_state.sql_model_assistant_tableau_project_id or "").strip()
#     project_name = str(st.session_state.sql_model_assistant_tableau_project_name or "").strip()
#     username = str(st.session_state.sql_model_assistant_tableau_username or "").strip()
#     password = str(st.session_state.sql_model_assistant_tableau_password or "")
#     source_datasource_name = str(st.session_state.sql_model_assistant_tableau_source_datasource_name or "").strip()
#     publish_extract = bool(st.session_state.sql_model_assistant_tableau_publish_extract)

#     # If stale sidebar state still points to Tableau Public, prefer the config file value.
#     cfg_server_url = str(cfg_defaults.get("server_url") or "").strip()
#     if "public.tableau.com" in server_url.lower() and cfg_server_url and "public.tableau.com" not in cfg_server_url.lower():
#         server_url = _tableau_normalize_server_url(cfg_server_url)
#         st.session_state.sql_model_assistant_tableau_server_url = server_url
#         st.session_state.sql_model_assistant_tableau_server_url_input = server_url

#     if not site_content_url:
#         inferred_site_content_url = _tableau_extract_site_content_url(raw_server_url)
#         if inferred_site_content_url:
#             site_content_url = inferred_site_content_url
#             st.session_state.sql_model_assistant_tableau_site_content_url = site_content_url
#             st.session_state.sql_model_assistant_tableau_site_content_url_input = site_content_url

#     if not site_content_url and cfg_defaults.get("site_content_url"):
#         site_content_url = str(cfg_defaults.get("site_content_url") or "").strip()
#         st.session_state.sql_model_assistant_tableau_site_content_url = site_content_url
#         st.session_state.sql_model_assistant_tableau_site_content_url_input = site_content_url
#     if not project_id and cfg_defaults.get("project_id"):
#         project_id = str(cfg_defaults.get("project_id") or "").strip()
#         st.session_state.sql_model_assistant_tableau_project_id = project_id
#         st.session_state.sql_model_assistant_tableau_project_id_input = project_id
#     if not project_name and cfg_defaults.get("project_name"):
#         project_name = str(cfg_defaults.get("project_name") or "").strip()
#         st.session_state.sql_model_assistant_tableau_project_name = project_name
#         st.session_state.sql_model_assistant_tableau_project_name_input = project_name
#     if not username and cfg_defaults.get("username"):
#         username = str(cfg_defaults.get("username") or "").strip()
#         st.session_state.sql_model_assistant_tableau_username = username
#         st.session_state.sql_model_assistant_tableau_username_input = username
#     if not password and cfg_defaults.get("password"):
#         password = str(cfg_defaults.get("password") or "")
#         st.session_state.sql_model_assistant_tableau_password = password
#         st.session_state.sql_model_assistant_tableau_password_input = password
#     if not source_datasource_name and cfg_defaults.get("source_datasource_name"):
#         source_datasource_name = str(cfg_defaults.get("source_datasource_name") or "").strip()
#         st.session_state.sql_model_assistant_tableau_source_datasource_name = source_datasource_name
#         st.session_state.sql_model_assistant_tableau_source_ds_name_input = source_datasource_name

#     if not server_url:
#         raise ValueError("Missing Tableau Cloud Server URL.")
#     if "public.tableau.com" in server_url.lower():
#         raise ValueError("Tableau Public is not supported for this REST publish workflow. Use Tableau Cloud URL.")
#     if not username or not password:
#         raise ValueError("Missing Tableau Cloud username/password in SQL Model Assistant sidebar settings.")

#     try:
#         import tableauserverclient as TSC
#     except Exception as exc:
#         raise RuntimeError(
#             "tableauserverclient is required for Tableau Cloud publish from SQL Model Assistant. "
#             "Install it with: pip install tableauserverclient"
#         ) from exc

#     timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
#     datasource_name = _tableau_timestamped_name("validated_semantic_model", timestamp_utc)
#     workbook_name = _tableau_timestamped_name(f"{generated_twb_path.stem}_consumer", timestamp_utc)
#     extract_report: dict[str, Any] | None = None

#     with tempfile.TemporaryDirectory(prefix="sql_model_assistant_tableau_publish_") as tmp_raw:
#         tmp_dir = Path(tmp_raw)

#         if publish_extract:
#             tds_path, resolved_source_datasource_name, extract_report = _build_extract_datasource_package_for_publish(
#                 source_twb_path=generated_twb_path,
#                 output_dir=tmp_dir,
#                 datasource_name=datasource_name,
#                 source_datasource_name=source_datasource_name,
#             )
#         else:
#             tds_path, resolved_source_datasource_name = _build_datasource_tds_from_workbook(
#                 source_twb_path=generated_twb_path,
#                 output_tds_path=tmp_dir / f"{_tableau_safe_name(datasource_name)}.tds",
#                 datasource_name=datasource_name,
#                 source_datasource_name=source_datasource_name,
#                 strict_mode=False,
#             )

#         auth = TSC.TableauAuth(username=username, password=password, site_id=site_content_url)
#         server = TSC.Server(server_url, use_server_version=True)

#         selected_project_id = ""
#         selected_project_name = ""
#         published_datasource = None
#         published_workbook = None
#         sign_out_warning = ""
#         signed_in = False

#         try:
#             _tableau_call_with_transient_retries(
#                 action=lambda: server.auth.sign_in(auth),
#                 action_label="sign in to Tableau Cloud",
#             )
#             signed_in = True
#             project_candidates = _tableau_resolve_project_candidates(
#                 server=server,
#                 tsc_module=TSC,
#                 requested_project_id=project_id,
#                 requested_project_name=project_name,
#             )
#             selected_project_id = ""
#             selected_project_name = ""
#             published_datasource = None
#             published_workbook = None
#             project_permission_errors: list[str] = []

#             for project_candidate in project_candidates:
#                 candidate_project_id = str(project_candidate.get("id", "")).strip()
#                 candidate_project_name = str(project_candidate.get("name", "")).strip()

#                 datasource_item = TSC.DatasourceItem(project_id=candidate_project_id, name=datasource_name)
#                 try:
#                     published_datasource = _tableau_publish_datasource_with_fallback(
#                         server=server,
#                         datasource_item=datasource_item,
#                         datasource_path=tds_path,
#                         publish_mode=TSC.Server.PublishMode.CreateNew,
#                     )
#                 except Exception as datasource_publish_exc:
#                     if publish_extract:
#                         if _tableau_is_permission_error(datasource_publish_exc):
#                             project_permission_errors.append(
#                                 f"{candidate_project_name or candidate_project_id}: {datasource_publish_exc}"
#                             )
#                             continue

#                         recovered_datasource = None
#                         async_publish_error = ""
#                         if _tableau_is_transient_publish_error(datasource_publish_exc):
#                             recovered_datasource = _tableau_find_published_datasource_by_name(
#                                 server=server,
#                                 tsc_module=TSC,
#                                 datasource_name=datasource_name,
#                                 project_id=candidate_project_id,
#                             )
#                             if recovered_datasource is None:
#                                 try:
#                                     recovered_datasource = _tableau_publish_datasource_as_job(
#                                         server=server,
#                                         tsc_module=TSC,
#                                         datasource_item=datasource_item,
#                                         datasource_path=tds_path,
#                                         publish_mode=TSC.Server.PublishMode.CreateNew,
#                                     )
#                                 except Exception as async_exc:
#                                     async_publish_error = f" Async job retry also failed: {async_exc}."
#                         if recovered_datasource is not None:
#                             published_datasource = recovered_datasource
#                             try:
#                                 st.warning(
#                                     "Tableau Cloud returned a transient error after datasource upload, "
#                                     "but the datasource now exists on the site. Continuing with workbook publish."
#                                 )
#                             except Exception:
#                                 pass
#                         else:
#                             debug_artifact_path = _tableau_copy_publish_artifact_for_debug(
#                                 source_path=tds_path,
#                                 timestamp_utc=timestamp_utc,
#                                 label="failed_datasource_upload",
#                             )
#                             debug_detail = ""
#                             if debug_artifact_path:
#                                 debug_detail = (
#                                     " Saved the generated datasource package for manual upload/testing at: "
#                                     f"{debug_artifact_path}"
#                                 )
#                             raise RuntimeError(
#                                 "Failed to publish extract datasource to Tableau Cloud. "
#                                 f"Error: {datasource_publish_exc}.{async_publish_error}{debug_detail}"
#                             ) from datasource_publish_exc
#                     else:
#                         recovered_datasource = None
#                         if _tableau_is_transient_publish_error(datasource_publish_exc):
#                             recovered_datasource = _tableau_find_published_datasource_by_name(
#                                 server=server,
#                                 tsc_module=TSC,
#                                 datasource_name=datasource_name,
#                                 project_id=candidate_project_id,
#                             )
#                         if recovered_datasource is not None:
#                             published_datasource = recovered_datasource
#                         else:
#                             strict_tds_path, strict_selected_name = _build_datasource_tds_from_workbook(
#                                 source_twb_path=generated_twb_path,
#                                 output_tds_path=tmp_dir / f"{_tableau_safe_name(datasource_name)}_strict.tds",
#                                 datasource_name=datasource_name,
#                                 source_datasource_name=source_datasource_name,
#                                 strict_mode=True,
#                             )
#                             if strict_selected_name:
#                                 resolved_source_datasource_name = strict_selected_name
#                             try:
#                                 published_datasource = _tableau_publish_datasource_with_fallback(
#                                     server=server,
#                                     datasource_item=datasource_item,
#                                     datasource_path=strict_tds_path,
#                                     publish_mode=TSC.Server.PublishMode.CreateNew,
#                                 )
#                             except Exception as strict_publish_exc:
#                                 combined_error = RuntimeError(
#                                     "Failed to publish datasource to Tableau Cloud. "
#                                     f"Primary attempt error: {datasource_publish_exc}. "
#                                     f"Strict retry error: {strict_publish_exc}."
#                                 )
#                                 if _tableau_is_permission_error(combined_error):
#                                     project_permission_errors.append(
#                                         f"{candidate_project_name or candidate_project_id}: {combined_error}"
#                                     )
#                                     continue
#                                 raise combined_error from strict_publish_exc

#                 if published_datasource is None:
#                     raise RuntimeError(
#                         "Datasource publish did not return a Tableau datasource item."
#                     )

#                 linked_twb_path = tmp_dir / f"{_tableau_safe_name(workbook_name)}.twb"
#                 _build_linked_workbook_for_published_datasource(
#                     source_twb_path=generated_twb_path,
#                     output_twb_path=linked_twb_path,
#                     published_datasource=published_datasource,
#                     server_url=server_url,
#                     site_content_url=site_content_url,
#                     source_datasource_name=resolved_source_datasource_name,
#                 )

#                 workbook_item = TSC.WorkbookItem(project_id=candidate_project_id, name=workbook_name)
#                 try:
#                     published_workbook = _tableau_publish_workbook_with_fallback(
#                         server=server,
#                         workbook_item=workbook_item,
#                         workbook_path=linked_twb_path,
#                         publish_mode=TSC.Server.PublishMode.CreateNew,
#                     )
#                 except Exception as publish_exc:
#                     message = str(publish_exc)
#                     if not _tableau_should_retry_packaged_publish(message):
#                         if _tableau_is_permission_error(publish_exc):
#                             project_permission_errors.append(
#                                 f"{candidate_project_name or candidate_project_id}: {publish_exc}"
#                             )
#                             continue
#                         raise
#                     linked_twbx_path = tmp_dir / f"{_tableau_safe_name(workbook_name)}.twbx"
#                     _tableau_package_twb_as_twbx(
#                         twb_path=linked_twb_path,
#                         twbx_path=linked_twbx_path,
#                     )
#                     try:
#                         published_workbook = _tableau_publish_workbook_with_fallback(
#                             server=server,
#                             workbook_item=workbook_item,
#                             workbook_path=linked_twbx_path,
#                             publish_mode=TSC.Server.PublishMode.CreateNew,
#                         )
#                     except Exception as twbx_publish_exc:
#                         if _tableau_is_permission_error(twbx_publish_exc):
#                             project_permission_errors.append(
#                                 f"{candidate_project_name or candidate_project_id}: {twbx_publish_exc}"
#                             )
#                             continue
#                         raise

#                 selected_project_id = candidate_project_id
#                 selected_project_name = candidate_project_name
#                 break

#             if not selected_project_id:
#                 if project_permission_errors:
#                     attempted = "; ".join(project_permission_errors)
#                     raise PermissionError(
#                         "User does not have publish permission on the attempted Tableau project(s). "
#                         f"Attempted: {attempted}. "
#                         "Set a writable Project ID/Project Name in SQL Model Assistant settings, "
#                         "or ask your Tableau admin to grant publish rights (datasource + workbook)."
#                     )
#                 raise RuntimeError(
#                     "Failed to publish to Tableau Cloud project. "
#                     "No writable project candidate succeeded."
#                 )
#         finally:
#             if signed_in:
#                 sign_out_warning = _tableau_sign_out_safely(server)

#     return {
#         "status": "published",
#         "timestamp_utc": timestamp_utc,
#         "server_url": server_url,
#         "site_content_url": site_content_url,
#         "datasource_publish_mode": "extract" if publish_extract else "live",
#         "extract_report": extract_report if publish_extract else {},
#         "project_id": selected_project_id,
#         "project_name": selected_project_name or project_name,
#         "source_datasource_name": resolved_source_datasource_name,
#         "datasource_name": getattr(published_datasource, "name", datasource_name),
#         "datasource_id": getattr(published_datasource, "id", None),
#         "datasource_content_url": getattr(published_datasource, "content_url", None),
#         "datasource_webpage_url": getattr(published_datasource, "webpage_url", None),
#         "workbook_name": getattr(published_workbook, "name", workbook_name),
#         "workbook_id": getattr(published_workbook, "id", None),
#         "workbook_content_url": getattr(published_workbook, "content_url", None),
#         "workbook_webpage_url": getattr(published_workbook, "webpage_url", None),
#         "sign_out_warning": sign_out_warning,
#     }


# def _build_datasource_tds_from_workbook(
#     source_twb_path: Path,
#     output_tds_path: Path,
#     datasource_name: str,
#     source_datasource_name: str,
#     strict_mode: bool = False,
# ) -> tuple[Path, str]:
#     root = ET.fromstring(source_twb_path.read_text(encoding="utf-8"))
#     _tableau_strip_namespaces(root)

#     datasources_node = _tableau_find_first_child(root, "datasources")
#     if datasources_node is None:
#         raise ValueError("Generated TWB has no <datasources> section.")

#     datasource_nodes = [
#         node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
#     ]
#     if not datasource_nodes:
#         raise ValueError("Generated TWB has no datasource nodes to publish.")

#     selected = _tableau_select_datasource_node(datasource_nodes, source_datasource_name)
#     selected_name = str(selected.attrib.get("name") or selected.attrib.get("caption") or "").strip()

#     datasource_payload = copy.deepcopy(selected)
#     _tableau_prepare_datasource_payload_for_publish(
#         datasource_payload=datasource_payload,
#         datasource_name=datasource_name,
#         strict_mode=strict_mode,
#     )

#     output_tds_path.parent.mkdir(parents=True, exist_ok=True)
#     output_bytes = ET.tostring(datasource_payload, encoding="utf-8", xml_declaration=True)
#     output_tds_path.write_bytes(output_bytes)
#     return output_tds_path, selected_name


# def _build_extract_datasource_package_for_publish(
#     source_twb_path: Path,
#     output_dir: Path,
#     datasource_name: str,
#     source_datasource_name: str,
# ) -> tuple[Path, str, dict[str, Any]]:
#     publish_context = st.session_state.get("sql_model_assistant_tableau_publish_context", {})
#     if not isinstance(publish_context, dict):
#         raise ValueError(
#             "Missing SQL Model Assistant publish context for extract mode. "
#             "Regenerate the TWB in this session before publishing extract."
#         )

#     data_source = publish_context.get("data_source")
#     db_catalog = publish_context.get("db_catalog")
#     if not isinstance(data_source, dict) or not data_source:
#         raise ValueError(
#             "Extract publish requires datasource metadata from the current SQL Model Assistant run."
#         )
#     if not isinstance(db_catalog, dict):
#         raise ValueError(
#             "Extract publish requires DB catalog metadata. "
#             "Make sure schema validation/TWB generation succeeded with DB introspection."
#         )

#     output_dir.mkdir(parents=True, exist_ok=True)
#     safe_name = _tableau_safe_name(datasource_name)

#     live_tds_path, selected_name = _build_datasource_tds_from_workbook(
#         source_twb_path=source_twb_path,
#         output_tds_path=output_dir / f"{safe_name}_live.tds",
#         datasource_name=datasource_name,
#         source_datasource_name=source_datasource_name,
#         strict_mode=True,
#     )

#     hyper_path = output_dir / f"{safe_name}.hyper"
#     extract_report = build_hyper_extract_from_catalog(
#         output_hyper_path=hyper_path,
#         data_sources=[data_source],
#         db_catalog=db_catalog,
#         max_rows_per_table=None,
#     )
#     if str(extract_report.get("status", "")).strip().lower() != "created":
#         reason = str(extract_report.get("reason") or extract_report.get("error") or "unknown error").strip()
#         raise RuntimeError(f"Failed to build extract for datasource publish: {reason}")

#     extract_tds_path = output_dir / f"{safe_name}.tds"
#     _tableau_rewrite_datasource_tds_for_hyper_extract(
#         source_tds_path=live_tds_path,
#         output_tds_path=extract_tds_path,
#         hyper_relative_path=f"Data/Extracts/{hyper_path.name}",
#     )

#     extract_tdsx_path = output_dir / f"{safe_name}.tdsx"
#     _tableau_package_tds_and_hyper_as_tdsx(
#         tds_path=extract_tds_path,
#         hyper_path=hyper_path,
#         tdsx_path=extract_tdsx_path,
#     )
#     return extract_tdsx_path, selected_name, extract_report


# def _tableau_rewrite_datasource_tds_for_hyper_extract(
#     source_tds_path: Path,
#     output_tds_path: Path,
#     hyper_relative_path: str,
# ) -> Path:
#     if not source_tds_path.exists():
#         raise FileNotFoundError(f"TDS file not found for extract rewrite: {source_tds_path}")

#     root = ET.fromstring(source_tds_path.read_text(encoding="utf-8"))
#     _tableau_strip_namespaces(root)
#     root.attrib["hasconnection"] = "true"

#     renamed_connections: dict[str, str] = {}

#     for connection_node in root.findall(".//connection"):
#         current_class = str(connection_node.attrib.get("class", "") or "").strip().lower()
#         if current_class == "federated":
#             continue

#         parent = _tableau_find_parent(root, connection_node)
#         if parent is not None and _tableau_local_name(parent.tag) == "named-connection":
#             old_name = str(parent.attrib.get("name", "") or "").strip()
#             new_name = "hyper.extract"
#             if old_name:
#                 renamed_connections[old_name] = new_name
#             parent.attrib["name"] = new_name
#             parent.attrib["caption"] = "Extract"

#         connection_node.attrib.clear()
#         connection_node.set("class", "hyper")
#         connection_node.set("dbname", hyper_relative_path)

#     for relation_node in root.findall(".//relation"):
#         relation_type = str(relation_node.attrib.get("type", "") or "").strip().lower()
#         if relation_type != "table":
#             continue
#         relation_name = str(relation_node.attrib.get("name", "") or "").strip()
#         if not relation_name:
#             relation_name = _table_leaf_from_table_reference(str(relation_node.attrib.get("table", "") or ""))
#         if not relation_name:
#             continue
#         connection_name = str(relation_node.attrib.get("connection", "") or "").strip()
#         if connection_name in renamed_connections:
#             relation_node.set("connection", renamed_connections[connection_name])
#         relation_node.set("table", f"[Extract].[{relation_name}]")

#     _tableau_upsert_extract_metadata(root)
#     _tableau_reorder_datasource_children(root)

#     output_tds_path.parent.mkdir(parents=True, exist_ok=True)
#     output_tds_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
#     return output_tds_path


# def _tableau_upsert_extract_metadata(datasource_node: ET.Element) -> ET.Element:
#     extract_node = _tableau_find_first_child(datasource_node, "extract")
#     if extract_node is None:
#         extract_node = ET.SubElement(datasource_node, "extract")

#     extract_node.attrib["enabled"] = "true"
#     extract_node.attrib["units"] = "records"
#     extract_node.attrib["count"] = "-1"
#     return extract_node


# def _tableau_find_parent(root: ET.Element, target: ET.Element) -> ET.Element | None:
#     for parent in root.iter():
#         for child in list(parent):
#             if child is target:
#                 return parent
#     return None


# def _tableau_package_tds_and_hyper_as_tdsx(
#     tds_path: Path,
#     hyper_path: Path,
#     tdsx_path: Path,
# ) -> Path:
#     if not tds_path.exists():
#         raise FileNotFoundError(f"TDS file not found for TDSX packaging: {tds_path}")
#     if not hyper_path.exists():
#         raise FileNotFoundError(f"Hyper file not found for TDSX packaging: {hyper_path}")

#     tdsx_path.parent.mkdir(parents=True, exist_ok=True)
#     with zipfile.ZipFile(tdsx_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
#         zf.write(tds_path, tds_path.name)
#         zf.write(hyper_path, f"Data/Extracts/{hyper_path.name}")

#     return tdsx_path


# def _tableau_prepare_datasource_payload_for_publish(
#     datasource_payload: ET.Element,
#     datasource_name: str,
#     strict_mode: bool,
# ) -> None:
#     datasource_payload.tag = "datasource"
#     datasource_payload.attrib["name"] = datasource_name
#     datasource_payload.attrib["caption"] = datasource_name
#     datasource_payload.attrib.setdefault("inline", "true")
#     datasource_payload.attrib.setdefault("version", "18.1")
#     datasource_payload.attrib.pop("hasconnection", None)

#     tags_to_remove = {"repository-location"}
#     if strict_mode:
#         tags_to_remove.update({"layout", "style", "semantic-values", "object-graph"})

#     for child in [c for c in list(datasource_payload) if _tableau_local_name(c.tag) in tags_to_remove]:
#         datasource_payload.remove(child)

#     _tableau_remove_internal_table_object_columns(datasource_payload)
#     if strict_mode:
#         _tableau_remove_connection_metadata_object_ids(datasource_payload)
#     _tableau_reorder_datasource_children(datasource_payload)


# def _tableau_remove_internal_table_object_columns(datasource_payload: ET.Element) -> None:
#     removed_column_names: set[str] = set()

#     for column_node in list(datasource_payload):
#         if _tableau_local_name(column_node.tag) != "column":
#             continue
#         datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
#         name_attr = str(column_node.attrib.get("name", "") or "").strip()
#         if datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
#             if name_attr:
#                 removed_column_names.add(name_attr)
#             datasource_payload.remove(column_node)

#     for column_instance_node in list(datasource_payload):
#         if _tableau_local_name(column_instance_node.tag) != "column-instance":
#             continue
#         column_name = str(column_instance_node.attrib.get("column", "") or "").strip()
#         if (
#             column_name.startswith("[__tableau_internal_object_id__].")
#             or column_name in removed_column_names
#         ):
#             datasource_payload.remove(column_instance_node)


# def _tableau_remove_connection_metadata_object_ids(datasource_payload: ET.Element) -> None:
#     connection_node = _tableau_find_first_child(datasource_payload, "connection")
#     if connection_node is None:
#         return

#     metadata_node = _tableau_find_first_child(connection_node, "metadata-records")
#     if metadata_node is None:
#         return

#     for record_node in list(metadata_node):
#         if _tableau_local_name(record_node.tag) != "metadata-record":
#             continue
#         for child in list(record_node):
#             if _tableau_local_name(child.tag) == "object-id":
#                 record_node.remove(child)


# def _build_linked_workbook_for_published_datasource(
#     source_twb_path: Path,
#     output_twb_path: Path,
#     published_datasource: Any,
#     server_url: str,
#     site_content_url: str,
#     source_datasource_name: str,
# ) -> None:
#     root = ET.fromstring(source_twb_path.read_text(encoding="utf-8"))
#     _tableau_strip_namespaces(root)

#     datasources_node = _tableau_find_first_child(root, "datasources")
#     if datasources_node is None:
#         raise ValueError("Generated TWB has no <datasources> section.")

#     datasource_nodes = [
#         node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
#     ]
#     if not datasource_nodes:
#         raise ValueError("Generated TWB has no datasource nodes to link.")

#     selected = _tableau_select_datasource_node(datasource_nodes, source_datasource_name)
#     published_name = str(getattr(published_datasource, "name", "") or "").strip() or "published_datasource"
#     content_url = str(getattr(published_datasource, "content_url", "") or "").strip() or published_name
#     datasource_id = str(getattr(published_datasource, "id", "") or "").strip() or content_url

#     # Keep datasource 'name' unchanged so worksheet references remain valid.
#     selected.attrib["caption"] = published_name
#     selected.attrib.setdefault("inline", "true")
#     selected.attrib.setdefault("version", "18.1")

#     for child in [
#         c
#         for c in list(selected)
#         if _tableau_local_name(c.tag) in {"repository-location", "connection"}
#     ]:
#         selected.remove(child)

#     repository_attrs: dict[str, str] = {
#         "id": datasource_id,
#         "path": "/datasources",
#         "revision": "1.0",
#     }
#     if site_content_url:
#         repository_attrs["site"] = site_content_url
#     if content_url:
#         repository_attrs["derived-from"] = _tableau_build_datasource_derived_from_url(
#             server_url=server_url,
#             site_content_url=site_content_url,
#             datasource_content_url=content_url,
#         )
#     ET.SubElement(selected, "repository-location", attrib=repository_attrs)
#     _tableau_reorder_datasource_children(selected)

#     output_twb_path.parent.mkdir(parents=True, exist_ok=True)
#     ET.register_namespace("user", "http://www.tableausoftware.com/xml/user")
#     output_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
#     output_twb_path.write_bytes(output_bytes)


# def _tableau_publish_datasource_with_fallback(
#     server: Any,
#     datasource_item: Any,
#     datasource_path: Path,
#     publish_mode: Any,
# ) -> Any:
#     try:
#         return _tableau_publish_datasource(
#             server=server,
#             datasource_item=datasource_item,
#             datasource_path=datasource_path,
#             publish_mode=publish_mode,
#         )
#     except Exception as publish_exc:
#         message = str(publish_exc)
#         if datasource_path.suffix.lower() != ".tds" or not _tableau_should_retry_packaged_publish(message):
#             raise

#         tdsx_path = datasource_path.with_suffix(".tdsx")
#         _tableau_package_tds_as_tdsx(
#             tds_path=datasource_path,
#             tdsx_path=tdsx_path,
#         )
#         return _tableau_publish_datasource(
#             server=server,
#             datasource_item=datasource_item,
#             datasource_path=tdsx_path,
#             publish_mode=publish_mode,
#         )


# def _tableau_publish_datasource(
#     server: Any,
#     datasource_item: Any,
#     datasource_path: Path,
#     publish_mode: Any,
# ) -> Any:
#     def _publish() -> Any:
#         try:
#             return server.datasources.publish(
#                 datasource_item,
#                 str(datasource_path),
#                 publish_mode,
#                 as_job=False,
#             )
#         except TypeError:
#             return server.datasources.publish(
#                 datasource_item,
#                 str(datasource_path),
#                 publish_mode,
#             )

#     return _tableau_call_with_transient_retries(
#         action=_publish,
#         action_label=f"publish datasource package {datasource_path.name}",
#     )


# def _tableau_publish_datasource_as_job(
#     server: Any,
#     tsc_module: Any,
#     datasource_item: Any,
#     datasource_path: Path,
#     publish_mode: Any,
# ) -> Any | None:
#     datasource_name = str(getattr(datasource_item, "name", "") or "").strip()
#     project_id = str(getattr(datasource_item, "project_id", "") or "").strip()

#     if not datasource_name:
#         return None

#     def _publish_job() -> Any:
#         return server.datasources.publish(
#             datasource_item,
#             str(datasource_path),
#             publish_mode,
#             as_job=True,
#         )

#     try:
#         job_item = _tableau_call_with_transient_retries(
#             action=_publish_job,
#             action_label=f"start async datasource publish job for {datasource_path.name}",
#         )
#     except TypeError as exc:
#         message = str(exc).lower()
#         if "as_job" in message or "unexpected keyword" in message:
#             return None
#         raise

#     if job_item is None:
#         return None

#     result_name = str(getattr(job_item, "name", "") or "").strip()
#     result_project_id = str(getattr(job_item, "project_id", "") or "").strip()
#     if (
#         result_name
#         and _name_key(result_name) == _name_key(datasource_name)
#         and (not project_id or not result_project_id or result_project_id == project_id)
#     ):
#         return job_item

#     jobs_endpoint = getattr(server, "jobs", None)
#     wait_for_job = getattr(jobs_endpoint, "wait_for_job", None)
#     if callable(wait_for_job):
#         _tableau_call_with_transient_retries(
#             action=lambda: wait_for_job(job_item),
#             action_label=f"wait for async datasource publish job for {datasource_path.name}",
#         )

#     return _tableau_find_published_datasource_by_name(
#         server=server,
#         tsc_module=tsc_module,
#         datasource_name=datasource_name,
#         project_id=project_id,
#     )


# def _tableau_find_published_datasource_by_name(
#     server: Any,
#     tsc_module: Any,
#     datasource_name: str,
#     project_id: str,
# ) -> Any | None:
#     target_name = str(datasource_name or "").strip()
#     target_project_id = str(project_id or "").strip()
#     if not target_name:
#         return None

#     for delay_seconds in TABLEAU_RECOVERY_LOOKUP_DELAYS_SECONDS:
#         if delay_seconds > 0:
#             try:
#                 st.warning(
#                     "Checking whether Tableau created the datasource despite the transient upload error "
#                     f"in {delay_seconds} seconds..."
#                 )
#             except Exception:
#                 pass
#             time.sleep(delay_seconds)

#         try:
#             datasources = _tableau_call_with_transient_retries(
#                 action=lambda: [item for item in tsc_module.Pager(server.datasources)],
#                 action_label=f"look up datasource {target_name}",
#             )
#         except Exception:
#             continue

#         for datasource in datasources:
#             name = str(getattr(datasource, "name", "") or "").strip()
#             content_url = str(getattr(datasource, "content_url", "") or "").strip()
#             if name != target_name and _name_key(name) != _name_key(target_name):
#                 if content_url != target_name and _name_key(content_url) != _name_key(target_name):
#                     continue

#             datasource_project_id = str(getattr(datasource, "project_id", "") or "").strip()
#             if target_project_id and datasource_project_id and datasource_project_id != target_project_id:
#                 continue

#             return datasource

#     return None


# def _tableau_copy_publish_artifact_for_debug(
#     source_path: Path,
#     timestamp_utc: str,
#     label: str,
# ) -> str:
#     try:
#         if not source_path.exists():
#             return ""

#         debug_dir = ROOT_DIR / "output" / "tableau_publish_debug"
#         debug_dir.mkdir(parents=True, exist_ok=True)
#         debug_name = (
#             f"{_tableau_safe_name(timestamp_utc)}_"
#             f"{_tableau_safe_name(label)}_"
#             f"{_tableau_safe_name(source_path.name)}"
#         )
#         debug_path = debug_dir / debug_name
#         shutil.copy2(source_path, debug_path)
#         size_mb = debug_path.stat().st_size / (1024 * 1024)
#         return f"{debug_path} ({size_mb:.2f} MB)"
#     except Exception:
#         return ""


# def _tableau_should_retry_packaged_publish(message: str) -> bool:
#     lowered = str(message or "").strip().lower()
#     if not lowered:
#         return False
#     retry_hints = (
#         "error opening archive file",
#         "invalid twb or twbx file",
#         "invalid tds or tdsx file",
#         "invalid twb",
#         "invalid tds",
#     )
#     return any(hint in lowered for hint in retry_hints)


# def _tableau_call_with_transient_retries(action: Any, action_label: str) -> Any:
#     retry_delays = list(TABLEAU_TRANSIENT_RETRY_DELAYS_SECONDS)
#     attempt_index = 1

#     while True:
#         try:
#             return action()
#         except Exception as exc:
#             is_maintenance = _tableau_is_maintenance_publish_error(exc)
#             if is_maintenance and len(retry_delays) < len(TABLEAU_MAINTENANCE_RETRY_DELAYS_SECONDS):
#                 retry_delays = list(TABLEAU_MAINTENANCE_RETRY_DELAYS_SECONDS)

#             delay_seconds = retry_delays[attempt_index - 1] if attempt_index - 1 < len(retry_delays) else 0
#             is_last_attempt = delay_seconds == 0
#             if is_last_attempt or not _tableau_is_transient_publish_error(exc):
#                 raise
#             try:
#                 retry_reason = (
#                     "Tableau Cloud is temporarily unavailable for maintenance. "
#                     if is_maintenance
#                     else "Tableau Cloud had a temporary error. "
#                 )
#                 st.warning(
#                     f"{retry_reason}Retrying {action_label} in {delay_seconds} seconds "
#                     f"(attempt {attempt_index + 1})."
#                 )
#             except Exception:
#                 pass
#             time.sleep(delay_seconds)
#             attempt_index += 1


# def _tableau_sign_out_safely(server: Any) -> str:
#     try:
#         server.auth.sign_out()
#         return ""
#     except Exception as exc:
#         message = _tableau_format_publish_error(exc)
#         try:
#             st.warning(
#                 "Tableau Cloud publish appears complete, but Tableau returned an error during sign-out. "
#                 "This does not necessarily mean the datasource/workbook publish failed. "
#                 f"Sign-out detail: {message}"
#             )
#         except Exception:
#             pass
#         return message


# def _tableau_format_publish_error(error: Exception | str) -> str:
#     compact_message = _tableau_compact_error_message(error)
#     if _tableau_is_maintenance_publish_error(error):
#         return (
#             "Tableau Cloud is down for maintenance (HTTP 503). "
#             "Retry publishing after maintenance ends. "
#             f"Detail: {compact_message}"
#         )
#     return compact_message


# def _tableau_compact_error_message(error: Exception | str) -> str:
#     raw_message = str(error or "").strip()
#     if not raw_message:
#         return "Unknown Tableau Cloud error."

#     normalized = raw_message.replace("\\r", "\n").replace("\\n", "\n")
#     normalized = normalized.replace("b'<", "<").replace('b"<', "<")

#     if "<" in normalized and ">" in normalized:
#         normalized = re.sub(r"<[^>]+>", " ", normalized)
#         normalized = html.unescape(normalized)

#     normalized = normalized.replace("\n", " ")
#     normalized = re.sub(r"\s+", " ", normalized).strip(" '\"")
#     if len(normalized) > 600:
#         normalized = normalized[:597].rstrip() + "..."
#     return normalized or "Unknown Tableau Cloud error."


# def _tableau_is_maintenance_publish_error(error: Exception | str) -> bool:
#     message = str(error or "").strip().lower()
#     if not message:
#         return False

#     maintenance_hints = (
#         "down for maintenance",
#         "this application is down for maintenance",
#         "we'll be back shortly",
#     )
#     if any(hint in message for hint in maintenance_hints):
#         return True

#     return "503" in message and "signin" in message and "online.tableau.com" in message


# def _tableau_is_transient_publish_error(error: Exception | str) -> bool:
#     message = str(error or "").strip().lower()
#     if not message:
#         return False

#     transient_hints = (
#         "503",
#         "502",
#         "504",
#         "429",
#         "bad gateway",
#         "gateway timeout",
#         "service unavailable",
#         "temporarily unavailable",
#         "too many requests",
#         "upstream connect error",
#         "disconnect/reset before headers",
#         "connection termination",
#         "connection reset",
#         "remote end closed connection",
#         "read timed out",
#         "timeout",
#         "timed out",
#         "down for maintenance",
#         "we'll be back shortly",
#     )
#     return any(hint in message for hint in transient_hints)


# def _tableau_publish_workbook_with_fallback(
#     server: Any,
#     workbook_item: Any,
#     workbook_path: Path,
#     publish_mode: Any,
# ) -> Any:
#     def _publish() -> Any:
#         try:
#             return server.workbooks.publish(
#                 workbook_item,
#                 str(workbook_path),
#                 publish_mode,
#                 as_job=False,
#                 skip_connection_check=False,
#             )
#         except TypeError:
#             return server.workbooks.publish(
#                 workbook_item,
#                 str(workbook_path),
#                 publish_mode,
#                 as_job=False,
#             )

#     return _tableau_call_with_transient_retries(
#         action=_publish,
#         action_label=f"publish workbook package {workbook_path.name}",
#     )


# def _tableau_package_twb_as_twbx(twb_path: Path, twbx_path: Path) -> Path:
#     if not twb_path.exists():
#         raise FileNotFoundError(f"TWB file not found for packaging: {twb_path}")
#     return _tableau_package_file_as_zip(twb_path, twbx_path)


# def _tableau_package_tds_as_tdsx(tds_path: Path, tdsx_path: Path) -> Path:
#     if not tds_path.exists():
#         raise FileNotFoundError(f"TDS file not found for packaging: {tds_path}")
#     return _tableau_package_file_as_zip(tds_path, tdsx_path)


# def _tableau_package_file_as_zip(source_path: Path, archive_path: Path) -> Path:
#     archive_path.parent.mkdir(parents=True, exist_ok=True)
#     with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
#         zf.write(source_path, arcname=source_path.name)
#     return archive_path


# def _tableau_resolve_project_candidates(
#     server: Any,
#     tsc_module: Any,
#     requested_project_id: str,
#     requested_project_name: str,
# ) -> list[dict[str, str]]:
#     projects = [project for project in tsc_module.Pager(server.projects)]
#     if not projects:
#         raise ValueError("No Tableau projects are available for the authenticated account.")

#     requested_id = str(requested_project_id or "").strip()
#     requested_name = str(requested_project_name or "").strip()

#     if requested_id:
#         exact_by_id = [
#             project
#             for project in projects
#             if str(getattr(project, "id", "") or "").strip() == requested_id
#         ]
#         if not exact_by_id:
#             raise ValueError(f"Tableau project ID not found: {requested_id}")
#         return [
#             {
#                 "id": str(exact_by_id[0].id),
#                 "name": str(getattr(exact_by_id[0], "name", "") or ""),
#             }
#         ]

#     if requested_name:
#         matches = [project for project in projects if getattr(project, "name", None) == requested_name]
#         if not matches:
#             requested_lower = requested_name.lower()
#             matches = [
#                 project
#                 for project in projects
#                 if isinstance(getattr(project, "name", None), str)
#                 and str(getattr(project, "name")).lower() == requested_lower
#             ]
#         if not matches:
#             raise ValueError(f"Tableau project not found: {requested_name}")

#         # Keep backward-compatible behavior for "Default" but allow permission fallback.
#         if requested_name.lower() == "default":
#             ordered_projects = matches + [project for project in projects if project not in matches]
#             return [
#                 {
#                     "id": str(project.id),
#                     "name": str(getattr(project, "name", "") or ""),
#                 }
#                 for project in ordered_projects
#             ]

#         return [
#             {
#                 "id": str(project.id),
#                 "name": str(getattr(project, "name", "") or ""),
#             }
#             for project in matches
#         ]

#     default_matches = [
#         project
#         for project in projects
#         if str(getattr(project, "name", "")).strip().lower() == "default"
#     ]
#     ordered_projects = default_matches + [project for project in projects if project not in default_matches]
#     return [
#         {
#             "id": str(project.id),
#             "name": str(getattr(project, "name", "") or ""),
#         }
#         for project in ordered_projects
#     ]


# def _tableau_resolve_project_id(server: Any, tsc_module: Any, project_name: str) -> str:
#     candidates = _tableau_resolve_project_candidates(
#         server=server,
#         tsc_module=tsc_module,
#         requested_project_id="",
#         requested_project_name=project_name,
#     )
#     if not candidates:
#         raise ValueError("No Tableau project candidates were resolved.")
#     return str(candidates[0].get("id", "") or "")


# def _tableau_is_permission_error(error: Exception | str) -> bool:
#     message = str(error or "").strip().lower()
#     if not message:
#         return False
#     permission_hints = (
#         "forbidden",
#         "does not have permission",
#         "permission for action",
#         "permission denied",
#         "500000",
#         "403",
#     )
#     return any(hint in message for hint in permission_hints)


# def _tableau_select_datasource_node(
#     datasource_nodes: list[ET.Element],
#     source_datasource_name: str,
# ) -> ET.Element:
#     requested = str(source_datasource_name or "").strip().lower()
#     if requested:
#         for node in datasource_nodes:
#             name = str(node.attrib.get("name", "") or "").strip().lower()
#             caption = str(node.attrib.get("caption", "") or "").strip().lower()
#             if requested in {name, caption}:
#                 return node

#     for node in datasource_nodes:
#         name = str(node.attrib.get("name", "") or "").strip().lower()
#         caption = str(node.attrib.get("caption", "") or "").strip().lower()
#         if name == "parameters" or caption == "parameters":
#             continue
#         return node
#     return datasource_nodes[0]


# def _tableau_find_first_child(parent: ET.Element, child_local_name: str) -> ET.Element | None:
#     for child in list(parent):
#         if _tableau_local_name(child.tag) == child_local_name:
#             return child
#     return None


# def _tableau_local_name(tag: str) -> str:
#     return tag.split("}", 1)[-1]


# def _tableau_strip_namespaces(root: ET.Element) -> None:
#     for elem in root.iter():
#         if "}" in elem.tag:
#             elem.tag = elem.tag.split("}", 1)[1]


# def _tableau_reorder_datasource_children(datasource_node: ET.Element) -> None:
#     order = [
#         "repository-location",
#         "connection",
#         "utility-dimensions",
#         "dimension",
#         "overridable-settings",
#         "aliases",
#         "column",
#         "column-instance",
#         "group",
#         "mapped-images",
#         "drill-paths",
#         "unlinked-server-hierarchies",
#         "folder",
#         "actions",
#         "calculated-members",
#         "extract",
#         "layout",
#         "style",
#         "semantic-values",
#         "date-options",
#         "default-date-format",
#         "default-sorts",
#         "field-sort-info",
#         "datasource-dependencies",
#         "explainability",
#         "filter",
#     ]
#     rank = {name: idx for idx, name in enumerate(order)}
#     children = list(datasource_node)
#     children.sort(key=lambda el: rank.get(_tableau_local_name(el.tag), 10_000))
#     for child in list(datasource_node):
#         datasource_node.remove(child)
#     for child in children:
#         datasource_node.append(child)


# def _tableau_build_datasource_derived_from_url(
#     server_url: str,
#     site_content_url: str,
#     datasource_content_url: str,
# ) -> str:
#     parsed = urlparse(server_url)
#     scheme = parsed.scheme or "https"
#     netloc = parsed.netloc or parsed.path
#     origin = f"{scheme}://{netloc}".rstrip("/")
#     site_segment = f"/t/{site_content_url.strip('/')}" if site_content_url.strip() else ""
#     content = datasource_content_url.strip().strip("/")
#     return f"{origin}{site_segment}/datasources/{content}"


# def _tableau_normalize_server_url(server_url: str) -> str:
#     raw = str(server_url or "").strip()
#     if not raw:
#         return ""

#     if "://" not in raw and "." in raw and "/" not in raw:
#         raw = f"https://{raw}"

#     parsed = urlparse(raw)
#     if parsed.scheme and parsed.netloc:
#         return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

#     if parsed.scheme and parsed.path and not parsed.netloc:
#         return f"{parsed.scheme}://{parsed.path}".rstrip("/")

#     return raw.rstrip("/")


# def _tableau_extract_site_content_url(server_url: str) -> str:
#     raw = str(server_url or "").strip()
#     if not raw:
#         return ""

#     parsed = urlparse(raw)
#     search_space = " ".join([parsed.path, parsed.fragment])
#     match = re.search(r"(?:^|/)(?:site|t)/([^/?#]+)/?", search_space, flags=re.IGNORECASE)
#     if not match:
#         return ""
#     return str(match.group(1) or "").strip()


# def _tableau_build_sqlproxy_connection_attrs(
#     server_url: str,
#     datasource_content_url: str,
#     datasource_name: str,
# ) -> dict[str, str]:
#     parsed = urlparse(server_url)
#     scheme = parsed.scheme or "https"
#     host = parsed.hostname or parsed.netloc or parsed.path
#     default_port = 443 if scheme.lower() == "https" else 80
#     port = parsed.port or default_port

#     dbname = datasource_content_url.strip().strip("/")
#     if "/" in dbname:
#         dbname = dbname.rsplit("/", 1)[-1]
#     if not dbname:
#         dbname = _tableau_safe_name(datasource_name) or "published_datasource"

#     return {
#         "class": "sqlproxy",
#         "channel": scheme.lower(),
#         "dataserver-permissions": "true",
#         "dbname": dbname,
#         "directory": "/dataserver",
#         "port": str(port),
#         "server": host,
#         "server-ds-friendly-name": datasource_name,
#         "server-oauth": "",
#         "username": "",
#         "workgroup-auth-mode": "prompt",
#     }


# def _tableau_timestamped_name(base_name: str, timestamp_utc: str) -> str:
#     return f"{str(base_name or '').strip() or 'semantic_model'}_{timestamp_utc}"


# def _tableau_safe_name(value: str) -> str:
#     cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
#     return cleaned.strip("._") or "artifact"


# def _load_template_xml(uploaded_template: Any, template_path: str) -> str:
#     if uploaded_template is not None:
#         content = uploaded_template.getvalue()
#         if not content:
#             raise ValueError("Uploaded template is empty.")
#         for encoding in ("utf-8-sig", "utf-8", "latin-1"):
#             try:
#                 return content.decode(encoding)
#             except UnicodeDecodeError:
#                 continue
#         raise ValueError("Could not decode uploaded TWB file.")

#     path = Path(str(template_path or "")).expanduser()
#     if not path.exists():
#         raise FileNotFoundError(f"Template not found: {path}")

#     for encoding in ("utf-8-sig", "utf-8", "latin-1"):
#         try:
#             return path.read_text(encoding=encoding)
#         except UnicodeDecodeError:
#             continue
#     raise ValueError(f"Could not decode TWB template at: {path}")


# def _generate_twb_from_validated_model(
#     template_xml: str,
#     data_source: dict[str, Any],
#     dataset: dict[str, Any],
#     validated_model: dict[str, Any],
# ) -> str:
#     datasource_payload = copy.deepcopy(data_source) if isinstance(data_source, dict) else {}
#     dataset_payload = copy.deepcopy(dataset) if isinstance(dataset, dict) else {}
#     table_instances = _build_table_instances_from_model(validated_model)

#     template_datasource_name = _first_template_datasource_name(template_xml)
#     preferred_exposed_names = _extract_template_exposed_name_preferences(
#         template_xml=template_xml,
#         target_datasource_name=template_datasource_name,
#     )
#     if template_datasource_name:
#         datasource_payload["name"] = template_datasource_name
#         if isinstance(dataset_payload, dict):
#             dataset_payload["data_source_name"] = template_datasource_name

#     db_catalog = _build_db_catalog_for_twb_generation(
#         data_source=datasource_payload,
#         dataset=dataset_payload,
#         table_instances=table_instances,
#     )

#     if _is_sql_datasource(datasource_payload):
#         ds_name = str(datasource_payload.get("name", "") or "").strip()
#         if not _catalog_has_table_metadata(db_catalog, ds_name):
#             raise ValueError(
#                 "Database introspection failed for the target datasource. "
#                 "Cannot safely rebuild TWB columns/map without SQL metadata."
#             )

#     connected_xml = inject_datasource_connections(
#         xml_content=template_xml,
#         data_sources=[datasource_payload] if datasource_payload else [],
#         data_sets=[dataset_payload] if dataset_payload else [],
#         db_catalog=db_catalog,
#     )

#     return _apply_validated_schema_to_twb(
#         xml_content=connected_xml,
#         model=validated_model,
#         data_source=datasource_payload,
#         target_datasource_name=template_datasource_name,
#         preferred_exposed_names=preferred_exposed_names,
#     )


# def _build_tableau_publish_context(
#     template_xml: str,
#     data_source: dict[str, Any],
#     dataset: dict[str, Any],
#     validated_model: dict[str, Any],
# ) -> dict[str, Any]:
#     datasource_payload = copy.deepcopy(data_source) if isinstance(data_source, dict) else {}
#     dataset_payload = copy.deepcopy(dataset) if isinstance(dataset, dict) else {}
#     template_datasource_name = _first_template_datasource_name(template_xml)
#     if template_datasource_name and datasource_payload:
#         datasource_payload["name"] = template_datasource_name
#         if isinstance(dataset_payload, dict):
#             dataset_payload["data_source_name"] = template_datasource_name

#     table_instances = _build_table_instances_from_model(validated_model)
#     db_catalog = _build_db_catalog_for_twb_generation(
#         data_source=datasource_payload,
#         dataset=dataset_payload,
#         table_instances=table_instances,
#     )

#     return {
#         "data_source": datasource_payload,
#         "dataset": dataset_payload,
#         "db_catalog": db_catalog,
#     }


# def _first_template_datasource_name(template_xml: str) -> str:
#     try:
#         root = ET.fromstring(template_xml)
#     except ET.ParseError:
#         return ""

#     datasources_node = root.find("datasources")
#     if datasources_node is None:
#         return ""

#     datasource_node = datasources_node.find("datasource")
#     if datasource_node is None:
#         return ""

#     return str(datasource_node.attrib.get("name", "") or "").strip()


# def _extract_template_exposed_name_preferences(
#     template_xml: str,
#     target_datasource_name: str = "",
# ) -> dict[tuple[str, str], str]:
#     try:
#         root = ET.fromstring(template_xml)
#     except ET.ParseError:
#         return {}

#     datasources_node = root.find("datasources")
#     if datasources_node is None:
#         return {}

#     datasource_node = None
#     target_key = _name_key(target_datasource_name)
#     if target_key:
#         for candidate in datasources_node.findall("datasource"):
#             if _name_key(str(candidate.attrib.get("name", "") or "")) == target_key:
#                 datasource_node = candidate
#                 break
#     if datasource_node is None:
#         datasource_node = datasources_node.find("datasource")
#     if datasource_node is None:
#         return {}

#     connection_node = datasource_node.find("connection")
#     if connection_node is None:
#         return {}

#     cols_node = connection_node.find("cols")
#     if cols_node is None:
#         return {}

#     preferences: dict[tuple[str, str], str] = {}
#     for map_node in [child for child in list(cols_node) if child.tag == "map"]:
#         key_raw = str(map_node.attrib.get("key", "") or "").strip()
#         value_raw = str(map_node.attrib.get("value", "") or "").strip()
#         table_name, column_name = _parse_cols_map_table_column(value_raw)
#         exposed_name = _clean_name(key_raw)
#         if not table_name or not column_name or not exposed_name:
#             continue
#         preferences[(_name_key(table_name), _name_key(column_name))] = exposed_name

#     return preferences


# def _build_db_catalog_for_twb_generation(
#     data_source: dict[str, Any],
#     dataset: dict[str, Any],
#     table_instances: list[dict[str, Any]],
# ) -> dict[str, Any] | None:
#     if not isinstance(data_source, dict) or not data_source:
#         return None

#     ds_name = str(data_source.get("name", "") or "").strip()
#     provider = str(data_source.get("provider", "") or "").strip().lower()
#     connection_string = str(data_source.get("connection_string", "") or "").strip()
#     if not ds_name or "sql" not in provider or not connection_string:
#         return None

#     provider_class = str(data_source.get("provider_class", "") or "sqlserver").strip().lower() or "sqlserver"
#     connection_info = data_source.get("connection_info", {}) if isinstance(data_source, dict) else {}
#     schema_hint = ""
#     if isinstance(connection_info, dict):
#         schema_hint = str(connection_info.get("schema", "") or "").strip()

#     synthetic_sets: list[dict[str, Any]] = []
#     if isinstance(dataset, dict):
#         dataset_query = str(dataset.get("query", "") or "").strip()
#         if dataset_query:
#             synthetic_sets.append(
#                 {
#                     "name": str(dataset.get("name", "") or "__selected_dataset"),
#                     "query": dataset_query,
#                     "data_source_name": ds_name,
#                 }
#             )

#     seen_tables: set[str] = set()
#     for instance in table_instances:
#         if not isinstance(instance, dict):
#             continue
#         physical_table = str(instance.get("physical_table", "") or "").strip()
#         if not physical_table:
#             continue
#         table_key = _name_key(physical_table)
#         if table_key in seen_tables:
#             continue
#         seen_tables.add(table_key)

#         table_ref = _normalize_table_reference_for_tableau(physical_table, schema_hint, provider_class)
#         synthetic_sets.append(
#             {
#                 "name": f"__model_table_{len(synthetic_sets) + 1}",
#                 "query": f"SELECT TOP 1 * FROM {table_ref}",
#                 "data_source_name": ds_name,
#             }
#         )

#     if not synthetic_sets:
#         return None

#     try:
#         return build_db_catalog(
#             data_sources=[data_source],
#             data_sets=synthetic_sets,
#         )
#     except Exception:
#         return None


# def _is_sql_datasource(data_source: dict[str, Any]) -> bool:
#     provider = str(data_source.get("provider", "") or "").strip().lower()
#     connection_string = str(data_source.get("connection_string", "") or "").strip()
#     return "sql" in provider and bool(connection_string)


# def _catalog_has_table_metadata(db_catalog: dict[str, Any] | None, datasource_name: str) -> bool:
#     if not isinstance(db_catalog, dict):
#         return False

#     sources = db_catalog.get("datasources")
#     if not isinstance(sources, list):
#         return False

#     target = _name_key(datasource_name)
#     for item in sources:
#         if not isinstance(item, dict):
#             continue
#         if _name_key(str(item.get("name", "") or "")) != target:
#             continue

#         tables = item.get("tables")
#         if not isinstance(tables, list) or not tables:
#             return False

#         for table in tables:
#             if not isinstance(table, dict):
#                 continue
#             columns = table.get("columns")
#             if isinstance(columns, list) and columns:
#                 return True
#         return False

#     return False


# def _ensure_datasource_connection_before_aliases(datasource_node: ET.Element) -> None:
#     connection_node = datasource_node.find("connection")
#     aliases_node = datasource_node.find("aliases")
#     if connection_node is None or aliases_node is None:
#         return

#     children = list(datasource_node)
#     connection_index = children.index(connection_node)
#     aliases_index = children.index(aliases_node)
#     if connection_index < aliases_index:
#         return

#     datasource_node.remove(connection_node)
#     aliases_index = list(datasource_node).index(aliases_node)
#     datasource_node.insert(aliases_index, connection_node)


# def _apply_validated_schema_to_twb(
#     xml_content: str,
#     model: dict[str, Any],
#     data_source: dict[str, Any],
#     target_datasource_name: str = "",
#     preferred_exposed_names: dict[tuple[str, str], str] | None = None,
# ) -> str:
#     root = ET.fromstring(xml_content)
#     datasources_node = root.find("datasources")
#     if datasources_node is None:
#         raise ValueError("Template TWB has no datasources section.")

#     datasource_node = None
#     target_key = _name_key(target_datasource_name)
#     if target_key:
#         for candidate in datasources_node.findall("datasource"):
#             if _name_key(str(candidate.attrib.get("name", "") or "")) == target_key:
#                 datasource_node = candidate
#                 break
#     if datasource_node is None:
#         datasource_node = datasources_node.find("datasource")
#     if datasource_node is None:
#         raise ValueError("Template TWB has no datasource node.")

#     # Tableau can infer this; removing the flag keeps generated files closer to canonical templates.
#     datasource_node.attrib.pop("hasconnection", None)

#     connection_node = datasource_node.find("connection")
#     if connection_node is None:
#         aliases_node = datasource_node.find("aliases")
#         connection_node = ET.Element("connection", attrib={"class": "federated"})
#         if aliases_node is None:
#             datasource_node.append(connection_node)
#         else:
#             aliases_index = list(datasource_node).index(aliases_node)
#             datasource_node.insert(aliases_index, connection_node)

#     _ensure_datasource_connection_before_aliases(datasource_node)

#     provider_class = "sqlserver"
#     connection_info = data_source.get("connection_info", {}) if isinstance(data_source, dict) else {}
#     if isinstance(connection_info, dict):
#         provider_class = str(connection_info.get("provider_class", "") or "").strip().lower() or provider_class
#     provider_class = (
#         str(data_source.get("provider_class", "") or "").strip().lower() or provider_class
#         if isinstance(data_source, dict)
#         else provider_class
#     )

#     named_connections = connection_node.find("named-connections")
#     if named_connections is None:
#         named_connections = ET.SubElement(connection_node, "named-connections")

#     named_connection = named_connections.find("named-connection")
#     if named_connection is None:
#         named_connection = ET.SubElement(
#             named_connections,
#             "named-connection",
#             attrib={"name": f"{provider_class}.main", "caption": "Data Source"},
#         )
#     named_connection_name = str(named_connection.attrib.get("name", "")).strip() or f"{provider_class}.main"
#     named_connection.attrib["name"] = named_connection_name

#     inner_connection = named_connection.find("connection")
#     if inner_connection is None:
#         inner_connection = ET.SubElement(named_connection, "connection")
#     inner_connection.attrib.setdefault("class", provider_class)

#     schema_hint = ""
#     if isinstance(connection_info, dict):
#         schema_hint = str(connection_info.get("schema", "") or "").strip()

#     table_instances = _build_table_instances_from_model(model)
#     if not table_instances:
#         raise ValueError("Validated model has no tables to project to TWB.")

#     table_instances = _order_table_instances_for_structure(table_instances=table_instances, model=model)
#     _apply_existing_object_ids_to_instances(datasource_node=datasource_node, table_instances=table_instances)

#     _replace_relation_collection(
#         connection_node=connection_node,
#         named_connection_name=named_connection_name,
#         table_instances=table_instances,
#         schema_hint=schema_hint,
#         provider_class=provider_class,
#     )

#     _normalize_connection_cols_and_metadata(
#         connection_node=connection_node,
#         datasource_node=datasource_node,
#         table_instances=table_instances,
#         preferred_exposed_names=preferred_exposed_names or {},
#     )

#     _rebuild_object_graph(
#         datasource_node=datasource_node,
#         table_instances=table_instances,
#         named_connection_name=named_connection_name,
#         model=model,
#         schema_hint=schema_hint,
#         provider_class=provider_class,
#     )

#     _upsert_table_object_columns(datasource_node=datasource_node, table_instances=table_instances)

#     _sync_connection_metadata_with_current_model(
#         connection_node=connection_node,
#         datasource_node=datasource_node,
#     )

#     _normalize_connection_child_order(connection_node)

#     # Preserve Tableau user namespace expected by working templates.
#     if "xmlns:user" not in root.attrib:
#         root.set("xmlns:user", "http://www.tableausoftware.com/xml/user")

#     # Keep output readable and easier to validate manually.
#     ET.indent(root, space="  ")

#     return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


# def _normalize_connection_child_order(connection_node: ET.Element) -> None:
#     preferred_tags = ["named-connections", "relation", "cols", "metadata-records"]

#     children = list(connection_node)
#     grouped: dict[str, list[ET.Element]] = {tag: [] for tag in preferred_tags}
#     trailing: list[ET.Element] = []

#     for child in children:
#         if child.tag in grouped:
#             grouped[child.tag].append(child)
#         else:
#             trailing.append(child)

#     for child in children:
#         connection_node.remove(child)

#     for tag in preferred_tags:
#         for child in grouped[tag]:
#             connection_node.append(child)

#     for child in trailing:
#         connection_node.append(child)


# def _order_table_instances_for_structure(
#     table_instances: list[dict[str, Any]],
#     model: dict[str, Any],
# ) -> list[dict[str, Any]]:
#     if len(table_instances) <= 1:
#         return list(table_instances)

#     ordered = list(table_instances)
#     semantic_map: dict[str, dict[str, Any]] = {}
#     alias_map: dict[str, dict[str, Any]] = {}
#     physical_map: dict[str, dict[str, Any]] = {}
#     index_by_instance_id: dict[int, int] = {}

#     for index, instance in enumerate(ordered):
#         index_by_instance_id[id(instance)] = index
#         semantic_key = _name_key(str(instance.get("semantic_name", "") or ""))
#         alias_key = _name_key(str(instance.get("alias", "") or ""))
#         physical_key = _name_key(str(instance.get("physical_table", "") or ""))
#         if semantic_key and semantic_key not in semantic_map:
#             semantic_map[semantic_key] = instance
#         if alias_key:
#             alias_map[alias_key] = instance
#         if physical_key and physical_key not in physical_map:
#             physical_map[physical_key] = instance

#     adjacency: dict[int, set[int]] = {idx: set() for idx in range(len(ordered))}
#     for relationship in model.get("relationships", []):
#         if not isinstance(relationship, dict):
#             continue

#         from_instance = _resolve_instance_for_relationship_side(
#             table_name=str(relationship.get("from_table", "") or ""),
#             alias=str(relationship.get("from_alias", "") or ""),
#             semantic_map=semantic_map,
#             alias_map=alias_map,
#             physical_map=physical_map,
#         )
#         to_instance = _resolve_instance_for_relationship_side(
#             table_name=str(relationship.get("to_table", "") or ""),
#             alias=str(relationship.get("to_alias", "") or ""),
#             semantic_map=semantic_map,
#             alias_map=alias_map,
#             physical_map=physical_map,
#         )
#         if from_instance is None or to_instance is None:
#             continue

#         from_index = index_by_instance_id.get(id(from_instance))
#         to_index = index_by_instance_id.get(id(to_instance))
#         if from_index is None or to_index is None or from_index == to_index:
#             continue

#         adjacency[from_index].add(to_index)
#         adjacency[to_index].add(from_index)

#     degrees = {idx: len(neighbors) for idx, neighbors in adjacency.items()}

#     root_index: int | None = None
#     fact_name = ""
#     fact_tables = [item for item in model.get("fact_tables", []) if isinstance(item, dict)]
#     if fact_tables:
#         fact_name = str(fact_tables[0].get("name", "") or "").strip()

#     if fact_name:
#         fact_key = _name_key(fact_name)
#         for idx, instance in enumerate(ordered):
#             if _name_key(str(instance.get("semantic_name", "") or "")) == fact_key:
#                 root_index = idx
#                 break

#     if root_index is None:
#         for idx, instance in enumerate(ordered):
#             if bool(instance.get("is_fact")):
#                 root_index = idx
#                 break

#     if root_index is None:
#         root_index = 0

#     visited: set[int] = set()
#     ordered_indexes: list[int] = []

#     def _neighbor_sort_key(index: int) -> tuple[int, int, str]:
#         instance = ordered[index]
#         relation_name = str(
#             instance.get("relation_name", "") or instance.get("caption", "") or instance.get("semantic_name", "")
#         )
#         return (
#             -degrees.get(index, 0),
#             1 if bool(instance.get("is_fact")) else 0,
#             _name_key(relation_name),
#         )

#     def _visit(index: int) -> None:
#         if index in visited:
#             return
#         visited.add(index)
#         ordered_indexes.append(index)

#         for neighbor in sorted(adjacency.get(index, set()), key=_neighbor_sort_key):
#             _visit(neighbor)

#     _visit(root_index)

#     remaining = [idx for idx in range(len(ordered)) if idx not in visited]
#     remaining.sort(
#         key=lambda idx: (
#             0 if bool(ordered[idx].get("is_fact")) else 1,
#             -degrees.get(idx, 0),
#             _name_key(
#                 str(
#                     ordered[idx].get("relation_name", "")
#                     or ordered[idx].get("caption", "")
#                     or ordered[idx].get("semantic_name", "")
#                 )
#             ),
#         )
#     )

#     ordered_indexes.extend(remaining)
#     return [ordered[index] for index in ordered_indexes]


# def _apply_existing_object_ids_to_instances(
#     datasource_node: ET.Element,
#     table_instances: list[dict[str, Any]],
# ) -> None:
#     existing_lookup = _build_table_object_id_lookup_from_current_object_graph(datasource_node)
#     if not existing_lookup:
#         return

#     used_object_ids: set[str] = set()
#     for instance in table_instances:
#         candidates = [
#             str(instance.get("relation_name", "") or "").strip(),
#             str(instance.get("semantic_name", "") or "").strip(),
#             _table_leaf_from_table_reference(str(instance.get("physical_table", "") or "")),
#             str(instance.get("caption", "") or "").strip(),
#             str(instance.get("alias", "") or "").strip(),
#         ]

#         selected_object_id = ""
#         for candidate in candidates:
#             for key in (_name_key(candidate), _semantic_name_key(candidate)):
#                 if not key:
#                     continue
#                 object_id = str(existing_lookup.get(key, "") or "").strip()
#                 if object_id and object_id not in used_object_ids:
#                     selected_object_id = object_id
#                     break
#             if selected_object_id:
#                 break

#         if selected_object_id:
#             instance["object_id"] = selected_object_id
#             used_object_ids.add(selected_object_id)


# def _normalize_connection_cols_and_metadata(
#     connection_node: ET.Element,
#     datasource_node: ET.Element,
#     table_instances: list[dict[str, Any]],
#     preferred_exposed_names: dict[tuple[str, str], str],
# ) -> None:
#     cols_node = connection_node.find("cols")
#     if cols_node is None:
#         return

#     maps = [child for child in list(cols_node) if child.tag == "map"]
#     if not maps:
#         return

#     table_order: dict[str, int] = {}
#     for index, instance in enumerate(table_instances):
#         for candidate in (
#             str(instance.get("relation_name", "") or ""),
#             str(instance.get("semantic_name", "") or ""),
#             str(instance.get("caption", "") or ""),
#             str(instance.get("physical_table", "") or ""),
#             _table_leaf_from_table_reference(str(instance.get("physical_table", "") or "")),
#         ):
#             key = _name_key(candidate)
#             if key and key not in table_order:
#                 table_order[key] = index

#     entries: list[dict[str, Any]] = []
#     for index, map_node in enumerate(maps):
#         key_raw = str(map_node.attrib.get("key", "") or "").strip()
#         value_raw = str(map_node.attrib.get("value", "") or "").strip()
#         old_exposed = _clean_name(key_raw)
#         table_name, column_name = _parse_cols_map_table_column(value_raw)

#         preferred_name = ""
#         if table_name and column_name:
#             preferred_name = str(
#                 preferred_exposed_names.get((_name_key(table_name), _name_key(column_name)), "") or ""
#             ).strip()

#         candidate_name = _clean_name(preferred_name or old_exposed or column_name)
#         if not candidate_name:
#             candidate_name = _clean_name(column_name)

#         entries.append(
#             {
#                 "index": index,
#                 "map_node": map_node,
#                 "table_name": table_name,
#                 "column_name": column_name,
#                 "old_exposed": old_exposed,
#                 "candidate_name": candidate_name,
#                 "preferred_name": _clean_name(preferred_name),
#                 "final_name": "",
#             }
#         )

#     grouped: dict[str, list[dict[str, Any]]] = {}
#     for entry in entries:
#         grouped.setdefault(_name_key(entry["candidate_name"]), []).append(entry)

#     used_final_names: set[str] = set()

#     def _make_unique_name(base_name: str) -> str:
#         proposal = _clean_name(base_name) or "Column"
#         proposal_key = _name_key(proposal)
#         if proposal_key not in used_final_names:
#             used_final_names.add(proposal_key)
#             return proposal

#         suffix = 2
#         while True:
#             candidate = f"{proposal} {suffix}"
#             candidate_key = _name_key(candidate)
#             if candidate_key not in used_final_names:
#                 used_final_names.add(candidate_key)
#                 return candidate
#             suffix += 1

#     for group_key, group_entries in grouped.items():
#         if not group_key:
#             for entry in group_entries:
#                 entry["final_name"] = _make_unique_name(entry["candidate_name"])
#             continue

#         ordered_group = sorted(
#             group_entries,
#             key=lambda entry: (
#                 0 if _name_key(entry["preferred_name"]) == group_key and entry["preferred_name"] else 1,
#                 table_order.get(_name_key(entry["table_name"]), 10_000),
#                 entry["index"],
#             ),
#         )

#         for position, entry in enumerate(ordered_group):
#             base_name = entry["candidate_name"]
#             if position > 0 and entry["table_name"]:
#                 base_name = f"{entry['candidate_name']} ({entry['table_name']})"
#             entry["final_name"] = _make_unique_name(base_name)

#     old_to_new: dict[str, str] = {}
#     pair_to_final: dict[tuple[str, str], str] = {}
#     for entry in entries:
#         final_name = str(entry.get("final_name", "") or entry.get("candidate_name", "") or "").strip()
#         if not final_name:
#             continue

#         entry["map_node"].attrib["key"] = f"[{final_name}]"

#         old_key = _name_key(entry.get("old_exposed", ""))
#         if old_key:
#             old_to_new[old_key] = final_name

#         table_name = str(entry.get("table_name", "") or "").strip()
#         column_name = str(entry.get("column_name", "") or "").strip()
#         if table_name and column_name:
#             pair_to_final[(_name_key(table_name), _name_key(column_name))] = final_name

#     for map_node in maps:
#         cols_node.remove(map_node)
#     for map_node in sorted(
#         maps,
#         key=lambda node: (
#             _name_key(_clean_name(str(node.attrib.get("key", "") or ""))),
#             _name_key(str(node.attrib.get("value", "") or "")),
#         ),
#     ):
#         cols_node.append(map_node)

#     metadata_node = connection_node.find("metadata-records")
#     if metadata_node is not None:
#         for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
#             remote_name_node = record.find("remote-name")
#             parent_name_node = record.find("parent-name")
#             if remote_name_node is None or parent_name_node is None:
#                 continue

#             remote_name = str(remote_name_node.text or "").strip()
#             parent_name = _clean_name(str(parent_name_node.text or "").strip())
#             final_name = pair_to_final.get((_name_key(parent_name), _name_key(remote_name)), "")
#             if not final_name:
#                 continue

#             local_name_node = record.find("local-name")
#             if local_name_node is None:
#                 local_name_node = ET.SubElement(record, "local-name")
#             local_name_node.text = f"[{final_name}]"

#     for column_node in [child for child in list(datasource_node) if child.tag == "column"]:
#         name_attr = str(column_node.attrib.get("name", "") or "").strip()
#         datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
#         if not name_attr or datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
#             continue

#         clean_name = _clean_name(name_attr)
#         new_name = old_to_new.get(_name_key(clean_name), "")
#         if new_name and _name_key(new_name) != _name_key(clean_name):
#             column_node.attrib["name"] = f"[{new_name}]"


# def _sync_connection_metadata_with_current_model(
#     connection_node: ET.Element,
#     datasource_node: ET.Element,
# ) -> None:
#     metadata_node = connection_node.find("metadata-records")
#     if metadata_node is None:
#         return

#     local_key_to_table_name = _build_cols_local_key_to_table_name(connection_node)
#     if not local_key_to_table_name:
#         for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
#             metadata_node.remove(record)
#         return

#     table_object_id_lookup = _build_table_object_id_lookup_from_current_object_graph(datasource_node)
#     seen_local_keys: set[str] = set()

#     for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
#         local_name_node = record.find("local-name")
#         local_name = str(local_name_node.text or "").strip() if local_name_node is not None else ""
#         local_key = _clean_name(local_name).lower()
#         expected_table_name = local_key_to_table_name.get(local_key, "")

#         if not local_key or not expected_table_name or local_key in seen_local_keys:
#             metadata_node.remove(record)
#             continue

#         seen_local_keys.add(local_key)

#         parent_name_node = record.find("parent-name")
#         if parent_name_node is None:
#             parent_name_node = ET.SubElement(record, "parent-name")
#         parent_name_node.text = f"[{expected_table_name}]"

#         object_id = table_object_id_lookup.get(_name_key(expected_table_name), "")
#         object_id_node = record.find("object-id")
#         if object_id:
#             if object_id_node is None:
#                 object_id_node = ET.SubElement(record, "object-id")
#             object_id_node.text = f"[{object_id}]"
#         elif object_id_node is not None:
#             record.remove(object_id_node)


# def _build_cols_local_key_to_table_name(connection_node: ET.Element) -> dict[str, str]:
#     lookup: dict[str, str] = {}
#     cols_node = connection_node.find("cols")
#     if cols_node is None:
#         return lookup

#     for map_node in [child for child in list(cols_node) if child.tag == "map"]:
#         key_raw = str(map_node.attrib.get("key", "") or "").strip()
#         value_raw = str(map_node.attrib.get("value", "") or "").strip()
#         local_key = _clean_name(key_raw).lower()
#         table_name, _ = _parse_cols_map_table_column(value_raw)
#         if not local_key or not table_name:
#             continue
#         lookup[local_key] = table_name

#     return lookup


# def _build_table_object_id_lookup_from_current_object_graph(datasource_node: ET.Element) -> dict[str, str]:
#     lookup: dict[str, str] = {}

#     object_graph = datasource_node.find("object-graph")
#     if object_graph is None:
#         return lookup

#     objects_node = object_graph.find("objects")
#     if objects_node is None:
#         return lookup

#     for object_node in [child for child in list(objects_node) if child.tag == "object"]:
#         object_id = str(object_node.attrib.get("id", "") or "").strip()
#         if not object_id:
#             continue

#         caption = str(object_node.attrib.get("caption", "") or "").strip()
#         caption_key = _name_key(caption)
#         caption_semantic_key = _semantic_name_key(caption)
#         if caption_key:
#             lookup.setdefault(caption_key, object_id)
#         if caption_semantic_key:
#             lookup.setdefault(caption_semantic_key, object_id)
#         if " (" in caption:
#             base_caption = caption.split(" (", 1)[0].strip()
#             base_caption_key = _name_key(base_caption)
#             base_caption_semantic_key = _semantic_name_key(base_caption)
#             if base_caption_key:
#                 lookup.setdefault(base_caption_key, object_id)
#             if base_caption_semantic_key:
#                 lookup.setdefault(base_caption_semantic_key, object_id)

#         relation_node = object_node.find("properties/relation")
#         if relation_node is None:
#             continue

#         relation_name = str(relation_node.attrib.get("name", "") or "").strip()
#         relation_name_key = _name_key(relation_name)
#         relation_name_semantic_key = _semantic_name_key(relation_name)
#         if relation_name_key:
#             lookup.setdefault(relation_name_key, object_id)
#         if relation_name_semantic_key:
#             lookup.setdefault(relation_name_semantic_key, object_id)

#         table_ref = str(relation_node.attrib.get("table", "") or "").strip()
#         table_leaf = _table_leaf_from_table_reference(table_ref)
#         table_leaf_key = _name_key(table_leaf)
#         table_leaf_semantic_key = _semantic_name_key(table_leaf)
#         if table_leaf_key:
#             lookup.setdefault(table_leaf_key, object_id)
#         if table_leaf_semantic_key:
#             lookup.setdefault(table_leaf_semantic_key, object_id)

#     return lookup


# def _table_leaf_from_table_reference(table_ref: str) -> str:
#     tokens = re.findall(r"\[([^\]]+)\]", table_ref or "")
#     if tokens:
#         return tokens[-1].strip()

#     cleaned = _clean_name(table_ref)
#     parts = [part.strip() for part in cleaned.split(".") if part.strip()]
#     if not parts:
#         return ""
#     return parts[-1]


# def _build_table_instances_from_model(model: dict[str, Any]) -> list[dict[str, Any]]:
#     instances: list[dict[str, Any]] = []

#     fact_name = ""
#     fact_tables = [item for item in model.get("fact_tables", []) if isinstance(item, dict)]
#     if fact_tables:
#         fact_name = str(fact_tables[0].get("name", "") or "").strip()
#         if fact_name:
#             instances.append(
#                 {
#                     "semantic_name": fact_name,
#                     "physical_table": fact_name,
#                     "alias": "",
#                     "role": "",
#                     "is_fact": True,
#                 }
#             )

#     for group_name in ("direct_dimensions", "snowflake_dimensions"):
#         for dimension in _normalize_dimension_list(model.get(group_name, [])):
#             semantic_name = str(dimension.get("name", "") or "").strip()
#             physical_table = str(dimension.get("physical_table", "") or semantic_name).strip()
#             if not semantic_name or not physical_table:
#                 continue
#             instances.append(
#                 {
#                     "semantic_name": semantic_name,
#                     "physical_table": physical_table,
#                     "alias": str(dimension.get("alias", "") or "").strip(),
#                     "role": str(dimension.get("semantic_role", "") or "").strip(),
#                     "is_fact": False,
#                 }
#             )

#     known_semantic_keys = {_name_key(str(item.get("semantic_name", ""))) for item in instances}
#     for relationship in model.get("relationships", []):
#         if not isinstance(relationship, dict):
#             continue
#         for side in ("from", "to"):
#             table_name = str(relationship.get(f"{side}_table", "") or "").strip()
#             if not table_name:
#                 continue
#             key = _name_key(table_name)
#             if key and key in known_semantic_keys:
#                 continue
#             instances.append(
#                 {
#                     "semantic_name": table_name,
#                     "physical_table": table_name,
#                     "alias": str(relationship.get(f"{side}_alias", "") or "").strip(),
#                     "role": "",
#                     "is_fact": _same_name(table_name, fact_name),
#                 }
#             )
#             if key:
#                 known_semantic_keys.add(key)

#     display_used: dict[str, int] = {}
#     physical_leaf_counts: dict[str, int] = {}
#     for instance in instances:
#         semantic_name = str(instance.get("semantic_name", "") or "").strip()
#         parsed_physical, parsed_role = _split_dimension_name_role(semantic_name)
#         physical = str(instance.get("physical_table", "") or parsed_physical or semantic_name).strip()

#         leaf = _clean_name(physical).split(".")[-1] if physical else _clean_name(parsed_physical)
#         leaf_key = _name_key(leaf)
#         if leaf_key:
#             physical_leaf_counts[leaf_key] = physical_leaf_counts.get(leaf_key, 0) + 1

#         role = str(instance.get("role", "") or parsed_role).strip()
#         alias = str(instance.get("alias", "") or "").strip()

#         caption = leaf or semantic_name or "Table"
#         if role and not instance.get("is_fact"):
#             caption = f"{caption} ({role})"
#         elif alias and not instance.get("is_fact") and _name_key(alias) != _name_key(caption):
#             caption = f"{caption} ({alias})"

#         count = display_used.get(caption.lower(), 0) + 1
#         display_used[caption.lower()] = count
#         if count > 1:
#             caption = f"{caption} {count}"

#         instance["caption"] = caption
#         instance["physical_leaf"] = leaf
#         instance["object_id"] = _make_table_object_id(f"{semantic_name}|{physical}|{caption}")

#     for instance in instances:
#         caption = str(instance.get("caption", "") or "Table")
#         physical_leaf = str(instance.get("physical_leaf", "") or "").strip()
#         physical_leaf_key = _name_key(physical_leaf)

#         relation_name = physical_leaf or caption
#         # Keep relation names aligned with cols/map table references when unique.
#         if physical_leaf_key and physical_leaf_counts.get(physical_leaf_key, 0) > 1:
#             relation_name = caption

#         # Keep object captions aligned with relation names for robust Tableau field resolution.
#         instance["caption"] = relation_name
#         instance["relation_name"] = relation_name
#         semantic_name = str(instance.get("semantic_name", "") or "").strip()
#         physical = str(instance.get("physical_table", "") or "").strip()
#         instance["object_id"] = _make_table_object_id(f"{semantic_name}|{physical}|{relation_name}")
#         instance.pop("physical_leaf", None)

#     return instances


# def _make_table_object_id(value: str) -> str:
#     base = re.sub(r"[^A-Za-z0-9_]+", "", _clean_name(value).split("|")[0])
#     base = base or "Table"
#     digest = hashlib.md5(value.encode("utf-8")).hexdigest().upper()
#     return f"{base}_{digest}"


# def _replace_relation_collection(
#     connection_node: ET.Element,
#     named_connection_name: str,
#     table_instances: list[dict[str, Any]],
#     schema_hint: str,
#     provider_class: str,
# ) -> None:
#     for child in list(connection_node):
#         if child.tag == "relation":
#             connection_node.remove(child)

#     relation_collection = ET.Element("relation", attrib={"type": "collection"})
#     for instance in table_instances:
#         caption = str(instance.get("caption", "") or "Table")
#         relation_name = str(instance.get("relation_name", "") or caption)
#         physical = str(instance.get("physical_table", "") or caption)
#         ET.SubElement(
#             relation_collection,
#             "relation",
#             attrib={
#                 "connection": named_connection_name,
#                 "name": relation_name,
#                 "table": _normalize_table_reference_for_tableau(physical, schema_hint, provider_class),
#                 "type": "table",
#             },
#         )

#     children = list(connection_node)
#     insert_at = len(children)
#     for index, child in enumerate(children):
#         if child.tag == "named-connections":
#             insert_at = index + 1
#             break
#         if child.tag == "cols":
#             insert_at = index
#             break
#     connection_node.insert(insert_at, relation_collection)


# def _normalize_table_reference_for_tableau(table_name: str, schema_hint: str, provider_class: str) -> str:
#     parts = [_clean_name(part) for part in str(table_name or "").split(".") if _clean_name(part)]
#     if not parts:
#         return "[UnknownTable]"

#     normalized_provider = str(provider_class or "").strip().lower()
#     if len(parts) == 1:
#         if normalized_provider == "sqlserver":
#             schema = _clean_name(schema_hint) or "dbo"
#             return f"[{schema}].[{parts[0]}]"
#         return f"[{parts[0]}]"

#     return ".".join(f"[{part}]" for part in parts)


# def _rebuild_object_graph(
#     datasource_node: ET.Element,
#     table_instances: list[dict[str, Any]],
#     named_connection_name: str,
#     model: dict[str, Any],
#     schema_hint: str,
#     provider_class: str,
# ) -> None:
#     object_graph = datasource_node.find("object-graph")
#     if object_graph is None:
#         object_graph = ET.SubElement(datasource_node, "object-graph")

#     objects_node = object_graph.find("objects")
#     if objects_node is None:
#         objects_node = ET.SubElement(object_graph, "objects")
#     relationships_node = object_graph.find("relationships")
#     if relationships_node is None:
#         relationships_node = ET.SubElement(object_graph, "relationships")

#     for child in list(objects_node):
#         objects_node.remove(child)
#     for child in list(relationships_node):
#         relationships_node.remove(child)

#     semantic_map: dict[str, dict[str, Any]] = {}
#     alias_map: dict[str, dict[str, Any]] = {}
#     physical_map: dict[str, dict[str, Any]] = {}
#     object_table_by_id: dict[str, str] = {}
#     fact_instance: dict[str, Any] | None = None

#     connection_node = datasource_node.find("connection")
#     exposed_lookup = _build_exposed_key_lookup_from_cols(connection_node)
#     relationship_nodes: list[tuple[tuple[int, str, str, str, str], ET.Element]] = []

#     for instance in table_instances:
#         caption = str(instance.get("caption", "") or "Table")
#         relation_name = str(instance.get("relation_name", "") or caption)
#         object_id = str(instance.get("object_id", "") or _make_table_object_id(caption))
#         instance["object_id"] = object_id

#         table_ref = _normalize_table_reference_for_tableau(
#             str(instance.get("physical_table", "") or caption),
#             schema_hint,
#             provider_class,
#         )

#         object_node = ET.SubElement(objects_node, "object", attrib={"caption": caption, "id": object_id})
#         properties_node = ET.SubElement(object_node, "properties", attrib={"context": ""})
#         ET.SubElement(
#             properties_node,
#             "relation",
#             attrib={
#                 "connection": named_connection_name,
#                 "name": relation_name,
#                 "table": table_ref,
#                 "type": "table",
#             },
#         )
#         object_table_by_id[object_id] = str(instance.get("physical_table", "") or caption)

#         semantic_key = _name_key(str(instance.get("semantic_name", "") or ""))
#         physical_key = _name_key(str(instance.get("physical_table", "") or ""))
#         alias_key = _name_key(str(instance.get("alias", "") or ""))

#         if semantic_key and semantic_key not in semantic_map:
#             semantic_map[semantic_key] = instance
#         if alias_key:
#             alias_map[alias_key] = instance
#         if physical_key and physical_key not in physical_map:
#             physical_map[physical_key] = instance
#         if instance.get("is_fact") and fact_instance is None:
#             fact_instance = instance

#     for relationship in model.get("relationships", []):
#         if not isinstance(relationship, dict):
#             continue

#         from_instance = _resolve_instance_for_relationship_side(
#             table_name=str(relationship.get("from_table", "") or ""),
#             alias=str(relationship.get("from_alias", "") or ""),
#             semantic_map=semantic_map,
#             alias_map=alias_map,
#             physical_map=physical_map,
#         )
#         to_instance = _resolve_instance_for_relationship_side(
#             table_name=str(relationship.get("to_table", "") or ""),
#             alias=str(relationship.get("to_alias", "") or ""),
#             semantic_map=semantic_map,
#             alias_map=alias_map,
#             physical_map=physical_map,
#         )
#         if from_instance is None or to_instance is None:
#             continue

#         from_col, to_col = _resolve_join_columns_for_relationship(relationship)
#         if not from_col or not to_col:
#             continue

#         cardinality = str(relationship.get("cardinality", "") or "").strip().lower()
#         relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()

#         from_instance, to_instance, from_col, to_col, cardinality = _orient_relationship_instance_sides(
#             from_instance=from_instance,
#             to_instance=to_instance,
#             from_col=from_col,
#             to_col=to_col,
#             cardinality=cardinality,
#             fact_instance=fact_instance,
#         )

#         from_object_id = str(from_instance.get("object_id", "") or "")
#         to_object_id = str(to_instance.get("object_id", "") or "")
#         from_exposed = _resolve_exposed_relationship_column(
#             exposed_lookup=exposed_lookup,
#             object_table_by_id=object_table_by_id,
#             object_id=from_object_id,
#             column_name=from_col,
#         )
#         to_exposed = _resolve_exposed_relationship_column(
#             exposed_lookup=exposed_lookup,
#             object_table_by_id=object_table_by_id,
#             object_id=to_object_id,
#             column_name=to_col,
#         )
#         if not from_exposed:
#             from_exposed = _clean_name(from_col)
#         if not to_exposed:
#             to_exposed = _clean_name(to_col)

#         rel_node = ET.Element("relationship")
#         expression_node = ET.SubElement(rel_node, "expression", attrib={"op": "="})
#         ET.SubElement(expression_node, "expression", attrib={"op": f"[{from_exposed}]"})
#         ET.SubElement(expression_node, "expression", attrib={"op": f"[{to_exposed}]"})

#         first_attrs = {"object-id": str(from_instance.get("object_id", ""))}
#         second_attrs = {"object-id": str(to_instance.get("object_id", ""))}

#         first_is_fact_like = _is_fact_like_instance(from_instance)
#         second_is_fact_like = _is_fact_like_instance(to_instance)
#         if from_instance is fact_instance:
#             first_is_fact_like = True
#         if to_instance is fact_instance:
#             second_is_fact_like = True

#         _set_relationship_endpoint_attributes(
#             first_attrs=first_attrs,
#             second_attrs=second_attrs,
#             cardinality=cardinality,
#             relation_type=relation_type,
#             first_is_fact_like=first_is_fact_like,
#             second_is_fact_like=second_is_fact_like,
#         )

#         ET.SubElement(rel_node, "first-end-point", attrib=first_attrs)
#         ET.SubElement(rel_node, "second-end-point", attrib=second_attrs)

#         relationship_nodes.append(
#             (
#                 (
#                     0 if first_is_fact_like else 1,
#                     _name_key(from_exposed),
#                     _name_key(to_exposed),
#                     _name_key(from_object_id),
#                     _name_key(to_object_id),
#                 ),
#                 rel_node,
#             )
#         )

#     for _, rel_node in sorted(relationship_nodes, key=lambda item: item[0]):
#         relationships_node.append(rel_node)


# def _resolve_instance_for_relationship_side(
#     table_name: str,
#     alias: str,
#     semantic_map: dict[str, dict[str, Any]],
#     alias_map: dict[str, dict[str, Any]],
#     physical_map: dict[str, dict[str, Any]],
# ) -> dict[str, Any] | None:
#     alias_key = _name_key(alias)
#     if alias_key and alias_key in alias_map:
#         return alias_map[alias_key]

#     table_key = _name_key(table_name)
#     if table_key and table_key in semantic_map:
#         return semantic_map[table_key]
#     if table_key and table_key in physical_map:
#         return physical_map[table_key]
#     return None


# def _resolve_join_columns_for_relationship(relationship: dict[str, Any]) -> tuple[str, str]:
#     join_condition = str(relationship.get("join_condition", "") or "")
#     pairs = _extract_join_pairs(join_condition)
#     if not pairs:
#         return "", ""

#     from_alias = str(relationship.get("from_alias", "") or "").strip().lower()
#     to_alias = str(relationship.get("to_alias", "") or "").strip().lower()

#     for pair in pairs:
#         left_alias = pair["left_alias"].lower()
#         right_alias = pair["right_alias"].lower()
#         if from_alias and to_alias and left_alias == from_alias and right_alias == to_alias:
#             return pair["left_column"], pair["right_column"]
#         if from_alias and to_alias and left_alias == to_alias and right_alias == from_alias:
#             return pair["right_column"], pair["left_column"]

#     if from_alias:
#         for pair in pairs:
#             if pair["left_alias"].lower() == from_alias:
#                 return pair["left_column"], pair["right_column"]
#             if pair["right_alias"].lower() == from_alias:
#                 return pair["right_column"], pair["left_column"]

#     first_pair = pairs[0]
#     return first_pair["left_column"], first_pair["right_column"]


# def _invert_cardinality(cardinality: str) -> str:
#     normalized = str(cardinality or "").strip().lower()
#     if normalized == "many-to-one":
#         return "one-to-many"
#     if normalized == "one-to-many":
#         return "many-to-one"
#     return normalized


# def _orient_relationship_instance_sides(
#     from_instance: dict[str, Any],
#     to_instance: dict[str, Any],
#     from_col: str,
#     to_col: str,
#     cardinality: str,
#     fact_instance: dict[str, Any] | None,
# ) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
#     should_swap = False

#     if to_instance is fact_instance and from_instance is not fact_instance:
#         should_swap = True
#     else:
#         from_is_primary_fact = from_instance is fact_instance
#         to_is_primary_fact = to_instance is fact_instance

#         # For non-primary fact relationships, place the dimension side first.
#         if not from_is_primary_fact and not to_is_primary_fact:
#             from_is_fact_like = _is_fact_like_instance(from_instance)
#             to_is_fact_like = _is_fact_like_instance(to_instance)
#             if from_is_fact_like and not to_is_fact_like:
#                 should_swap = True

#     if not should_swap:
#         return from_instance, to_instance, from_col, to_col, str(cardinality or "").strip().lower()

#     return (
#         to_instance,
#         from_instance,
#         to_col,
#         from_col,
#         _invert_cardinality(cardinality),
#     )


# def _is_fact_like_table_name(table_name: str) -> bool:
#     key = _name_key(table_name)
#     return bool(key) and key.startswith("fact")


# def _is_fact_like_instance(instance: dict[str, Any] | None) -> bool:
#     if not isinstance(instance, dict):
#         return False
#     if bool(instance.get("is_fact")):
#         return True
#     semantic_name = str(instance.get("semantic_name", "") or "")
#     physical_name = str(instance.get("physical_table", "") or "")
#     alias = str(instance.get("alias", "") or "")
#     return (
#         _is_fact_like_table_name(semantic_name)
#         or _is_fact_like_table_name(physical_name)
#         or _is_fact_like_table_name(alias)
#     )


# def _is_fact_like_object_id(object_id: str, object_table_by_id: dict[str, str]) -> bool:
#     table_name = object_table_by_id.get(object_id, "")
#     return _is_fact_like_table_name(table_name)


# def _set_relationship_endpoint_attributes(
#     first_attrs: dict[str, str],
#     second_attrs: dict[str, str],
#     cardinality: str,
#     relation_type: str,
#     first_is_fact_like: bool,
#     second_is_fact_like: bool,
# ) -> None:
#     def _set_unique(attrs: dict[str, str]) -> None:
#         attrs["unique-key"] = "true"
#         attrs["is-db-set-unique-key"] = "true"

#     def _set_guaranteed(attrs: dict[str, str]) -> None:
#         attrs["guaranteed-value"] = "true"
#         attrs["is-db-set-guaranteed-value"] = "true"

#     if cardinality in {"one-to-one", "one-to-many"}:
#         _set_unique(first_attrs)
#     if cardinality in {"one-to-one", "many-to-one"}:
#         _set_unique(second_attrs)

#     if cardinality == "many-to-one":
#         _set_guaranteed(first_attrs)
#     elif cardinality == "one-to-many":
#         _set_guaranteed(second_attrs)

#     if cardinality == "" and relation_type == "fact_to_dimension":
#         if first_is_fact_like and not second_is_fact_like:
#             _set_guaranteed(first_attrs)
#             _set_unique(second_attrs)
#         elif second_is_fact_like and not first_is_fact_like:
#             _set_guaranteed(second_attrs)
#             _set_unique(first_attrs)
#         else:
#             _set_guaranteed(first_attrs)
#             _set_unique(second_attrs)

#     if "guaranteed-value" not in first_attrs and "guaranteed-value" not in second_attrs:
#         if first_is_fact_like and not second_is_fact_like:
#             _set_guaranteed(first_attrs)
#         elif second_is_fact_like and not first_is_fact_like:
#             _set_guaranteed(second_attrs)

#     if "unique-key" not in first_attrs and "unique-key" not in second_attrs:
#         if first_is_fact_like and not second_is_fact_like:
#             _set_unique(second_attrs)
#         elif second_is_fact_like and not first_is_fact_like:
#             _set_unique(first_attrs)


# def _update_existing_object_graph_relationships(datasource_node: ET.Element, model: dict[str, Any]) -> bool:
#     object_graph = datasource_node.find("object-graph")
#     if object_graph is None:
#         return False

#     objects_node = object_graph.find("objects")
#     if objects_node is None:
#         return False

#     relationships_node = object_graph.find("relationships")

#     object_lookup, object_table_by_id = _build_object_graph_indexes(objects_node)
#     if not object_lookup:
#         return _sync_single_object_graph_relation_from_connection(datasource_node)

#     connection_node = datasource_node.find("connection")
#     exposed_lookup = _build_exposed_key_lookup_from_cols(connection_node)

#     fact_object_ids: set[str] = set()
#     for fact in model.get("fact_tables", []):
#         if not isinstance(fact, dict):
#             continue
#         fact_name = str(fact.get("name", "") or "").strip()
#         if not fact_name:
#             continue
#         object_id = _resolve_object_id_for_relationship_side(
#             table_name=fact_name,
#             alias="",
#             object_lookup=object_lookup,
#         )
#         if object_id:
#             fact_object_ids.add(object_id)

#     if not fact_object_ids:
#         for relationship in model.get("relationships", []):
#             if not isinstance(relationship, dict):
#                 continue
#             relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()
#             if relation_type != "fact_to_dimension":
#                 continue
#             object_id = _resolve_object_id_for_relationship_side(
#                 table_name=str(relationship.get("from_table", "") or ""),
#                 alias=str(relationship.get("from_alias", "") or ""),
#                 object_lookup=object_lookup,
#             )
#             if object_id:
#                 fact_object_ids.add(object_id)

#     new_relationship_nodes: list[ET.Element] = []
#     for relationship in model.get("relationships", []):
#         if not isinstance(relationship, dict):
#             continue

#         from_object_id = _resolve_object_id_for_relationship_side(
#             table_name=str(relationship.get("from_table", "") or ""),
#             alias=str(relationship.get("from_alias", "") or ""),
#             object_lookup=object_lookup,
#         )
#         to_object_id = _resolve_object_id_for_relationship_side(
#             table_name=str(relationship.get("to_table", "") or ""),
#             alias=str(relationship.get("to_alias", "") or ""),
#             object_lookup=object_lookup,
#         )
#         if not from_object_id or not to_object_id:
#             continue

#         from_col, to_col = _resolve_join_columns_for_relationship(relationship)
#         if not from_col or not to_col:
#             continue

#         if to_object_id in fact_object_ids and from_object_id not in fact_object_ids:
#             from_object_id, to_object_id = to_object_id, from_object_id
#             from_col, to_col = to_col, from_col

#         from_exposed = _resolve_exposed_relationship_column(
#             exposed_lookup=exposed_lookup,
#             object_table_by_id=object_table_by_id,
#             object_id=from_object_id,
#             column_name=from_col,
#         )
#         to_exposed = _resolve_exposed_relationship_column(
#             exposed_lookup=exposed_lookup,
#             object_table_by_id=object_table_by_id,
#             object_id=to_object_id,
#             column_name=to_col,
#         )
#         if not from_exposed or not to_exposed:
#             continue

#         cardinality = str(relationship.get("cardinality", "") or "").strip().lower()
#         relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()

#         rel_node = ET.Element("relationship")
#         expression_node = ET.SubElement(rel_node, "expression", attrib={"op": "="})
#         ET.SubElement(expression_node, "expression", attrib={"op": f"[{from_exposed}]"})
#         ET.SubElement(expression_node, "expression", attrib={"op": f"[{to_exposed}]"})

#         first_attrs = {"object-id": from_object_id}
#         second_attrs = {"object-id": to_object_id}

#         first_is_fact_like = (
#             from_object_id in fact_object_ids
#             or _is_fact_like_object_id(from_object_id, object_table_by_id)
#         )
#         second_is_fact_like = (
#             to_object_id in fact_object_ids
#             or _is_fact_like_object_id(to_object_id, object_table_by_id)
#         )

#         _set_relationship_endpoint_attributes(
#             first_attrs=first_attrs,
#             second_attrs=second_attrs,
#             cardinality=cardinality,
#             relation_type=relation_type,
#             first_is_fact_like=first_is_fact_like,
#             second_is_fact_like=second_is_fact_like,
#         )

#         ET.SubElement(rel_node, "first-end-point", attrib=first_attrs)
#         ET.SubElement(rel_node, "second-end-point", attrib=second_attrs)
#         new_relationship_nodes.append(rel_node)

#     if not new_relationship_nodes:
#         return _sync_single_object_graph_relation_from_connection(datasource_node)

#     if relationships_node is None:
#         relationships_node = ET.SubElement(object_graph, "relationships")

#     for child in list(relationships_node):
#         relationships_node.remove(child)
#     for rel_node in new_relationship_nodes:
#         relationships_node.append(rel_node)
#     return True


# def _sync_single_object_graph_relation_from_connection(datasource_node: ET.Element) -> bool:
#     object_graph = datasource_node.find("object-graph")
#     if object_graph is None:
#         return False

#     objects_node = object_graph.find("objects")
#     if objects_node is None:
#         return False

#     object_nodes = [child for child in list(objects_node) if child.tag == "object"]
#     if len(object_nodes) != 1:
#         return False

#     connection_node = datasource_node.find("connection")
#     if connection_node is None:
#         return False

#     connection_relation = None
#     for child in list(connection_node):
#         if child.tag == "relation":
#             connection_relation = child
#             break
#     if connection_relation is None:
#         return False

#     object_node = object_nodes[0]
#     properties_node = object_node.find("properties")
#     if properties_node is None:
#         properties_node = ET.SubElement(object_node, "properties", attrib={"context": ""})

#     relation_node = properties_node.find("relation")
#     cloned_relation = copy.deepcopy(connection_relation)
#     if relation_node is None:
#         properties_node.append(cloned_relation)
#     else:
#         relation_index = list(properties_node).index(relation_node)
#         properties_node.remove(relation_node)
#         properties_node.insert(relation_index, cloned_relation)

#     return True


# def _build_object_graph_indexes(objects_node: ET.Element) -> tuple[dict[str, str], dict[str, str]]:
#     object_lookup: dict[str, str] = {}
#     object_table_by_id: dict[str, str] = {}

#     for object_node in [child for child in list(objects_node) if child.tag == "object"]:
#         object_id = str(object_node.attrib.get("id", "") or "").strip()
#         if not object_id:
#             continue

#         caption = str(object_node.attrib.get("caption", "") or "").strip()
#         if caption:
#             caption_key = _name_key(caption)
#             if caption_key:
#                 object_lookup.setdefault(caption_key, object_id)
#             semantic_caption_key = _semantic_name_key(caption)
#             if semantic_caption_key:
#                 object_lookup.setdefault(semantic_caption_key, object_id)
#             if " (" in caption:
#                 base_caption = caption.split(" (", 1)[0].strip()
#                 base_key = _name_key(base_caption)
#                 if base_key:
#                     object_lookup.setdefault(base_key, object_id)

#         relation_node = object_node.find("properties/relation")
#         relation_name = ""
#         table_leaf = ""
#         if relation_node is not None:
#             relation_name = str(relation_node.attrib.get("name", "") or "").strip()
#             table_ref = str(relation_node.attrib.get("table", "") or "").strip()
#             tokens = re.findall(r"\[([^\]]+)\]", table_ref)
#             if tokens:
#                 table_leaf = tokens[-1].strip()
#             elif table_ref:
#                 parts = [part.strip() for part in _clean_name(table_ref).split(".") if part.strip()]
#                 if parts:
#                     table_leaf = parts[-1]

#         for candidate in (relation_name, table_leaf):
#             candidate_key = _name_key(candidate)
#             if candidate_key:
#                 object_lookup.setdefault(candidate_key, object_id)
#             semantic_candidate_key = _semantic_name_key(candidate)
#             if semantic_candidate_key:
#                 object_lookup.setdefault(semantic_candidate_key, object_id)

#         object_table_by_id[object_id] = table_leaf or relation_name or caption

#     return object_lookup, object_table_by_id


# def _resolve_object_id_for_relationship_side(
#     table_name: str,
#     alias: str,
#     object_lookup: dict[str, str],
# ) -> str:
#     candidates: list[str] = []
#     for raw in (alias, table_name):
#         text = str(raw or "").strip()
#         if not text:
#             continue
#         candidates.append(_name_key(text))
#         candidates.append(_semantic_name_key(text))
#         if " (" in text:
#             candidates.append(_name_key(text.split(" (", 1)[0].strip()))

#     for candidate in candidates:
#         if candidate and candidate in object_lookup:
#             return object_lookup[candidate]

#     alias_key = _name_key(alias)
#     if alias_key:
#         for key, object_id in object_lookup.items():
#             if alias_key == key:
#                 return object_id
#         for key, object_id in object_lookup.items():
#             if alias_key in key:
#                 return object_id

#     return ""


# def _build_exposed_key_lookup_from_cols(connection_node: ET.Element | None) -> dict[tuple[str, str], str]:
#     lookup: dict[tuple[str, str], str] = {}
#     if connection_node is None:
#         return lookup

#     cols_node = connection_node.find("cols")
#     if cols_node is None:
#         return lookup

#     for map_node in [child for child in list(cols_node) if child.tag == "map"]:
#         key_raw = str(map_node.attrib.get("key", "") or "").strip()
#         value_raw = str(map_node.attrib.get("value", "") or "").strip()
#         exposed_name = _clean_name(key_raw)
#         table_name, column_name = _parse_cols_map_table_column(value_raw)
#         if not exposed_name or not table_name or not column_name:
#             continue
#         lookup[(_name_key(table_name), _name_key(column_name))] = exposed_name

#     return lookup


# def _parse_cols_map_table_column(value_raw: str) -> tuple[str, str]:
#     tokens = re.findall(r"\[([^\]]+)\]", value_raw or "")
#     if len(tokens) >= 2:
#         return tokens[-2].strip(), tokens[-1].strip()

#     cleaned = _clean_name(value_raw)
#     parts = [part.strip() for part in cleaned.split(".") if part.strip()]
#     if len(parts) >= 2:
#         return parts[-2], parts[-1]
#     return "", ""


# def _resolve_exposed_relationship_column(
#     exposed_lookup: dict[tuple[str, str], str],
#     object_table_by_id: dict[str, str],
#     object_id: str,
#     column_name: str,
# ) -> str:
#     clean_column = _clean_name(column_name)
#     parts = [part.strip() for part in clean_column.split(".") if part.strip()]
#     base_column = parts[-1] if parts else clean_column.strip()
#     if not base_column:
#         return ""

#     table_name = str(object_table_by_id.get(object_id, "") or "").strip()
#     direct_match = exposed_lookup.get((_name_key(table_name), _name_key(base_column)), "")
#     if direct_match:
#         return direct_match

#     column_key = _name_key(base_column)
#     candidates = {
#         exposed_name
#         for (table_key, col_key), exposed_name in exposed_lookup.items()
#         if col_key == column_key
#     }
#     if len(candidates) == 1:
#         return next(iter(candidates))

#     return base_column


# def _upsert_table_object_columns(datasource_node: ET.Element, table_instances: list[dict[str, Any]]) -> None:
#     for child in list(datasource_node):
#         if child.tag == "metadata-records":
#             datasource_node.remove(child)

#     for column_node in [child for child in list(datasource_node) if child.tag == "column"]:
#         datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
#         name_attr = str(column_node.attrib.get("name", "") or "")
#         if datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
#             datasource_node.remove(column_node)

#     children = list(datasource_node)
#     insert_at = len(children)
#     for index, child in enumerate(children):
#         if child.tag == "layout":
#             insert_at = index
#             break

#     for offset, instance in enumerate(table_instances):
#         caption = str(instance.get("caption", "") or "Table")
#         object_id = str(instance.get("object_id", "") or _make_table_object_id(caption))
#         table_column = ET.Element(
#             "column",
#             attrib={
#                 "caption": caption,
#                 "datatype": "table",
#                 "name": f"[__tableau_internal_object_id__].[{object_id}]",
#                 "role": "measure",
#                 "type": "quantitative",
#             },
#         )
#         datasource_node.insert(insert_at + offset, table_column)


# def _generate_response(
#     sql_query: str,
#     conversation: list[dict[str, Any]],
#     llm_config_path: str,
# ) -> tuple[str, dict[str, Any], bool]:
#     if not sql_query.strip():
#         raise ValueError("SQL query must be non-empty.")

#     sql_evidence_model = _heuristic_model(sql_query, conversation)
#     llm = _load_optional_llm(llm_config_path)
#     if llm is not None:
#         try:
#             raw = llm.chat(SYSTEM_PROMPT, _build_user_prompt(sql_query, conversation))
#             payload = _parse_json_payload(raw)
#             assistant_text = payload.get("assistant_response") or "Generated an updated dimensional model."
#             llm_model = _normalize_model(payload.get("model", {}))
#             structured_result = _merge_llm_with_sql_evidence(llm_model, sql_evidence_model)
#             return assistant_text, structured_result, False
#         except Exception as exc:
#             sql_evidence_model["warnings"].insert(0, f"LLM analysis failed; SQL-evidence model used. Details: {exc}")
#             _refresh_model_output(sql_evidence_model)
#             return (
#                 "Built the dimensional model from SQL evidence and applied any recognized follow-up corrections.",
#                 sql_evidence_model,
#                 True,
#             )

#     sql_evidence_model["warnings"].insert(0, "LLM config not available; SQL-evidence model used.")
#     _refresh_model_output(sql_evidence_model)
#     return (
#         "Built the dimensional model from SQL evidence and applied any recognized follow-up corrections.",
#         sql_evidence_model,
#         True,
#     )


# def _merge_llm_with_sql_evidence(
#     llm_model: dict[str, Any],
#     sql_evidence_model: dict[str, Any],
# ) -> dict[str, Any]:
#     merged = copy.deepcopy(sql_evidence_model)

#     known_table_keys = _known_table_keys(sql_evidence_model)
#     llm_table_names = _table_names_from_model(llm_model)
#     ignored_llm_tables = [name for name in llm_table_names if _name_key(name) not in known_table_keys]

#     if merged.get("fact_tables") and llm_model.get("fact_tables"):
#         merged_fact = merged["fact_tables"][0]
#         llm_fact = llm_model["fact_tables"][0]
#         if _same_name(str(llm_fact.get("name", "")), str(merged_fact.get("name", ""))):
#             merged_fact["measures"] = _unique(
#                 _as_string_list(merged_fact.get("measures", []))
#                 + _as_string_list(llm_fact.get("measures", []))
#             )
#             llm_fact_keys = [
#                 key for key in _as_string_list(llm_fact.get("foreign_keys", [])) if _looks_like_key_column(key)
#             ]
#             merged_fact["foreign_keys"] = _unique(_as_string_list(merged_fact.get("foreign_keys", [])) + llm_fact_keys)

#     llm_dimensions = _merge_dimensions(
#         _normalize_dimension_list(llm_model.get("direct_dimensions", [])),
#         _normalize_dimension_list(llm_model.get("snowflake_dimensions", [])),
#     )
#     merged["direct_dimensions"] = _enrich_dimension_attributes(merged.get("direct_dimensions", []), llm_dimensions)
#     merged["snowflake_dimensions"] = _enrich_dimension_attributes(
#         merged.get("snowflake_dimensions", []),
#         llm_dimensions,
#     )

#     if not merged.get("relationships") and llm_model.get("relationships"):
#         candidate_relationships: list[dict[str, Any]] = []
#         for relationship in llm_model.get("relationships", []):
#             if not isinstance(relationship, dict):
#                 continue
#             from_table = str(relationship.get("from_table", "")).strip()
#             to_table = str(relationship.get("to_table", "")).strip()
#             if not from_table or not to_table:
#                 continue
#             if _name_key(from_table) not in known_table_keys or _name_key(to_table) not in known_table_keys:
#                 continue
#             candidate_relationships.append(relationship)
#         if candidate_relationships:
#             merged["relationships"] = candidate_relationships

#     merged["assumptions"] = _unique(
#         _as_string_list(merged.get("assumptions", [])) + _as_string_list(llm_model.get("assumptions", []))
#     )
#     merged["review_notes"] = _unique(
#         _as_string_list(merged.get("review_notes", [])) + _as_string_list(llm_model.get("review_notes", []))
#     )
#     merged["warnings"] = _unique(
#         _as_string_list(merged.get("warnings", [])) + _as_string_list(llm_model.get("warnings", []))
#     )

#     if ignored_llm_tables:
#         merged["warnings"].insert(
#             0,
#             "Ignored LLM-only tables not supported by SQL evidence: " + ", ".join(_unique(ignored_llm_tables)),
#         )

#     _refresh_model_output(merged)
#     return merged


# def _enrich_dimension_attributes(
#     base_dimensions: list[dict[str, Any]],
#     extra_dimensions: list[dict[str, Any]],
# ) -> list[dict[str, Any]]:
#     base = _normalize_dimension_list(base_dimensions)
#     extra_normalized = _normalize_dimension_list(extra_dimensions)
#     extra_lookup_by_identity = {
#         _dimension_identity_key(dimension): dimension
#         for dimension in extra_normalized
#         if _dimension_identity_key(dimension)
#     }
#     extra_lookup_by_name = {
#         _name_key(dimension["name"]): dimension
#         for dimension in extra_normalized
#         if dimension.get("name")
#     }

#     enriched: list[dict[str, Any]] = []
#     for dimension in base:
#         identity_key = _dimension_identity_key(dimension)
#         name_key = _name_key(dimension["name"])
#         extra = extra_lookup_by_identity.get(identity_key) or extra_lookup_by_name.get(name_key)
#         if not extra:
#             enriched.append(dimension)
#             continue
#         enriched.append(
#             {
#                 "name": dimension["name"],
#                 "physical_table": str(dimension.get("physical_table", "")).strip()
#                 or str(extra.get("physical_table", "")).strip()
#                 or dimension["name"],
#                 "alias": str(dimension.get("alias", "")).strip() or str(extra.get("alias", "")).strip(),
#                 "semantic_role": str(dimension.get("semantic_role", "")).strip()
#                 or str(extra.get("semantic_role", "")).strip(),
#                 "attributes": _unique(
#                     _as_string_list(dimension.get("attributes", [])) + _as_string_list(extra.get("attributes", []))
#                 ),
#                 "natural_key": str(dimension.get("natural_key", "")).strip()
#                 or str(extra.get("natural_key", "")).strip(),
#             }
#         )
#     return enriched


# def _table_names_from_model(model: dict[str, Any]) -> list[str]:
#     names: list[str] = []
#     for fact in model.get("fact_tables", []):
#         if isinstance(fact, dict) and fact.get("name"):
#             names.append(str(fact["name"]))
#     for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])):
#         names.append(dimension["name"])
#     for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", [])):
#         names.append(dimension["name"])
#     for relationship in model.get("relationships", []):
#         if not isinstance(relationship, dict):
#             continue
#         from_table = str(relationship.get("from_table", "")).strip()
#         to_table = str(relationship.get("to_table", "")).strip()
#         if from_table:
#             names.append(from_table)
#         if to_table:
#             names.append(to_table)
#     return _unique(names)


# def _known_table_keys(model: dict[str, Any]) -> set[str]:
#     keys = {_name_key(name) for name in _table_names_from_model(model) if _name_key(name)}
#     for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])):
#         physical_key = _name_key(str(dimension.get("physical_table", "")))
#         if physical_key:
#             keys.add(physical_key)
#     for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", [])):
#         physical_key = _name_key(str(dimension.get("physical_table", "")))
#         if physical_key:
#             keys.add(physical_key)
#     return keys


# def _load_optional_llm(llm_config_path: str):
#     candidates: list[Path] = []
#     if llm_config_path:
#         raw = Path(llm_config_path).expanduser()
#         candidates.append(raw)
#         if not raw.is_absolute():
#             candidates.append(ROOT_DIR / raw)
#     candidates.extend([SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG])

#     deduped: list[Path] = []
#     seen: set[str] = set()
#     for candidate in candidates:
#         key = str(candidate)
#         if key in seen:
#             continue
#         seen.add(key)
#         deduped.append(candidate)

#     for candidate in deduped:
#         if not candidate.exists():
#             continue
#         try:
#             return load_llm_from_config(candidate)
#         except Exception:
#             continue
#     return None


# def _build_user_prompt(sql_query: str, conversation: list[dict[str, Any]]) -> str:
#     history_lines: list[str] = []
#     for index, message in enumerate(conversation, start=1):
#         history_lines.append(f"{index}. {message['role'].upper()}: {message['content']}")
#         if message.get("structured_result"):
#             history_lines.append(json.dumps(message["structured_result"], indent=2))

#     history = "\n".join(history_lines) if history_lines else "1. USER: Analyze the SQL query."
#     return (
#         f"SQL query:\n{sql_query}\n\n"
#         f"Conversation history:\n{history}\n\n"
#         "Return JSON only."
#     )


# def _parse_json_payload(raw_text: str) -> dict[str, Any]:
#     text = raw_text.strip()
#     if text.startswith("```"):
#         text = re.sub(r"^```(?:json)?", "", text).strip()
#         text = re.sub(r"```$", "", text).strip()

#     try:
#         payload = json.loads(text)
#         if isinstance(payload, dict):
#             return payload
#     except json.JSONDecodeError:
#         pass

#     start = text.find("{")
#     if start < 0:
#         raise ValueError("Response was not valid JSON.")

#     depth = 0
#     in_string = False
#     escape = False
#     for index in range(start, len(text)):
#         char = text[index]
#         if in_string:
#             if escape:
#                 escape = False
#             elif char == "\\":
#                 escape = True
#             elif char == '"':
#                 in_string = False
#             continue
#         if char == '"':
#             in_string = True
#             continue
#         if char == "{":
#             depth += 1
#         elif char == "}":
#             depth -= 1
#             if depth == 0:
#                 payload = json.loads(text[start : index + 1])
#                 if isinstance(payload, dict):
#                     return payload
#                 break
#     raise ValueError("Response was not valid JSON.")


# def _normalize_model(model: dict[str, Any]) -> dict[str, Any]:
#     normalized = {
#         "model_type": _normalize_model_type(str(model.get("model_type", "Unknown") or "Unknown")),
#         "schema_confidence": _normalize_confidence(str(model.get("schema_confidence", "") or "")),
#         "model_summary": str(model.get("model_summary", "") or "").strip(),
#         "fact_tables": [],
#         "direct_dimensions": [],
#         "snowflake_dimensions": [],
#         "relationships": [],
#         "review_notes": _as_string_list(model.get("review_notes", [])),
#         "assumptions": _as_string_list(model.get("assumptions", [])),
#         "warnings": _as_string_list(model.get("warnings", [])),
#     }

#     for item in model.get("fact_tables", []):
#         if not isinstance(item, dict):
#             continue
#         normalized["fact_tables"].append(
#             {
#                 "name": str(item.get("name", "")).strip(),
#                 "measures": _as_string_list(item.get("measures", [])),
#                 "foreign_keys": _as_string_list(item.get("foreign_keys", [])),
#             }
#         )

#     explicit_direct = _normalize_dimension_list(model.get("direct_dimensions", []))
#     explicit_snowflake = _normalize_dimension_list(model.get("snowflake_dimensions", []))
#     legacy_dimensions = _normalize_dimension_list(model.get("dimension_tables", []))
#     branch_hints = _branch_dimension_names(model.get("snowflake_branches", []))
#     branch_dimensions = [{"name": table, "attributes": [], "natural_key": ""} for table in branch_hints]

#     if explicit_direct or explicit_snowflake:
#         normalized["direct_dimensions"] = explicit_direct
#         normalized["snowflake_dimensions"] = explicit_snowflake
#     else:
#         normalized["direct_dimensions"] = legacy_dimensions
#         normalized["snowflake_dimensions"] = branch_dimensions

#     for item in model.get("relationships", []):
#         if not isinstance(item, dict):
#             continue
#         normalized["relationships"].append(
#             {
#                 "from_table": str(item.get("from_table", "")).strip(),
#                 "to_table": str(item.get("to_table", "")).strip(),
#                 "from_alias": str(item.get("from_alias", "")).strip(),
#                 "to_alias": str(item.get("to_alias", "")).strip(),
#                 "relationship_type": _normalize_relationship_type(str(item.get("relationship_type", "") or "")),
#                 "cardinality": str(item.get("cardinality", "")).strip(),
#                 "join_condition": str(item.get("join_condition", "")).strip(),
#             }
#         )

#     _refresh_model_output(normalized)
#     return normalized


# def _normalize_model_type(value: str) -> str:
#     lowered = value.strip().lower()
#     if lowered == "star":
#         return "Star"
#     if lowered == "snowflake":
#         return "Snowflake"
#     if lowered == "hybrid":
#         return "Hybrid"
#     return "Unknown"


# def _normalize_confidence(value: str) -> str:
#     lowered = value.strip().lower()
#     if lowered in {"high", "medium", "low"}:
#         return lowered
#     return ""


# def _normalize_relationship_type(value: str) -> str:
#     lowered = value.strip().lower().replace("-", "_")
#     if lowered in {"fact_to_dimension", "dimension_to_dimension"}:
#         return lowered
#     return ""


# def _split_dimension_name_role(name: str) -> tuple[str, str]:
#     text = str(name or "").strip()
#     match = re.match(r"^(.*?)\s*\[role:\s*([^\]]+)\](?:\s*\(\d+\))?\s*$", text, flags=re.IGNORECASE)
#     if not match:
#         return text, ""
#     return match.group(1).strip(), match.group(2).strip()


# def _normalize_dimension_list(value: Any) -> list[dict[str, Any]]:
#     if not isinstance(value, list):
#         return []
#     dimensions: list[dict[str, Any]] = []
#     for item in value:
#         if isinstance(item, str):
#             name = item.strip()
#             if name:
#                 parsed_physical, parsed_role = _split_dimension_name_role(name)
#                 dimensions.append(
#                     {
#                         "name": name,
#                         "physical_table": parsed_physical or name,
#                         "alias": "",
#                         "semantic_role": parsed_role,
#                         "attributes": [],
#                         "natural_key": "",
#                     }
#                 )
#             continue
#         if not isinstance(item, dict):
#             continue
#         name = str(item.get("name", "")).strip()
#         if not name:
#             continue
#         parsed_physical, parsed_role = _split_dimension_name_role(name)
#         physical_table = str(item.get("physical_table", "")).strip() or parsed_physical or name
#         semantic_role = str(item.get("semantic_role", "")).strip() or parsed_role
#         dimensions.append(
#             {
#                 "name": name,
#                 "physical_table": physical_table,
#                 "alias": str(item.get("alias", "")).strip(),
#                 "semantic_role": semantic_role,
#                 "attributes": _as_string_list(item.get("attributes", [])),
#                 "natural_key": str(item.get("natural_key", "")).strip(),
#             }
#         )
#     return dimensions


# def _dimension_identity_key(dimension: dict[str, Any]) -> str:
#     alias = str(dimension.get("alias", "")).strip().lower()
#     if alias:
#         return f"alias:{alias}"

#     physical_key = _name_key(str(dimension.get("physical_table", "")))
#     role_key = _name_key(str(dimension.get("semantic_role", "")))
#     if physical_key and role_key:
#         return f"physical:{physical_key}|role:{role_key}"

#     name = _name_key(str(dimension.get("name", "")).strip())
#     if name:
#         return f"name:{name}"
#     return ""


# def _merge_dimensions(*dimension_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
#     merged: dict[str, dict[str, Any]] = {}
#     order: list[str] = []
#     for dimensions in dimension_groups:
#         for dimension in dimensions:
#             normalized = _normalize_dimension_list([dimension])
#             if not normalized:
#                 continue
#             candidate = normalized[0]
#             name = str(candidate.get("name", "")).strip()
#             if not name:
#                 continue
#             key = _dimension_identity_key(candidate)
#             if not key:
#                 continue
#             if key not in merged:
#                 merged[key] = {
#                     "name": name,
#                     "physical_table": str(candidate.get("physical_table", "")).strip() or name,
#                     "alias": str(candidate.get("alias", "")).strip(),
#                     "semantic_role": str(candidate.get("semantic_role", "")).strip(),
#                     "attributes": [],
#                     "natural_key": "",
#                 }
#                 order.append(key)
#             merged[key]["attributes"] = _unique(
#                 merged[key]["attributes"] + _as_string_list(candidate.get("attributes", []))
#             )
#             if not merged[key]["physical_table"]:
#                 merged[key]["physical_table"] = str(candidate.get("physical_table", "")).strip()
#             if not merged[key]["alias"]:
#                 merged[key]["alias"] = str(candidate.get("alias", "")).strip()
#             if not merged[key]["semantic_role"]:
#                 merged[key]["semantic_role"] = str(candidate.get("semantic_role", "")).strip()

#             natural_key = str(candidate.get("natural_key", "")).strip()
#             if natural_key and not merged[key]["natural_key"]:
#                 merged[key]["natural_key"] = natural_key
#     return [merged[key] for key in order]


# def _branch_dimension_names(value: Any) -> set[str]:
#     if not isinstance(value, list):
#         return set()
#     names: set[str] = set()
#     for branch in value:
#         if not isinstance(branch, dict):
#             continue
#         for table_name in _as_string_list(branch.get("branch_tables", [])):
#             if table_name.strip():
#                 names.add(table_name.strip())
#     return names


# def _infer_relationship_type(from_table: str, to_table: str, fact_keys: set[str]) -> str:
#     if _name_key(from_table) in fact_keys or _name_key(to_table) in fact_keys:
#         return "fact_to_dimension"
#     return "dimension_to_dimension"


# def _table_graph(relationships: list[dict[str, Any]]) -> dict[str, set[str]]:
#     graph: dict[str, set[str]] = {}
#     for relationship in relationships:
#         left_key = _name_key(str(relationship.get("from_table", "")))
#         right_key = _name_key(str(relationship.get("to_table", "")))
#         if not left_key or not right_key:
#             continue
#         graph.setdefault(left_key, set()).add(right_key)
#         graph.setdefault(right_key, set()).add(left_key)
#     return graph


# def _table_distances(start_key: str, graph: dict[str, set[str]]) -> dict[str, int]:
#     if not start_key:
#         return {}
#     distances: dict[str, int] = {start_key: 0}
#     queue = [start_key]
#     index = 0
#     while index < len(queue):
#         current = queue[index]
#         index += 1
#         for neighbor in graph.get(current, set()):
#             if neighbor in distances:
#                 continue
#             distances[neighbor] = distances[current] + 1
#             queue.append(neighbor)
#     return distances


# def _shortest_table_path(start_key: str, target_key: str, graph: dict[str, set[str]]) -> list[str]:
#     if not start_key or not target_key:
#         return []
#     if start_key == target_key:
#         return [start_key]

#     parents: dict[str, str | None] = {start_key: None}
#     queue = [start_key]
#     index = 0
#     while index < len(queue):
#         current = queue[index]
#         index += 1
#         for neighbor in sorted(graph.get(current, set())):
#             if neighbor in parents:
#                 continue
#             parents[neighbor] = current
#             if neighbor == target_key:
#                 break
#             queue.append(neighbor)
#         if target_key in parents:
#             break

#     if target_key not in parents:
#         return []

#     path: list[str] = []
#     cursor: str | None = target_key
#     while cursor is not None:
#         path.append(cursor)
#         cursor = parents.get(cursor)
#     path.reverse()
#     return path


# def _nearest_table_key(start_key: str, candidate_keys: set[str], graph: dict[str, set[str]]) -> str:
#     if not start_key or not candidate_keys:
#         return ""

#     distances = _table_distances(start_key, graph)
#     ranked = sorted(
#         ((distances[key], key) for key in candidate_keys if key in distances),
#         key=lambda item: (item[0], item[1]),
#     )
#     if not ranked:
#         return ""
#     return ranked[0][1]


# def _snowflake_origin_lookup(
#     fact_table: str,
#     direct_dimensions: list[dict[str, Any]],
#     snowflake_dimensions: list[dict[str, Any]],
#     relationships: list[dict[str, Any]],
# ) -> dict[str, str]:
#     fact_key = _name_key(fact_table)
#     graph = _table_graph(relationships)
#     direct_lookup = {
#         _name_key(dimension["name"]): dimension["name"]
#         for dimension in direct_dimensions
#         if dimension.get("name")
#     }
#     direct_keys = set(direct_lookup.keys())

#     origins: dict[str, str] = {}
#     for snowflake_dimension in snowflake_dimensions:
#         snowflake_name = str(snowflake_dimension.get("name", "")).strip()
#         snowflake_key = _name_key(snowflake_name)
#         if not snowflake_key:
#             continue

#         origin = ""
#         if fact_key:
#             path = _shortest_table_path(fact_key, snowflake_key, graph)
#             for path_key in path[1:]:
#                 if path_key in direct_lookup:
#                     origin = direct_lookup[path_key]
#                     break
#         if not origin:
#             nearest_key = _nearest_table_key(snowflake_key, direct_keys, graph)
#             if nearest_key:
#                 origin = direct_lookup.get(nearest_key, "")

#         origins[snowflake_key] = origin

#     return origins


# def _classify_dimensions(
#     all_dimensions: list[dict[str, Any]],
#     fact_table: str,
#     relationships: list[dict[str, Any]],
#     direct_hints: set[str],
#     snowflake_hints: set[str],
# ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
#     fact_key = _name_key(fact_table)
#     distances = _table_distances(fact_key, _table_graph(relationships)) if fact_key else {}

#     direct_dimensions: list[dict[str, Any]] = []
#     snowflake_dimensions: list[dict[str, Any]] = []
#     for dimension in all_dimensions:
#         name = str(dimension.get("name", "")).strip()
#         if not name or (fact_key and _name_key(name) == fact_key):
#             continue
#         key = _name_key(name)
#         distance = distances.get(key)
#         if key in snowflake_hints and key not in direct_hints:
#             snowflake_dimensions.append(dimension)
#             continue
#         if key in direct_hints and key not in snowflake_hints:
#             direct_dimensions.append(dimension)
#             continue
#         if distance is not None and distance > 1:
#             snowflake_dimensions.append(dimension)
#             continue
#         if key in direct_hints or distance == 1 or distance is None:
#             direct_dimensions.append(dimension)
#             continue
#         snowflake_dimensions.append(dimension)

#     return _merge_dimensions(direct_dimensions), _merge_dimensions(snowflake_dimensions)


# def _extract_fact_keys_from_relationship(relationship: dict[str, Any], fact_table: str) -> list[str]:
#     if relationship.get("relationship_type") != "fact_to_dimension":
#         return []

#     pairs = _extract_join_pairs(str(relationship.get("join_condition", "")))
#     if not pairs:
#         return []

#     if _same_name(str(relationship.get("from_table", "")), fact_table):
#         return _unique([pair["left_column"] for pair in pairs if pair["left_column"]])
#     if _same_name(str(relationship.get("to_table", "")), fact_table):
#         return _unique([pair["right_column"] for pair in pairs if pair["right_column"]])
#     return []


# def _dedupe_relationships(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
#     deduped: list[dict[str, Any]] = []
#     seen: set[tuple[str, str, str, str, str, str, str]] = set()

#     for relationship in relationships:
#         from_table = str(relationship.get("from_table", "")).strip()
#         to_table = str(relationship.get("to_table", "")).strip()
#         from_alias = str(relationship.get("from_alias", "")).strip().lower()
#         to_alias = str(relationship.get("to_alias", "")).strip().lower()
#         relationship_type = str(relationship.get("relationship_type", "")).strip().lower()
#         cardinality = str(relationship.get("cardinality", "")).strip().lower()
#         join_condition = " ".join(str(relationship.get("join_condition", "")).split()).lower()

#         forward_key = (
#             _name_key(from_table),
#             _name_key(to_table),
#             from_alias,
#             to_alias,
#             relationship_type,
#             cardinality,
#             join_condition,
#         )
#         reverse_key = (
#             _name_key(to_table),
#             _name_key(from_table),
#             to_alias,
#             from_alias,
#             relationship_type,
#             _reverse_cardinality(cardinality),
#             join_condition,
#         )

#         if forward_key in seen or reverse_key in seen:
#             continue

#         seen.add(forward_key)
#         deduped.append(relationship)

#     return deduped


# def _refresh_model_output(model: dict[str, Any], forced_model_type: str | None = None) -> None:
#     fact_tables: list[dict[str, Any]] = []
#     for item in model.get("fact_tables", []):
#         if not isinstance(item, dict):
#             continue
#         name = str(item.get("name", "")).strip()
#         if not name:
#             continue
#         fact_tables.append(
#             {
#                 "name": name,
#                 "measures": _as_string_list(item.get("measures", [])),
#                 "foreign_keys": _as_string_list(item.get("foreign_keys", [])),
#             }
#         )
#     model["fact_tables"] = fact_tables

#     fact_keys = {_name_key(fact["name"]) for fact in fact_tables if fact.get("name")}
#     relationships: list[dict[str, Any]] = []
#     for item in model.get("relationships", []):
#         if not isinstance(item, dict):
#             continue
#         from_table = str(item.get("from_table", "")).strip()
#         to_table = str(item.get("to_table", "")).strip()
#         if not from_table or not to_table:
#             continue
#         relationship_type = _normalize_relationship_type(str(item.get("relationship_type", "") or ""))
#         if not relationship_type:
#             relationship_type = _infer_relationship_type(from_table, to_table, fact_keys)
#         relationships.append(
#             {
#                 "from_table": from_table,
#                 "to_table": to_table,
#                 "from_alias": str(item.get("from_alias", "")).strip(),
#                 "to_alias": str(item.get("to_alias", "")).strip(),
#                 "relationship_type": relationship_type,
#                 "cardinality": str(item.get("cardinality", "")).strip(),
#                 "join_condition": str(item.get("join_condition", "")).strip(),
#             }
#         )
#     relationships = _dedupe_relationships(relationships)
#     model["relationships"] = relationships

#     direct_dimensions = _normalize_dimension_list(model.get("direct_dimensions", []))
#     snowflake_dimensions = _normalize_dimension_list(model.get("snowflake_dimensions", []))
#     all_dimensions = _merge_dimensions(direct_dimensions, snowflake_dimensions)
#     known_dimension_name_keys = {_name_key(str(dimension.get("name", ""))) for dimension in all_dimensions}
#     primary_fact = fact_tables[0]["name"] if fact_tables else ""

#     for relationship in relationships:
#         for table_name in (relationship["from_table"], relationship["to_table"]):
#             if primary_fact and _same_name(table_name, primary_fact):
#                 continue
#             name_key = _name_key(table_name)
#             if name_key and name_key in known_dimension_name_keys:
#                 continue
#             parsed_physical, parsed_role = _split_dimension_name_role(table_name)
#             all_dimensions = _merge_dimensions(
#                 all_dimensions,
#                 [
#                     {
#                         "name": table_name,
#                         "physical_table": parsed_physical or table_name,
#                         "alias": "",
#                         "semantic_role": parsed_role,
#                         "attributes": [],
#                         "natural_key": "",
#                     }
#                 ],
#             )
#             if name_key:
#                 known_dimension_name_keys.add(name_key)

#     direct_hints = {_name_key(dimension["name"]) for dimension in direct_dimensions}
#     snowflake_hints = {_name_key(dimension["name"]) for dimension in snowflake_dimensions}
#     classified_direct, classified_snowflake = _classify_dimensions(
#         all_dimensions=all_dimensions,
#         fact_table=primary_fact,
#         relationships=relationships,
#         direct_hints=direct_hints,
#         snowflake_hints=snowflake_hints,
#     )
#     model["direct_dimensions"] = classified_direct
#     model["snowflake_dimensions"] = classified_snowflake

#     for fact in model["fact_tables"]:
#         derived_keys: list[str] = []
#         for relationship in relationships:
#             derived_keys.extend(_extract_fact_keys_from_relationship(relationship, fact["name"]))
#         fact["foreign_keys"] = _unique(derived_keys) or _unique(fact.get("foreign_keys", []))

#     derived_type = _derive_model_type(
#         fact_table=primary_fact,
#         relationships=relationships,
#         direct_dimensions=model["direct_dimensions"],
#         snowflake_dimensions=model["snowflake_dimensions"],
#     )
#     if forced_model_type:
#         model["model_type"] = _normalize_model_type(forced_model_type)
#     elif derived_type != "Unknown":
#         model["model_type"] = derived_type
#     else:
#         model["model_type"] = _normalize_model_type(str(model.get("model_type", "Unknown") or "Unknown"))

#     model["schema_confidence"] = _derive_schema_confidence(model)
#     model["model_summary"] = _build_model_summary(model)
#     existing_notes = _as_string_list(model.get("review_notes", []))
#     model["review_notes"] = _unique(existing_notes + _derived_review_notes(model))
#     model["assumptions"] = _as_string_list(model.get("assumptions", []))
#     model["warnings"] = _as_string_list(model.get("warnings", []))


# def _derive_model_type(
#     fact_table: str,
#     relationships: list[dict[str, Any]],
#     direct_dimensions: list[dict[str, Any]],
#     snowflake_dimensions: list[dict[str, Any]],
# ) -> str:
#     if not fact_table:
#         return "Unknown"
#     has_fact_to_dimension = any(
#         relationship.get("relationship_type") == "fact_to_dimension" for relationship in relationships
#     )
#     has_dimension_to_dimension = any(
#         relationship.get("relationship_type") == "dimension_to_dimension" for relationship in relationships
#     )
#     has_snowflake = bool(snowflake_dimensions) or has_dimension_to_dimension
#     has_direct = bool(direct_dimensions)

#     if has_fact_to_dimension and not has_snowflake:
#         return "Star"
#     if has_dimension_to_dimension and has_fact_to_dimension:
#         return "Snowflake"
#     if has_snowflake and has_direct:
#         return "Hybrid"
#     if has_snowflake:
#         return "Snowflake"
#     if has_fact_to_dimension:
#         return "Star"
#     return "Unknown"


# def _derive_schema_confidence(model: dict[str, Any]) -> str:
#     score = 0
#     if model.get("fact_tables"):
#         score += 2
#     if model.get("relationships"):
#         score += 2
#     if any(relationship.get("join_condition") for relationship in model.get("relationships", [])):
#         score += 1
#     if any(
#         relationship.get("relationship_type") == "fact_to_dimension"
#         for relationship in model.get("relationships", [])
#     ):
#         score += 1
#     if model.get("warnings"):
#         score -= 2
#     if not model.get("direct_dimensions") and not model.get("snowflake_dimensions"):
#         score -= 1

#     if score >= 5:
#         return "high"
#     if score >= 2:
#         return "medium"
#     return "low"


# def _build_model_summary(model: dict[str, Any]) -> str:
#     fact_name = (
#         str(model["fact_tables"][0].get("name", "")).split(".")[-1]
#         if model.get("fact_tables")
#         else "an unknown fact table"
#     )
#     direct_count = len(model.get("direct_dimensions", []))
#     snowflake_count = len(model.get("snowflake_dimensions", []))
#     return (
#         f"{model.get('model_type', 'Unknown')} model centered on {fact_name} with "
#         f"{direct_count} direct dimensions and {snowflake_count} snowflake dimensions."
#     )


# def _derived_review_notes(model: dict[str, Any]) -> list[str]:
#     notes: list[str] = []
#     if not model.get("relationships"):
#         notes.append("No joins were parsed; verify the SQL join conditions.")
#     if model.get("snowflake_dimensions"):
#         notes.append("Dimension-to-dimension paths were detected and classified as snowflake dimensions.")
#     all_dimensions = _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
#         model.get("snowflake_dimensions", [])
#     )
#     by_physical: dict[str, dict[str, Any]] = {}
#     for dimension in all_dimensions:
#         physical_label = str(dimension.get("physical_table", "")).strip() or str(dimension.get("name", "")).strip()
#         physical_key = _name_key(physical_label)
#         if not physical_key:
#             continue
#         role = str(dimension.get("semantic_role", "")).strip() or str(dimension.get("alias", "")).strip()
#         bucket = by_physical.setdefault(physical_key, {"label": physical_label, "roles": set()})
#         bucket["roles"].add(role or str(dimension.get("name", "")))
#     role_playing_tables = [bucket["label"] for bucket in by_physical.values() if len(bucket["roles"]) > 1]
#     if role_playing_tables:
#         notes.append(
#             "Role-playing dimensions detected on physical tables: " + ", ".join(_unique(role_playing_tables))
#         )
#     if model.get("fact_tables"):
#         fact = model["fact_tables"][0]
#         if model.get("direct_dimensions") and not fact.get("foreign_keys"):
#             notes.append("Fact foreign keys could not be extracted from join predicates.")
#     if model.get("model_type") == "Unknown":
#         notes.append("Schema type could not be confidently determined from the available SQL structure.")
#     return notes


# def _format_role_name(value: str) -> str:
#     token = _clean_name(value).strip()
#     if not token:
#         return ""
#     parts = [part for part in re.split(r"[^A-Za-z0-9]+", token) if part]
#     if len(parts) > 1:
#         return "".join(part[:1].upper() + part[1:] for part in parts)
#     return token[:1].upper() + token[1:]


# def _infer_semantic_role_from_fk(fact_fk: str, alias: str, physical_table: str) -> str:
#     fk = _clean_name(fact_fk).strip()
#     if fk:
#         role = re.sub(r"(?i)(?:_|)(id|key|code|number|num)$", "", fk).strip("_ ")
#         role_name = _format_role_name(role)
#         if role_name:
#             return role_name

#     alias_role = _format_role_name(alias)
#     if len(alias_role) > 2:
#         return alias_role

#     physical = _clean_name(physical_table).split(".")[-1]
#     physical = re.sub(r"(?i)^dim_?", "", physical)
#     return _format_role_name(physical)


# def _fact_side_foreign_keys_for_alias(
#     dimension_alias: str,
#     joins: list[dict[str, str]],
#     fact_aliases: set[str],
# ) -> list[str]:
#     alias_key = dimension_alias.lower()
#     keys: list[str] = []
#     for join in joins:
#         for pair in _extract_join_pairs(join.get("condition", "")):
#             left_alias = pair["left_alias"].lower()
#             right_alias = pair["right_alias"].lower()
#             if left_alias in fact_aliases and right_alias == alias_key:
#                 keys.append(pair["left_column"])
#             elif right_alias in fact_aliases and left_alias == alias_key:
#                 keys.append(pair["right_column"])
#     return _unique(keys)


# def _join_paths_for_alias(dimension_alias: str, joins: list[dict[str, str]]) -> list[str]:
#     alias_key = dimension_alias.lower()
#     paths: list[str] = []
#     for join in joins:
#         condition = " ".join(str(join.get("condition", "")).split())
#         if not condition:
#             continue
#         pairs = _extract_join_pairs(condition)
#         if any(pair["left_alias"].lower() == alias_key or pair["right_alias"].lower() == alias_key for pair in pairs):
#             paths.append(condition)
#     return _unique(paths)


# def _build_semantic_dimensions(
#     table_refs: list[tuple[str, str]],
#     alias_to_table: dict[str, str],
#     joins: list[dict[str, str]],
#     select_columns: list[dict[str, Any]],
#     fact_table: str,
# ) -> tuple[list[dict[str, Any]], dict[str, str]]:
#     fact_aliases = {alias.lower() for table, alias in table_refs if _same_name(table, fact_table)}
#     candidates: list[dict[str, Any]] = []
#     for table, alias in table_refs:
#         if _same_name(table, fact_table):
#             continue

#         alias_key = alias.lower()
#         attributes = [
#             column["output_name"]
#             for column in select_columns
#             if not column["is_measure"] and str(column.get("table_alias", "")).lower() == alias_key
#         ]
#         fact_foreign_keys = _fact_side_foreign_keys_for_alias(alias, joins, fact_aliases)
#         natural_key = _dimension_key_from_joins(
#             table_aliases={alias},
#             joins=joins,
#             alias_to_table=alias_to_table,
#             fact_table=fact_table,
#         )
#         semantic_role = _infer_semantic_role_from_fk(
#             fact_foreign_keys[0] if fact_foreign_keys else "",
#             alias=alias,
#             physical_table=table,
#         )
#         candidates.append(
#             {
#                 "physical_table": table,
#                 "alias": alias,
#                 "semantic_role": semantic_role,
#                 "fact_foreign_keys": fact_foreign_keys,
#                 "join_paths": _join_paths_for_alias(alias, joins),
#                 "attributes": _unique(attributes),
#                 "natural_key": natural_key,
#             }
#         )

#     by_physical_table: dict[str, list[dict[str, Any]]] = {}
#     for candidate in candidates:
#         by_physical_table.setdefault(_name_key(candidate["physical_table"]), []).append(candidate)

#     semantic_dimensions: list[dict[str, Any]] = []
#     alias_to_semantic_name: dict[str, str] = {}
#     used_names: set[str] = set()

#     for group in by_physical_table.values():
#         signatures = {
#             (
#                 tuple(_as_string_list(item.get("fact_foreign_keys", []))),
#                 tuple(_as_string_list(item.get("join_paths", []))),
#             )
#             for item in group
#         }
#         is_role_playing = len(group) > 1 and len(signatures) > 1

#         if not is_role_playing:
#             merged_attributes: list[str] = []
#             merged_natural_key = ""
#             for item in group:
#                 merged_attributes.extend(_as_string_list(item.get("attributes", [])))
#                 if not merged_natural_key:
#                     merged_natural_key = str(item.get("natural_key", "")).strip()

#             lead = group[0]
#             semantic_name = str(lead["physical_table"])
#             semantic_dimensions.append(
#                 {
#                     "name": semantic_name,
#                     "physical_table": str(lead["physical_table"]),
#                     "alias": str(lead["alias"]),
#                     "semantic_role": str(lead.get("semantic_role", "")),
#                     "attributes": _unique(merged_attributes),
#                     "natural_key": merged_natural_key,
#                 }
#             )
#             for item in group:
#                 alias_to_semantic_name[str(item["alias"]).lower()] = semantic_name
#             used_names.add(_name_key(semantic_name))
#             continue

#         for item in group:
#             role = str(item.get("semantic_role", "")).strip() or _format_role_name(str(item.get("alias", "")))
#             semantic_name = f"{item['physical_table']} [role: {role}]"
#             base_name = semantic_name
#             suffix = 2
#             while _name_key(semantic_name) in used_names:
#                 semantic_name = f"{base_name} ({suffix})"
#                 suffix += 1

#             semantic_dimensions.append(
#                 {
#                     "name": semantic_name,
#                     "physical_table": str(item["physical_table"]),
#                     "alias": str(item["alias"]),
#                     "semantic_role": role,
#                     "attributes": _as_string_list(item.get("attributes", [])),
#                     "natural_key": str(item.get("natural_key", "")).strip(),
#                 }
#             )
#             alias_to_semantic_name[str(item["alias"]).lower()] = semantic_name
#             used_names.add(_name_key(semantic_name))

#     return _merge_dimensions(semantic_dimensions), alias_to_semantic_name


# def _apply_semantic_names_to_relationships(
#     relationships: list[dict[str, str]],
#     alias_to_semantic_name: dict[str, str],
#     fact_aliases: set[str],
#     fact_table: str,
# ) -> list[dict[str, str]]:
#     output: list[dict[str, str]] = []
#     for relationship in relationships:
#         if not isinstance(relationship, dict):
#             continue
#         from_alias = str(relationship.get("from_alias", "")).strip()
#         to_alias = str(relationship.get("to_alias", "")).strip()

#         from_table = str(relationship.get("from_table", "")).strip()
#         to_table = str(relationship.get("to_table", "")).strip()

#         if from_alias.lower() in fact_aliases:
#             from_table = fact_table
#         elif from_alias.lower() in alias_to_semantic_name:
#             from_table = alias_to_semantic_name[from_alias.lower()]

#         if to_alias.lower() in fact_aliases:
#             to_table = fact_table
#         elif to_alias.lower() in alias_to_semantic_name:
#             to_table = alias_to_semantic_name[to_alias.lower()]

#         enriched = {
#             "from_table": from_table,
#             "to_table": to_table,
#             "relationship_type": str(relationship.get("relationship_type", "")).strip(),
#             "cardinality": str(relationship.get("cardinality", "")).strip(),
#             "join_condition": str(relationship.get("join_condition", "")).strip(),
#         }
#         if from_alias:
#             enriched["from_alias"] = from_alias
#         if to_alias:
#             enriched["to_alias"] = to_alias
#         output.append(enriched)
#     return output


# def _heuristic_model(sql_query: str, conversation: list[dict[str, Any]]) -> dict[str, Any]:
#     cleaned_sql = _sanitize_sql(sql_query)
#     cte_primary_tables = _extract_cte_primary_tables(cleaned_sql)
#     raw_table_refs = _extract_table_refs(cleaned_sql)
#     table_refs = _resolve_table_refs(raw_table_refs, cte_primary_tables)
#     joins = _extract_joins(cleaned_sql)
#     select_columns = _extract_select_columns(cleaned_sql)
#     group_by = _extract_group_by(cleaned_sql)

#     if not table_refs:
#         return _normalize_model(
#             {
#                 "model_type": "Unknown",
#                 "schema_confidence": "low",
#                 "model_summary": "Unable to infer a dimensional model because source tables were not detected.",
#                 "review_notes": ["The parser could not identify tables from FROM/JOIN clauses."],
#                 "assumptions": ["The SQL parser could not confidently identify source tables."],
#                 "warnings": ["Use a configured LLM endpoint for richer modeling output."],
#             }
#         )

#     alias_to_table = _build_alias_to_table_map(table_refs)
#     tables = _unique([table for table, _alias in table_refs])
#     fact_table = _pick_fact_table(tables, select_columns, table_refs, joins)
#     fact_aliases = {alias.lower() for table, alias in table_refs if _same_name(table, fact_table)}
#     semantic_dimensions, alias_to_semantic_name = _build_semantic_dimensions(
#         table_refs=table_refs,
#         alias_to_table=alias_to_table,
#         joins=joins,
#         select_columns=select_columns,
#         fact_table=fact_table,
#     )

#     relationships: list[dict[str, Any]] = []
#     for join in joins:
#         relationship = _build_relationship_from_join(
#             join=join,
#             alias_to_table=alias_to_table,
#             fact_table=fact_table,
#             group_by=group_by,
#             select_columns=select_columns,
#         )
#         if relationship:
#             relationships.append(relationship)
#     relationships = _apply_semantic_names_to_relationships(
#         relationships=relationships,
#         alias_to_semantic_name=alias_to_semantic_name,
#         fact_aliases=fact_aliases,
#         fact_table=fact_table,
#     )

#     fact_measures = [
#         column["output_name"]
#         for column in select_columns
#         if column["is_measure"]
#         and _same_name(_resolve_alias_table(alias_to_table, column["table_alias"]), fact_table)
#     ]
#     if not fact_measures:
#         fact_measures = [column["output_name"] for column in select_columns if column["is_measure"]]

#     fact_foreign_keys: list[str] = []
#     for relationship in relationships:
#         fact_foreign_keys.extend(_extract_fact_keys_from_relationship(relationship, fact_table))

#     fact_tables = [
#         {
#             "name": fact_table,
#             "measures": _unique(fact_measures),
#             "foreign_keys": _unique(fact_foreign_keys),
#         }
#     ]

#     direct_dimensions, snowflake_dimensions = _classify_dimensions(
#         all_dimensions=semantic_dimensions,
#         fact_table=fact_table,
#         relationships=relationships,
#         direct_hints=set(),
#         snowflake_hints=set(),
#     )

#     model = _normalize_model(
#         {
#             "fact_tables": fact_tables,
#             "direct_dimensions": direct_dimensions,
#             "snowflake_dimensions": snowflake_dimensions,
#             "relationships": relationships,
#             "assumptions": [
#                 "Fact and dimension roles were inferred from table names, aggregate usage, and join paths.",
#             ],
#             "warnings": [],
#         }
#     )

#     _apply_follow_up_corrections(model, conversation)
#     return model


# def _extract_table_refs(sql_query: str) -> list[tuple[str, str]]:
#     pattern = re.compile(
#         r"\b(?:from|(?:(?:inner|left|right|full|cross)(?:\s+outer)?\s+)?join)\s+([A-Za-z0-9_\[\]\.]+)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
#         flags=re.IGNORECASE,
#     )
#     table_refs: list[tuple[str, str]] = []
#     for raw_table, raw_alias in pattern.findall(sql_query):
#         table = _clean_name(raw_table)
#         if not table:
#             continue
#         alias = raw_alias.strip() if raw_alias else table.split(".")[-1]
#         if (table, alias) not in table_refs:
#             table_refs.append((table, alias))
#     return table_refs


# def _extract_joins(sql_query: str) -> list[dict[str, str]]:
#     pattern = re.compile(
#         r"\b(?:(inner|left|right|full|cross)(?:\s+outer)?\s+)?join\s+([A-Za-z0-9_\[\]\.]+)"
#         r"(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?\s+on\s+"
#         r"(.*?)(?=\b(?:(?:inner|left|right|full|cross)(?:\s+outer)?\s+)?join\b|\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bqualify\b|\bunion\b|$)",
#         flags=re.IGNORECASE | re.DOTALL,
#     )
#     joins = []
#     for raw_join_type, raw_table, raw_alias, condition in pattern.findall(sql_query):
#         joins.append(
#             {
#                 "table": _clean_name(raw_table),
#                 "alias": raw_alias.strip() if raw_alias else _clean_name(raw_table).split(".")[-1],
#                 "join_type": (raw_join_type or "inner").strip().lower(),
#                 "condition": " ".join(condition.split()),
#             }
#         )
#     return joins


# def _extract_join_pairs(condition: str) -> list[dict[str, str]]:
#     pattern = re.compile(
#         r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
#         flags=re.IGNORECASE,
#     )
#     pairs: list[dict[str, str]] = []
#     for match in pattern.finditer(condition):
#         pairs.append(
#             {
#                 "left_alias": match.group(1),
#                 "left_column": _clean_name(match.group(2)),
#                 "right_alias": match.group(3),
#                 "right_column": _clean_name(match.group(4)),
#             }
#         )
#     return pairs


# def _build_relationship_from_join(
#     join: dict[str, str],
#     alias_to_table: dict[str, str],
#     fact_table: str,
#     group_by: list[str],
#     select_columns: list[dict[str, Any]],
# ) -> dict[str, str] | None:
#     condition = join.get("condition", "")
#     pairs = _extract_join_pairs(condition)
#     join_alias = join.get("alias", "")
#     join_type = join.get("join_type", "inner")

#     left_alias = ""
#     right_alias = ""
#     for pair in pairs:
#         if join_alias and join_alias.lower() in {pair["left_alias"].lower(), pair["right_alias"].lower()}:
#             if pair["left_alias"].lower() == join_alias.lower():
#                 left_alias, right_alias = pair["right_alias"], pair["left_alias"]
#             else:
#                 left_alias, right_alias = pair["left_alias"], pair["right_alias"]
#             break

#     if not left_alias or not right_alias:
#         if pairs:
#             left_alias, right_alias = pairs[0]["left_alias"], pairs[0]["right_alias"]
#         else:
#             left_alias, right_alias = _aliases_from_join(condition)

#     left_table = _resolve_alias_table(alias_to_table, left_alias)
#     right_table = _resolve_alias_table(alias_to_table, right_alias)
#     if not left_table or not right_table:
#         return None

#     from_table, to_table = left_table, right_table
#     relationship_type = "dimension_to_dimension"
#     if _same_name(left_table, fact_table) and not _same_name(right_table, fact_table):
#         relationship_type = "fact_to_dimension"
#     elif _same_name(right_table, fact_table) and not _same_name(left_table, fact_table):
#         relationship_type = "fact_to_dimension"

#     cardinality = _infer_join_cardinality(
#         from_table=from_table,
#         to_table=to_table,
#         left_alias=left_alias,
#         right_alias=right_alias,
#         join_pairs=pairs,
#         fact_table=fact_table,
#         join_type=join_type,
#         group_by=group_by,
#         select_columns=select_columns,
#     )

#     return {
#         "from_table": from_table,
#         "to_table": to_table,
#         "from_alias": left_alias,
#         "to_alias": right_alias,
#         "relationship_type": relationship_type,
#         "cardinality": cardinality,
#         "join_condition": condition,
#     }


# def _dimension_key_from_joins(
#     table_aliases: set[str],
#     joins: list[dict[str, str]],
#     alias_to_table: dict[str, str],
#     fact_table: str,
# ) -> str:
#     alias_keys = {alias.lower() for alias in table_aliases}
#     candidates: list[tuple[int, str]] = []
#     for join in joins:
#         for pair in _extract_join_pairs(join["condition"]):
#             if pair["left_alias"].lower() in alias_keys:
#                 other_table = _resolve_alias_table(alias_to_table, pair["right_alias"])
#                 priority = 0 if _same_name(other_table, fact_table) else 1
#                 candidates.append((priority, pair["left_column"]))
#             if pair["right_alias"].lower() in alias_keys:
#                 other_table = _resolve_alias_table(alias_to_table, pair["left_alias"])
#                 priority = 0 if _same_name(other_table, fact_table) else 1
#                 candidates.append((priority, pair["right_column"]))
#     if not candidates:
#         return ""
#     candidates.sort(key=lambda item: item[0])
#     return candidates[0][1]


# def _extract_select_columns(sql_query: str) -> list[dict[str, Any]]:
#     main_query = _extract_main_query(sql_query)
#     select_section = _extract_top_level_clause(main_query, "select", ["from"])
#     if not select_section:
#         return []

#     columns = []
#     for chunk in _split_sql_list(select_section):
#         expression = " ".join(chunk.split())
#         alias_match = re.search(r"\bas\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)$", expression, flags=re.IGNORECASE)
#         if not alias_match:
#             alias_match = re.search(r"\)\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)$", expression)
#         output_name = _clean_name(alias_match.group(1)) if alias_match else expression
#         column_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)", expression)
#         table_alias = column_match.group(1) if column_match else ""
#         is_measure = any(re.search(fr"\b{func}\s*\(", expression, flags=re.IGNORECASE) for func in AGG_FUNCS)
#         columns.append(
#             {
#                 "expression": expression,
#                 "output_name": output_name,
#                 "table_alias": table_alias,
#                 "is_measure": is_measure,
#             }
#         )
#     return columns


# def _extract_group_by(sql_query: str) -> list[str]:
#     main_query = _extract_main_query(sql_query)
#     group_section = _extract_top_level_clause(main_query, "group by", ["having", "order by", "qualify", "union"])
#     if not group_section:
#         return []
#     return [" ".join(chunk.split()) for chunk in _split_sql_list(group_section)]


# def _split_sql_list(text: str) -> list[str]:
#     items: list[str] = []
#     current: list[str] = []
#     depth = 0
#     for char in text:
#         if char == "(":
#             depth += 1
#         elif char == ")":
#             depth = max(0, depth - 1)
#         elif char == "," and depth == 0:
#             item = "".join(current).strip()
#             if item:
#                 items.append(item)
#             current = []
#             continue
#         current.append(char)
#     tail = "".join(current).strip()
#     if tail:
#         items.append(tail)
#     return items


# def _pick_fact_table(
#     tables: list[str],
#     select_columns: list[dict[str, Any]],
#     table_refs: list[tuple[str, str]],
#     joins: list[dict[str, str]],
# ) -> str:
#     if not tables:
#         return ""

#     alias_to_table = _build_alias_to_table_map(table_refs)
#     measure_tables = {
#         _resolve_alias_table(alias_to_table, column["table_alias"])
#         for column in select_columns
#         if column["is_measure"] and column["table_alias"]
#     }

#     adjacency: dict[str, set[str]] = {_name_key(table): set() for table in tables}
#     for join in joins:
#         pairs = _extract_join_pairs(join.get("condition", ""))
#         if pairs:
#             first_pair = pairs[0]
#             left_table = _resolve_alias_table(alias_to_table, first_pair["left_alias"])
#             right_table = _resolve_alias_table(alias_to_table, first_pair["right_alias"])
#         else:
#             left_alias, right_alias = _aliases_from_join(join.get("condition", ""))
#             left_table = _resolve_alias_table(alias_to_table, left_alias)
#             right_table = _resolve_alias_table(alias_to_table, right_alias)
#         if not left_table or not right_table:
#             continue
#         left_key = _name_key(left_table)
#         right_key = _name_key(right_table)
#         if not left_key or not right_key or left_key == right_key:
#             continue
#         adjacency.setdefault(left_key, set()).add(right_key)
#         adjacency.setdefault(right_key, set()).add(left_key)

#     best_score = None
#     best_table = tables[0]

#     for index, table in enumerate(tables):
#         score = 0
#         normalized = table.lower()
#         if any(hint in normalized for hint in FACT_HINTS):
#             score += 4
#         if any(hint in normalized for hint in DIM_HINTS):
#             score -= 3
#         if any(_same_name(table, measure_table) for measure_table in measure_tables if measure_table):
#             score += 3
#         score += len(adjacency.get(_name_key(table), set()))
#         if index == 0:
#             score += 1
#         if best_score is None or score > best_score:
#             best_score = score
#             best_table = table
#     return best_table


# def _sanitize_sql(sql_query: str) -> str:
#     no_block = re.sub(r"/\*.*?\*/", " ", sql_query or "", flags=re.DOTALL)
#     no_line = re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)
#     return " ".join(no_line.split())


# def _resolve_table_refs(
#     table_refs: list[tuple[str, str]],
#     cte_primary_tables: dict[str, str],
# ) -> list[tuple[str, str]]:
#     resolved: list[tuple[str, str]] = []
#     for table, alias in table_refs:
#         replacement = cte_primary_tables.get(_name_key(table), table)
#         candidate = (replacement, alias)
#         if candidate not in resolved:
#             resolved.append(candidate)
#     return resolved


# def _extract_cte_primary_tables(sql_query: str) -> dict[str, str]:
#     text = _sanitize_sql(sql_query)
#     if not re.match(r"^with\b", text, flags=re.IGNORECASE):
#         return {}

#     mapping: dict[str, str] = {}
#     index = len("with")
#     length = len(text)

#     while index < length:
#         index = _skip_whitespace(text, index)
#         cte_name, index = _read_sql_identifier(text, index)
#         if not cte_name:
#             break

#         index = _skip_whitespace(text, index)
#         if index < length and text[index] == "(":
#             _columns_block, index = _read_parenthesized_block(text, index)
#             index = _skip_whitespace(text, index)

#         as_match = re.match(r"as\b", text[index:], flags=re.IGNORECASE)
#         if not as_match:
#             break
#         index += len(as_match.group(0))
#         index = _skip_whitespace(text, index)

#         if index >= length or text[index] != "(":
#             break

#         cte_body, index = _read_parenthesized_block(text, index)
#         cte_refs = _extract_table_refs(cte_body)
#         cte_joins = _extract_joins(cte_body)
#         cte_select = _extract_select_columns(cte_body)
#         cte_tables = _unique([table for table, _alias in cte_refs])
#         if cte_tables:
#             mapping[_name_key(cte_name)] = _pick_fact_table(cte_tables, cte_select, cte_refs, cte_joins)

#         index = _skip_whitespace(text, index)
#         if index < length and text[index] == ",":
#             index += 1
#             continue
#         break

#     return mapping


# def _skip_whitespace(text: str, index: int) -> int:
#     while index < len(text) and text[index].isspace():
#         index += 1
#     return index


# def _read_sql_identifier(text: str, index: int) -> tuple[str, int]:
#     if index >= len(text):
#         return "", index

#     if text[index] == "[":
#         end = text.find("]", index + 1)
#         if end < 0:
#             return "", index
#         return _clean_name(text[index + 1 : end]), end + 1

#     if text[index] == '"':
#         end = text.find('"', index + 1)
#         if end < 0:
#             return "", index
#         return _clean_name(text[index + 1 : end]), end + 1

#     match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
#     if not match:
#         return "", index
#     return match.group(0), index + len(match.group(0))


# def _read_parenthesized_block(text: str, start_index: int) -> tuple[str, int]:
#     if start_index >= len(text) or text[start_index] != "(":
#         return "", start_index

#     depth = 0
#     in_single = False
#     in_double = False
#     in_bracket = False
#     index = start_index

#     while index < len(text):
#         char = text[index]

#         if in_single:
#             if char == "'":
#                 if index + 1 < len(text) and text[index + 1] == "'":
#                     index += 2
#                     continue
#                 in_single = False
#             index += 1
#             continue
#         if in_double:
#             if char == '"':
#                 in_double = False
#             index += 1
#             continue
#         if in_bracket:
#             if char == "]":
#                 in_bracket = False
#             index += 1
#             continue

#         if char == "'":
#             in_single = True
#             index += 1
#             continue
#         if char == '"':
#             in_double = True
#             index += 1
#             continue
#         if char == "[":
#             in_bracket = True
#             index += 1
#             continue

#         if char == "(":
#             depth += 1
#         elif char == ")":
#             depth -= 1
#             if depth == 0:
#                 return text[start_index + 1 : index], index + 1
#         index += 1

#     return text[start_index + 1 :], len(text)


# def _extract_main_query(sql_query: str) -> str:
#     cleaned = _sanitize_sql(sql_query)
#     start, _matched = _find_top_level_phrase(cleaned, ["select"])
#     if start < 0:
#         return cleaned
#     return cleaned[start:]


# def _extract_top_level_clause(sql_query: str, start_phrase: str, end_phrases: list[str]) -> str:
#     start, _matched = _find_top_level_phrase(sql_query, [start_phrase])
#     if start < 0:
#         return ""

#     content_start = start + len(start_phrase)
#     end, _end_match = _find_top_level_phrase(sql_query, end_phrases, start_index=content_start)
#     if end < 0:
#         end = len(sql_query)
#     return sql_query[content_start:end].strip()


# def _find_top_level_phrase(sql_query: str, phrases: list[str], start_index: int = 0) -> tuple[int, str]:
#     text = sql_query or ""
#     lowered = text.lower()
#     normalized_phrases = sorted({phrase.lower().strip() for phrase in phrases if phrase and phrase.strip()}, key=len, reverse=True)
#     if not normalized_phrases:
#         return -1, ""

#     depth = 0
#     in_single = False
#     in_double = False
#     in_bracket = False
#     index = max(0, start_index)

#     while index < len(lowered):
#         char = lowered[index]

#         if in_single:
#             if char == "'":
#                 if index + 1 < len(lowered) and lowered[index + 1] == "'":
#                     index += 2
#                     continue
#                 in_single = False
#             index += 1
#             continue
#         if in_double:
#             if char == '"':
#                 in_double = False
#             index += 1
#             continue
#         if in_bracket:
#             if char == "]":
#                 in_bracket = False
#             index += 1
#             continue

#         if char == "'":
#             in_single = True
#             index += 1
#             continue
#         if char == '"':
#             in_double = True
#             index += 1
#             continue
#         if char == "[":
#             in_bracket = True
#             index += 1
#             continue
#         if char == "(":
#             depth += 1
#             index += 1
#             continue
#         if char == ")":
#             depth = max(0, depth - 1)
#             index += 1
#             continue

#         if depth == 0:
#             for phrase in normalized_phrases:
#                 if _phrase_matches_at(lowered, index, phrase):
#                     return index, phrase

#         index += 1

#     return -1, ""


# def _phrase_matches_at(text: str, index: int, phrase: str) -> bool:
#     if not text.startswith(phrase, index):
#         return False

#     before = text[index - 1] if index > 0 else " "
#     after_index = index + len(phrase)
#     after = text[after_index] if after_index < len(text) else " "

#     if (before.isalnum() or before == "_") or (after.isalnum() or after == "_"):
#         return False
#     return True


# def _build_alias_to_table_map(table_refs: list[tuple[str, str]]) -> dict[str, str]:
#     alias_to_table: dict[str, str] = {}
#     for table, alias in table_refs:
#         if table and alias:
#             alias_to_table[alias] = table
#             alias_to_table[alias.lower()] = table
#         if table:
#             base = table.split(".")[-1]
#             alias_to_table.setdefault(base, table)
#             alias_to_table.setdefault(base.lower(), table)
#     return alias_to_table


# def _resolve_alias_table(alias_to_table: dict[str, str], alias: str) -> str:
#     value = alias_to_table.get(alias, "")
#     if value:
#         return value
#     return alias_to_table.get(alias.lower(), "")


# def _infer_join_cardinality(
#     from_table: str,
#     to_table: str,
#     left_alias: str,
#     right_alias: str,
#     join_pairs: list[dict[str, str]],
#     fact_table: str,
#     join_type: str,
#     group_by: list[str],
#     select_columns: list[dict[str, Any]],
# ) -> str:
#     del join_type

#     from_columns = _relationship_side_columns(left_alias, join_pairs)
#     to_columns = _relationship_side_columns(right_alias, join_pairs)

#     from_unique = _is_likely_unique_side(from_table, from_columns, fact_table, group_by, select_columns)
#     to_unique = _is_likely_unique_side(to_table, to_columns, fact_table, group_by, select_columns)

#     if from_unique and to_unique:
#         return "one-to-one"
#     if from_unique and not to_unique:
#         return "one-to-many"
#     if not from_unique and to_unique:
#         return "many-to-one"

#     if _same_name(from_table, fact_table) and not _same_name(to_table, fact_table):
#         return "many-to-one"
#     if _same_name(to_table, fact_table) and not _same_name(from_table, fact_table):
#         return "one-to-many"
#     return "many-to-many"


# def _relationship_side_columns(alias: str, join_pairs: list[dict[str, str]]) -> list[str]:
#     if not alias:
#         return []
#     alias_key = alias.lower()
#     columns: list[str] = []
#     for pair in join_pairs:
#         if pair["left_alias"].lower() == alias_key:
#             columns.append(pair["left_column"])
#         if pair["right_alias"].lower() == alias_key:
#             columns.append(pair["right_column"])
#     return _unique(columns)


# def _is_likely_unique_side(
#     table_name: str,
#     join_columns: list[str],
#     fact_table: str,
#     group_by: list[str],
#     select_columns: list[dict[str, Any]],
# ) -> bool:
#     if not join_columns:
#         return False

#     score = 0
#     table_key = _name_key(table_name)
#     semantic_table_key = _semantic_name_key(table_name)
#     is_fact_side = _same_name(table_name, fact_table)

#     if any(hint in table_key for hint in DIM_HINTS):
#         score += 1
#     if any(hint in table_key for hint in FACT_HINTS):
#         score -= 1
#     if is_fact_side:
#         score -= 2

#     grouped_columns = {_group_column_name(expression) for expression in group_by}
#     grouped_columns.discard("")

#     for column in join_columns:
#         col_key = _name_key(column)
#         if _looks_like_key_column(col_key):
#             entity_key = _column_entity_key(col_key)
#             if entity_key and semantic_table_key and entity_key != semantic_table_key:
#                 score -= 1
#             else:
#                 score += 2
#         if any(hint in col_key for hint in NON_KEY_COLUMN_HINTS):
#             score -= 1
#         if not is_fact_side and col_key in grouped_columns:
#             score += 1

#     selected_non_measure = {
#         _name_key(column.get("output_name", ""))
#         for column in select_columns
#         if isinstance(column, dict) and not column.get("is_measure")
#     }
#     if not is_fact_side and any(_name_key(column) in selected_non_measure for column in join_columns):
#         score += 1

#     return score >= 2


# def _group_column_name(expression: str) -> str:
#     cleaned = " ".join((expression or "").split())
#     match = re.match(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z0-9_\[\]]+)$", cleaned)
#     if not match:
#         return ""
#     return _name_key(match.group(1))


# def _aliases_from_join(condition: str) -> tuple[str, str]:
#     match = re.search(
#         r"([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z0-9_\[\]]+\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z0-9_\[\]]+",
#         condition,
#         flags=re.IGNORECASE,
#     )
#     if not match:
#         return "", ""
#     return match.group(1), match.group(2)


# def _fact_key_from_join(condition: str, table_refs: list[tuple[str, str]], fact_table: str) -> str:
#     alias_to_table = {alias: table for table, alias in table_refs}
#     match = re.search(
#         r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
#         condition,
#         flags=re.IGNORECASE,
#     )
#     if not match:
#         return ""
#     if alias_to_table.get(match.group(1), "") == fact_table:
#         return _clean_name(match.group(2))
#     if alias_to_table.get(match.group(3), "") == fact_table:
#         return _clean_name(match.group(4))
#     return ""


# def _dimension_key_from_join(condition: str, aliases: set[str]) -> str:
#     match = re.search(
#         r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
#         condition,
#         flags=re.IGNORECASE,
#     )
#     if not match:
#         return ""
#     if match.group(1) in aliases:
#         return _clean_name(match.group(2))
#     if match.group(3) in aliases:
#         return _clean_name(match.group(4))
#     return ""


# def _apply_follow_up_corrections(model: dict[str, Any], conversation: list[dict[str, Any]]) -> None:
#     user_messages = [item["content"] for item in conversation if item.get("role") == "user" and item.get("content")]
#     if not user_messages:
#         return

#     merged = "\n".join(user_messages)
#     lower = merged.lower()
#     forced_model_type: str | None = None

#     for model_type in ("star", "snowflake", "hybrid"):
#         if re.search(fr"\b(it is|it's|should be|schema is)\s+{model_type}\b", lower):
#             forced_model_type = model_type.title()
#             break

#     for relation_match in re.finditer(
#         r"([A-Za-z0-9_\.\[\]]+)\s+(?:to|->)\s+([A-Za-z0-9_\.\[\]]+).*?\b(one-to-one|one-to-many|many-to-one|many-to-many)\b",
#         lower,
#         flags=re.DOTALL,
#     ):
#         left_name, right_name, cardinality = relation_match.groups()
#         _apply_relationship_correction(
#             model=model,
#             left_name=left_name,
#             right_name=right_name,
#             cardinality=cardinality,
#             relationship_type="",
#         )

#     for relation_type_match in re.finditer(
#         r"([A-Za-z0-9_\.\[\]]+)\s+(?:to|->)\s+([A-Za-z0-9_\.\[\]]+).*?\b(fact[-_\s]?to[-_\s]?dimension|dimension[-_\s]?to[-_\s]?dimension)\b",
#         lower,
#         flags=re.DOTALL,
#     ):
#         left_name, right_name, relation_type = relation_type_match.groups()
#         normalized_relation_type = _normalize_relationship_type(relation_type.replace(" ", "_").replace("-", "_"))
#         if normalized_relation_type:
#             _apply_relationship_correction(
#                 model=model,
#                 left_name=left_name,
#                 right_name=right_name,
#                 cardinality="",
#                 relationship_type=normalized_relation_type,
#             )

#     fact_match = re.search(
#         r"\bfact table\b.*?\b(?:is|should be)\s+([A-Za-z0-9_\.\[\]]+)",
#         merged,
#         flags=re.IGNORECASE | re.DOTALL,
#     )
#     if fact_match:
#         fact_name = _resolve_table_name(model, fact_match.group(1)) or fact_match.group(1).strip()
#         if model.get("fact_tables"):
#             model["fact_tables"][0]["name"] = fact_name
#         else:
#             model["fact_tables"] = [{"name": fact_name, "measures": [], "foreign_keys": []}]
#         model["direct_dimensions"] = [
#             dimension
#             for dimension in _normalize_dimension_list(model.get("direct_dimensions", []))
#             if not _same_name(dimension["name"], fact_name)
#         ]
#         model["snowflake_dimensions"] = [
#             dimension
#             for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", []))
#             if not _same_name(dimension["name"], fact_name)
#         ]

#     for classification_match in re.finditer(
#         r"([A-Za-z0-9_\.\[\]]+)\s+(?:is|should be)\s+(?:a\s+)?(direct|snowflake)\s+dimension",
#         merged,
#         flags=re.IGNORECASE,
#     ):
#         table_name, target_group = classification_match.groups()
#         _move_dimension_between_groups(model, table_name, target_group.lower())

#     fk_match = re.search(
#         r"\bforeign keys?\b(?:\s+for\s+([A-Za-z0-9_\.\[\]]+))?.*?\b(?:are|is|should be)\s+([A-Za-z0-9_\.\[\],\s]+)",
#         merged,
#         flags=re.IGNORECASE | re.DOTALL,
#     )
#     if fk_match and model.get("fact_tables"):
#         target_table, key_list = fk_match.groups()
#         fact_name = model["fact_tables"][0]["name"]
#         if not target_table or _same_name(target_table, fact_name):
#             keys = [
#                 _clean_name(token.strip())
#                 for token in re.split(r",| and ", key_list, flags=re.IGNORECASE)
#                 if token.strip()
#             ]
#             if keys:
#                 model["fact_tables"][0]["foreign_keys"] = _unique(keys)

#     _refresh_model_output(model, forced_model_type=forced_model_type)


# def _resolve_table_name(model: dict[str, Any], candidate: str) -> str:
#     lookup_name = candidate.strip()
#     if not lookup_name:
#         return ""
#     known_names: list[str] = []
#     dimension_alias_map: dict[str, str] = {}
#     for fact in model.get("fact_tables", []):
#         if isinstance(fact, dict) and fact.get("name"):
#             known_names.append(str(fact["name"]))
#     for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
#         model.get("snowflake_dimensions", [])
#     ):
#         known_names.append(dimension["name"])
#         physical_table = str(dimension.get("physical_table", "")).strip()
#         alias = str(dimension.get("alias", "")).strip()
#         if physical_table:
#             known_names.append(physical_table)
#         if alias:
#             dimension_alias_map[alias.lower()] = dimension["name"]
#     for relationship in model.get("relationships", []):
#         if isinstance(relationship, dict):
#             known_names.extend(
#                 [
#                     str(relationship.get("from_table", "")),
#                     str(relationship.get("to_table", "")),
#                 ]
#             )
#             from_alias = str(relationship.get("from_alias", "")).strip()
#             to_alias = str(relationship.get("to_alias", "")).strip()
#             if from_alias:
#                 dimension_alias_map.setdefault(from_alias.lower(), str(relationship.get("from_table", "")))
#             if to_alias:
#                 dimension_alias_map.setdefault(to_alias.lower(), str(relationship.get("to_table", "")))
#     if lookup_name.lower() in dimension_alias_map:
#         return dimension_alias_map[lookup_name.lower()]
#     for name in known_names:
#         if _same_name(name, lookup_name):
#             return name
#     return ""


# def _apply_relationship_correction(
#     model: dict[str, Any],
#     left_name: str,
#     right_name: str,
#     cardinality: str,
#     relationship_type: str,
# ) -> None:
#     relationships = model.get("relationships", [])
#     if not isinstance(relationships, list):
#         relationships = []
#         model["relationships"] = relationships

#     matched = False
#     for relationship in relationships:
#         if not isinstance(relationship, dict):
#             continue
#         direct_match = _same_name(str(relationship.get("from_table", "")), left_name) and _same_name(
#             str(relationship.get("to_table", "")), right_name
#         )
#         reverse_match = _same_name(str(relationship.get("from_table", "")), right_name) and _same_name(
#             str(relationship.get("to_table", "")), left_name
#         )
#         if not direct_match and not reverse_match:
#             continue
#         if cardinality:
#             relationship["cardinality"] = cardinality if direct_match else _reverse_cardinality(cardinality)
#         if relationship_type:
#             relationship["relationship_type"] = relationship_type
#         matched = True
#         break

#     if matched:
#         return

#     resolved_left = _resolve_table_name(model, left_name) or _clean_name(left_name)
#     resolved_right = _resolve_table_name(model, right_name) or _clean_name(right_name)
#     fact_keys = {_name_key(fact.get("name", "")) for fact in model.get("fact_tables", []) if isinstance(fact, dict)}
#     inferred_type = relationship_type or _infer_relationship_type(resolved_left, resolved_right, fact_keys)
#     model["relationships"].append(
#         {
#             "from_table": resolved_left,
#             "to_table": resolved_right,
#             "relationship_type": inferred_type,
#             "cardinality": cardinality,
#             "join_condition": "",
#         }
#     )


# def _move_dimension_between_groups(model: dict[str, Any], table_name: str, target_group: str) -> None:
#     resolved_name = _resolve_table_name(model, table_name) or _clean_name(table_name)
#     direct = _normalize_dimension_list(model.get("direct_dimensions", []))
#     snowflake = _normalize_dimension_list(model.get("snowflake_dimensions", []))

#     existing = None
#     for dimension in direct + snowflake:
#         if _same_name(dimension["name"], resolved_name):
#             existing = dimension
#             break
#     if existing is None:
#         existing = {
#             "name": resolved_name,
#             "physical_table": resolved_name,
#             "alias": "",
#             "semantic_role": "",
#             "attributes": [],
#             "natural_key": "",
#         }

#     direct = [dimension for dimension in direct if not _same_name(dimension["name"], resolved_name)]
#     snowflake = [dimension for dimension in snowflake if not _same_name(dimension["name"], resolved_name)]

#     if target_group == "snowflake":
#         snowflake.append(existing)
#     else:
#         direct.append(existing)

#     model["direct_dimensions"] = _merge_dimensions(direct)
#     model["snowflake_dimensions"] = _merge_dimensions(snowflake)


# def _reverse_cardinality(cardinality: str) -> str:
#     lowered = cardinality.strip().lower()
#     if lowered == "one-to-many":
#         return "many-to-one"
#     if lowered == "many-to-one":
#         return "one-to-many"
#     return lowered


# def _render_structured_result(model: dict[str, Any]) -> None:
#     if not model:
#         return

#     fact_tables = [fact for fact in model.get("fact_tables", []) if isinstance(fact, dict)]
#     primary_fact = str(fact_tables[0].get("name", "")).strip() if fact_tables else ""
#     direct_dimensions = _normalize_dimension_list(model.get("direct_dimensions", []))
#     snowflake_dimensions = _normalize_dimension_list(model.get("snowflake_dimensions", []))
#     relationships = [item for item in model.get("relationships", []) if isinstance(item, dict)]
#     snowflake_origins = _snowflake_origin_lookup(
#         fact_table=primary_fact,
#         direct_dimensions=direct_dimensions,
#         snowflake_dimensions=snowflake_dimensions,
#         relationships=relationships,
#     )

#     model_col, confidence_col, dimensions_col, relationships_col = st.columns(4)
#     with model_col:
#         st.metric("Model Type", str(model.get("model_type", "Unknown")))
#     with confidence_col:
#         confidence = str(model.get("schema_confidence", "low") or "low").strip().lower()
#         st.metric("Confidence", confidence.title())
#     with dimensions_col:
#         st.metric("Dimensions", str(len(direct_dimensions) + len(snowflake_dimensions)))
#     with relationships_col:
#         st.metric("Relationships", str(len(relationships)))

#     if model.get("model_summary"):
#         st.info(str(model["model_summary"]))

#     facts_tab, dimensions_tab, relationships_tab, notes_tab = st.tabs(
#         ["Fact Tables", "Dimensions", "Relationships", "Review"]
#     )

#     with facts_tab:
#         if not fact_tables:
#             st.write("No fact tables identified.")
#         else:
#             st.dataframe(
#                 [
#                     {
#                         "Table": fact.get("name", ""),
#                         "Measures": len(_as_string_list(fact.get("measures", []))),
#                         "Foreign Keys": len(_as_string_list(fact.get("foreign_keys", []))),
#                     }
#                     for fact in fact_tables
#                 ],
#                 use_container_width=True,
#                 hide_index=True,
#             )
#             for fact in fact_tables:
#                 fact_name = str(fact.get("name", "")).strip() or "Fact Table"
#                 with st.expander(f"{fact_name} details", expanded=False):
#                     st.markdown("**Measures**")
#                     _render_text_list(_as_string_list(fact.get("measures", [])), "No measures identified.")
#                     st.markdown("**Foreign keys**")
#                     _render_text_list(_as_string_list(fact.get("foreign_keys", [])), "No foreign keys identified.")

#     with dimensions_tab:
#         direct_col, snowflake_col = st.columns(2)
#         with direct_col:
#             st.markdown("#### Direct Dimensions")
#             if direct_dimensions:
#                 st.dataframe(
#                     [
#                         {
#                             "Dimension": dimension["name"],
#                             "Physical Table": dimension.get("physical_table", "") or dimension["name"],
#                             "Alias": dimension.get("alias", "") or "-",
#                             "Semantic Role": dimension.get("semantic_role", "") or "-",
#                             "Natural Key": dimension.get("natural_key", "") or "-",
#                             "Attributes": len(_as_string_list(dimension.get("attributes", []))),
#                         }
#                         for dimension in direct_dimensions
#                     ],
#                     use_container_width=True,
#                     hide_index=True,
#                 )
#             else:
#                 st.write("No direct dimensions identified.")
#         with snowflake_col:
#             st.markdown("#### Snowflake Dimensions")
#             if snowflake_dimensions:
#                 st.dataframe(
#                     [
#                         {
#                             "Dimension": dimension["name"],
#                             "Physical Table": dimension.get("physical_table", "") or dimension["name"],
#                             "Alias": dimension.get("alias", "") or "-",
#                             "Semantic Role": dimension.get("semantic_role", "") or "-",
#                             "Origin (Star)": snowflake_origins.get(_name_key(dimension["name"]), "") or "-",
#                             "Natural Key": dimension.get("natural_key", "") or "-",
#                             "Attributes": len(_as_string_list(dimension.get("attributes", []))),
#                         }
#                         for dimension in snowflake_dimensions
#                     ],
#                     use_container_width=True,
#                     hide_index=True,
#                 )
#             else:
#                 st.write("No snowflake dimensions identified.")

#         all_dimensions = [("Direct", dimension) for dimension in direct_dimensions] + [
#             ("Snowflake", dimension) for dimension in snowflake_dimensions
#         ]
#         if all_dimensions:
#             with st.expander("Dimension attributes", expanded=False):
#                 for dimension_type, dimension in all_dimensions:
#                     st.markdown(f"**{dimension['name']}** ({dimension_type})")
#                     st.caption(
#                         "Physical table: "
#                         f"{dimension.get('physical_table', '') or dimension['name']} | "
#                         f"Alias: {dimension.get('alias', '') or '-'} | "
#                         f"Role: {dimension.get('semantic_role', '') or '-'}"
#                     )
#                     if dimension_type == "Snowflake":
#                         origin = snowflake_origins.get(_name_key(dimension["name"]), "")
#                         st.caption(f"Branch origin table (star schema): {origin or 'Unknown'}")
#                     _render_text_list(
#                         _as_string_list(dimension.get("attributes", [])),
#                         "No attributes identified.",
#                     )

#     with relationships_tab:
#         if not relationships:
#             st.write("No relationships identified.")
#         else:
#             st.dataframe(
#                 [
#                     {
#                         "From": relationship.get("from_table", ""),
#                         "From Alias": relationship.get("from_alias", "") or "-",
#                         "To": relationship.get("to_table", ""),
#                         "To Alias": relationship.get("to_alias", "") or "-",
#                         "Type": relationship.get("relationship_type", "") or "unknown",
#                         "Cardinality": relationship.get("cardinality", "") or "unknown",
#                     }
#                     for relationship in relationships
#                 ],
#                 use_container_width=True,
#                 hide_index=True,
#             )
#             with st.expander("Join conditions", expanded=False):
#                 for relationship in relationships:
#                     label = f"{relationship.get('from_table', '')} -> {relationship.get('to_table', '')}"
#                     condition = str(relationship.get("join_condition", "")).strip()
#                     if condition:
#                         st.markdown(f"**{label}**")
#                         st.code(condition, language="sql")
#                     else:
#                         st.caption(f"{label}: no SQL join predicate captured.")

#     with notes_tab:
#         review_col, assumptions_col, warnings_col = st.columns(3)
#         with review_col:
#             st.markdown("#### Review Notes")
#             _render_text_list(_as_string_list(model.get("review_notes", [])), "None")
#         with assumptions_col:
#             st.markdown("#### Assumptions")
#             _render_text_list(_as_string_list(model.get("assumptions", [])), "None")
#         with warnings_col:
#             st.markdown("#### Warnings")
#             _render_text_list(_as_string_list(model.get("warnings", [])), "None")


# def _render_text_list(items: list[str], empty_message: str) -> None:
#     if not items:
#         st.caption(empty_message)
#         return
#     for item in items:
#         st.write(f"- {item}")


# def _as_string_list(value: Any) -> list[str]:
#     if not isinstance(value, list):
#         return []
#     return [str(item) for item in value if str(item).strip()]


# def _looks_like_key_column(column_name: str) -> bool:
#     key = _name_key(column_name)
#     if not key:
#         return False
#     if key in {"id", "key"}:
#         return True
#     return any(key.endswith(suffix) for suffix in UNIQUE_KEY_SUFFIX_HINTS)


# def _column_entity_key(column_name: str) -> str:
#     key = _name_key(column_name)
#     if not key:
#         return ""
#     for suffix in sorted(UNIQUE_KEY_SUFFIX_HINTS, key=len, reverse=True):
#         if not key.endswith(suffix):
#             continue
#         prefix = key[: -len(suffix)].rstrip("_")
#         if prefix:
#             return prefix
#     return ""


# def _clean_name(value: str) -> str:
#     return value.replace("[", "").replace("]", "").strip()


# def _name_key(value: str) -> str:
#     cleaned = _clean_name(value).lower()
#     return cleaned.split(".")[-1] if cleaned else ""


# def _semantic_name_key(value: str) -> str:
#     return re.sub(r"^(dim|fact|fct)_?", "", _name_key(value))


# def _same_name(left: str, right: str) -> bool:
#     left_key = _name_key(left)
#     right_key = _name_key(right)
#     if not left_key or not right_key:
#         return False
#     if left_key == right_key:
#         return True
#     return _semantic_name_key(left) == _semantic_name_key(right)


# def _unique(values: list[str]) -> list[str]:
#     seen: set[str] = set()
#     output: list[str] = []
#     for value in values:
#         if not value or value in seen:
#             continue
#         seen.add(value)
#         output.append(value)
#     return output


# if __name__ == "__main__":
#     main()


from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.parse import urlparse
import uuid
import xml.etree.ElementTree as ET
import zipfile

import streamlit as st

from src.schema_flow_component import render_schema_flow
from src.rdl_ai_editor.nlp_agent import load_llm_from_config
from src.rdl_to_twb.db_introspection import build_db_catalog, inspect_sqlserver_datasource_inventory
from src.rdl_to_twb.pipeline import run_conversion
from src.rdl_to_twb.rdl_parser import parse_rdl_file
from src.rdl_to_twb.tableau_extract import (
    build_hyper_extract_from_catalog,
    build_twbx_package,
    rewrite_workbook_for_hyper,
)
from src.rdl_to_twb.twb_builder import inject_datasource_connections


ROOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT_DIR.parent
ASSETS_DIR = ROOT_DIR / "assets"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
RDL_TO_TWB_OUTPUT_DIR = OUTPUT_DIR / "rdl_to_twb"
RDL_TO_TWB_INPUT_DIR_NAME = "00_input_report"
RDL_TO_TWB_VISUAL_MAPPING_DIR_NAME = "03_visual_mapping"
RDL_TO_TWB_VISUAL_MAPPING_TWB_NAME = "visual_content_mapped.twb"
SQL_ASSISTANT_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.tableau_cloud_sql_assistant.json"
DEFAULT_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.json"
FALLBACK_LLM_CONFIG = ROOT_DIR / "config" / "llm_config.example.json"
OUTPUT_TEMPLATE_PATH = OUTPUT_DIR / "template_semantic_model.twb"
OUTPUT_TEMPLATE_COPY_PATH = OUTPUT_DIR / "template_semantic_model - Copy.twb"
DEFAULT_TEMPLATE_PATH = ASSETS_DIR / "Semantic_model.twb"
FALLBACK_TEMPLATE_PATH = Path.home() / "Desktop" / "Semantic_model.twb"
TABLEAU_DATASOURCE_PROJECT_NAME = "published_datasources"
TABLEAU_WORKBOOK_PROJECT_NAME = "published_reports"
FLUX_PIPELINE_EXECUTOR = ThreadPoolExecutor(max_workers=4)

SYSTEM_PROMPT = """You are SQL Model Assistant.
You read a SQL query and the conversation history, then return the latest dimensional model.
Output JSON only.

Return this shape:
{
  "assistant_response": "natural conversational reply to the user's latest message",
  "model": {
    "model_type": "Star | Snowflake | Hybrid | Unknown",
    "schema_confidence": "high | medium | low",
    "model_summary": "short plain-language summary",
    "fact_tables": [
      {
        "name": "string",
        "measures": ["string"],
        "foreign_keys": ["string"]
      }
    ],
    "direct_dimensions": [
      {
        "name": "string",
        "physical_table": "string",
        "alias": "string",
        "semantic_role": "string",
        "attributes": ["string"],
        "natural_key": "string"
      }
    ],
    "snowflake_dimensions": [
      {
        "name": "string",
        "physical_table": "string",
        "alias": "string",
        "semantic_role": "string",
        "attributes": ["string"],
        "natural_key": "string"
      }
    ],
    "relationships": [
      {
        "from_table": "string",
        "to_table": "string",
        "relationship_type": "fact_to_dimension | dimension_to_dimension",
        "cardinality": "string",
        "join_condition": "string"
      }
    ],
    "review_notes": ["string"],
    "assumptions": ["string"],
    "warnings": ["string"]
  }
}

Rules:
- Apply the latest user correction when possible.
- Return the full current model, not only the delta.
- Make assistant_response feel like a normal chat reply, not a machine dump.
- When the user asks an informational question, answer it directly in assistant_response.
- When the user asks for schema changes, summarize the change in assistant_response.
- When datasource context is provided, use it to answer questions about the current database and available tables.
- Do not invent physical tables outside the SQL evidence or datasource context.
- Preserve real SQL join paths in join_condition.
- Use fact-side column names for fact foreign keys.
- Distinguish fact-to-dimension and dimension-to-dimension relationships.
- Keep direct_dimensions separate from snowflake_dimensions.
- Keep role-playing dimensions as distinct semantic instances (same physical table, different alias/role).
- Use empty strings or empty lists instead of null.
"""

FACT_HINTS = (
    "fact",
    "fct",
    "sales",
    "order",
    "orders",
    "transaction",
    "invoice",
    "payment",
    "event",
    "line",
)
DIM_HINTS = (
    "dim",
    "date",
    "calendar",
    "customer",
    "product",
    "region",
    "store",
    "location",
    "employee",
    "lookup",
    "reference",
)
AGG_FUNCS = ("sum", "count", "avg", "min", "max")
UNIQUE_KEY_SUFFIX_HINTS = ("id", "key", "code", "number", "num")
NON_KEY_COLUMN_HINTS = (
    "name",
    "description",
    "label",
    "comment",
    "amount",
    "total",
    "qty",
    "quantity",
    "price",
    "cost",
    "revenue",
    "sales",
    "metric",
    "value",
)
TABLEAU_TRANSIENT_RETRY_DELAYS_SECONDS = (15, 45)
TABLEAU_RECOVERY_LOOKUP_DELAYS_SECONDS = (0, 10)


def _inject_ui_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.15rem;
            padding-bottom: 2rem;
            max-width: 1480px;
        }
        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1rem;
        }
        h1, h2, h3 {
            letter-spacing: 0 !important;
        }
        .sqlma-hero {
            padding: 1rem 1.15rem 1.05rem;
            border: 1px solid rgba(120, 120, 140, 0.18);
            border-radius: 16px;
            background: linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.86));
            margin-bottom: 1rem;
        }
        .sqlma-kicker {
            font-size: 0.73rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #6b7280;
            margin-bottom: 0.25rem;
        }
        .sqlma-hero h1 {
            font-size: 1.72rem;
            line-height: 1.2;
            margin: 0 0 0.35rem 0;
        }
        .sqlma-hero p {
            margin: 0;
            color: rgba(55, 65, 81, 0.9);
            font-size: 0.98rem;
        }
        .sqlma-section-label {
            font-size: 0.8rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #6b7280;
            margin: 0.15rem 0 0.55rem 0;
        }
        .sqlma-step {
            display: flex;
            gap: 0.55rem;
            align-items: flex-start;
            padding: 0.55rem 0.65rem;
            margin: 0.35rem 0;
            border: 1px solid rgba(120, 120, 140, 0.14);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.72);
        }
        .sqlma-step-state {
            font-size: 0.72rem;
            font-weight: 700;
            border-radius: 999px;
            padding: 0.18rem 0.45rem;
            white-space: nowrap;
            line-height: 1.2;
        }
        .sqlma-step-state.done {
            background: #e8f7ee;
            color: #166534;
        }
        .sqlma-step-state.pending {
            background: #f3f4f6;
            color: #4b5563;
        }
        .sqlma-step-state.error {
            background: #fef2f2;
            color: #b91c1c;
        }
        .sqlma-step-title {
            font-weight: 600;
            line-height: 1.2;
        }
        .sqlma-step-detail {
            font-size: 0.78rem;
            color: #6b7280;
            margin-top: 0.14rem;
            word-break: break-word;
        }
        .sqlma-callout {
            padding: 0.75rem 0.9rem;
            border: 1px solid rgba(120, 120, 140, 0.14);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.66);
            margin: 0.35rem 0 0.8rem 0;
        }
        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.76);
            border: 1px solid rgba(120, 120, 140, 0.14);
            padding: 0.72rem 0.8rem;
            border-radius: 14px;
        }
        div[data-testid="stExpander"] {
            border: 1px solid rgba(120, 120, 140, 0.14);
            border-radius: 14px;
            overflow: hidden;
        }
        .stButton > button,
        .stDownloadButton > button {
            border-radius: 10px;
            font-weight: 600;
        }
        div[data-testid="stChatMessage"] {
            padding-top: 0.25rem;
            padding-bottom: 0.25rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_page_header() -> None:
    st.markdown(
        """
        <div class="sqlma-hero">
          <div class="sqlma-kicker">SQL Model Assistant</div>
          <h1>Chat with your schema</h1>
          <p>Ask questions, review relationships, and move from RDL to a publishable workbook without the clutter.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="SQL Model Assistant", page_icon=":mag:", layout="wide")
    _inject_ui_styles()
    _render_page_header()

    _init_state()
    parallel_flux_state = _poll_parallel_fluxes()

    with st.sidebar:
        _render_pipeline_status_sidebar()
        with st.expander("Workspace", expanded=False):
            st.session_state.sql_model_assistant_llm_config = st.text_input(
                "LLM config path",
                value=st.session_state.sql_model_assistant_llm_config,
            )
        _sync_tableau_settings_from_config_if_needed()
        with st.expander("Publish settings", expanded=False):
            _render_tableau_publish_settings()
        if st.button("Reset chat", use_container_width=True):
            _reset_state()
            st.rerun()

    if st.session_state.sql_model_assistant_sql_query:
        with st.expander("SQL preview", expanded=False):
            st.code(st.session_state.sql_model_assistant_sql_query, language="sql")
    else:
        _render_rdl_intake()

    _render_chat_transcript()
    _render_parallel_flux_progress(parallel_flux_state)
    _render_flux1_results()

    if st.session_state.sql_model_assistant_sql_query:
        latest_model = _latest_assistant_model()
        if latest_model:
            _render_validation_and_twb_tools(latest_model)

        follow_up = st.chat_input(
            "Ask about the model or request a schema change..."
        )
        if follow_up:
            _handle_follow_up(follow_up.strip())
            st.rerun()

    _render_consumer_workbook_results()

    if parallel_flux_state.get("flux2_pending", False):
        time.sleep(0.75)
        st.rerun()


def _init_state() -> None:
    st.session_state.setdefault("sql_model_assistant_rdl_payload", b"")
    st.session_state.setdefault("sql_model_assistant_pending_sql", "")
    st.session_state.setdefault("sql_model_assistant_sql_query", "")
    st.session_state.setdefault("sql_model_assistant_messages", [])
    st.session_state.setdefault("sql_model_assistant_rdl_report", {})
    st.session_state.setdefault("sql_model_assistant_rdl_filename", "")
    st.session_state.setdefault("sql_model_assistant_selected_dataset_name", "")
    st.session_state.setdefault("sql_model_assistant_selected_datasource_name", "")
    st.session_state.setdefault("sql_model_assistant_schema_validated", False)
    st.session_state.setdefault("sql_model_assistant_validated_model", {})
    st.session_state.setdefault("sql_model_assistant_generated_twb", "")
    st.session_state.setdefault("sql_model_assistant_generated_twb_name", "validated_semantic_model.twb")
    st.session_state.setdefault("sql_model_assistant_flux1_visual_workbook_path", "")
    st.session_state.setdefault("sql_model_assistant_flux1_output_dir", "")
    st.session_state.setdefault("sql_model_assistant_flux1_error", "")
    st.session_state.setdefault("sql_model_assistant_consumer_workbook_path", "")
    st.session_state.setdefault("sql_model_assistant_parallel_flux1_future", None)
    st.session_state.setdefault("sql_model_assistant_parallel_flux2_future", None)
    st.session_state.setdefault("sql_model_assistant_parallel_query", "")
    preferred_template_path = _template_default_path()
    st.session_state.setdefault("sql_model_assistant_template_path", preferred_template_path)
    current_template_path = str(st.session_state.sql_model_assistant_template_path or "").strip()
    legacy_output_template_key = _template_path_key(OUTPUT_TEMPLATE_PATH)
    current_template_key = _template_path_key(current_template_path)
    preferred_template_key = _template_path_key(preferred_template_path)
    if preferred_template_key and current_template_key in {"", legacy_output_template_key}:
        st.session_state.sql_model_assistant_template_path = preferred_template_path
    st.session_state.setdefault(
        "sql_model_assistant_llm_config",
        str(_preferred_sql_assistant_llm_config_path()),
    )
    # Upgrade old sessions that still point to the generic config.
    current_cfg_value = str(st.session_state.sql_model_assistant_llm_config or "").strip()
    generic_default_value = str(DEFAULT_LLM_CONFIG)
    generic_default_resolved = str(DEFAULT_LLM_CONFIG.resolve()) if DEFAULT_LLM_CONFIG.exists() else generic_default_value
    if SQL_ASSISTANT_LLM_CONFIG.exists() and current_cfg_value in {generic_default_value, generic_default_resolved}:
        st.session_state.sql_model_assistant_llm_config = str(SQL_ASSISTANT_LLM_CONFIG)

    tableau_defaults = _load_tableau_publish_defaults(st.session_state.sql_model_assistant_llm_config)
    st.session_state.setdefault("sql_model_assistant_tableau_auto_publish", True)
    st.session_state.setdefault(
        "sql_model_assistant_tableau_server_url",
        str(
            tableau_defaults.get("server_url")
            or os.getenv("TABLEAU_SERVER_URL")
            or ""
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_site_content_url",
        str(
            tableau_defaults.get("site_content_url")
            or os.getenv("TABLEAU_SITE_CONTENT_URL")
            or ""
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_project_name",
        str(
            tableau_defaults.get("project_name")
            or os.getenv("TABLEAU_PROJECT_NAME")
            or "Default"
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_datasource_project_name",
        str(
            tableau_defaults.get("datasource_project_name")
            or os.getenv("TABLEAU_DATASOURCE_PROJECT_NAME")
            or TABLEAU_DATASOURCE_PROJECT_NAME
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_workbook_project_name",
        str(
            tableau_defaults.get("workbook_project_name")
            or os.getenv("TABLEAU_WORKBOOK_PROJECT_NAME")
            or TABLEAU_WORKBOOK_PROJECT_NAME
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_username",
        str(
            tableau_defaults.get("username")
            or os.getenv("TABLEAU_USERNAME")
            or ""
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_password",
        str(
            tableau_defaults.get("password")
            or os.getenv("TABLEAU_PASSWORD")
            or ""
        ),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_source_datasource_name",
        str(tableau_defaults.get("source_datasource_name") or ""),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_empty_workbook_template_path",
        str(tableau_defaults.get("empty_workbook_template_path") or ""),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_visual_source_twb_path",
        str(tableau_defaults.get("visual_source_twb_path") or ""),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_loaded_config_path",
        "",
    )
    st.session_state.setdefault("sql_model_assistant_tableau_visual_source_twb_path_pending_sync", False)
    st.session_state.setdefault("sql_model_assistant_tableau_last_publish_report", {})
    st.session_state.setdefault("sql_model_assistant_tableau_last_publish_error", "")
    st.session_state.setdefault("sql_model_assistant_tableau_publish_context", {})
    st.session_state.setdefault("sql_model_assistant_schema_flow_catalog_cache", {})
    st.session_state.setdefault("sql_model_assistant_database_inventory_cache", {})


def _reset_state() -> None:
    _clear_parallel_flux_state(cancel_running=True)
    st.session_state.sql_model_assistant_rdl_payload = b""
    st.session_state.sql_model_assistant_pending_sql = ""
    st.session_state.sql_model_assistant_sql_query = ""
    st.session_state.sql_model_assistant_messages = []
    st.session_state.sql_model_assistant_rdl_report = {}
    st.session_state.sql_model_assistant_rdl_filename = ""
    st.session_state.sql_model_assistant_selected_dataset_name = ""
    st.session_state.sql_model_assistant_selected_datasource_name = ""
    st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
    st.session_state.sql_model_assistant_flux1_output_dir = ""
    st.session_state.sql_model_assistant_flux1_error = ""
    st.session_state.sql_model_assistant_consumer_workbook_path = ""
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path_pending_sync = False
    st.session_state.sql_model_assistant_template_path = _template_default_path()
    _clear_validation_outputs()


def _clear_validation_outputs() -> None:
    st.session_state.sql_model_assistant_schema_validated = False
    st.session_state.sql_model_assistant_validated_model = {}
    st.session_state.sql_model_assistant_generated_twb = ""
    st.session_state.sql_model_assistant_generated_twb_name = "validated_semantic_model.twb"
    st.session_state.sql_model_assistant_consumer_workbook_path = ""
    st.session_state.sql_model_assistant_tableau_last_publish_report = {}
    st.session_state.sql_model_assistant_tableau_last_publish_error = ""
    st.session_state.sql_model_assistant_tableau_publish_context = {}
    st.session_state.sql_model_assistant_database_inventory_cache = {}


def _get_parallel_flux_future(session_key: str) -> Any:
    candidate = st.session_state.get(session_key)
    if candidate is None:
        return None
    if not callable(getattr(candidate, "done", None)):
        return None
    if not callable(getattr(candidate, "result", None)):
        return None
    return candidate


def _clear_parallel_flux_state(cancel_running: bool = False) -> None:
    for session_key in [
        "sql_model_assistant_parallel_flux1_future",
        "sql_model_assistant_parallel_flux2_future",
    ]:
        future = _get_parallel_flux_future(session_key)
        if cancel_running and future is not None and not future.done():
            future.cancel()
        st.session_state[session_key] = None
    st.session_state.sql_model_assistant_parallel_query = ""


def _current_parallel_flux_state() -> dict[str, bool]:
    flux1_pending = _get_parallel_flux_future("sql_model_assistant_parallel_flux1_future") is not None
    flux2_pending = _get_parallel_flux_future("sql_model_assistant_parallel_flux2_future") is not None
    return {
        "active": flux1_pending or flux2_pending,
        "flux1_pending": flux1_pending,
        "flux2_pending": flux2_pending,
    }


def _poll_parallel_fluxes() -> dict[str, bool]:
    state = _current_parallel_flux_state()
    if not state["active"]:
        return state

    query = str(
        st.session_state.get("sql_model_assistant_parallel_query", "")
        or st.session_state.get("sql_model_assistant_sql_query", "")
        or ""
    ).strip()
    initial_conversation = _seed_initial_sql_model_conversation()

    flux2_future = _get_parallel_flux_future("sql_model_assistant_parallel_flux2_future")
    if flux2_future is not None and flux2_future.done():
        try:
            assistant_text, structured_result, used_fallback = flux2_future.result()
        except Exception as exc:
            assistant_text = f"Flux 2 failed: {exc}"
            structured_result = {}
            used_fallback = False
        display_mode = _assistant_display_mode(initial_conversation, structured_result)

        _clear_validation_outputs()
        st.session_state.sql_model_assistant_sql_query = query
        st.session_state.sql_model_assistant_messages = initial_conversation + [
            {
                "role": "assistant",
                "content": assistant_text,
                "structured_result": structured_result,
                "used_fallback": used_fallback,
                "display_mode": display_mode,
            }
        ]
        st.session_state.sql_model_assistant_parallel_flux2_future = None

    flux1_future = _get_parallel_flux_future("sql_model_assistant_parallel_flux1_future")
    if flux1_future is not None and flux1_future.done():
        flux1_result: dict[str, str] | None = None
        flux1_error = ""
        try:
            flux1_result = flux1_future.result()
        except Exception as exc:
            flux1_error = str(exc)

        st.session_state.sql_model_assistant_flux1_error = flux1_error
        if flux1_result is not None:
            visual_workbook_path = str(flux1_result.get("visual_workbook_path", "") or "").strip()
            output_dir = str(flux1_result.get("output_dir", "") or "").strip()
            st.session_state.sql_model_assistant_flux1_visual_workbook_path = visual_workbook_path
            st.session_state.sql_model_assistant_flux1_output_dir = output_dir
            if visual_workbook_path:
                _schedule_visual_source_sidebar_sync(visual_workbook_path)
        else:
            st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
            st.session_state.sql_model_assistant_flux1_output_dir = ""

        st.session_state.sql_model_assistant_parallel_flux1_future = None

    state = _current_parallel_flux_state()
    if not state["active"]:
        st.session_state.sql_model_assistant_parallel_query = ""
    return state


def _render_pipeline_status_sidebar() -> None:
    st.markdown('<div class="sqlma-section-label">Run Status</div>', unsafe_allow_html=True)

    _datasource, dataset = _resolve_current_rdl_context()
    dataset_query = str(dataset.get("query", "") or "").strip() if isinstance(dataset, dict) else ""
    latest_model = _latest_assistant_model()
    rdl_report = st.session_state.get("sql_model_assistant_rdl_report", {})
    rdl_filename = str(st.session_state.get("sql_model_assistant_rdl_filename", "") or "").strip()
    selected_dataset_name = str(st.session_state.get("sql_model_assistant_selected_dataset_name", "") or "").strip()
    schema_validated = bool(st.session_state.get("sql_model_assistant_schema_validated", False))
    generated_twb = str(st.session_state.get("sql_model_assistant_generated_twb", "") or "").strip()
    generated_twb_name = str(
        st.session_state.get("sql_model_assistant_generated_twb_name", "") or ""
    ).strip()
    publish_report = st.session_state.get("sql_model_assistant_tableau_last_publish_report", {})
    publish_error = str(st.session_state.get("sql_model_assistant_tableau_last_publish_error", "") or "").strip()
    flux1_path = str(st.session_state.get("sql_model_assistant_flux1_visual_workbook_path", "") or "").strip()
    consumer_path = str(st.session_state.get("sql_model_assistant_consumer_workbook_path", "") or "").strip()
    flux1_error = str(st.session_state.get("sql_model_assistant_flux1_error", "") or "").strip()

    steps = [
        {
            "title": "RDL loaded",
            "state": "done" if isinstance(rdl_report, dict) and bool(rdl_report) else "pending",
            "detail": rdl_filename,
        },
        {
            "title": "Flux 1 visual workbook",
            "state": "error" if flux1_error else "done" if flux1_path else "pending",
            "detail": flux1_path or flux1_error,
        },
        {
            "title": "Flux 2 SQL selected",
            "state": "done" if bool(dataset_query or st.session_state.get("sql_model_assistant_sql_query", "")) else "pending",
            "detail": selected_dataset_name,
        },
        {
            "title": "Flux 2 schema proposed",
            "state": "done" if bool(latest_model) else "pending",
            "detail": str(latest_model.get("model_type", "")) if latest_model else "",
        },
        {
            "title": "Flux 2 schema validated",
            "state": "done" if schema_validated else "pending",
            "detail": "Locked for template update" if schema_validated else "",
        },
        {
            "title": "Flux 2 semantic workbook",
            "state": "done" if bool(generated_twb) else "pending",
            "detail": generated_twb_name if generated_twb else "",
        },
        {
            "title": "Datasource published",
            "state": "error" if publish_error else "done" if isinstance(publish_report, dict) and bool(publish_report) else "pending",
            "detail": _pipeline_publish_detail(publish_report, publish_error),
        },
        {
            "title": "Consumer workbook fused",
            "state": "done" if consumer_path else "pending",
            "detail": consumer_path,
        },
    ]

    completed_steps = sum(1 for step in steps if step["state"] == "done")
    st.progress(completed_steps / len(steps))
    for step in steps:
        _render_pipeline_step(step["title"], step["state"], step.get("detail", ""))


def _pipeline_publish_detail(publish_report: Any, publish_error: str) -> str:
    if publish_error:
        return "Last publish failed"
    if isinstance(publish_report, dict) and publish_report:
        datasource_name = str(publish_report.get("datasource_name") or "").strip()
        workbook_name = str(publish_report.get("workbook_name") or "").strip()
        if datasource_name and workbook_name:
            return f"{datasource_name} / {workbook_name}"
        return str(publish_report.get("status") or "Published")
    return ""


def _render_pipeline_step(title: str, state: str, detail: str = "") -> None:
    label = "Done" if state == "done" else "Error" if state == "error" else "Pending"
    detail_html = (
        f"<div class='sqlma-step-detail'>{html.escape(str(detail or ''))}</div>"
        if detail
        else ""
    )
    st.markdown(
        (
            "<div class='sqlma-step'>"
            f"<span class='sqlma-step-state {state}'>{html.escape(label)}</span>"
            "<div>"
            f"<div class='sqlma-step-title'>{html.escape(title)}</div>"
            f"{detail_html}"
            "</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_chat_transcript() -> None:
    st.markdown('<div class="sqlma-section-label">Conversation</div>', unsafe_allow_html=True)

    messages = st.session_state.get("sql_model_assistant_messages", [])
    rdl_report = st.session_state.get("sql_model_assistant_rdl_report", {})
    if not messages:
        with st.chat_message("assistant"):
            if rdl_report:
                st.write(
                    "Run Flux 1 + Flux 2, then ask about tables, relationships, or the current model."
                )
            else:
                st.write(
                    "Upload an RDL report to get started."
                )
        return

    for message_index, message in enumerate(messages):
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message["role"] == "assistant":
                if message.get("used_fallback"):
                    st.caption("SQL-evidence mode used for this response.")
                structured_result = message.get("structured_result", {})
                display_mode = str(message.get("display_mode", "model") or "model").strip().lower()
                if isinstance(structured_result, dict) and structured_result:
                    if display_mode == "model":
                        _render_structured_result(
                            structured_result,
                            render_key_suffix=f"message_{message_index}",
                        )
                    else:
                        with st.expander("Show current schema model", expanded=False):
                            _render_structured_result(
                                structured_result,
                                render_key_suffix=f"message_{message_index}",
                            )


def _render_parallel_flux_progress(parallel_flux_state: dict[str, bool]) -> None:
    if not parallel_flux_state.get("active", False):
        return

    st.markdown('<div class="sqlma-section-label">Parallel Run</div>', unsafe_allow_html=True)
    flux1_pending = bool(parallel_flux_state.get("flux1_pending", False))
    flux2_pending = bool(parallel_flux_state.get("flux2_pending", False))

    if flux1_pending and flux2_pending:
        st.caption("Flux 1 and Flux 2 started together. Flux 2 will appear first when it finishes.")
        return

    if flux1_pending and not flux2_pending:
        st.caption("Flux 2 is ready. Flux 1 is still running in the background.")
        st.button("Refresh Flux 1 Status", key="refresh_flux1_status", use_container_width=True)
        return

    if flux2_pending and not flux1_pending:
        st.caption("Flux 1 is ready. Flux 2 is still analyzing the model.")


def _render_flux1_results() -> None:
    flux1_path_value = str(st.session_state.get("sql_model_assistant_flux1_visual_workbook_path", "") or "").strip()
    flux1_error = str(st.session_state.get("sql_model_assistant_flux1_error", "") or "").strip()
    if not flux1_path_value and not flux1_error:
        return

    st.markdown('<div class="sqlma-section-label">Flux 1 Output</div>', unsafe_allow_html=True)
    if flux1_error:
        st.error(f"Flux 1 failed: {flux1_error}")
        return

    flux1_path = Path(flux1_path_value)
    st.success("Visual workbook ready.")
    st.caption(str(flux1_path))
    if flux1_path.exists():
        st.download_button(
            "Download visual workbook",
            data=flux1_path.read_bytes(),
            file_name=flux1_path.name,
            mime="application/xml",
            key="download_flux1_visual_workbook",
            use_container_width=True,
        )


def _render_consumer_workbook_results() -> None:
    consumer_path_value = str(st.session_state.get("sql_model_assistant_consumer_workbook_path", "") or "").strip()
    if not consumer_path_value:
        return

    consumer_path = Path(consumer_path_value)
    st.markdown('<div class="sqlma-section-label">Final Workbook</div>', unsafe_allow_html=True)
    st.success("Consumer workbook ready.")
    st.caption(str(consumer_path))
    if consumer_path.exists():
        st.download_button(
            "Download final workbook",
            data=consumer_path.read_bytes(),
            file_name=consumer_path.name,
            mime="application/xml",
            key="download_final_consumer_workbook",
            use_container_width=True,
        )


def _schedule_visual_source_sidebar_sync(path_value: str) -> None:
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path = str(path_value or "").strip()
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path_pending_sync = True


def _start_conversation(sql_query: str) -> None:
    st.session_state.sql_model_assistant_sql_query = sql_query
    st.session_state.sql_model_assistant_messages = [
        {
            "role": "user",
            "content": "Analyze this SQL query and produce the current dimensional model.",
        }
    ]
    _clear_validation_outputs()
    _append_assistant_response()


def _handle_follow_up(message: str) -> None:
    if not message:
        return
    _clear_validation_outputs()
    st.session_state.sql_model_assistant_messages.append(
        {
            "role": "user",
            "content": message,
        }
    )
    _append_assistant_response()


def _append_assistant_response() -> None:
    sql_query = st.session_state.sql_model_assistant_sql_query
    conversation = st.session_state.sql_model_assistant_messages
    llm_config_path = st.session_state.sql_model_assistant_llm_config

    with st.spinner("Thinking..."):
        assistant_text, structured_result, used_fallback = _generate_response(
            sql_query=sql_query,
            conversation=conversation,
            llm_config_path=llm_config_path,
        )
    display_mode = _assistant_display_mode(conversation, structured_result)

    st.session_state.sql_model_assistant_messages.append(
        {
            "role": "assistant",
            "content": assistant_text,
            "structured_result": structured_result,
            "used_fallback": used_fallback,
            "display_mode": display_mode,
        }
    )


def _seed_initial_sql_model_conversation() -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": "Analyze this SQL query and produce the current dimensional model.",
        }
    ]


def _resolve_existing_sql_assistant_llm_config_path(llm_config_path: str) -> Path:
    candidates: list[Path] = []
    raw_value = str(llm_config_path or "").strip()
    if raw_value:
        raw_path = Path(raw_value).expanduser()
        candidates.append(raw_path)
        if not raw_path.is_absolute():
            candidates.append(ROOT_DIR / raw_path)
            candidates.append(PROJECT_ROOT / raw_path)
    candidates.extend([SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG])

    seen: set[str] = set()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No usable LLM config file was found for SQL Model Assistant or visual migration."
    )


def _flux1_output_dir_for_rdl(file_name: str) -> Path:
    report_stem = Path(str(file_name or "uploaded_report.rdl")).stem or "uploaded_report"
    safe_report_name = _tableau_safe_name(report_stem)
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RDL_TO_TWB_OUTPUT_DIR / f"{timestamp_utc}_{safe_report_name}" / RDL_TO_TWB_VISUAL_MAPPING_DIR_NAME


def _flux1_input_report_path(output_dir: Path, file_name: str) -> Path:
    run_dir = output_dir.parent if output_dir.name == RDL_TO_TWB_VISUAL_MAPPING_DIR_NAME else output_dir
    input_dir = run_dir / RDL_TO_TWB_INPUT_DIR_NAME
    input_dir.mkdir(parents=True, exist_ok=True)
    return input_dir / _tableau_safe_name(Path(str(file_name or "uploaded_report.rdl")).name)


def _flux1_canonical_visual_workbook_path(visual_workbook_path: str, output_dir: Path) -> str:
    source_path = Path(visual_workbook_path)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    source_path = source_path.resolve(strict=True)

    target_path = (output_dir / RDL_TO_TWB_VISUAL_MAPPING_TWB_NAME).resolve(strict=False)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path != target_path:
        if source_path.parent == target_path.parent:
            if target_path.exists():
                target_path.unlink()
            source_path.replace(target_path)
        else:
            shutil.copy2(source_path, target_path)
    return str(target_path)


def _run_flux1_visual_migration(
    rdl_payload: bytes,
    file_name: str,
    llm_config_path: str,
) -> dict[str, str]:
    if not rdl_payload:
        raise ValueError("RDL payload is missing for Flux 1 visual migration.")

    suffix = Path(str(file_name or "uploaded_report.rdl")).suffix or ".rdl"
    output_dir = _flux1_output_dir_for_rdl(file_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    _flux1_input_report_path(output_dir, file_name).write_bytes(rdl_payload)
    temp_rdl_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(rdl_payload)
            temp_rdl_path = Path(handle.name)

        result = run_conversion(
            rdl_path=temp_rdl_path,
            rdl_xsd_path=ASSETS_DIR / "ReportDefinition.xsd",
            twb_xsd_path=ASSETS_DIR / "twb_2026.1.0.xsd",
            output_dir=output_dir,
            config_path=_resolve_existing_sql_assistant_llm_config_path(llm_config_path),
            publish_enabled=False,
            artifact_mode=os.getenv("RDL_TO_TWB_ARTIFACT_MODE") or "runtime",
        )
    finally:
        if temp_rdl_path is not None and temp_rdl_path.exists():
            temp_rdl_path.unlink(missing_ok=True)

    visual_workbook_path = str(result.get("twb") or "").strip()
    if not visual_workbook_path:
        raise RuntimeError("Flux 1 completed without producing a visual workbook.")
    visual_workbook_path = _flux1_canonical_visual_workbook_path(visual_workbook_path, output_dir)
    if isinstance(result, dict):
        result["twb"] = visual_workbook_path

    return {
        "visual_workbook_path": visual_workbook_path,
        "output_dir": str(output_dir),
    }


def _run_flux2_schema_analysis(
    sql_query: str,
    llm_config_path: str,
    database_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], bool]:
    return _generate_response(
        sql_query=sql_query,
        conversation=_seed_initial_sql_model_conversation(),
        llm_config_path=llm_config_path,
        database_context=database_context,
    )


def _run_parallel_fluxes_for_current_selection(query: str) -> None:
    rdl_payload = st.session_state.get("sql_model_assistant_rdl_payload", b"")
    rdl_file_name = str(st.session_state.get("sql_model_assistant_rdl_filename", "") or "uploaded_report.rdl")
    llm_config_path = str(st.session_state.get("sql_model_assistant_llm_config", "") or "")
    database_context = _build_current_database_context()

    if not isinstance(rdl_payload, (bytes, bytearray)) or not rdl_payload:
        raise ValueError("RDL payload is not available anymore. Re-upload the report first.")

    _clear_parallel_flux_state(cancel_running=True)
    initial_conversation = _seed_initial_sql_model_conversation()

    st.session_state.sql_model_assistant_sql_query = query
    st.session_state.sql_model_assistant_pending_sql = query
    st.session_state.sql_model_assistant_messages = initial_conversation
    st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
    st.session_state.sql_model_assistant_flux1_output_dir = ""
    st.session_state.sql_model_assistant_flux1_error = ""
    st.session_state.sql_model_assistant_parallel_query = query
    _schedule_visual_source_sidebar_sync("")
    _clear_validation_outputs()

    st.session_state.sql_model_assistant_parallel_flux1_future = FLUX_PIPELINE_EXECUTOR.submit(
        _run_flux1_visual_migration,
        bytes(rdl_payload),
        rdl_file_name,
        llm_config_path,
    )
    st.session_state.sql_model_assistant_parallel_flux2_future = FLUX_PIPELINE_EXECUTOR.submit(
        _run_flux2_schema_analysis,
        query,
        llm_config_path,
        database_context,
    )


def _template_default_path() -> str:
    candidates = _template_default_candidates()
    if candidates:
        return str(max(candidates, key=_template_candidate_score))
    return str(OUTPUT_TEMPLATE_PATH)


def _template_default_candidates() -> list[Path]:
    candidates: list[Path] = []

    configured_path = ""
    try:
        configured_path = str(
            st.session_state.get("sql_model_assistant_tableau_empty_workbook_template_path", "") or ""
        ).strip()
    except Exception:
        configured_path = ""

    if configured_path:
        candidates.append(Path(configured_path).expanduser())

    if _is_active_regionalsales_report():
        candidates.extend(_regional_sales_semantic_workbook_candidates())

    output_dir = OUTPUT_TEMPLATE_PATH.parent
    if output_dir.exists():
        candidates.extend(sorted(output_dir.glob("template_semantic_model*.twb")))

    candidates.extend([OUTPUT_DIR / "Book1.twb", OUTPUT_DIR / "book1.twb"])
    candidates.extend([OUTPUT_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH, FALLBACK_TEMPLATE_PATH])

    existing_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.suffix.lower() != ".twb" or not path.exists():
            continue
        path_key = _template_path_key(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        existing_candidates.append(path)

    return existing_candidates


def _template_candidate_score(path: Path) -> tuple[int, int, int, int, int, int, int, int]:
    configured_path = ""
    try:
        configured_path = str(
            st.session_state.get("sql_model_assistant_tableau_empty_workbook_template_path", "") or ""
        ).strip()
    except Exception:
        configured_path = ""

    configured_key = _template_path_key(configured_path)
    path_key = _template_path_key(path)
    is_configured = 1 if configured_key and path_key == configured_key else 0
    is_output_template_family = 1 if path.parent == OUTPUT_TEMPLATE_PATH.parent else 0

    cols_count = 0
    metadata_count = 0
    relationship_count = 0
    suffixed_name_count = 0
    try:
        root = ET.fromstring(_read_twb_text_with_fallback(path))
        datasource_node = root.find("datasources/datasource")
        if datasource_node is not None:
            connection_node = datasource_node.find("connection")
            if connection_node is not None:
                cols_node = connection_node.find("cols")
                if cols_node is not None:
                    map_nodes = [child for child in list(cols_node) if child.tag == "map"]
                    cols_count = len(map_nodes)
                    for map_node in map_nodes:
                        key_raw = str(map_node.attrib.get("key", "") or "").strip()
                        if re.search(r"\[[^\]]+\s+\([^\]]+\)\]", key_raw):
                            suffixed_name_count += 1

                metadata_node = connection_node.find("metadata-records")
                if metadata_node is not None:
                    metadata_count = len([child for child in list(metadata_node) if child.tag == "metadata-record"])

            relationships_node = datasource_node.find("object-graph/relationships")
            if relationships_node is not None:
                relationship_count = len(
                    [child for child in list(relationships_node) if child.tag == "relationship"]
                )
    except Exception:
        pass

    try:
        stat = path.stat()
        size = int(stat.st_size)
        modified = int(stat.st_mtime_ns)
    except OSError:
        size = 0
        modified = 0

    return (
        is_configured,
        is_output_template_family,
        cols_count,
        metadata_count,
        relationship_count,
        suffixed_name_count,
        size,
        modified,
    )


def _read_twb_text_with_fallback(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _template_path_key(value: str | Path) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _preferred_sql_assistant_llm_config_path() -> Path:
    for candidate in [SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG]:
        if candidate.exists():
            return candidate
    return SQL_ASSISTANT_LLM_CONFIG


def _render_tableau_publish_settings() -> None:
    st.session_state.setdefault(
        "sql_model_assistant_tableau_auto_publish_input",
        bool(st.session_state.sql_model_assistant_tableau_auto_publish),
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_server_url_input",
        st.session_state.sql_model_assistant_tableau_server_url,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_site_content_url_input",
        st.session_state.sql_model_assistant_tableau_site_content_url,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_project_name_input",
        st.session_state.sql_model_assistant_tableau_project_name,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_datasource_project_name_input",
        st.session_state.sql_model_assistant_tableau_datasource_project_name,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_workbook_project_name_input",
        st.session_state.sql_model_assistant_tableau_workbook_project_name,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_username_input",
        st.session_state.sql_model_assistant_tableau_username,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_password_input",
        st.session_state.sql_model_assistant_tableau_password,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_source_ds_name_input",
        st.session_state.sql_model_assistant_tableau_source_datasource_name,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_empty_workbook_template_path_input",
        st.session_state.sql_model_assistant_tableau_empty_workbook_template_path,
    )
    st.session_state.setdefault(
        "sql_model_assistant_tableau_visual_source_twb_path_input",
        st.session_state.sql_model_assistant_tableau_visual_source_twb_path,
    )
    if st.session_state.get("sql_model_assistant_tableau_visual_source_twb_path_pending_sync", False):
        st.session_state.sql_model_assistant_tableau_visual_source_twb_path_input = str(
            st.session_state.sql_model_assistant_tableau_visual_source_twb_path or ""
        )
        st.session_state.sql_model_assistant_tableau_visual_source_twb_path_pending_sync = False

    st.checkbox(
        "Auto publish",
        key="sql_model_assistant_tableau_auto_publish_input",
    )
    server_col, site_col = st.columns(2)
    with server_col:
        st.text_input(
            "Server URL",
            key="sql_model_assistant_tableau_server_url_input",
            placeholder="https://<pod>.online.tableau.com",
        )
    with site_col:
        st.text_input(
            "Site URI",
            key="sql_model_assistant_tableau_site_content_url_input",
            placeholder="site-content-url",
        )

    datasource_project_col, workbook_project_col = st.columns(2)
    with datasource_project_col:
        st.text_input(
            "Datasource project",
            key="sql_model_assistant_tableau_datasource_project_name_input",
        )
    with workbook_project_col:
        st.text_input(
            "Workbook project",
            key="sql_model_assistant_tableau_workbook_project_name_input",
        )

    project_col, user_col = st.columns(2)
    with project_col:
        st.text_input(
            "Legacy project fallback",
            key="sql_model_assistant_tableau_project_name_input",
        )
    with user_col:
        st.text_input(
            "Username",
            key="sql_model_assistant_tableau_username_input",
        )

    st.text_input(
        "Password",
        type="password",
        key="sql_model_assistant_tableau_password_input",
    )

    show_optional_fields = st.checkbox(
        "Show optional fields",
        key="sql_model_assistant_tableau_show_optional_publish_fields",
    )
    if show_optional_fields:
        st.text_input(
            "Source datasource name",
            key="sql_model_assistant_tableau_source_ds_name_input",
        )
        st.text_input(
            "Template path (.twb)",
            key="sql_model_assistant_tableau_empty_workbook_template_path_input",
        )
        st.text_input(
            "Visual source TWB path (.twb)",
            key="sql_model_assistant_tableau_visual_source_twb_path_input",
        )

    st.session_state.sql_model_assistant_tableau_auto_publish = bool(
        st.session_state.sql_model_assistant_tableau_auto_publish_input
    )
    st.session_state.sql_model_assistant_tableau_server_url = str(
        st.session_state.sql_model_assistant_tableau_server_url_input or ""
    )
    st.session_state.sql_model_assistant_tableau_site_content_url = str(
        st.session_state.sql_model_assistant_tableau_site_content_url_input or ""
    )
    st.session_state.sql_model_assistant_tableau_project_name = str(
        st.session_state.sql_model_assistant_tableau_project_name_input or ""
    )
    st.session_state.sql_model_assistant_tableau_datasource_project_name = str(
        st.session_state.sql_model_assistant_tableau_datasource_project_name_input
        or TABLEAU_DATASOURCE_PROJECT_NAME
    )
    st.session_state.sql_model_assistant_tableau_workbook_project_name = str(
        st.session_state.sql_model_assistant_tableau_workbook_project_name_input
        or TABLEAU_WORKBOOK_PROJECT_NAME
    )
    st.session_state.sql_model_assistant_tableau_username = str(
        st.session_state.sql_model_assistant_tableau_username_input or ""
    )
    st.session_state.sql_model_assistant_tableau_password = str(
        st.session_state.sql_model_assistant_tableau_password_input or ""
    )
    st.session_state.sql_model_assistant_tableau_source_datasource_name = str(
        st.session_state.sql_model_assistant_tableau_source_ds_name_input or ""
    )
    st.session_state.sql_model_assistant_tableau_empty_workbook_template_path = str(
        st.session_state.sql_model_assistant_tableau_empty_workbook_template_path_input or ""
    )
    st.session_state.sql_model_assistant_tableau_visual_source_twb_path = str(
        st.session_state.sql_model_assistant_tableau_visual_source_twb_path_input or ""
    )


def _sync_tableau_settings_from_config_if_needed() -> None:
    current_config_path = str(st.session_state.sql_model_assistant_llm_config or "").strip()
    if not current_config_path:
        current_config_path = str(_preferred_sql_assistant_llm_config_path())
        st.session_state.sql_model_assistant_llm_config = current_config_path
    loaded_config_path = str(st.session_state.get("sql_model_assistant_tableau_loaded_config_path", "") or "").strip()
    defaults = _load_tableau_publish_defaults(current_config_path)
    current_server = str(st.session_state.get("sql_model_assistant_tableau_server_url", "") or "").strip().lower()
    defaults_server = str(defaults.get("server_url") or "").strip().lower()
    stale_public_server = (
        current_server == "https://public.tableau.com"
        and defaults_server
        and defaults_server != "https://public.tableau.com"
    )

    if current_config_path == loaded_config_path and not stale_public_server:
        return

    if defaults:
        if defaults.get("server_url"):
            st.session_state.sql_model_assistant_tableau_server_url = str(defaults["server_url"])
            st.session_state.sql_model_assistant_tableau_server_url_input = str(defaults["server_url"])
        if defaults.get("site_content_url"):
            st.session_state.sql_model_assistant_tableau_site_content_url = str(defaults["site_content_url"])
            st.session_state.sql_model_assistant_tableau_site_content_url_input = str(defaults["site_content_url"])
        if defaults.get("project_name"):
            st.session_state.sql_model_assistant_tableau_project_name = str(defaults["project_name"])
            st.session_state.sql_model_assistant_tableau_project_name_input = str(defaults["project_name"])
        if defaults.get("datasource_project_name"):
            st.session_state.sql_model_assistant_tableau_datasource_project_name = str(
                defaults["datasource_project_name"]
            )
            st.session_state.sql_model_assistant_tableau_datasource_project_name_input = str(
                defaults["datasource_project_name"]
            )
        if defaults.get("workbook_project_name"):
            st.session_state.sql_model_assistant_tableau_workbook_project_name = str(
                defaults["workbook_project_name"]
            )
            st.session_state.sql_model_assistant_tableau_workbook_project_name_input = str(
                defaults["workbook_project_name"]
            )
        if defaults.get("username"):
            st.session_state.sql_model_assistant_tableau_username = str(defaults["username"])
            st.session_state.sql_model_assistant_tableau_username_input = str(defaults["username"])
        if defaults.get("password"):
            st.session_state.sql_model_assistant_tableau_password = str(defaults["password"])
            st.session_state.sql_model_assistant_tableau_password_input = str(defaults["password"])
        if defaults.get("source_datasource_name"):
            st.session_state.sql_model_assistant_tableau_source_datasource_name = str(
                defaults["source_datasource_name"]
            )
            st.session_state.sql_model_assistant_tableau_source_ds_name_input = str(
                defaults["source_datasource_name"]
            )
        if defaults.get("empty_workbook_template_path"):
            st.session_state.sql_model_assistant_tableau_empty_workbook_template_path = str(
                defaults["empty_workbook_template_path"]
            )
            st.session_state.sql_model_assistant_tableau_empty_workbook_template_path_input = str(
                defaults["empty_workbook_template_path"]
            )
        if defaults.get("visual_source_twb_path"):
            st.session_state.sql_model_assistant_tableau_visual_source_twb_path = str(
                defaults["visual_source_twb_path"]
            )
            st.session_state.sql_model_assistant_tableau_visual_source_twb_path_input = str(
                defaults["visual_source_twb_path"]
            )

    st.session_state.sql_model_assistant_tableau_loaded_config_path = current_config_path


def _tableau_normalize_datasource_publish_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if normalized in {"tds", "live", "live_tds", "live_datasource", "live_datasource_tds"}:
        return "live_tds"
    if normalized in {"tdsx", "extract", "hyper", "hyper_extract", "extract_tdsx", "tdsx_extract"}:
        return "extract"
    return ""


def _tableau_datasource_publish_mode_from_config(tableau_cfg: dict[str, Any]) -> str:
    for key in [
        "datasource_publish_mode",
        "datasource_publish_format",
        "publish_datasource_mode",
        "publish_datasource_format",
    ]:
        mode = _tableau_normalize_datasource_publish_mode(tableau_cfg.get(key))
        if mode:
            return mode
    return "extract" if bool(tableau_cfg.get("build_hyper_extract", False)) else "live_tds"


def _load_tableau_publish_defaults(config_path: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    raw_path = Path(str(config_path or "")).expanduser()
    candidates = [raw_path]
    if not raw_path.is_absolute():
        candidates.append(ROOT_DIR / raw_path)
        candidates.append(PROJECT_ROOT / raw_path)

    resolved_path: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            resolved_path = candidate
            break
    if resolved_path is None:
        return defaults

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    tableau_cfg = None
    if isinstance(payload, dict):
        # Prefer a Cloud-specific key name, but keep backward compatibility.
        candidate = payload.get("tableau_cloud")
        if isinstance(candidate, dict):
            tableau_cfg = candidate
        else:
            legacy_candidate = payload.get("tableau_server")
            if isinstance(legacy_candidate, dict):
                tableau_cfg = legacy_candidate
    if not isinstance(tableau_cfg, dict):
        return defaults

    for key in [
        "server_url",
        "site_content_url",
        "project_name",
        "datasource_project_name",
        "workbook_project_name",
        "source_datasource_name",
        "empty_workbook_template_path",
        "visual_source_twb_path",
    ]:
        value = tableau_cfg.get(key)
        if isinstance(value, str) and value.strip():
            defaults[key] = value.strip()

    defaults.setdefault("datasource_project_name", TABLEAU_DATASOURCE_PROJECT_NAME)
    defaults.setdefault("workbook_project_name", TABLEAU_WORKBOOK_PROJECT_NAME)

    datasource_publish_mode = _tableau_datasource_publish_mode_from_config(tableau_cfg)
    defaults["datasource_publish_mode"] = datasource_publish_mode
    defaults["build_hyper_extract"] = datasource_publish_mode == "extract"
    try:
        defaults["hyper_max_rows_per_table"] = int(tableau_cfg.get("hyper_max_rows_per_table") or 0)
    except (TypeError, ValueError):
        defaults["hyper_max_rows_per_table"] = 0

    raw_auth_method = str(tableau_cfg.get("auth_method") or tableau_cfg.get("auth_type") or "").strip().lower()
    if raw_auth_method in {"pat", "personal_access_token", "personalaccesstoken"}:
        defaults["auth_method"] = "pat"
    elif raw_auth_method in {"username_password", "usernamepassword", "password"}:
        defaults["auth_method"] = "username_password"

    username = _resolve_config_secret(tableau_cfg.get("username"), fallback_env="TABLEAU_USERNAME")
    if username:
        defaults["username"] = username

    password = _resolve_config_secret(tableau_cfg.get("password"), fallback_env="TABLEAU_PASSWORD")
    if password:
        defaults["password"] = password

    pat_name = _resolve_config_secret(tableau_cfg.get("pat_name"), fallback_env="TABLEAU_PAT_NAME")
    if pat_name:
        defaults["pat_name"] = pat_name

    pat_secret = _resolve_config_secret(tableau_cfg.get("pat_secret"), fallback_env="TABLEAU_PAT_SECRET")
    if pat_secret:
        defaults["pat_secret"] = pat_secret

    return defaults


def _resolve_config_secret(value: object, fallback_env: str) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            if stripped.lower().startswith("env:"):
                env_name = stripped.split(":", 1)[1].strip()
                if not env_name:
                    return None
                env_value = os.getenv(env_name)
                if isinstance(env_value, str) and env_value.strip():
                    return env_value.strip()
                return None
            return stripped

    env_fallback = os.getenv(fallback_env)
    if isinstance(env_fallback, str) and env_fallback.strip():
        return env_fallback.strip()
    return None


def _render_rdl_intake() -> None:
    st.markdown('<div class="sqlma-section-label">Source Report</div>', unsafe_allow_html=True)
    uploaded_rdl = st.file_uploader(
        "Upload report (.rdl)",
        type=["rdl"],
        key="sql_model_assistant_rdl_upload",
    )

    action_col, clear_col = st.columns(2)
    with action_col:
        if st.button("Load RDL", type="primary", key="extract_sql_from_rdl", use_container_width=True):
            if uploaded_rdl is None:
                st.warning("Please upload an RDL file first.")
            else:
                try:
                    report = _parse_uploaded_rdl(uploaded_rdl)
                except Exception as exc:
                    st.error(f"Failed to parse RDL: {exc}")
                else:
                    _clear_parallel_flux_state(cancel_running=True)
                    st.session_state.sql_model_assistant_rdl_payload = uploaded_rdl.getvalue()
                    st.session_state.sql_model_assistant_sql_query = ""
                    st.session_state.sql_model_assistant_messages = []
                    st.session_state.sql_model_assistant_rdl_report = report
                    st.session_state.sql_model_assistant_rdl_filename = str(
                        getattr(uploaded_rdl, "name", "uploaded_report.rdl")
                    )
                    st.session_state.sql_model_assistant_selected_dataset_name = _pick_default_dataset_name(
                        report.get("data_sets", [])
                    )
                    datasource, _dataset = _resolve_current_rdl_context()
                    st.session_state.sql_model_assistant_selected_datasource_name = str(
                        datasource.get("name", "") if datasource else ""
                    )
                    st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
                    st.session_state.sql_model_assistant_flux1_output_dir = ""
                    st.session_state.sql_model_assistant_flux1_error = ""
                    _schedule_visual_source_sidebar_sync("")
                    _clear_validation_outputs()
                    st.rerun()

    with clear_col:
        if st.button("Clear", key="clear_rdl_selection", use_container_width=True):
            _clear_parallel_flux_state(cancel_running=True)
            st.session_state.sql_model_assistant_rdl_payload = b""
            st.session_state.sql_model_assistant_sql_query = ""
            st.session_state.sql_model_assistant_messages = []
            st.session_state.sql_model_assistant_rdl_report = {}
            st.session_state.sql_model_assistant_rdl_filename = ""
            st.session_state.sql_model_assistant_selected_dataset_name = ""
            st.session_state.sql_model_assistant_selected_datasource_name = ""
            st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
            st.session_state.sql_model_assistant_flux1_output_dir = ""
            st.session_state.sql_model_assistant_flux1_error = ""
            _schedule_visual_source_sidebar_sync("")
            st.session_state.sql_model_assistant_pending_sql = ""
            _clear_validation_outputs()
            st.rerun()

    report = st.session_state.get("sql_model_assistant_rdl_report", {})
    if not isinstance(report, dict) or not report:
        st.caption("Upload an RDL file to begin.")
        return

    data_sets = report.get("data_sets", [])
    if not isinstance(data_sets, list) or not data_sets:
        st.error("No datasets were found in this RDL file.")
        return

    dataset_names = [
        str(dataset.get("name", "")).strip()
        for dataset in data_sets
        if isinstance(dataset, dict) and str(dataset.get("name", "")).strip()
    ]
    dataset_names = _unique(dataset_names)
    if not dataset_names:
        st.error("The RDL datasets do not contain valid dataset names.")
        return

    current_name = str(st.session_state.sql_model_assistant_selected_dataset_name or "").strip()
    if current_name not in dataset_names:
        current_name = dataset_names[0]
        st.session_state.sql_model_assistant_selected_dataset_name = current_name

    selected_name = st.selectbox(
        "Select dataset/query to analyze",
        options=dataset_names,
        index=dataset_names.index(current_name),
    )
    if selected_name != current_name:
        _clear_parallel_flux_state(cancel_running=True)
        st.session_state.sql_model_assistant_sql_query = ""
        st.session_state.sql_model_assistant_messages = []
        st.session_state.sql_model_assistant_flux1_visual_workbook_path = ""
        st.session_state.sql_model_assistant_flux1_output_dir = ""
        st.session_state.sql_model_assistant_flux1_error = ""
        _schedule_visual_source_sidebar_sync("")
        _clear_validation_outputs()
    st.session_state.sql_model_assistant_selected_dataset_name = selected_name

    dataset = _find_dataset_by_name(data_sets, selected_name)
    if not dataset:
        st.error("Could not resolve the selected dataset.")
        return

    datasource = _find_datasource_for_dataset(report, dataset)
    st.session_state.sql_model_assistant_selected_datasource_name = str(
        datasource.get("name", "") if datasource else ""
    )

    summary_file = str(st.session_state.get("sql_model_assistant_rdl_filename", "") or "").strip() or "Uploaded report"
    summary_source = str(datasource.get("name", "") if datasource else "").strip() or "Unknown datasource"
    file_col, dataset_col, source_col = st.columns(3)
    with file_col:
        st.metric("RDL", summary_file)
    with dataset_col:
        st.metric("Datasets", str(len(dataset_names)))
    with source_col:
        st.metric("Datasource", summary_source)

    query = str(dataset.get("query", "") or "").strip()
    if query:
        with st.expander("SQL preview", expanded=False):
            st.code(query, language="sql")
    else:
        st.warning("The selected dataset does not contain a SQL CommandText.")

    with st.expander("RDL datasource details", expanded=False):
        if datasource:
            info = datasource.get("connection_info", {}) if isinstance(datasource, dict) else {}
            st.write({
                "name": datasource.get("name", ""),
                "provider": datasource.get("provider", ""),
                "provider_class": datasource.get("provider_class", ""),
                "server": info.get("server", "") if isinstance(info, dict) else "",
                "database": info.get("database", "") if isinstance(info, dict) else "",
                "security_type": datasource.get("security_type", ""),
            })
        else:
            st.caption("No datasource metadata found for this dataset.")

    if st.button("Run Flux 1 + Flux 2", type="primary", key="analyze_rdl_sql", use_container_width=True):
        if not query:
            st.warning("The selected dataset has no SQL query to analyze.")
            return
        try:
            _run_parallel_fluxes_for_current_selection(query)
        except Exception as exc:
            st.error(f"Failed to start the parallel pipeline: {exc}")
            return
        st.rerun()


def _parse_uploaded_rdl(uploaded_rdl: Any) -> dict[str, Any]:
    payload = uploaded_rdl.getvalue()
    if not payload:
        raise ValueError("Uploaded RDL file is empty.")

    file_name = str(getattr(uploaded_rdl, "name", "uploaded_report.rdl"))
    suffix = Path(file_name).suffix or ".rdl"
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)

        parsed_report = parse_rdl_file(temp_path)
        return parsed_report.to_dict()
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _pick_default_dataset_name(data_sets: Any) -> str:
    if not isinstance(data_sets, list):
        return ""

    first_named = ""
    for dataset in data_sets:
        if not isinstance(dataset, dict):
            continue
        name = str(dataset.get("name", "") or "").strip()
        if not name:
            continue
        if not first_named:
            first_named = name
        query = str(dataset.get("query", "") or "").strip()
        if query:
            return name
    return first_named


def _find_dataset_by_name(data_sets: Any, dataset_name: str) -> dict[str, Any]:
    if not isinstance(data_sets, list):
        return {}
    target = str(dataset_name or "").strip()
    if not target:
        return {}
    for dataset in data_sets:
        if not isinstance(dataset, dict):
            continue
        if str(dataset.get("name", "") or "").strip() == target:
            return dataset
    return {}


def _find_datasource_for_dataset(report: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    data_sources = report.get("data_sources", []) if isinstance(report, dict) else []
    if not isinstance(data_sources, list):
        return {}

    data_source_name = str(dataset.get("data_source_name", "") or "").strip() if isinstance(dataset, dict) else ""
    if data_source_name:
        for source in data_sources:
            if not isinstance(source, dict):
                continue
            if str(source.get("name", "") or "").strip() == data_source_name:
                return source

    for source in data_sources:
        if isinstance(source, dict):
            return source
    return {}


def _resolve_current_rdl_context() -> tuple[dict[str, Any], dict[str, Any]]:
    report = st.session_state.get("sql_model_assistant_rdl_report", {})
    if not isinstance(report, dict) or not report:
        return {}, {}

    data_sets = report.get("data_sets", [])
    selected_name = str(st.session_state.get("sql_model_assistant_selected_dataset_name", "") or "").strip()
    dataset = _find_dataset_by_name(data_sets, selected_name)
    if not dataset:
        fallback_name = _pick_default_dataset_name(data_sets)
        dataset = _find_dataset_by_name(data_sets, fallback_name)
        if fallback_name:
            st.session_state.sql_model_assistant_selected_dataset_name = fallback_name

    datasource = _find_datasource_for_dataset(report, dataset) if dataset else {}
    return datasource, dataset


def _latest_assistant_model() -> dict[str, Any]:
    for message in reversed(st.session_state.sql_model_assistant_messages):
        if message.get("role") != "assistant":
            continue
        structured_result = message.get("structured_result")
        if isinstance(structured_result, dict) and structured_result:
            return structured_result
    return {}


def _render_validation_and_twb_tools(latest_model: dict[str, Any]) -> None:
    st.markdown('<div class="sqlma-section-label">Actions</div>', unsafe_allow_html=True)
    validate_col, status_col = st.columns([1, 2])
    with validate_col:
        validate_clicked = st.button("Validate schema", type="primary", key="validate_star_schema", use_container_width=True)
    with status_col:
        if st.session_state.sql_model_assistant_schema_validated:
            st.caption("Schema locked and ready for workbook generation.")

    if validate_clicked:
        st.session_state.sql_model_assistant_schema_validated = True
        st.session_state.sql_model_assistant_validated_model = copy.deepcopy(latest_model)

    if not st.session_state.sql_model_assistant_schema_validated:
        return

    datasource, dataset = _resolve_current_rdl_context()
    if not datasource or not dataset:
        st.error("RDL context is missing. Re-upload the report and analyze a dataset before generating TWB.")
        return

    with st.expander("Workbook settings", expanded=False):
        template_upload = st.file_uploader(
            "Template upload (.twb)",
            type=["twb"],
            key="sql_model_assistant_template_upload",
        )
        template_path = st.text_input(
            "Template path (.twb)",
            value=st.session_state.sql_model_assistant_template_path,
            key="sql_model_assistant_template_path_input",
        )
        st.session_state.sql_model_assistant_template_path = template_path

        output_name = st.text_input(
            "Output workbook name",
            value=st.session_state.sql_model_assistant_generated_twb_name,
            key="sql_model_assistant_output_twb_name",
        )

    if st.button("Generate semantic workbook", type="primary", key="update_template_twb", use_container_width=True):
        try:
            template_xml = _load_template_xml(template_upload, template_path)
            validated_model = st.session_state.sql_model_assistant_validated_model
            generated_xml = _generate_twb_from_validated_model(
                template_xml=template_xml,
                data_source=datasource,
                dataset=dataset,
                validated_model=validated_model,
            )
            st.session_state.sql_model_assistant_tableau_publish_context = _build_tableau_publish_context(
                template_xml=template_xml,
                data_source=datasource,
                dataset=dataset,
                validated_model=validated_model,
            )
            safe_name = _safe_output_twb_name(output_name)
            st.session_state.sql_model_assistant_generated_twb = generated_xml
            st.session_state.sql_model_assistant_generated_twb_name = safe_name

            output_path = OUTPUT_DIR / safe_name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(generated_xml, encoding="utf-8")
            st.success("Semantic workbook saved.")
            st.caption(str(output_path))

            if st.session_state.sql_model_assistant_tableau_auto_publish:
                try:
                    with st.spinner(
                        "Publishing the validated datasource and building the final consumer workbook..."
                    ):
                        publish_report = _run_tableau_cloud_publish_workflow(output_path)
                    st.session_state.sql_model_assistant_tableau_last_publish_report = publish_report
                    st.session_state.sql_model_assistant_tableau_last_publish_error = ""
                    st.session_state.sql_model_assistant_consumer_workbook_path = str(
                        publish_report.get("consumer_workbook_path") or ""
                    ).strip()
                    _tableau_render_publish_outcome_message(publish_report)
                except Exception as publish_exc:
                    publish_error_message = _tableau_format_user_publish_failure(publish_exc)
                    st.session_state.sql_model_assistant_consumer_workbook_path = ""
                    st.session_state.sql_model_assistant_tableau_last_publish_error = publish_error_message
                    st.error(publish_error_message)
        except Exception as exc:
            st.error(f"Failed to build TWB: {exc}")

    generated_twb = st.session_state.sql_model_assistant_generated_twb
    generated_twb_path = OUTPUT_DIR / st.session_state.sql_model_assistant_generated_twb_name
    if generated_twb:
        st.download_button(
            "Download semantic workbook",
            data=generated_twb.encode("utf-8"),
            file_name=st.session_state.sql_model_assistant_generated_twb_name,
            mime="application/xml",
            key="download_generated_twb",
            use_container_width=True,
        )
        if st.button("Publish datasource and build final workbook", key="publish_generated_twb_now", use_container_width=True):
            try:
                with st.spinner(
                    "Publishing the validated datasource and building the final consumer workbook..."
                ):
                    publish_report = _run_tableau_cloud_publish_workflow(generated_twb_path)
                st.session_state.sql_model_assistant_tableau_last_publish_report = publish_report
                st.session_state.sql_model_assistant_tableau_last_publish_error = ""
                st.session_state.sql_model_assistant_consumer_workbook_path = str(
                    publish_report.get("consumer_workbook_path") or ""
                ).strip()
                _tableau_render_publish_outcome_message(publish_report)
            except Exception as exc:
                publish_error_message = _tableau_format_user_publish_failure(exc)
                st.session_state.sql_model_assistant_consumer_workbook_path = ""
                st.session_state.sql_model_assistant_tableau_last_publish_error = publish_error_message
                st.error(publish_error_message)

    publish_report = st.session_state.sql_model_assistant_tableau_last_publish_report
    publish_error = str(st.session_state.sql_model_assistant_tableau_last_publish_error or "").strip()
    if publish_report:
        with st.expander("Publish report", expanded=False):
            st.json(publish_report)
    elif publish_error:
        with st.expander("Publish error", expanded=True):
            st.error(publish_error)


def _safe_output_twb_name(value: str) -> str:
    name = Path(str(value or "validated_semantic_model.twb").strip()).name or "validated_semantic_model.twb"
    if not name.lower().endswith(".twb"):
        name = f"{name}.twb"
    return name


def _tableau_render_publish_outcome_message(publish_report: dict[str, Any]) -> None:
    status = str(publish_report.get("status") or "").strip().lower()
    datasource_name = str(publish_report.get("datasource_name") or "-")
    workbook_name = str(publish_report.get("workbook_name") or "-")
    consumer_workbook_path = str(publish_report.get("consumer_workbook_path") or "").strip()

    if status == "datasource_published_workbook_publish_skipped_for_validation":
        linked_artifact = str(
            publish_report.get("saved_publish_artifacts", {}).get("linked_workbook")
            if isinstance(publish_report.get("saved_publish_artifacts"), dict)
            else ""
        ).strip()
        guidance = (
            "Datasource published successfully. Workbook publish is intentionally skipped for validation. "
            f"Datasource '{datasource_name}' is available."
        )
        if linked_artifact:
            guidance += f" Linked workbook artifact: {linked_artifact}."
        if consumer_workbook_path:
            guidance += f" Final consumer workbook: {consumer_workbook_path}."
        st.info(guidance)
        return

    if status == "datasource_published_workbook_publish_forbidden":
        details = str(publish_report.get("workbook_publish_error") or "").strip()
        linked_artifact = str(
            publish_report.get("saved_publish_artifacts", {}).get("linked_workbook")
            if isinstance(publish_report.get("saved_publish_artifacts"), dict)
            else ""
        ).strip()
        guidance = (
            "Datasource published successfully, but Tableau denied workbook publish permissions. "
            f"Datasource '{datasource_name}' is available."
        )
        if linked_artifact:
            guidance += f" Linked workbook artifact: {linked_artifact}."
        if consumer_workbook_path:
            guidance += f" Final consumer workbook: {consumer_workbook_path}."
        if details:
            guidance += f" Details: {details}"
        st.warning(guidance)
        return

    st.success(
        "Tableau Cloud publish succeeded: "
        f"datasource '{datasource_name}', "
        f"workbook '{workbook_name}'."
    )


def _tableau_format_user_publish_failure(error: Exception | str) -> str:
    details = str(error or "").strip() or "Unknown Tableau Cloud error."
    if _tableau_is_transient_publish_error(details):
        return (
            "Tableau Cloud publish failed because Tableau returned a temporary server error "
            "(503/upstream connection termination). Your TWB was saved successfully. "
            "Wait 1-2 minutes, then click 'Publish to Tableau Cloud Now' to retry without regenerating.\n\n"
            f"Details: {details}"
        )
    if "401001" in details and "signin error" in details.lower():
        return (
            "Tableau Cloud publish failed because Tableau rejected the sign-in. "
            "Verify `server_url`, `site_content_url`, and the credentials in your active config. "
            "If your Tableau Cloud site uses MFA/SSO, switch the config to PAT authentication.\n\n"
            f"Details: {details}"
        )
    return f"Tableau Cloud publish failed: {details}"


def _tableau_resolve_source_twb_for_publish(generated_twb_path: Path) -> Path:
    target_name = str(generated_twb_path.name or "").strip().lower()
    if target_name != "validated_semantic_model.twb":
        return generated_twb_path

    candidates: list[Path] = []
    env_override = str(os.getenv("SQL_MODEL_ASSISTANT_CANONICAL_TWB") or "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.extend(
        [
            *_regional_sales_semantic_workbook_candidates(),
            OUTPUT_DIR / "template_semantic_model - Copy.twb",
            ROOT_DIR / "config" / "RegionalSales.canonical.twb",
            OUTPUT_DIR / "template_semantic_model.twb",
        ]
    )

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if resolved.is_file():
            return resolved

    return generated_twb_path


def _is_active_regionalsales_report() -> bool:
    file_name = str(st.session_state.get("sql_model_assistant_rdl_filename", "") or "").strip().lower()
    if Path(file_name).stem == "regionalsales":
        return True

    report = st.session_state.get("sql_model_assistant_rdl_report", {})
    if isinstance(report, dict):
        report_name = str(report.get("name") or report.get("report_name") or "").strip().lower()
        if Path(report_name).stem == "regionalsales" or report_name == "regionalsales":
            return True

    return False


def _regional_sales_semantic_workbook_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_override = str(os.getenv("REGIONALSALES_PUBLISH_WORKBOOK_TWB") or "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.extend(
        [
            OUTPUT_DIR / "Book1.twb",
            OUTPUT_DIR / "book1.twb",
            PROJECT_ROOT / "outputs" / "Book1.twb",
            PROJECT_ROOT / "outputs" / "book1.twb",
        ]
    )
    return candidates


def _regional_sales_visual_workbook_candidates() -> list[Path]:
    candidates: list[Path] = [
        OUTPUT_DIR / "converted_report_perfect.twb",
        PROJECT_ROOT / "outputs" / "converted_report_perfect.twb",
        PROJECT_ROOT / "converted_report_perfect.twb",
    ]
    env_override = str(os.getenv("REGIONALSALES_VISUAL_TWB") or "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())
    return candidates


def _first_existing_twb(candidates: list[Path]) -> Path | None:
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if resolved.suffix.lower() != ".twb" or not resolved.is_file():
            continue
        key = _template_path_key(resolved)
        if key in seen:
            continue
        seen.add(key)
        return resolved
    return None


def _run_tableau_cloud_publish_workflow(
    generated_twb_path: Path,
    timestamp_utc: str | None = None,
) -> dict[str, Any]:
    if not generated_twb_path.exists():
        raise FileNotFoundError(f"Generated TWB file not found: {generated_twb_path}")

    canonical_generated_source_path = _tableau_resolve_generated_source_content_path(
        generated_twb_path,
        "generated_source_twb",
    )
    datasource_source_twb_path = canonical_generated_source_path

    cfg_defaults = _load_tableau_publish_defaults(str(st.session_state.sql_model_assistant_llm_config or ""))

    raw_server_url = str(st.session_state.sql_model_assistant_tableau_server_url or "").strip()
    server_url = _tableau_normalize_server_url(raw_server_url)
    site_content_url = str(st.session_state.sql_model_assistant_tableau_site_content_url or "").strip()
    project_name = str(st.session_state.sql_model_assistant_tableau_project_name or "").strip()
    datasource_project_name = str(
        getattr(st.session_state, "sql_model_assistant_tableau_datasource_project_name", "")
        or cfg_defaults.get("datasource_project_name")
        or TABLEAU_DATASOURCE_PROJECT_NAME
    ).strip()
    workbook_project_name = str(
        getattr(st.session_state, "sql_model_assistant_tableau_workbook_project_name", "")
        or cfg_defaults.get("workbook_project_name")
        or TABLEAU_WORKBOOK_PROJECT_NAME
    ).strip()
    username = str(st.session_state.sql_model_assistant_tableau_username or "").strip()
    password = str(st.session_state.sql_model_assistant_tableau_password or "")
    auth_method = str(
        getattr(st.session_state, "sql_model_assistant_tableau_auth_method", "")
        or cfg_defaults.get("auth_method")
        or "username_password"
    ).strip().lower()
    pat_name = str(
        getattr(st.session_state, "sql_model_assistant_tableau_pat_name", "")
        or cfg_defaults.get("pat_name")
        or ""
    ).strip()
    pat_secret = str(
        getattr(st.session_state, "sql_model_assistant_tableau_pat_secret", "")
        or cfg_defaults.get("pat_secret")
        or ""
    )
    datasource_publish_mode = str(
        getattr(st.session_state, "sql_model_assistant_tableau_datasource_publish_mode", "")
        or cfg_defaults.get("datasource_publish_mode")
        or ""
    ).strip().lower()
    if not datasource_publish_mode:
        datasource_publish_mode = "extract" if bool(cfg_defaults.get("build_hyper_extract", False)) else "live_tds"
    publish_extract = datasource_publish_mode == "extract"
    try:
        hyper_max_rows_per_table = int(cfg_defaults.get("hyper_max_rows_per_table") or 0)
    except (TypeError, ValueError):
        hyper_max_rows_per_table = 0
    source_datasource_name = str(st.session_state.sql_model_assistant_tableau_source_datasource_name or "").strip()
    empty_workbook_template_path = str(
        st.session_state.sql_model_assistant_tableau_empty_workbook_template_path or ""
    ).strip()
    visual_source_twb_path_value = str(
        st.session_state.sql_model_assistant_tableau_visual_source_twb_path or ""
    ).strip()

    # If stale sidebar state still points to Tableau Public, prefer the config file value.
    cfg_server_url = str(cfg_defaults.get("server_url") or "").strip()
    if "public.tableau.com" in server_url.lower() and cfg_server_url and "public.tableau.com" not in cfg_server_url.lower():
        server_url = _tableau_normalize_server_url(cfg_server_url)
        st.session_state.sql_model_assistant_tableau_server_url = server_url

    if not site_content_url:
        inferred_site_content_url = _tableau_extract_site_content_url(raw_server_url)
        if inferred_site_content_url:
            site_content_url = inferred_site_content_url
            st.session_state.sql_model_assistant_tableau_site_content_url = site_content_url

    if not site_content_url and cfg_defaults.get("site_content_url"):
        site_content_url = str(cfg_defaults.get("site_content_url") or "").strip()
        st.session_state.sql_model_assistant_tableau_site_content_url = site_content_url
    if not project_name and cfg_defaults.get("project_name"):
        project_name = str(cfg_defaults.get("project_name") or "").strip()
        st.session_state.sql_model_assistant_tableau_project_name = project_name
    if not datasource_project_name:
        datasource_project_name = TABLEAU_DATASOURCE_PROJECT_NAME
    if not workbook_project_name:
        workbook_project_name = TABLEAU_WORKBOOK_PROJECT_NAME
    st.session_state.sql_model_assistant_tableau_datasource_project_name = datasource_project_name
    st.session_state.sql_model_assistant_tableau_workbook_project_name = workbook_project_name
    if not username and cfg_defaults.get("username"):
        username = str(cfg_defaults.get("username") or "").strip()
        st.session_state.sql_model_assistant_tableau_username = username
    if not password and cfg_defaults.get("password"):
        password = str(cfg_defaults.get("password") or "")
        st.session_state.sql_model_assistant_tableau_password = password
    if not source_datasource_name and cfg_defaults.get("source_datasource_name"):
        source_datasource_name = str(cfg_defaults.get("source_datasource_name") or "").strip()
        st.session_state.sql_model_assistant_tableau_source_datasource_name = source_datasource_name
    if not empty_workbook_template_path and cfg_defaults.get("empty_workbook_template_path"):
        empty_workbook_template_path = str(cfg_defaults.get("empty_workbook_template_path") or "").strip()
        st.session_state.sql_model_assistant_tableau_empty_workbook_template_path = empty_workbook_template_path
    if not visual_source_twb_path_value and cfg_defaults.get("visual_source_twb_path"):
        visual_source_twb_path_value = str(cfg_defaults.get("visual_source_twb_path") or "").strip()
        _schedule_visual_source_sidebar_sync(visual_source_twb_path_value)

    linked_workbook_source_path = canonical_generated_source_path
    linked_workbook_source_mode = (
        "generated_twb"
        if _template_path_key(canonical_generated_source_path) == _template_path_key(generated_twb_path)
        else "generated_twb_canonical"
    )
    linked_workbook_source_datasource_name = source_datasource_name

    visual_source_twb_path = _tableau_resolve_visual_source_twb_path(
        generated_twb_path=generated_twb_path,
        configured_path=visual_source_twb_path_value,
    )
    if visual_source_twb_path is not None:
        linked_workbook_source_mode = f"{linked_workbook_source_mode}_with_visual_overlay"

    if not server_url:
        raise ValueError("Missing Tableau Cloud Server URL.")
    if "public.tableau.com" in server_url.lower():
        raise ValueError("Tableau Public is not supported for this REST publish workflow. Use Tableau Cloud URL.")

    if auth_method == "pat":
        if not pat_name or not pat_secret:
            raise ValueError(
                "Missing Tableau Cloud PAT settings. Set `auth_method`/`auth_type` to `pat` and provide "
                "`pat_name` plus `pat_secret` in the active config or environment."
            )
    else:
        if not username or not password:
            raise ValueError("Missing Tableau Cloud username/password in SQL Model Assistant sidebar settings.")

    try:
        import tableauserverclient as TSC
    except Exception as exc:
        raise RuntimeError(
            "tableauserverclient is required for Tableau Cloud publish from SQL Model Assistant. "
            "Install it with: pip install tableauserverclient"
        ) from exc

    timestamp_utc = timestamp_utc or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    datasource_name = _tableau_timestamped_name("validated_semantic_model", timestamp_utc)
    workbook_name = _tableau_timestamped_name(generated_twb_path.stem, timestamp_utc)
    consumer_workbook_name = _tableau_timestamped_name(
        f"{generated_twb_path.stem}_connected_to_published_datasource",
        timestamp_utc,
    )
    extract_report: dict[str, Any] | None = None
    workbook_extract_report: dict[str, Any] = {}
    saved_publish_artifacts: dict[str, str] = {}
    linked_workbook_artifact = ""
    extract_workbook_artifact = ""
    consumer_workbook_path = ""
    datasource_project_id = ""
    workbook_project_id = ""
    generated_source_artifact = _tableau_copy_publish_artifact(
        source_path=generated_twb_path,
        timestamp_utc=timestamp_utc,
        label="generated_source_twb",
    )
    if generated_source_artifact:
        saved_publish_artifacts["generated_source_twb"] = generated_source_artifact
    if visual_source_twb_path is not None:
        visual_source_artifact = _tableau_copy_publish_artifact(
            source_path=visual_source_twb_path,
            timestamp_utc=timestamp_utc,
            label="visual_source_twb",
        )
        if visual_source_artifact:
            saved_publish_artifacts["visual_source_twb"] = visual_source_artifact

    with _TableauPublishWorkDirectory(timestamp_utc) as tmp_dir:
        if publish_extract:
            datasource_package_path, resolved_source_datasource_name, extract_report = (
                _build_extract_datasource_package_for_publish(
                    source_twb_path=datasource_source_twb_path,
                    output_dir=tmp_dir,
                    datasource_name=datasource_name,
                    source_datasource_name=source_datasource_name,
                    hyper_max_rows_per_table=hyper_max_rows_per_table or None,
                )
            )
            datasource_package_label = "extract_datasource_tdsx_for_tableau_cloud"
        else:
            datasource_package_path, resolved_source_datasource_name = _build_live_datasource_tds_for_publish(
                source_twb_path=datasource_source_twb_path,
                output_dir=tmp_dir,
                datasource_name=datasource_name,
                source_datasource_name=source_datasource_name,
            )
            datasource_package_label = "live_datasource_tds_for_tableau_cloud"
        if linked_workbook_source_mode.startswith("generated_twb"):
            linked_workbook_source_datasource_name = resolved_source_datasource_name
        datasource_package_artifact = _tableau_copy_publish_artifact(
            source_path=datasource_package_path,
            timestamp_utc=timestamp_utc,
            label=datasource_package_label,
        )
        if datasource_package_artifact:
            saved_publish_artifacts["datasource_package"] = datasource_package_artifact

        if auth_method == "pat":
            auth = TSC.PersonalAccessTokenAuth(
                token_name=pat_name,
                personal_access_token=pat_secret,
                site_id=site_content_url,
            )
        else:
            auth = TSC.TableauAuth(username=username, password=password, site_id=site_content_url)
        server = TSC.Server(server_url, use_server_version=True)
        _tableau_disable_environment_proxies(server)
        try:
            server.add_http_options(
                {
                    "timeout": 600,
                    "proxies": {
                        "http": None,
                        "https": None,
                    },
                }
            )
        except Exception:
            pass

        with server.auth.sign_in(auth):
            datasource_project_id = _tableau_resolve_project_id(
                server=server,
                tsc_module=TSC,
                project_name=datasource_project_name,
            )
            workbook_project_id = _tableau_resolve_project_id(
                server=server,
                tsc_module=TSC,
                project_name=workbook_project_name,
            )

            datasource_item = TSC.DatasourceItem(project_id=datasource_project_id, name=datasource_name)
            if not publish_extract:
                # Live datasource mode requires Tableau Bridge/private network routing.
                datasource_item.use_remote_query_agent = True
            try:
                published_datasource = _tableau_publish_datasource(
                    server=server,
                    tsc_module=TSC,
                    datasource_item=datasource_item,
                    datasource_path=datasource_package_path,
                    publish_mode=TSC.Server.PublishMode.CreateNew,
                )
            except Exception as datasource_publish_exc:
                recovered_datasource = None
                if _tableau_is_transient_publish_error(datasource_publish_exc):
                    recovered_datasource = _tableau_find_published_datasource_by_name(
                        server=server,
                        tsc_module=TSC,
                        datasource_name=datasource_name,
                        project_id=datasource_project_id,
                    )

                if recovered_datasource is not None:
                    published_datasource = recovered_datasource
                    st.warning(
                        "Tableau Cloud returned a transient error after datasource upload, "
                        "but the datasource now exists on the site. Continuing with linked workbook artifact generation."
                    )
                else:
                    debug_artifact_path = _tableau_copy_publish_artifact_for_debug(
                        source_path=datasource_package_path,
                        timestamp_utc=timestamp_utc,
                        label="failed_datasource_upload",
                    )
                    debug_detail = ""
                    if debug_artifact_path:
                        debug_detail = (
                            " Saved the generated datasource package for manual upload/testing at: "
                            f"{debug_artifact_path}"
                        )
                    if saved_publish_artifacts:
                        debug_detail += (
                            " Persistent Tableau publish artifacts: "
                            + "; ".join(
                                f"{name}={path}" for name, path in saved_publish_artifacts.items()
                            )
                        )
                    raise RuntimeError(
                        "Failed to publish datasource to Tableau Cloud. "
                        f"Error: {datasource_publish_exc}.{debug_detail}"
                    ) from datasource_publish_exc

            linked_workbook_path = tmp_dir / f"{_tableau_safe_name(consumer_workbook_name)}.twb"
            _build_linked_workbook_for_published_datasource(
                source_twb_path=generated_twb_path,
                output_twb_path=linked_workbook_path,
                published_datasource=published_datasource,
                server_url=server_url,
                site_content_url=site_content_url,
                source_datasource_name=linked_workbook_source_datasource_name,
            )

            linked_workbook_artifact = _tableau_copy_publish_artifact(
                source_path=linked_workbook_path,
                timestamp_utc=timestamp_utc,
                label="consumer_workbook_linked_to_published_datasource",
            )
            if linked_workbook_artifact:
                saved_publish_artifacts["linked_workbook"] = linked_workbook_artifact
                saved_publish_artifacts["consumer_workbook"] = linked_workbook_artifact

            consumer_output_override = str(
                getattr(st.session_state, "sql_model_assistant_tableau_consumer_output_path", "") or ""
            ).strip()
            consumer_output_path = (
                Path(consumer_output_override).expanduser()
                if consumer_output_override
                else OUTPUT_DIR / f"{_tableau_safe_name(generated_twb_path.stem)}_consumer_final.twb"
            )
            if consumer_output_path.suffix.lower() != linked_workbook_path.suffix.lower():
                consumer_output_path = consumer_output_path.with_suffix(linked_workbook_path.suffix)
            consumer_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(linked_workbook_path, consumer_output_path)
            consumer_workbook_path = str(consumer_output_path)

            workbook_publish_path, workbook_extract_report = _build_extract_workbook_package_for_publish(
                source_twb_path=generated_twb_path,
                output_dir=tmp_dir,
                workbook_name=workbook_name,
                hyper_max_rows_per_table=hyper_max_rows_per_table or None,
            )

            extract_workbook_artifact = _tableau_copy_publish_artifact(
                source_path=workbook_publish_path,
                timestamp_utc=timestamp_utc,
                label="extract_workbook_twbx_for_tableau_cloud",
            )
            if extract_workbook_artifact:
                saved_publish_artifacts["extract_workbook"] = extract_workbook_artifact
                saved_publish_artifacts["published_workbook_package"] = extract_workbook_artifact

            published_workbook = None
            workbook_publish_status = "pending"
            workbook_publish_error = ""
            workbook_item = TSC.WorkbookItem(project_id=workbook_project_id, name=workbook_name)
            try:
                published_workbook = _tableau_publish_workbook_with_fallback(
                    server=server,
                    workbook_item=workbook_item,
                    workbook_path=workbook_publish_path,
                    publish_mode=TSC.Server.PublishMode.CreateNew,
                )
                workbook_publish_status = "published"
            except Exception as publish_exc:
                message = str(publish_exc)
                if not _tableau_should_retry_packaged_publish(message):
                    workbook_publish_error = message
                    if _tableau_is_permission_error(publish_exc):
                        workbook_publish_status = "forbidden"
                    else:
                        raise RuntimeError(
                            "Datasource published, but final Tableau workbook publish failed. "
                            f"Error: {publish_exc}"
                        ) from publish_exc
                else:
                    try:
                        published_workbook = _tableau_publish_workbook_with_fallback(
                            server=server,
                            workbook_item=workbook_item,
                            workbook_path=workbook_publish_path,
                            publish_mode=TSC.Server.PublishMode.CreateNew,
                        )
                        workbook_publish_status = "published"
                        workbook_publish_error = ""
                    except Exception as twbx_publish_exc:
                        workbook_publish_error = str(twbx_publish_exc)
                        if _tableau_is_permission_error(twbx_publish_exc):
                            workbook_publish_status = "forbidden"
                        else:
                            raise RuntimeError(
                                "Datasource published, but final Tableau workbook publish failed. "
                                f"Error: {twbx_publish_exc}"
                            ) from twbx_publish_exc

    overall_status = "published"
    if workbook_publish_status == "forbidden":
        overall_status = "datasource_published_workbook_publish_forbidden"
    publish_message = (
        "Tableau Cloud publish completed: "
        f"datasource in '{datasource_project_name}' and final workbook in '{workbook_project_name}'. "
        "A connected consumer workbook was generated for download and was not published."
    )
    if workbook_publish_status == "forbidden":
        publish_message = (
            "Datasource published to Tableau Cloud, but Tableau denied final workbook publish permissions. "
            f"Datasource project: '{datasource_project_name}'. Workbook project: '{workbook_project_name}'."
        )

    return {
        "status": overall_status,
        "message": publish_message,
        "timestamp_utc": timestamp_utc,
        "server_url": server_url,
        "site_content_url": site_content_url,
        "project_id": datasource_project_id,
        "project_name": datasource_project_name,
        "datasource_project_id": datasource_project_id,
        "datasource_project_name": datasource_project_name,
        "workbook_project_id": workbook_project_id,
        "workbook_project_name": workbook_project_name,
        "datasource_publish_mode": datasource_publish_mode,
        "consumer_workbook_source_mode": linked_workbook_source_mode,
        "consumer_workbook_source_path": str(linked_workbook_source_path),
        "workbook_publish_status": workbook_publish_status,
        "workbook_publish_error": workbook_publish_error,
        "workbook_publish_attempted": True,
        "workbook_publish_mode": "extract_twbx",
        "consumer_workbook_publish_status": "not_published",
        "consumer_workbook_publish_attempted": False,
        "linked_workbook_artifact": linked_workbook_artifact,
        "extract_workbook_artifact": extract_workbook_artifact,
        "consumer_workbook_path": consumer_workbook_path,
        "extract_report": extract_report or {},
        "workbook_extract_report": workbook_extract_report,
        "saved_publish_artifacts": saved_publish_artifacts,
        "source_datasource_name": resolved_source_datasource_name,
        "datasource_name": getattr(published_datasource, "name", datasource_name),
        "datasource_id": getattr(published_datasource, "id", None),
        "datasource_content_url": getattr(published_datasource, "content_url", None),
        "datasource_webpage_url": getattr(published_datasource, "webpage_url", None),
        "workbook_name": getattr(published_workbook, "name", workbook_name),
        "workbook_id": getattr(published_workbook, "id", None),
        "workbook_content_url": getattr(published_workbook, "content_url", None),
        "workbook_webpage_url": getattr(published_workbook, "webpage_url", None),
    }


def _build_datasource_tds_from_workbook(
    source_twb_path: Path,
    output_tds_path: Path,
    datasource_name: str,
    source_datasource_name: str,
    strict_mode: bool = False,
) -> tuple[Path, str]:
    root = ET.fromstring(source_twb_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(root)

    datasources_node = _tableau_find_first_child(root, "datasources")
    if datasources_node is None:
        raise ValueError("Generated TWB has no <datasources> section.")

    datasource_nodes = [
        node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
    ]
    if not datasource_nodes:
        raise ValueError("Generated TWB has no datasource nodes to publish.")

    selected = _tableau_select_datasource_node(datasource_nodes, source_datasource_name)
    selected_name = str(selected.attrib.get("name") or selected.attrib.get("caption") or "").strip()

    datasource_payload = copy.deepcopy(selected)
    _tableau_prepare_datasource_payload_for_publish(
        datasource_payload=datasource_payload,
        datasource_name=datasource_name,
        strict_mode=strict_mode,
    )

    output_tds_path.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = ET.tostring(datasource_payload, encoding="utf-8", xml_declaration=True)
    output_tds_path.write_bytes(output_bytes)
    return output_tds_path, selected_name


def _build_extract_datasource_package_for_publish(
    source_twb_path: Path,
    output_dir: Path,
    datasource_name: str,
    source_datasource_name: str,
    hyper_max_rows_per_table: int | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    publish_context = st.session_state.get("sql_model_assistant_tableau_publish_context", {})
    if not isinstance(publish_context, dict):
        raise ValueError(
            "Missing SQL Model Assistant publish context for extract mode. "
            "Regenerate the TWB in this session before publishing extract."
        )

    data_source = publish_context.get("data_source")
    db_catalog = publish_context.get("db_catalog")
    if not isinstance(data_source, dict) or not data_source:
        raise ValueError(
            "Extract publish requires datasource metadata from the current SQL Model Assistant run."
        )
    if not isinstance(db_catalog, dict):
        raise ValueError(
            "Extract publish requires DB catalog metadata. "
            "Make sure schema validation/TWB generation succeeded with DB introspection."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _tableau_safe_name(datasource_name)

    live_tds_path, selected_name = _build_datasource_tds_from_workbook(
        source_twb_path=source_twb_path,
        output_tds_path=output_dir / f"{safe_name}_live.tds",
        datasource_name=datasource_name,
        source_datasource_name=source_datasource_name,
        strict_mode=True,
    )

    hyper_path = output_dir / f"{safe_name}.hyper"
    extract_report = build_hyper_extract_from_catalog(
        output_hyper_path=hyper_path,
        data_sources=[data_source],
        db_catalog=db_catalog,
        max_rows_per_table=hyper_max_rows_per_table,
    )
    if str(extract_report.get("status", "")).strip().lower() != "created":
        reason = str(extract_report.get("reason") or extract_report.get("error") or "unknown error").strip()
        raise RuntimeError(f"Failed to build extract for datasource publish: {reason}")

    extract_tds_path = output_dir / f"{safe_name}.tds"
    _tableau_rewrite_datasource_tds_for_hyper_extract(
        source_tds_path=live_tds_path,
        output_tds_path=extract_tds_path,
        hyper_relative_path=f"Data/Extracts/{hyper_path.name}",
    )

    extract_tdsx_path = output_dir / f"{safe_name}.tdsx"
    _tableau_package_tds_and_hyper_as_tdsx(
        tds_path=extract_tds_path,
        hyper_path=hyper_path,
        tdsx_path=extract_tdsx_path,
    )
    return extract_tdsx_path, selected_name, extract_report


def _build_extract_workbook_package_for_publish(
    source_twb_path: Path,
    output_dir: Path,
    workbook_name: str,
    hyper_max_rows_per_table: int | None = None,
) -> tuple[Path, dict[str, Any]]:
    publish_context = st.session_state.get("sql_model_assistant_tableau_publish_context", {})
    if not isinstance(publish_context, dict):
        raise ValueError(
            "Missing SQL Model Assistant publish context for workbook extract mode. "
            "Regenerate the TWB in this session before publishing the final workbook."
        )

    data_source = publish_context.get("data_source")
    db_catalog = publish_context.get("db_catalog")
    if not isinstance(data_source, dict) or not data_source:
        raise ValueError("Workbook extract publish requires datasource metadata from the current run.")
    if not isinstance(db_catalog, dict):
        raise ValueError(
            "Workbook extract publish requires DB catalog metadata. "
            "Make sure schema validation/TWB generation succeeded with DB introspection."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _tableau_safe_name(workbook_name)
    hyper_path = output_dir / f"{safe_name}_workbook.hyper"
    extract_report = build_hyper_extract_from_catalog(
        output_hyper_path=hyper_path,
        data_sources=[data_source],
        db_catalog=db_catalog,
        max_rows_per_table=hyper_max_rows_per_table,
    )
    if str(extract_report.get("status", "")).strip().lower() != "created":
        reason = str(extract_report.get("reason") or extract_report.get("error") or "unknown error").strip()
        raise RuntimeError(f"Failed to build extract for final workbook publish: {reason}")

    extract_twb_path = output_dir / f"{safe_name}_extract.twb"
    rewrite_report = rewrite_workbook_for_hyper(
        source_twb_path=source_twb_path,
        output_twb_path=extract_twb_path,
        hyper_relative_path=f"Data/Extracts/{hyper_path.name}",
    )
    _tableau_remove_unsupported_extract_fields_from_workbook(extract_twb_path)

    extract_twbx_path = output_dir / f"{safe_name}.twbx"
    package_report = build_twbx_package(
        twb_path=extract_twb_path,
        output_twbx_path=extract_twbx_path,
        hyper_path=hyper_path,
    )

    return extract_twbx_path, {
        **extract_report,
        "status": "created",
        "workbook_rewrite": rewrite_report,
        "twbx_package": package_report,
        "publish_input_path": str(extract_twbx_path),
        "workbook_publish_mode": "extract_twbx",
    }


def _build_live_datasource_tds_for_publish(
    source_twb_path: Path,
    output_dir: Path,
    datasource_name: str,
    source_datasource_name: str,
) -> tuple[Path, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _tableau_safe_name(datasource_name)
    return _build_datasource_tds_from_workbook(
        source_twb_path=source_twb_path,
        output_tds_path=output_dir / f"{safe_name}.tds",
        datasource_name=datasource_name,
        source_datasource_name=source_datasource_name,
        strict_mode=True,
    )


def _tableau_rewrite_datasource_tds_for_hyper_extract(
    source_tds_path: Path,
    output_tds_path: Path,
    hyper_relative_path: str,
) -> Path:
    if not source_tds_path.exists():
        raise FileNotFoundError(f"TDS file not found for extract rewrite: {source_tds_path}")

    root = ET.fromstring(source_tds_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(root)
    root.attrib["hasconnection"] = "true"

    renamed_connections: dict[str, str] = {}

    for connection_node in root.findall(".//connection"):
        current_class = str(connection_node.attrib.get("class", "") or "").strip().lower()
        if current_class == "federated":
            continue

        parent = _tableau_find_parent(root, connection_node)
        if parent is not None and _tableau_local_name(parent.tag) == "named-connection":
            old_name = str(parent.attrib.get("name", "") or "").strip()
            new_name = "hyper.extract"
            if old_name:
                renamed_connections[old_name] = new_name
            parent.attrib["name"] = new_name
            parent.attrib["caption"] = "Extract"

        connection_node.attrib.clear()
        connection_node.set("class", "hyper")
        connection_node.set("dbname", hyper_relative_path)

    for relation_node in root.findall(".//relation"):
        relation_type = str(relation_node.attrib.get("type", "") or "").strip().lower()
        if relation_type != "table":
            continue
        relation_name = str(relation_node.attrib.get("name", "") or "").strip()
        if not relation_name:
            relation_name = _table_leaf_from_table_reference(str(relation_node.attrib.get("table", "") or ""))
        if not relation_name:
            continue
        connection_name = str(relation_node.attrib.get("connection", "") or "").strip()
        if connection_name in renamed_connections:
            relation_node.set("connection", renamed_connections[connection_name])
        relation_node.set("table", f"[Extract].[{relation_name}]")

    _tableau_remove_unsupported_extract_fields(root)
    _tableau_upsert_extract_metadata(root)
    _tableau_reorder_datasource_children(root)

    output_tds_path.parent.mkdir(parents=True, exist_ok=True)
    output_tds_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return output_tds_path


def _tableau_remove_unsupported_extract_fields_from_workbook(workbook_path: Path) -> None:
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found for extract cleanup: {workbook_path}")

    root = ET.fromstring(workbook_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(root)

    datasources_node = _tableau_find_first_child(root, "datasources")
    if datasources_node is not None:
        for datasource_node in list(datasources_node):
            if _tableau_local_name(datasource_node.tag) == "datasource":
                _tableau_remove_unsupported_extract_fields(datasource_node)

    workbook_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _tableau_remove_unsupported_extract_fields(datasource_node: ET.Element) -> None:
    unsupported_names = _tableau_extract_unsupported_field_names(datasource_node)
    if not unsupported_names:
        return

    connection_node = _tableau_find_first_child(datasource_node, "connection")
    metadata_node = _tableau_find_first_child(connection_node, "metadata-records") if connection_node is not None else None
    if metadata_node is not None:
        for record_node in list(metadata_node):
            if _tableau_local_name(record_node.tag) != "metadata-record":
                continue
            local_name = _tableau_child_text(record_node, "local-name")
            remote_name = _tableau_child_text(record_node, "remote-name")
            parent_name = _tableau_child_text(record_node, "parent-name")
            full_remote = f"{parent_name}.{remote_name}" if parent_name and remote_name else ""
            if local_name in unsupported_names or remote_name in unsupported_names or full_remote in unsupported_names:
                metadata_node.remove(record_node)

    cols_node = _tableau_find_first_child(connection_node, "cols") if connection_node is not None else None
    if cols_node is not None:
        for map_node in list(cols_node):
            if _tableau_local_name(map_node.tag) != "map":
                continue
            key = str(map_node.attrib.get("key") or "").strip()
            value = str(map_node.attrib.get("value") or "").strip()
            if key in unsupported_names or value in unsupported_names:
                cols_node.remove(map_node)

    for child in list(datasource_node):
        local_name = _tableau_local_name(child.tag)
        if local_name == "column" and str(child.attrib.get("name") or "").strip() in unsupported_names:
            datasource_node.remove(child)
        elif local_name == "column-instance" and str(child.attrib.get("column") or "").strip() in unsupported_names:
            datasource_node.remove(child)


def _tableau_extract_unsupported_field_names(datasource_node: ET.Element) -> set[str]:
    unsupported: set[str] = set()
    connection_node = _tableau_find_first_child(datasource_node, "connection")
    metadata_node = _tableau_find_first_child(connection_node, "metadata-records") if connection_node is not None else None
    if metadata_node is None:
        return unsupported

    for record_node in list(metadata_node):
        if _tableau_local_name(record_node.tag) != "metadata-record":
            continue
        if not _tableau_metadata_record_is_binary(record_node):
            continue
        local_name = _tableau_child_text(record_node, "local-name")
        remote_name = _tableau_child_text(record_node, "remote-name")
        parent_name = _tableau_child_text(record_node, "parent-name")
        if local_name:
            unsupported.add(local_name)
        if remote_name:
            unsupported.add(remote_name)
        if parent_name and remote_name:
            unsupported.add(f"{parent_name}.{remote_name}")
    return unsupported


def _tableau_metadata_record_is_binary(record_node: ET.Element) -> bool:
    binary_tokens = {"binary", "varbinary", "image", "rowversion", "sql_c_binary", "sql_binary", "sql_varbinary"}
    remote_type = _tableau_child_text(record_node, "remote-type").strip()
    if remote_type == "128":
        return True
    attributes_node = _tableau_find_first_child(record_node, "attributes")
    if attributes_node is None:
        return False
    for attribute_node in list(attributes_node):
        if _tableau_local_name(attribute_node.tag) != "attribute":
            continue
        raw = str(attribute_node.text or "").strip().strip('"').lower()
        normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
        if normalized in binary_tokens:
            return True
    return False


def _tableau_child_text(parent: ET.Element, child_name: str) -> str:
    child = _tableau_find_first_child(parent, child_name)
    return str(child.text or "").strip() if child is not None else ""


def _tableau_upsert_extract_metadata(datasource_node: ET.Element) -> ET.Element:
    extract_node = _tableau_find_first_child(datasource_node, "extract")
    if extract_node is None:
        extract_node = ET.SubElement(datasource_node, "extract")

    extract_node.attrib["enabled"] = "true"
    extract_node.attrib["units"] = "records"
    extract_node.attrib["count"] = "-1"
    return extract_node


def _tableau_find_parent(root: ET.Element, target: ET.Element) -> ET.Element | None:
    for parent in root.iter():
        for child in list(parent):
            if child is target:
                return parent
    return None


def _tableau_package_tds_and_hyper_as_tdsx(
    tds_path: Path,
    hyper_path: Path,
    tdsx_path: Path,
) -> Path:
    if not tds_path.exists():
        raise FileNotFoundError(f"TDS file not found for TDSX packaging: {tds_path}")
    if not hyper_path.exists():
        raise FileNotFoundError(f"Hyper file not found for TDSX packaging: {hyper_path}")

    tdsx_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tdsx_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(tds_path, tds_path.name)
        zf.write(hyper_path, f"Data/Extracts/{hyper_path.name}")

    return tdsx_path


def _tableau_prepare_datasource_payload_for_publish(
    datasource_payload: ET.Element,
    datasource_name: str,
    strict_mode: bool,
) -> None:
    datasource_payload.tag = "datasource"
    datasource_payload.attrib["name"] = datasource_name
    datasource_payload.attrib["caption"] = datasource_name
    datasource_payload.attrib.setdefault("inline", "true")
    datasource_payload.attrib.setdefault("version", "18.1")
    datasource_payload.attrib.pop("hasconnection", None)

    tags_to_remove = {"repository-location"}

    for child in [c for c in list(datasource_payload) if _tableau_local_name(c.tag) in tags_to_remove]:
        datasource_payload.remove(child)

    _tableau_remove_internal_table_object_columns(datasource_payload)
    if strict_mode:
        _tableau_remove_connection_metadata_object_ids(datasource_payload)
    _tableau_remove_migrated_data_from_datasource(datasource_payload)
    _tableau_reorder_datasource_children(datasource_payload)


def _tableau_remove_internal_table_object_columns(datasource_payload: ET.Element) -> None:
    removed_column_names: set[str] = set()

    for column_node in list(datasource_payload):
        if _tableau_local_name(column_node.tag) != "column":
            continue
        datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
        name_attr = str(column_node.attrib.get("name", "") or "").strip()
        if datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
            if name_attr:
                removed_column_names.add(name_attr)
            datasource_payload.remove(column_node)

    for column_instance_node in list(datasource_payload):
        if _tableau_local_name(column_instance_node.tag) != "column-instance":
            continue
        column_name = str(column_instance_node.attrib.get("column", "") or "").strip()
        if (
            column_name.startswith("[__tableau_internal_object_id__].")
            or column_name in removed_column_names
        ):
            datasource_payload.remove(column_instance_node)


def _tableau_remove_connection_metadata_object_ids(datasource_payload: ET.Element) -> None:
    connection_node = _tableau_find_first_child(datasource_payload, "connection")
    if connection_node is None:
        return

    metadata_node = _tableau_find_first_child(connection_node, "metadata-records")
    if metadata_node is None:
        return

    for record_node in list(metadata_node):
        if _tableau_local_name(record_node.tag) != "metadata-record":
            continue
        for child in list(record_node):
            if _tableau_local_name(child.tag) == "object-id":
                record_node.remove(child)


def _tableau_resolve_visual_source_twb_path(
    generated_twb_path: Path,
    configured_path: str,
) -> Path | None:
    candidates: list[Path] = []

    flux1_visual_workbook_path = str(
        st.session_state.get("sql_model_assistant_flux1_visual_workbook_path", "") or ""
    ).strip()
    if flux1_visual_workbook_path:
        candidates.append(Path(flux1_visual_workbook_path).expanduser())

    configured_value = str(configured_path or "").strip()
    if configured_value:
        configured_candidate = Path(configured_value).expanduser()
        if not configured_candidate.is_absolute():
            candidates.append(PROJECT_ROOT / configured_candidate)
            configured_candidate = ROOT_DIR / configured_candidate
        candidates.append(configured_candidate)

    if _is_active_regionalsales_report():
        candidates.extend(_regional_sales_visual_workbook_candidates())

    candidates.extend(
        [
            OUTPUT_DIR / "converted_report_perfect.twb",
            OUTPUT_DIR / "converted_report_hyper.twb",
            OUTPUT_DIR / "converted_report.twb",
        ]
    )

    seen: set[str] = set()
    generated_resolved = generated_twb_path.resolve()
    for candidate in candidates:
        candidate_key = str(candidate)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)

        if not candidate.exists() or candidate.suffix.lower() != ".twb":
            continue

        try:
            if candidate.resolve() == generated_resolved:
                continue
        except Exception:
            continue

        if _tableau_workbook_has_rich_visual_content(candidate):
            return candidate
    return None


def _tableau_workbook_has_rich_visual_content(twb_path: Path) -> bool:
    try:
        root = ET.fromstring(twb_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    _tableau_strip_namespaces(root)

    worksheets_node = _tableau_find_first_child(root, "worksheets")
    dashboards_node = _tableau_find_first_child(root, "dashboards")

    worksheet_count = 0
    dashboard_count = 0
    if worksheets_node is not None:
        worksheet_count = sum(1 for child in list(worksheets_node) if _tableau_local_name(child.tag) == "worksheet")
    if dashboards_node is not None:
        dashboard_count = sum(1 for child in list(dashboards_node) if _tableau_local_name(child.tag) == "dashboard")

    return dashboard_count > 0 or worksheet_count > 1


def _tableau_clone_linked_workbook_with_visual_content(
    linked_workbook_path: Path,
    visual_source_twb_path: Path,
    output_twb_path: Path,
    preferred_datasource_name: str,
) -> None:
    if not linked_workbook_path.exists():
        raise FileNotFoundError(f"Linked workbook not found for visual clone: {linked_workbook_path}")
    if not visual_source_twb_path.exists():
        raise FileNotFoundError(f"Visual source workbook not found: {visual_source_twb_path}")

    linked_root = ET.fromstring(linked_workbook_path.read_text(encoding="utf-8"))
    visual_root = ET.fromstring(visual_source_twb_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(linked_root)
    _tableau_strip_namespaces(visual_root)

    datasources_node = _tableau_find_first_child(linked_root, "datasources")
    if datasources_node is None:
        raise ValueError("Linked workbook has no <datasources> section.")

    datasource_nodes = [
        node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
    ]
    if not datasource_nodes:
        raise ValueError("Linked workbook has no datasource node.")

    selected_datasource = _tableau_select_datasource_node(datasource_nodes, preferred_datasource_name)
    target_datasource_name = str(selected_datasource.attrib.get("name", "") or "").strip()

    visual_datasource_names = _tableau_collect_workbook_datasource_names(visual_root)
    inserted_sections = _tableau_replace_visual_sections(
        target_root=linked_root,
        visual_source_root=visual_root,
    )
    _tableau_normalize_cloned_visual_sections(inserted_sections)
    _tableau_rebind_visual_datasource_references(
        section_roots=inserted_sections,
        source_datasource_names=visual_datasource_names,
        target_datasource_name=target_datasource_name,
    )
    _tableau_rebind_visual_field_references(
        section_roots=inserted_sections,
        visual_source_root=visual_root,
        source_datasource_names=visual_datasource_names,
        target_datasource_node=selected_datasource,
    )
    _tableau_remove_migrated_data_artifacts(linked_root, selected_datasource)

    output_twb_path.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("user", "http://www.tableausoftware.com/xml/user")
    output_twb_path.write_bytes(ET.tostring(linked_root, encoding="utf-8", xml_declaration=True))


def _tableau_collect_workbook_datasource_names(root: ET.Element) -> list[str]:
    datasources_node = _tableau_find_first_child(root, "datasources")
    if datasources_node is None:
        return []

    names: list[str] = []
    for datasource_node in list(datasources_node):
        if _tableau_local_name(datasource_node.tag) != "datasource":
            continue
        name = str(datasource_node.attrib.get("name", "") or "").strip()
        if name:
            names.append(name)
    return names


def _tableau_replace_visual_sections(
    target_root: ET.Element,
    visual_source_root: ET.Element,
) -> list[ET.Element]:
    section_order = ["worksheets", "dashboards", "stories", "windows", "thumbnails"]

    source_sections: dict[str, ET.Element] = {}
    for section_name in section_order:
        section_node = _tableau_find_first_child(visual_source_root, section_name)
        if section_node is not None:
            source_sections[section_name] = copy.deepcopy(section_node)

    for child in [c for c in list(target_root) if _tableau_local_name(c.tag) in set(section_order)]:
        target_root.remove(child)

    children = list(target_root)
    insert_index = len(children)
    datasources_index = next(
        (idx for idx, child in enumerate(children) if _tableau_local_name(child.tag) == "datasources"),
        None,
    )
    if datasources_index is not None:
        insert_index = datasources_index + 1

    inserted: list[ET.Element] = []
    for section_name in section_order:
        section_node = source_sections.get(section_name)
        if section_node is None:
            continue
        target_root.insert(insert_index, section_node)
        insert_index += 1
        inserted.append(section_node)
    return inserted


def _tableau_normalize_cloned_visual_sections(section_roots: list[ET.Element]) -> None:
    for section_root in section_roots:
        section_name = _tableau_local_name(section_root.tag)
        if section_name == "worksheets":
            for worksheet_node in list(section_root):
                if _tableau_local_name(worksheet_node.tag) != "worksheet":
                    continue
                _tableau_ensure_simple_id_node(worksheet_node)
        elif section_name == "dashboards":
            for dashboard_node in list(section_root):
                if _tableau_local_name(dashboard_node.tag) != "dashboard":
                    continue
                _tableau_ensure_simple_id_node(dashboard_node)
        elif section_name == "windows":
            for window_node in list(section_root):
                if _tableau_local_name(window_node.tag) != "window":
                    continue
                _tableau_ensure_simple_id_node(window_node)


def _tableau_ensure_simple_id_node(parent_node: ET.Element) -> None:
    existing_simple_id = _tableau_find_first_child(parent_node, "simple-id")
    if existing_simple_id is not None:
        return

    ET.SubElement(parent_node, "simple-id", attrib={"uuid": _tableau_new_simple_id_uuid()})


def _tableau_new_simple_id_uuid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def _tableau_rebind_visual_datasource_references(
    section_roots: list[ET.Element],
    source_datasource_names: list[str],
    target_datasource_name: str,
) -> None:
    target_name = str(target_datasource_name or "").strip()
    if not target_name or not section_roots:
        return

    source_names: list[str] = []
    seen: set[str] = set()
    for name in source_datasource_names:
        candidate = str(name or "").strip()
        if not candidate or candidate == target_name or candidate in seen:
            continue
        seen.add(candidate)
        source_names.append(candidate)

    if not source_names:
        return

    source_name_set = set(source_names)
    for section_root in section_roots:
        for node in section_root.iter():
            if _tableau_local_name(node.tag) == "datasource":
                current_name = str(node.attrib.get("name", "") or "").strip()
                if current_name in source_name_set:
                    node.attrib["name"] = target_name

            current_datasource = str(node.attrib.get("datasource", "") or "").strip()
            if current_datasource in source_name_set:
                node.attrib["datasource"] = target_name

            for attr_name, attr_value in list(node.attrib.items()):
                value = str(attr_value or "")
                replaced = _tableau_replace_datasource_reference_tokens(value, source_names, target_name)
                if replaced != value:
                    node.attrib[attr_name] = replaced

            if node.text:
                node.text = _tableau_replace_datasource_reference_tokens(node.text, source_names, target_name)


def _tableau_replace_datasource_reference_tokens(
    value: str,
    source_datasource_names: list[str],
    target_datasource_name: str,
) -> str:
    updated = str(value or "")
    target = str(target_datasource_name or "")
    if not updated or not target:
        return updated

    for source_name in source_datasource_names:
        source = str(source_name or "")
        if not source:
            continue
        if updated == source:
            updated = target
        updated = updated.replace(f"[{source}].", f"[{target}].")
        updated = updated.replace(f"'{source}'", f"'{target}'")
        updated = updated.replace(f'"{source}"', f'"{target}"')
    return updated


def _tableau_rebind_visual_field_references(
    section_roots: list[ET.Element],
    visual_source_root: ET.Element,
    source_datasource_names: list[str],
    target_datasource_node: ET.Element,
) -> None:
    field_reference_map = _tableau_build_visual_field_reference_map(
        visual_source_root=visual_source_root,
        source_datasource_names=source_datasource_names,
        target_datasource_node=target_datasource_node,
    )
    if not field_reference_map:
        return

    for section_root in section_roots:
        for node in section_root.iter():
            for attr_name, attr_value in list(node.attrib.items()):
                value = str(attr_value or "")
                replaced = _tableau_replace_field_reference_tokens(value, field_reference_map)
                if replaced != value:
                    node.attrib[attr_name] = replaced

            if node.text:
                node.text = _tableau_replace_field_reference_tokens(node.text, field_reference_map)
            if node.tail:
                node.tail = _tableau_replace_field_reference_tokens(node.tail, field_reference_map)


def _tableau_build_visual_field_reference_map(
    visual_source_root: ET.Element,
    source_datasource_names: list[str],
    target_datasource_node: ET.Element,
) -> dict[str, str]:
    datasources_node = _tableau_find_first_child(visual_source_root, "datasources")
    if datasources_node is None:
        return {}

    source_names = {str(name or "").strip() for name in source_datasource_names if str(name or "").strip()}
    source_datasource_nodes: list[ET.Element] = []
    for datasource_node in list(datasources_node):
        if _tableau_local_name(datasource_node.tag) != "datasource":
            continue
        datasource_name = str(datasource_node.attrib.get("name", "") or "").strip()
        if source_names and datasource_name not in source_names:
            continue
        source_datasource_nodes.append(datasource_node)

    if not source_datasource_nodes:
        return {}

    source_specs: list[dict[str, str]] = []
    for datasource_node in source_datasource_nodes:
        source_specs.extend(_tableau_collect_datasource_field_specs(datasource_node))
    source_specs.extend(_tableau_collect_dependency_field_specs(visual_source_root))

    target_specs = _tableau_collect_datasource_field_specs(target_datasource_node)
    if not source_specs or not target_specs:
        return {}

    target_exact_names = {_name_key(spec["name"]): spec["name"] for spec in target_specs if spec["name"]}
    target_by_base = _tableau_build_unique_field_lookup(target_specs, "base_key")
    target_by_label = _tableau_build_unique_field_lookup(target_specs, "label_key")

    field_reference_map: dict[str, str] = {}
    for source_spec in source_specs:
        source_name = str(source_spec.get("name", "") or "").strip()
        if not source_name:
            continue

        source_exact_key = _name_key(source_name)
        if source_exact_key and source_exact_key in target_exact_names:
            continue

        candidate = ""
        source_base_key = str(source_spec.get("base_key", "") or "").strip()
        source_label_key = str(source_spec.get("label_key", "") or "").strip()
        if source_base_key:
            candidate = str(target_by_base.get(source_base_key, "") or "").strip()
        if not candidate and source_label_key:
            candidate = str(target_by_label.get(source_label_key, "") or "").strip()

        if candidate and candidate != source_name:
            field_reference_map[source_name] = candidate

    return field_reference_map


def _tableau_collect_datasource_field_specs(datasource_node: ET.Element) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for child in list(datasource_node):
        if _tableau_local_name(child.tag) != "column":
            continue

        datatype = str(child.attrib.get("datatype", "") or "").strip().lower()
        name = str(child.attrib.get("name", "") or "").strip()
        if not name or datatype == "table" or name.startswith("[__tableau_internal_object_id__]."):
            continue

        name_key = _name_key(name)
        if not name_key or name_key in seen_names:
            continue
        seen_names.add(name_key)

        caption = str(child.attrib.get("caption", "") or "").strip()
        specs.append(
            {
                "name": name,
                "caption": caption,
                "base_key": _tableau_field_base_key(name, caption),
                "label_key": _tableau_field_label_key(name, caption),
            }
        )

    return specs


def _tableau_collect_dependency_field_specs(root: ET.Element) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for dependencies_node in root.findall(".//datasource-dependencies"):
        for child in list(dependencies_node):
            if _tableau_local_name(child.tag) != "column":
                continue

            name = str(child.attrib.get("name", "") or "").strip()
            if not name:
                continue

            name_key = _name_key(name)
            if not name_key or name_key in seen_names:
                continue
            seen_names.add(name_key)

            caption = str(child.attrib.get("caption", "") or "").strip()
            specs.append(
                {
                    "name": name,
                    "caption": caption,
                    "base_key": _tableau_field_base_key(name, caption),
                    "label_key": _tableau_field_label_key(name, caption),
                }
            )

    return specs


def _tableau_build_unique_field_lookup(
    field_specs: list[dict[str, str]],
    key_name: str,
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for spec in field_specs:
        key = str(spec.get(key_name, "") or "").strip()
        name = str(spec.get("name", "") or "").strip()
        if not key or not name:
            continue
        grouped.setdefault(key, []).append(name)

    lookup: dict[str, str] = {}
    for key, names in grouped.items():
        unique_names = []
        seen: set[str] = set()
        for name in names:
            normalized = _name_key(name)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_names.append(name)
        if len(unique_names) == 1:
            lookup[key] = unique_names[0]
    return lookup


def _tableau_field_label_key(name: str, caption: str) -> str:
    label = str(caption or "").strip()
    if not label:
        label = _clean_name(name)
    return _name_key(label)


def _tableau_field_base_key(name: str, caption: str) -> str:
    label = str(caption or "").strip()
    if not label:
        label = _clean_name(name)
    label = re.sub(r"\s+\([^)]*\)$", "", label).strip()
    return _name_key(label)


def _tableau_replace_field_reference_tokens(
    value: str,
    field_reference_map: dict[str, str],
) -> str:
    updated = str(value or "")
    if not updated or not field_reference_map:
        return updated

    ordered_replacements = sorted(
        (
            (str(source_name or ""), str(target_name or ""))
            for source_name, target_name in field_reference_map.items()
            if str(source_name or "").strip() and str(target_name or "").strip()
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for source_name, target_name in ordered_replacements:
        updated = updated.replace(source_name, target_name)

    return updated


def _tableau_is_migrated_data_value(value: str) -> bool:
    normalized = re.sub(r"[_\s]+", " ", str(value or "").strip().lower())
    return "migrated data" in normalized


def _tableau_remove_migrated_data_from_datasource(datasource_node: ET.Element) -> None:
    removed_column_names: set[str] = set()

    for child in list(datasource_node):
        if _tableau_local_name(child.tag) != "column":
            continue
        name_attr = str(child.attrib.get("name", "") or "").strip()
        caption_attr = str(child.attrib.get("caption", "") or "").strip()
        if _tableau_is_migrated_data_value(f"{name_attr} {caption_attr}"):
            if name_attr:
                removed_column_names.add(name_attr)
            datasource_node.remove(child)

    for child in list(datasource_node):
        if _tableau_local_name(child.tag) != "column-instance":
            continue
        column_attr = str(child.attrib.get("column", "") or "").strip()
        name_attr = str(child.attrib.get("name", "") or "").strip()
        if (
            column_attr in removed_column_names
            or _tableau_is_migrated_data_value(column_attr)
            or _tableau_is_migrated_data_value(name_attr)
        ):
            datasource_node.remove(child)

    for parent in list(datasource_node.iter()):
        for child in list(parent):
            local_name = _tableau_local_name(child.tag)
            if local_name not in {"map", "metadata-record", "object", "relation", "object-id"}:
                continue

            candidate_parts: list[str] = [str(child.text or "")]
            candidate_parts.extend(str(value) for value in child.attrib.values())
            for grandchild in list(child):
                candidate_parts.append(str(grandchild.text or ""))
                candidate_parts.extend(str(value) for value in grandchild.attrib.values())

            if _tableau_is_migrated_data_value(" ".join(candidate_parts)):
                parent.remove(child)


def _tableau_remove_migrated_data_artifacts(
    workbook_root: ET.Element,
    datasource_node: ET.Element | None,
) -> None:
    if datasource_node is not None:
        _tableau_remove_migrated_data_from_datasource(datasource_node)

    for dependencies_node in workbook_root.findall(".//datasource-dependencies"):
        for child in list(dependencies_node):
            local_name = _tableau_local_name(child.tag)
            if local_name not in {"column", "column-instance"}:
                continue
            candidate = " ".join(str(value) for value in child.attrib.values())
            if _tableau_is_migrated_data_value(candidate):
                dependencies_node.remove(child)


def _build_linked_workbook_for_published_datasource(
    source_twb_path: Path,
    output_twb_path: Path,
    published_datasource: Any,
    server_url: str,
    site_content_url: str,
    source_datasource_name: str,
) -> None:
    root = ET.fromstring(source_twb_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(root)

    published_name = str(getattr(published_datasource, "name", "") or "").strip() or "published_datasource"
    content_url = str(getattr(published_datasource, "content_url", "") or "").strip() or published_name
    datasource_id = str(getattr(published_datasource, "id", "") or "").strip() or content_url
    datasource_reference = (
        published_name
        or content_url
        or datasource_id
    )
    remote_datasource_name = published_name

    datasources_node = _tableau_find_first_child(root, "datasources")
    if datasources_node is None:
        datasources_node = ET.Element("datasources")
        insert_index = len(list(root))
        for idx, child in enumerate(list(root)):
            if _tableau_local_name(child.tag) in {"worksheets", "dashboards", "stories", "windows"}:
                insert_index = idx
                break
        root.insert(insert_index, datasources_node)

    datasource_nodes = [
        node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
    ]
    selected: ET.Element
    if datasource_nodes:
        selected = _tableau_select_datasource_node(datasource_nodes, source_datasource_name)
        for datasource_node in list(datasources_node):
            if _tableau_local_name(datasource_node.tag) != "datasource":
                continue
            if datasource_node is selected:
                continue
            datasources_node.remove(datasource_node)
    else:
        selected_name = remote_datasource_name
        selected = ET.SubElement(datasources_node, "datasource", attrib={"name": selected_name})

    previous_datasource_name = str(selected.attrib.get("name", "") or "").strip()
    if previous_datasource_name and previous_datasource_name != remote_datasource_name:
        _tableau_rebind_visual_datasource_references(
            section_roots=[root],
            source_datasource_names=[previous_datasource_name],
            target_datasource_name=remote_datasource_name,
        )

    selected.attrib["name"] = remote_datasource_name
    selected.attrib["caption"] = published_name
    selected.attrib.setdefault("inline", "true")
    selected.attrib.setdefault("version", "18.1")
    _tableau_ensure_exposed_columns_from_connection_metadata(selected)
    _tableau_strip_embedded_logical_model_from_linked_datasource(selected)

    for child in [
        c
        for c in list(selected)
        if _tableau_local_name(c.tag) in {"repository-location", "connection"}
    ]:
        selected.remove(child)

    normalized_site = str(site_content_url or "").strip().strip("/")
    repository_path = "/datasources"
    if normalized_site:
        repository_path = f"/t/{normalized_site}/datasources"

    repository_id = datasource_id or content_url or published_name
    repository_attrs: dict[str, str] = {
        "id": repository_id,
        "path": repository_path,
        "revision": "1.0",
    }
    if normalized_site:
        repository_attrs["site"] = normalized_site
    if datasource_reference:
        repository_attrs["derived-from"] = _tableau_build_datasource_derived_from_url(
            server_url=server_url,
            site_content_url=normalized_site,
            datasource_content_url=datasource_reference,
        )
    ET.SubElement(selected, "repository-location", attrib=repository_attrs)

    connection_attrs = _tableau_build_sqlproxy_connection_attrs(
        server_url=server_url,
        datasource_content_url=datasource_reference,
        datasource_name=published_name,
    )
    connection_node = ET.SubElement(selected, "connection", attrib=connection_attrs)
    relation_collection = ET.SubElement(connection_node, "relation", attrib={"type": "collection"})
    ET.SubElement(
        relation_collection,
        "relation",
        attrib={"name": "sqlproxy", "table": "[sqlproxy]", "type": "table"},
    )

    aliases_node = _tableau_find_first_child(selected, "aliases")
    if aliases_node is None:
        ET.SubElement(selected, "aliases", attrib={"enabled": "yes"})
    else:
        aliases_node.attrib.setdefault("enabled", "yes")

    _tableau_reorder_datasource_children(selected)
    _tableau_remove_migrated_data_artifacts(root, selected)

    output_twb_path.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("user", "http://www.tableausoftware.com/xml/user")
    output_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output_twb_path.write_bytes(output_bytes)


def _tableau_strip_embedded_logical_model_from_linked_datasource(datasource_node: ET.Element) -> None:
    _tableau_remove_internal_table_object_columns(datasource_node)

    for child in list(datasource_node):
        if _tableau_local_name(child.tag) == "object-graph":
            datasource_node.remove(child)


def _tableau_finalize_linked_workbook_datasource_schema(
    workbook_path: Path,
    preferred_datasource_name: str,
) -> None:
    root = ET.fromstring(workbook_path.read_text(encoding="utf-8"))
    _tableau_strip_namespaces(root)

    datasources_node = _tableau_find_first_child(root, "datasources")
    if datasources_node is None:
        return

    datasource_nodes = [
        node for node in list(datasources_node) if _tableau_local_name(node.tag) == "datasource"
    ]
    if not datasource_nodes:
        return

    selected = _tableau_select_datasource_node(datasource_nodes, preferred_datasource_name)
    _tableau_reduce_linked_datasource_to_remote_reference(selected)
    _tableau_reorder_datasource_children(selected)

    workbook_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _tableau_reduce_linked_datasource_to_remote_reference(datasource_node: ET.Element) -> None:
    preserved_tags = {"repository-location", "connection", "aliases"}
    for child in list(datasource_node):
        if _tableau_local_name(child.tag) not in preserved_tags:
            datasource_node.remove(child)


def _tableau_rebuild_workbook_as_empty_consumer(
    root: ET.Element,
    datasource_node: ET.Element,
    sheet_name: str = "Sheet 1",
) -> None:
    datasource_name = str(datasource_node.attrib.get("name", "") or "").strip()
    datasource_caption = str(datasource_node.attrib.get("caption", "") or "").strip() or datasource_name

    root_sections_to_remove = {"worksheets", "dashboards", "stories", "windows", "thumbnails"}
    for child in [c for c in list(root) if _tableau_local_name(c.tag) in root_sections_to_remove]:
        root.remove(child)

    worksheets_node = ET.Element("worksheets")
    worksheet_node = ET.SubElement(worksheets_node, "worksheet", attrib={"name": sheet_name})
    table_node = ET.SubElement(worksheet_node, "table")
    view_node = ET.SubElement(table_node, "view")
    ET.SubElement(view_node, "datasources")
    ET.SubElement(view_node, "aggregation", attrib={"value": "true"})
    ET.SubElement(table_node, "style")

    panes_node = ET.SubElement(table_node, "panes")
    pane_node = ET.SubElement(
        panes_node,
        "pane",
        attrib={"selection-relaxation-option": "selection-relaxation-allow"},
    )
    pane_view_node = ET.SubElement(pane_node, "view")
    ET.SubElement(pane_view_node, "breakdown", attrib={"value": "auto"})
    ET.SubElement(pane_node, "mark", attrib={"class": "Automatic"})
    ET.SubElement(table_node, "rows")
    ET.SubElement(table_node, "cols")
    ET.SubElement(worksheet_node, "simple-id", attrib={"uuid": "{" + str(uuid.uuid4()).upper() + "}"})

    windows_node = ET.Element("windows")
    window_node = ET.SubElement(
        windows_node,
        "window",
        attrib={"class": "worksheet", "maximized": "true", "name": sheet_name},
    )
    cards_node = ET.SubElement(window_node, "cards")
    left_edge = ET.SubElement(cards_node, "edge", attrib={"name": "left"})
    left_strip = ET.SubElement(left_edge, "strip", attrib={"size": "160"})
    for card_type in ("pages", "filters", "marks"):
        ET.SubElement(left_strip, "card", attrib={"type": card_type})

    top_edge = ET.SubElement(cards_node, "edge", attrib={"name": "top"})
    for card_type in ("columns", "rows", "title"):
        top_strip = ET.SubElement(top_edge, "strip", attrib={"size": "2147483647"})
        ET.SubElement(top_strip, "card", attrib={"type": card_type})
    ET.SubElement(window_node, "simple-id", attrib={"uuid": "{" + str(uuid.uuid4()).upper() + "}"})

    insert_index = len(list(root))
    for idx, child in enumerate(list(root)):
        if _tableau_local_name(child.tag) == "datasources":
            insert_index = idx + 1
            break

    root.insert(insert_index, worksheets_node)
    root.insert(insert_index + 1, windows_node)


def _tableau_publish_datasource_with_fallback(
    server: Any,
    tsc_module: Any,
    datasource_item: Any,
    datasource_path: Path,
    publish_mode: Any,
) -> Any:
    try:
        return _tableau_publish_datasource(
            server=server,
            tsc_module=tsc_module,
            datasource_item=datasource_item,
            datasource_path=datasource_path,
            publish_mode=publish_mode,
        )
    except Exception as publish_exc:
        message = str(publish_exc)
        if datasource_path.suffix.lower() != ".tds" or not _tableau_should_retry_packaged_publish(message):
            raise

        tdsx_path = datasource_path.with_suffix(".tdsx")
        _tableau_package_tds_as_tdsx(
            tds_path=datasource_path,
            tdsx_path=tdsx_path,
        )
        return _tableau_publish_datasource(
            server=server,
            tsc_module=tsc_module,
            datasource_item=datasource_item,
            datasource_path=tdsx_path,
            publish_mode=publish_mode,
        )


def _tableau_publish_datasource(
    server: Any,
    tsc_module: Any,
    datasource_item: Any,
    datasource_path: Path,
    publish_mode: Any,
) -> Any:
    is_extract_package = datasource_path.suffix.lower() in {".tdsx", ".hyper"}

    def _publish() -> Any:
        if is_extract_package:
            try:
                st.info(f"Uploading extract datasource package '{datasource_path.name}' to Tableau Cloud...")
            except Exception:
                pass
        try:
            result = server.datasources.publish(
                datasource_item,
                str(datasource_path),
                publish_mode,
                as_job=is_extract_package,
            )
        except TypeError:
            result = server.datasources.publish(
                datasource_item,
                str(datasource_path),
                publish_mode,
            )
        if is_extract_package and _tableau_publish_result_looks_like_job(result):
            job_id = str(getattr(result, "id", "") or "").strip()
            try:
                st.info(
                    "Tableau accepted the extract upload"
                    + (f" as async job {job_id}." if job_id else ".")
                    + " Waiting for the publish job to finish..."
                )
            except Exception:
                pass
            return _tableau_poll_publish_job_until_complete(
                server=server,
                tsc_module=tsc_module,
                job=result,
                expected_datasource_name=str(getattr(datasource_item, "name", "") or ""),
                project_id=str(getattr(datasource_item, "project_id", "") or ""),
            )
        return result

    return _tableau_call_with_transient_retries(
        action=_publish,
        action_label=f"publish datasource package {datasource_path.name}",
    )


def _tableau_publish_result_looks_like_job(result: Any) -> bool:
    if result is None:
        return False

    has_job_type = bool(getattr(result, "type", None))
    has_datasource_attrs = bool(getattr(result, "project_id", None))
    if has_datasource_attrs:
        return False

    class_name = result.__class__.__name__.lower()
    return "job" in class_name or has_job_type


def _tableau_poll_publish_job_until_complete(
    server: Any,
    tsc_module: Any,
    job: Any,
    expected_datasource_name: str,
    project_id: str,
    poll_interval_seconds: int = 8,
    timeout_seconds: int = 600,
) -> Any:
    job_id = str(getattr(job, "id", "") or "").strip()
    if not job_id:
        return job

    jobs_endpoint = getattr(server, "jobs", None)
    status_placeholder = None
    try:
        status_placeholder = st.empty()
        status_placeholder.info(
            f"Waiting for Tableau async publish job {job_id} "
            f"(0/{timeout_seconds}s elapsed)..."
        )
    except Exception:
        status_placeholder = None

    elapsed_seconds = 0
    current_job = job
    last_state = _tableau_format_job_state(current_job)
    last_recovery_check_seconds = -30
    while elapsed_seconds < timeout_seconds:
        time.sleep(poll_interval_seconds)
        elapsed_seconds += poll_interval_seconds

        get_by_id = getattr(jobs_endpoint, "get_by_id", None)
        if callable(get_by_id):
            try:
                current_job = get_by_id(job_id)
            except Exception as exc:
                if _tableau_is_transient_publish_error(exc):
                    continue
                continue

        current_state = _tableau_format_job_state(current_job)
        if current_state:
            last_state = current_state

        if status_placeholder is not None:
            try:
                status_placeholder.info(
                    f"Waiting for Tableau async publish job {job_id} "
                    f"({elapsed_seconds}/{timeout_seconds}s elapsed, "
                    f"{last_state or 'pending'})."
                )
            except Exception:
                status_placeholder = None

        completed_at = getattr(current_job, "completed_at", None)
        finish_code = _tableau_job_finish_code(current_job)

        if completed_at is None and finish_code in {1, 2}:
            notes = _tableau_job_notes(current_job)
            terminal_label = "failed" if finish_code == 1 else "was cancelled"
            if status_placeholder is not None:
                try:
                    status_placeholder.error(
                        f"Tableau async publish job {job_id} {terminal_label}: {notes}"
                    )
                except Exception:
                    pass
            raise RuntimeError(
                f"Tableau Cloud async publish job {job_id} {terminal_label} "
                f"before Tableau reported a completion timestamp "
                f"(finish_code {finish_code}): {notes}"
            )

        if completed_at is not None:
            if finish_code in {0, 3}:
                published_datasource = _tableau_find_published_datasource_for_job(
                    server=server,
                    tsc_module=tsc_module,
                    job=current_job,
                    expected_datasource_name=expected_datasource_name,
                    project_id=project_id,
                )
                if published_datasource is not None:
                    if status_placeholder is not None:
                        try:
                            status_placeholder.success(f"Tableau async publish job {job_id} completed.")
                        except Exception:
                            pass
                    return published_datasource
                raise RuntimeError(
                    f"Tableau Cloud async publish job {job_id} completed, but the published datasource "
                    f"'{expected_datasource_name}' could not be found."
                )

            notes = _tableau_job_notes(current_job)
            if finish_code == 1:
                if status_placeholder is not None:
                    try:
                        status_placeholder.error(f"Tableau async publish job {job_id} failed: {notes}")
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Tableau Cloud async publish job {job_id} failed with finish_code 1: {notes}"
                )
            if finish_code == 2:
                if status_placeholder is not None:
                    try:
                        status_placeholder.error(f"Tableau async publish job {job_id} was cancelled: {notes}")
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Tableau Cloud async publish job {job_id} was cancelled: {notes}"
                )
            raise RuntimeError(
                f"Tableau Cloud async publish job {job_id} completed with unexpected finish_code "
                f"{finish_code}: {notes}"
            )

        if elapsed_seconds - last_recovery_check_seconds >= 30:
            last_recovery_check_seconds = elapsed_seconds
            published_datasource = _tableau_find_published_datasource_for_job(
                server=server,
                tsc_module=tsc_module,
                job=current_job,
                expected_datasource_name=expected_datasource_name,
                project_id=project_id,
                lookup_delays_seconds=(0,),
            )
            if published_datasource is not None:
                if status_placeholder is not None:
                    try:
                        status_placeholder.success(
                            "Tableau has not reported the async job complete yet, "
                            "but the published datasource exists. Continuing with publish workflow."
                        )
                    except Exception:
                        pass
                return published_datasource

    if status_placeholder is not None:
        try:
            status_placeholder.warning(
                f"Tableau async publish job {job_id} did not complete within {timeout_seconds}s."
            )
        except Exception:
            pass
    raise RuntimeError(
        f"Tableau Cloud async publish job {job_id} did not complete within {timeout_seconds}s "
        f"(last state: {last_state or 'unknown'})."
    )


def _tableau_job_finish_code(job: Any) -> int | None:
    raw_finish_code = getattr(job, "finish_code", None)
    if raw_finish_code is None:
        raw_finish_code = getattr(job, "finishCode", None)
    try:
        return int(raw_finish_code)
    except (TypeError, ValueError):
        return None


def _tableau_format_job_state(job: Any) -> str:
    completed_at = getattr(job, "completed_at", None)
    started_at = getattr(job, "started_at", None)
    progress = str(getattr(job, "progress", "") or "").strip()
    finish_code = _tableau_job_finish_code(job)

    if finish_code == 1:
        state = "failed"
    elif finish_code == 2:
        state = "cancelled"
    elif completed_at is not None:
        state = "completed"
    elif started_at is not None:
        state = "running"
    else:
        state = "pending"

    details = [f"state: {state}"]
    if progress:
        details.append(f"progress: {progress}")
    if finish_code is not None and finish_code >= 0:
        details.append(f"finish_code: {finish_code}")
    return ", ".join(details)


def _tableau_job_notes(job: Any) -> str:
    notes = getattr(job, "notes", None)
    if isinstance(notes, list):
        text = "; ".join(str(note).strip() for note in notes if note is not None and str(note).strip())
        if text:
            return text
    if isinstance(notes, str) and notes.strip():
        return notes.strip()

    for attr_name in ("status_notes", "statusNotes", "error_code", "errorCode"):
        value = str(getattr(job, attr_name, "") or "").strip()
        if value:
            return value
    return "no details available"


def _tableau_find_published_datasource_for_job(
    server: Any,
    tsc_module: Any,
    job: Any,
    expected_datasource_name: str,
    project_id: str,
    lookup_delays_seconds: tuple[int, ...] | None = None,
) -> Any | None:
    datasource_id = str(getattr(job, "datasource_id", "") or "").strip()
    if datasource_id:
        datasources_endpoint = getattr(server, "datasources", None)
        get_by_id = getattr(datasources_endpoint, "get_by_id", None)
        if callable(get_by_id):
            try:
                datasource = get_by_id(datasource_id)
            except Exception:
                datasource = None
            if datasource is not None:
                datasource_project_id = str(getattr(datasource, "project_id", "") or "").strip()
                if not project_id or not datasource_project_id or datasource_project_id == project_id:
                    return datasource

    datasource_name = str(getattr(job, "datasource_name", "") or "").strip() or expected_datasource_name
    return _tableau_find_published_datasource_by_name(
        server=server,
        tsc_module=tsc_module,
        datasource_name=datasource_name,
        project_id=project_id,
        lookup_delays_seconds=lookup_delays_seconds,
    )


def _tableau_should_retry_packaged_publish(message: str) -> bool:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    retry_hints = (
        "error opening archive file",
        "invalid twb or twbx file",
        "invalid tds or tdsx file",
        "invalid twb",
        "invalid tds",
    )
    return any(hint in lowered for hint in retry_hints)


def _tableau_call_with_transient_retries(action: Any, action_label: str) -> Any:
    attempt_index = 1

    while True:
        try:
            return action()
        except Exception as exc:
            delay_seconds = (
                TABLEAU_TRANSIENT_RETRY_DELAYS_SECONDS[attempt_index - 1]
                if attempt_index - 1 < len(TABLEAU_TRANSIENT_RETRY_DELAYS_SECONDS)
                else 0
            )
            if delay_seconds == 0 or not _tableau_is_transient_publish_error(exc):
                raise
            try:
                st.warning(
                    "Tableau Cloud had a temporary error. "
                    f"Retrying {action_label} in {delay_seconds} seconds "
                    f"(attempt {attempt_index + 1})."
                )
            except Exception:
                pass
            time.sleep(delay_seconds)
            attempt_index += 1


def _tableau_is_transient_publish_error(error: Exception | str) -> bool:
    message = str(error or "").strip().lower()
    if not message:
        return False

    transient_hints = (
        "503",
        "502",
        "504",
        "429",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "temporarily unavailable",
        "too many requests",
        "upstream connect error",
        "disconnect/reset before headers",
        "connection termination",
        "connection reset",
        "remote end closed connection",
        "read timed out",
        "timeout",
        "timed out",
    )
    return any(hint in message for hint in transient_hints)


def _tableau_is_permission_publish_error(error: Exception | str) -> bool:
    message = str(error or "").strip().lower()
    if not message:
        return False

    permission_hints = (
        "forbidden",
        "does not have permission",
        "permission for action",
        "not authorized",
        "insufficient permissions",
        "403",
    )
    return any(hint in message for hint in permission_hints)


def _tableau_is_permission_error(error: Exception | str) -> bool:
    return _tableau_is_permission_publish_error(error)


def _tableau_find_published_datasource_by_name(
    server: Any,
    tsc_module: Any,
    datasource_name: str,
    project_id: str,
    lookup_delays_seconds: tuple[int, ...] | None = None,
) -> Any | None:
    target_name = str(datasource_name or "").strip()
    target_project_id = str(project_id or "").strip()
    if not target_name:
        return None

    delays = TABLEAU_RECOVERY_LOOKUP_DELAYS_SECONDS if lookup_delays_seconds is None else lookup_delays_seconds
    for delay_seconds in delays:
        if delay_seconds > 0:
            try:
                st.warning(
                    "Checking whether Tableau created the datasource despite the transient upload error "
                    f"in {delay_seconds} seconds..."
                )
            except Exception:
                pass
            time.sleep(delay_seconds)

        try:
            datasources = [item for item in tsc_module.Pager(server.datasources)]
        except Exception:
            continue

        for datasource in datasources:
            name = str(getattr(datasource, "name", "") or "").strip()
            content_url = str(getattr(datasource, "content_url", "") or "").strip()
            if name != target_name and _name_key(name) != _name_key(target_name):
                if content_url != target_name and _name_key(content_url) != _name_key(target_name):
                    continue

            datasource_project_id = str(getattr(datasource, "project_id", "") or "").strip()
            if target_project_id and datasource_project_id and datasource_project_id != target_project_id:
                continue

            return datasource

    return None


def _tableau_is_template_semantic_model_source(path: Path) -> bool:
    name_key = _tableau_compact_token_key(str(path.stem or path.name or ""))
    return "templatesemanticmodel" in name_key


def _tableau_is_generated_semantic_model_source(path: Path) -> bool:
    name_key = _tableau_compact_token_key(str(path.stem or path.name or ""))
    return "templatesemanticmodel" in name_key or "validatedsemanticmodel" in name_key


def _tableau_compact_token_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _tableau_semantic_template_structure_score(path: Path) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    try:
        if not path.exists() or not path.is_file():
            return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        root = ET.fromstring(_read_twb_text_with_fallback(path))
        datasource_node = root.find("datasources/datasource")
        if datasource_node is None:
            return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        connection_node = datasource_node.find("connection")
        relation_names: list[str] = []
        map_count = 0
        metadata_count = 0
        if connection_node is not None:
            relation_collection = connection_node.find("relation")
            if relation_collection is not None:
                relation_names = [
                    str(child.attrib.get("name", "") or "").strip()
                    for child in list(relation_collection)
                    if _tableau_local_name(child.tag) == "relation"
                ]

            cols_node = connection_node.find("cols")
            if cols_node is not None:
                map_count = len([child for child in list(cols_node) if _tableau_local_name(child.tag) == "map"])

            metadata_node = connection_node.find("metadata-records")
            if metadata_node is not None:
                metadata_count = len(
                    [child for child in list(metadata_node) if _tableau_local_name(child.tag) == "metadata-record"]
                )

        object_nodes = datasource_node.findall("object-graph/objects/object")
        relationship_nodes = datasource_node.findall("object-graph/relationships/relationship")
        relation_keys = {_name_key(name) for name in relation_names}
        object_keys = {_name_key(str(node.attrib.get("caption", "") or "")) for node in object_nodes}
        known_keys = relation_keys | object_keys
        expected_prefix = ["FactResellerSales", "DimCurrency", "DimDate", "FactSalesQuota"]

        stat = path.stat()
        return (
            1 if "factresellersales" in known_keys else 0,
            1 if "dimcurrency" in known_keys else 0,
            1 if relation_names[: len(expected_prefix)] == expected_prefix else 0,
            len(relation_names),
            map_count,
            metadata_count,
            len(object_nodes),
            len(relationship_nodes),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )
    except Exception:
        return (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)


def _tableau_canonical_generated_source_candidates() -> list[Path]:
    candidates: list[Path] = []

    env_override = str(os.getenv("SQL_MODEL_ASSISTANT_CANONICAL_TWB") or "").strip()
    if env_override:
        candidates.append(Path(env_override).expanduser())

    if _is_active_regionalsales_report():
        candidates.extend(_regional_sales_semantic_workbook_candidates())

    candidates.extend([OUTPUT_TEMPLATE_COPY_PATH, OUTPUT_TEMPLATE_PATH])

    artifact_dir = OUTPUT_DIR / "tableau_publish_artifacts"
    if artifact_dir.exists():
        candidates.extend(sorted(artifact_dir.glob("*generated_source_twb_template_semantic_model*.twb")))

    existing: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if not resolved.is_file() or resolved.suffix.lower() != ".twb":
            continue
        path_key = _template_path_key(resolved)
        if path_key in seen:
            continue
        seen.add(path_key)
        existing.append(resolved)

    return existing


def _tableau_resolve_generated_source_content_path(source_path: Path, label: str) -> Path:
    if _tableau_compact_token_key(label) != "generatedsourcetwb":
        return source_path
    if not _tableau_is_generated_semantic_model_source(source_path):
        return source_path

    if _is_active_regionalsales_report():
        regional_sales_source = _first_existing_twb(_regional_sales_semantic_workbook_candidates())
        if regional_sales_source is not None:
            return regional_sales_source

    candidates = [source_path]
    candidates.extend(_tableau_canonical_generated_source_candidates())
    candidates = [candidate for candidate in candidates if candidate.exists() and candidate.is_file()]
    if not candidates:
        return source_path

    return max(candidates, key=_tableau_semantic_template_structure_score)


def _tableau_publish_artifact_dir(output_folder_name: str) -> Path:
    override = str(getattr(st.session_state, "sql_model_assistant_tableau_publish_artifact_dir", "") or "").strip()
    if override:
        base_dir = Path(override).expanduser()
        if output_folder_name == "tableau_publish_debug":
            return base_dir / "debug"
        return base_dir
    return OUTPUT_DIR / output_folder_name


def _tableau_copy_publish_artifact(
    source_path: Path,
    timestamp_utc: str,
    label: str,
    output_folder_name: str = "tableau_publish_artifacts",
) -> str:
    try:
        if not source_path.exists():
            return ""

        content_source_path = _tableau_resolve_generated_source_content_path(source_path, label)
        artifact_dir = _tableau_publish_artifact_dir(output_folder_name)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_name = (
            f"{_tableau_safe_name(timestamp_utc)}_"
            f"{_tableau_safe_name(label)}_"
            f"{_tableau_safe_name(source_path.name)}"
        )
        artifact_path = artifact_dir / artifact_name
        shutil.copy2(content_source_path, artifact_path)
        size_mb = artifact_path.stat().st_size / (1024 * 1024)
        return f"{artifact_path} ({size_mb:.2f} MB)"
    except Exception:
        return ""


def _tableau_copy_publish_artifact_for_debug(
    source_path: Path,
    timestamp_utc: str,
    label: str,
) -> str:
    return _tableau_copy_publish_artifact(
        source_path=source_path,
        timestamp_utc=timestamp_utc,
        label=label,
        output_folder_name="tableau_publish_debug",
    )


def _tableau_publish_workbook_with_fallback(
    server: Any,
    workbook_item: Any,
    workbook_path: Path,
    publish_mode: Any,
) -> Any:
    def _publish() -> Any:
        try:
            return server.workbooks.publish(
                workbook_item,
                str(workbook_path),
                publish_mode,
                as_job=False,
                skip_connection_check=True,
            )
        except TypeError:
            return server.workbooks.publish(
                workbook_item,
                str(workbook_path),
                publish_mode,
                as_job=False,
            )

    return _tableau_call_with_transient_retries(
        action=_publish,
        action_label=f"publish workbook package {workbook_path.name}",
    )


def _tableau_package_twb_as_twbx(twb_path: Path, twbx_path: Path) -> Path:
    if not twb_path.exists():
        raise FileNotFoundError(f"TWB file not found for packaging: {twb_path}")
    return _tableau_package_file_as_zip(twb_path, twbx_path)


def _tableau_package_tds_as_tdsx(tds_path: Path, tdsx_path: Path) -> Path:
    if not tds_path.exists():
        raise FileNotFoundError(f"TDS file not found for packaging: {tds_path}")
    return _tableau_package_file_as_zip(tds_path, tdsx_path)


def _tableau_package_file_as_zip(source_path: Path, archive_path: Path) -> Path:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_path, arcname=source_path.name)
    return archive_path


def _tableau_resolve_project_id(server: Any, tsc_module: Any, project_name: str) -> str:
    projects = [project for project in tsc_module.Pager(server.projects)]
    if not projects:
        raise ValueError("No Tableau projects are available for the authenticated account.")

    requested = str(project_name or "").strip()
    if requested:
        exact_matches = [p for p in projects if getattr(p, "name", None) == requested]
        if not exact_matches:
            requested_lower = requested.lower()
            exact_matches = [
                p
                for p in projects
                if isinstance(getattr(p, "name", None), str)
                and str(getattr(p, "name")).lower() == requested_lower
            ]
        if not exact_matches:
            raise ValueError(f"Tableau project not found: {requested}")
        if len(exact_matches) > 1:
            raise ValueError(
                "Multiple Tableau projects share this name. "
                "Use a unique project or adjust permissions."
            )
        return exact_matches[0].id

    default_matches = [p for p in projects if str(getattr(p, "name", "")).strip().lower() == "default"]
    if default_matches:
        return default_matches[0].id
    return projects[0].id


def _tableau_select_datasource_node(
    datasource_nodes: list[ET.Element],
    source_datasource_name: str,
) -> ET.Element:
    requested = str(source_datasource_name or "").strip().lower()
    if requested:
        for node in datasource_nodes:
            name = str(node.attrib.get("name", "") or "").strip().lower()
            caption = str(node.attrib.get("caption", "") or "").strip().lower()
            if requested in {name, caption}:
                return node

    for node in datasource_nodes:
        name = str(node.attrib.get("name", "") or "").strip().lower()
        caption = str(node.attrib.get("caption", "") or "").strip().lower()
        if name == "parameters" or caption == "parameters":
            continue
        return node
    return datasource_nodes[0]


def _tableau_find_first_child(parent: ET.Element, child_local_name: str) -> ET.Element | None:
    for child in list(parent):
        if _tableau_local_name(child.tag) == child_local_name:
            return child
    return None


def _tableau_local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _tableau_strip_namespaces(root: ET.Element) -> None:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def _tableau_reorder_datasource_children(datasource_node: ET.Element) -> None:
    order = [
        "repository-location",
        "connection",
        "utility-dimensions",
        "dimension",
        "overridable-settings",
        "aliases",
        "column",
        "column-instance",
        "group",
        "mapped-images",
        "drill-paths",
        "unlinked-server-hierarchies",
        "folder",
        "actions",
        "calculated-members",
        "extract",
        "layout",
        "style",
        "semantic-values",
        "date-options",
        "default-date-format",
        "default-sorts",
        "field-sort-info",
        "datasource-dependencies",
        "explainability",
        "filter",
    ]
    rank = {name: idx for idx, name in enumerate(order)}
    children = list(datasource_node)
    children.sort(key=lambda el: rank.get(_tableau_local_name(el.tag), 10_000))
    for child in list(datasource_node):
        datasource_node.remove(child)
    for child in children:
        datasource_node.append(child)


def _tableau_build_datasource_derived_from_url(
    server_url: str,
    site_content_url: str,
    datasource_content_url: str,
) -> str:
    parsed = urlparse(server_url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    origin = f"{scheme}://{netloc}".rstrip("/")
    site_segment = f"/t/{site_content_url.strip('/')}" if site_content_url.strip() else ""
    content = datasource_content_url.strip().strip("/")
    return f"{origin}{site_segment}/datasources/{content}"


def _tableau_normalize_server_url(server_url: str) -> str:
    raw = str(server_url or "").strip()
    if not raw:
        return ""

    if "://" not in raw and "." in raw and "/" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    if parsed.scheme and parsed.path and not parsed.netloc:
        return f"{parsed.scheme}://{parsed.path}".rstrip("/")

    return raw.rstrip("/")


def _tableau_extract_site_content_url(server_url: str) -> str:
    raw = str(server_url or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    search_space = " ".join([parsed.path, parsed.fragment])
    match = re.search(r"(?:^|/)(?:site|t)/([^/?#]+)/?", search_space, flags=re.IGNORECASE)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _tableau_build_sqlproxy_connection_attrs(
    server_url: str,
    datasource_content_url: str,
    datasource_name: str,
) -> dict[str, str]:
    parsed = urlparse(server_url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or parsed.netloc or parsed.path
    default_port = 443 if scheme.lower() == "https" else 80
    port = parsed.port or default_port

    dbname = datasource_content_url.strip().strip("/")
    if "/" in dbname:
        dbname = dbname.rsplit("/", 1)[-1]
    if not dbname:
        dbname = _tableau_safe_name(datasource_name) or "published_datasource"

    return {
        "class": "sqlproxy",
        "channel": scheme.lower(),
        "dataserver-permissions": "true",
        "dbname": dbname,
        "directory": "/dataserver",
        "port": str(port),
        "server": host,
        "server-ds-friendly-name": datasource_name,
        "server-oauth": "",
        "username": "",
        "workgroup-auth-mode": "prompt",
    }


def _tableau_timestamped_name(base_name: str, timestamp_utc: str) -> str:
    return f"{str(base_name or '').strip() or 'semantic_model'}_{timestamp_utc}"


def _tableau_safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return cleaned.strip("._") or "artifact"


class _TableauPublishWorkDirectory:
    def __init__(self, timestamp_utc: str):
        self.timestamp_utc = timestamp_utc
        self.path: Path | None = None

    def __enter__(self) -> Path:
        root_override = str(getattr(st.session_state, "sql_model_assistant_tableau_publish_work_dir", "") or "").strip()
        root = Path(root_override).expanduser() if root_override else OUTPUT_DIR / "tableau_publish_work"
        root.mkdir(parents=True, exist_ok=True)
        safe_timestamp = _tableau_safe_name(self.timestamp_utc)
        self.path = root / f"{safe_timestamp}_{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=False)
        return self.path

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        # Keep the publish work directory for diagnostics and to avoid Windows
        # temp cleanup failures when Tableau/ODBC still holds a file handle.
        return False


def _tableau_disable_environment_proxies(server: Any) -> None:
    session = getattr(server, "session", None)
    if session is None:
        return
    try:
        session.trust_env = False
    except Exception:
        pass
    try:
        session.proxies.clear()
    except Exception:
        pass


def _load_template_xml(uploaded_template: Any, template_path: str) -> str:
    if uploaded_template is not None:
        content = uploaded_template.getvalue()
        if not content:
            raise ValueError("Uploaded template is empty.")
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode uploaded TWB file.")

    path = Path(str(template_path or "")).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode TWB template at: {path}")


def _generate_twb_from_validated_model(
    template_xml: str,
    data_source: dict[str, Any],
    dataset: dict[str, Any],
    validated_model: dict[str, Any],
    preserve_template_exposed_columns: bool = False,
) -> str:
    datasource_payload = copy.deepcopy(data_source) if isinstance(data_source, dict) else {}
    dataset_payload = copy.deepcopy(dataset) if isinstance(dataset, dict) else {}
    table_instances = _build_table_instances_from_model(validated_model)

    template_datasource_name = _first_template_datasource_name(template_xml)
    preferred_exposed_names = _extract_template_exposed_name_preferences(
        template_xml=template_xml,
        target_datasource_name=template_datasource_name,
    )
    if template_datasource_name:
        datasource_payload["name"] = template_datasource_name
        if isinstance(dataset_payload, dict):
            dataset_payload["data_source_name"] = template_datasource_name

    db_catalog = _build_db_catalog_for_twb_generation(
        data_source=datasource_payload,
        dataset=dataset_payload,
        table_instances=table_instances,
    )
    canonical_model = _canonicalize_model_physical_tables_from_catalog(validated_model, db_catalog)
    if canonical_model != validated_model:
        table_instances = _build_table_instances_from_model(canonical_model)
        refreshed_catalog = _build_db_catalog_for_twb_generation(
            data_source=datasource_payload,
            dataset=dataset_payload,
            table_instances=table_instances,
        )
        if refreshed_catalog:
            db_catalog = refreshed_catalog
    else:
        canonical_model = validated_model

    if _is_sql_datasource(datasource_payload):
        ds_name = str(datasource_payload.get("name", "") or "").strip()
        if not _catalog_has_table_metadata(db_catalog, ds_name):
            # DB access is useful for exact physical metadata, but the React pipeline
            # should still be able to generate a TWB from the selected RDL dataset.
            # Passing None lets twb_builder fall back to RDL fields and SQL joins.
            db_catalog = None

    connected_xml = (
        template_xml
        if preserve_template_exposed_columns
        else inject_datasource_connections(
            xml_content=template_xml,
            data_sources=[datasource_payload] if datasource_payload else [],
            data_sets=[dataset_payload] if dataset_payload else [],
            db_catalog=db_catalog,
        )
    )

    return _apply_validated_schema_to_twb(
        xml_content=connected_xml,
        model=canonical_model,
        data_source=datasource_payload,
        target_datasource_name=template_datasource_name,
        preferred_exposed_names=preferred_exposed_names,
        preserve_template_exposed_columns=preserve_template_exposed_columns,
    )


def _first_template_datasource_name(template_xml: str) -> str:
    try:
        root = ET.fromstring(template_xml)
    except ET.ParseError:
        return ""

    datasources_node = root.find("datasources")
    if datasources_node is None:
        return ""

    datasource_node = datasources_node.find("datasource")
    if datasource_node is None:
        return ""

    return str(datasource_node.attrib.get("name", "") or "").strip()


def _build_tableau_publish_context(
    template_xml: str,
    data_source: dict[str, Any],
    dataset: dict[str, Any],
    validated_model: dict[str, Any],
) -> dict[str, Any]:
    datasource_payload = copy.deepcopy(data_source) if isinstance(data_source, dict) else {}
    dataset_payload = copy.deepcopy(dataset) if isinstance(dataset, dict) else {}
    template_datasource_name = _first_template_datasource_name(template_xml)
    if template_datasource_name and datasource_payload:
        datasource_payload["name"] = template_datasource_name
        if isinstance(dataset_payload, dict):
            dataset_payload["data_source_name"] = template_datasource_name

    table_instances = _build_table_instances_from_model(validated_model)
    db_catalog = _build_db_catalog_for_twb_generation(
        data_source=datasource_payload,
        dataset=dataset_payload,
        table_instances=table_instances,
    )
    canonical_model = _canonicalize_model_physical_tables_from_catalog(validated_model, db_catalog)
    if canonical_model != validated_model:
        table_instances = _build_table_instances_from_model(canonical_model)
        refreshed_catalog = _build_db_catalog_for_twb_generation(
            data_source=datasource_payload,
            dataset=dataset_payload,
            table_instances=table_instances,
        )
        if refreshed_catalog:
            db_catalog = refreshed_catalog

    if _is_sql_datasource(datasource_payload):
        ds_name = str(datasource_payload.get("name", "") or "").strip()
        if not _catalog_has_table_metadata(db_catalog, ds_name):
            db_catalog = None

    return {
        "data_source": datasource_payload,
        "dataset": dataset_payload,
        "db_catalog": db_catalog,
    }


def _extract_template_exposed_name_preferences(
    template_xml: str,
    target_datasource_name: str = "",
) -> dict[tuple[str, str], str]:
    try:
        root = ET.fromstring(template_xml)
    except ET.ParseError:
        return {}

    datasources_node = root.find("datasources")
    if datasources_node is None:
        return {}

    datasource_node = None
    target_key = _name_key(target_datasource_name)
    if target_key:
        for candidate in datasources_node.findall("datasource"):
            if _name_key(str(candidate.attrib.get("name", "") or "")) == target_key:
                datasource_node = candidate
                break
    if datasource_node is None:
        datasource_node = datasources_node.find("datasource")
    if datasource_node is None:
        return {}

    connection_node = datasource_node.find("connection")
    if connection_node is None:
        return {}

    cols_node = connection_node.find("cols")
    if cols_node is None:
        return {}

    preferences: dict[tuple[str, str], str] = {}
    for map_node in [child for child in list(cols_node) if child.tag == "map"]:
        key_raw = str(map_node.attrib.get("key", "") or "").strip()
        value_raw = str(map_node.attrib.get("value", "") or "").strip()
        table_name, column_name = _parse_cols_map_table_column(value_raw)
        exposed_name = _clean_name(key_raw)
        if not table_name or not column_name or not exposed_name:
            continue
        preferences[(_name_key(table_name), _name_key(column_name))] = exposed_name

    return preferences


def _canonicalize_model_physical_tables_from_catalog(
    model: dict[str, Any],
    db_catalog: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(model, dict):
        return {}

    lookup = _schema_flow_catalog_lookup(db_catalog or {})
    if not lookup:
        return copy.deepcopy(model)

    canonical_model = copy.deepcopy(model)

    def _canonical_table_name(*candidates: str) -> str:
        payload = _schema_flow_first_catalog_match([candidate for candidate in candidates if candidate], lookup)
        if not payload:
            return ""
        table_name = str(payload.get("name", "") or "").strip()
        if table_name:
            return table_name
        return _table_leaf_from_table_reference(str(payload.get("full_name", "") or "").strip())

    for fact in canonical_model.get("fact_tables", []) if isinstance(canonical_model.get("fact_tables", []), list) else []:
        if not isinstance(fact, dict):
            continue
        fact_name = str(fact.get("name", "") or "").strip()
        canonical_name = _canonical_table_name(fact_name)
        if canonical_name:
            fact["name"] = canonical_name

    for group_name in ("direct_dimensions", "snowflake_dimensions", "dimension_tables"):
        group = canonical_model.get(group_name, [])
        if not isinstance(group, list):
            continue
        for dimension in group:
            if not isinstance(dimension, dict):
                continue
            raw_name = str(dimension.get("name", "") or "").strip()
            parsed_physical, parsed_role = _split_dimension_name_role(raw_name)
            current_physical = str(dimension.get("physical_table", "") or parsed_physical or raw_name).strip()
            canonical_name = _canonical_table_name(current_physical, raw_name, parsed_physical)
            if not canonical_name:
                continue
            dimension["physical_table"] = canonical_name
            if _same_name(parsed_physical, current_physical):
                dimension["name"] = f"{canonical_name} [role: {parsed_role}]" if parsed_role else canonical_name

    relationships = canonical_model.get("relationships", [])
    if isinstance(relationships, list):
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            for side in ("from", "to"):
                current_table = str(relationship.get(f"{side}_table", "") or "").strip()
                canonical_name = _canonical_table_name(current_table)
                if canonical_name:
                    relationship[f"{side}_table"] = canonical_name

    return canonical_model


def _build_db_catalog_for_twb_generation(
    data_source: dict[str, Any],
    dataset: dict[str, Any],
    table_instances: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(data_source, dict) or not data_source:
        return None

    ds_name = str(data_source.get("name", "") or "").strip()
    provider = str(data_source.get("provider", "") or "").strip().lower()
    connection_string = str(data_source.get("connection_string", "") or "").strip()
    if not ds_name or "sql" not in provider or not connection_string:
        return None

    provider_class = str(data_source.get("provider_class", "") or "sqlserver").strip().lower() or "sqlserver"
    connection_info = data_source.get("connection_info", {}) if isinstance(data_source, dict) else {}
    schema_hint = ""
    if isinstance(connection_info, dict):
        schema_hint = str(connection_info.get("schema", "") or "").strip()

    synthetic_sets: list[dict[str, Any]] = []
    if isinstance(dataset, dict):
        dataset_query = str(dataset.get("query", "") or "").strip()
        if dataset_query:
            synthetic_sets.append(
                {
                    "name": str(dataset.get("name", "") or "__selected_dataset"),
                    "query": dataset_query,
                    "data_source_name": ds_name,
                }
            )

    seen_tables: set[str] = set()
    for instance in table_instances:
        if not isinstance(instance, dict):
            continue
        physical_table = str(instance.get("physical_table", "") or "").strip()
        if not physical_table:
            continue
        table_key = _name_key(physical_table)
        if table_key in seen_tables:
            continue
        seen_tables.add(table_key)

        table_ref = _normalize_table_reference_for_tableau(physical_table, schema_hint, provider_class)
        synthetic_sets.append(
            {
                "name": f"__model_table_{len(synthetic_sets) + 1}",
                "query": f"SELECT TOP 1 * FROM {table_ref}",
                "data_source_name": ds_name,
            }
        )

    if not synthetic_sets:
        return None

    try:
        db_catalog = build_db_catalog(
            data_sources=[data_source],
            data_sets=synthetic_sets,
        )
        return db_catalog if _catalog_has_table_metadata(db_catalog, ds_name) else None
    except Exception:
        return None


def _is_sql_datasource(data_source: dict[str, Any]) -> bool:
    provider = str(data_source.get("provider", "") or "").strip().lower()
    connection_string = str(data_source.get("connection_string", "") or "").strip()
    return "sql" in provider and bool(connection_string)


def _catalog_has_table_metadata(db_catalog: dict[str, Any] | None, datasource_name: str) -> bool:
    if not isinstance(db_catalog, dict):
        return False

    sources = db_catalog.get("datasources")
    if not isinstance(sources, list):
        return False

    target = _name_key(datasource_name)
    for item in sources:
        if not isinstance(item, dict):
            continue
        if _name_key(str(item.get("name", "") or "")) != target:
            continue

        tables = item.get("tables")
        if not isinstance(tables, list) or not tables:
            return False

        for table in tables:
            if not isinstance(table, dict):
                continue
            columns = table.get("columns")
            if isinstance(columns, list) and columns:
                return True
        return False

    return False


def _ensure_datasource_connection_before_aliases(datasource_node: ET.Element) -> None:
    connection_node = datasource_node.find("connection")
    aliases_node = datasource_node.find("aliases")
    if connection_node is None or aliases_node is None:
        return

    children = list(datasource_node)
    connection_index = children.index(connection_node)
    aliases_index = children.index(aliases_node)
    if connection_index < aliases_index:
        return

    datasource_node.remove(connection_node)
    aliases_index = list(datasource_node).index(aliases_node)
    datasource_node.insert(aliases_index, connection_node)


def _apply_validated_schema_to_twb(
    xml_content: str,
    model: dict[str, Any],
    data_source: dict[str, Any],
    target_datasource_name: str = "",
    preferred_exposed_names: dict[tuple[str, str], str] | None = None,
    preserve_template_exposed_columns: bool = False,
) -> str:
    root = ET.fromstring(xml_content)
    datasources_node = root.find("datasources")
    if datasources_node is None:
        raise ValueError("Template TWB has no datasources section.")

    datasource_node = None
    target_key = _name_key(target_datasource_name)
    if target_key:
        for candidate in datasources_node.findall("datasource"):
            if _name_key(str(candidate.attrib.get("name", "") or "")) == target_key:
                datasource_node = candidate
                break
    if datasource_node is None:
        datasource_node = datasources_node.find("datasource")
    if datasource_node is None:
        raise ValueError("Template TWB has no datasource node.")

    template_exposed_columns = (
        [copy.deepcopy(child) for child in list(datasource_node) if child.tag == "column"]
        if preserve_template_exposed_columns
        else []
    )

    # Tableau can infer this; removing the flag keeps generated files closer to canonical templates.
    datasource_node.attrib.pop("hasconnection", None)

    connection_node = datasource_node.find("connection")
    if connection_node is None:
        aliases_node = datasource_node.find("aliases")
        connection_node = ET.Element("connection", attrib={"class": "federated"})
        if aliases_node is None:
            datasource_node.append(connection_node)
        else:
            aliases_index = list(datasource_node).index(aliases_node)
            datasource_node.insert(aliases_index, connection_node)

    _ensure_datasource_connection_before_aliases(datasource_node)

    provider_class = "sqlserver"
    connection_info = data_source.get("connection_info", {}) if isinstance(data_source, dict) else {}
    if isinstance(connection_info, dict):
        provider_class = str(connection_info.get("provider_class", "") or "").strip().lower() or provider_class
    provider_class = (
        str(data_source.get("provider_class", "") or "").strip().lower() or provider_class
        if isinstance(data_source, dict)
        else provider_class
    )

    named_connections = connection_node.find("named-connections")
    if named_connections is None:
        named_connections = ET.SubElement(connection_node, "named-connections")

    named_connection = named_connections.find("named-connection")
    if named_connection is None:
        named_connection = ET.SubElement(
            named_connections,
            "named-connection",
            attrib={"name": f"{provider_class}.main", "caption": "Data Source"},
        )
    named_connection_name = str(named_connection.attrib.get("name", "")).strip() or f"{provider_class}.main"
    named_connection.attrib["name"] = named_connection_name

    inner_connection = named_connection.find("connection")
    if inner_connection is None:
        inner_connection = ET.SubElement(named_connection, "connection")
    inner_connection.attrib.setdefault("class", provider_class)

    schema_hint = ""
    if isinstance(connection_info, dict):
        schema_hint = str(connection_info.get("schema", "") or "").strip()

    table_instances = _build_table_instances_from_model(model)
    if not table_instances:
        raise ValueError("Validated model has no tables to project to TWB.")

    table_instances = _order_table_instances_for_structure(table_instances=table_instances, model=model)
    _apply_existing_object_ids_to_instances(datasource_node=datasource_node, table_instances=table_instances)

    _replace_relation_collection(
        connection_node=connection_node,
        named_connection_name=named_connection_name,
        table_instances=table_instances,
        schema_hint=schema_hint,
        provider_class=provider_class,
    )

    _normalize_connection_cols_and_metadata(
        connection_node=connection_node,
        datasource_node=datasource_node,
        table_instances=table_instances,
        preferred_exposed_names=preferred_exposed_names or {},
    )

    _rebuild_object_graph(
        datasource_node=datasource_node,
        table_instances=table_instances,
        named_connection_name=named_connection_name,
        model=model,
        schema_hint=schema_hint,
        provider_class=provider_class,
    )

    _upsert_table_object_columns(datasource_node=datasource_node, table_instances=table_instances)

    _sync_connection_metadata_with_current_model(
        connection_node=connection_node,
        datasource_node=datasource_node,
    )
    if preserve_template_exposed_columns:
        _restore_template_exposed_columns(datasource_node, template_exposed_columns)
    else:
        _tableau_ensure_exposed_columns_from_connection_metadata(datasource_node)

    _normalize_connection_child_order(connection_node)

    # Preserve Tableau user namespace expected by working templates.
    if "xmlns:user" not in root.attrib:
        root.set("xmlns:user", "http://www.tableausoftware.com/xml/user")

    # Keep output readable and easier to validate manually.
    ET.indent(root, space="  ")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _normalize_connection_child_order(connection_node: ET.Element) -> None:
    preferred_tags = ["named-connections", "relation", "cols", "metadata-records"]

    children = list(connection_node)
    grouped: dict[str, list[ET.Element]] = {tag: [] for tag in preferred_tags}
    trailing: list[ET.Element] = []

    for child in children:
        if child.tag in grouped:
            grouped[child.tag].append(child)
        else:
            trailing.append(child)

    for child in children:
        connection_node.remove(child)

    for tag in preferred_tags:
        for child in grouped[tag]:
            connection_node.append(child)

    for child in trailing:
        connection_node.append(child)


def _restore_template_exposed_columns(datasource_node: ET.Element, template_columns: list[ET.Element]) -> None:
    if not template_columns:
        return

    for child in [item for item in list(datasource_node) if item.tag == "column"]:
        datasource_node.remove(child)

    children = list(datasource_node)
    insert_at = 0
    for index, child in enumerate(children):
        if child.tag == "aliases":
            insert_at = index + 1
            break
        if child.tag == "connection":
            insert_at = index + 1

    for offset, column_node in enumerate(template_columns):
        datasource_node.insert(insert_at + offset, copy.deepcopy(column_node))


def _order_table_instances_for_structure(
    table_instances: list[dict[str, Any]],
    model: dict[str, Any],
) -> list[dict[str, Any]]:
    if len(table_instances) <= 1:
        return list(table_instances)

    ordered = list(table_instances)
    semantic_map: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, dict[str, Any]] = {}
    physical_map: dict[str, dict[str, Any]] = {}
    index_by_instance_id: dict[int, int] = {}

    for index, instance in enumerate(ordered):
        index_by_instance_id[id(instance)] = index
        semantic_key = _name_key(str(instance.get("semantic_name", "") or ""))
        alias_key = _name_key(str(instance.get("alias", "") or ""))
        physical_key = _name_key(str(instance.get("physical_table", "") or ""))
        if semantic_key and semantic_key not in semantic_map:
            semantic_map[semantic_key] = instance
        if alias_key:
            alias_map[alias_key] = instance
        if physical_key and physical_key not in physical_map:
            physical_map[physical_key] = instance

    adjacency: dict[int, set[int]] = {idx: set() for idx in range(len(ordered))}
    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue

        from_instance = _resolve_instance_for_relationship_side(
            table_name=str(relationship.get("from_table", "") or ""),
            alias=str(relationship.get("from_alias", "") or ""),
            semantic_map=semantic_map,
            alias_map=alias_map,
            physical_map=physical_map,
        )
        to_instance = _resolve_instance_for_relationship_side(
            table_name=str(relationship.get("to_table", "") or ""),
            alias=str(relationship.get("to_alias", "") or ""),
            semantic_map=semantic_map,
            alias_map=alias_map,
            physical_map=physical_map,
        )
        if from_instance is None or to_instance is None:
            continue

        from_index = index_by_instance_id.get(id(from_instance))
        to_index = index_by_instance_id.get(id(to_instance))
        if from_index is None or to_index is None or from_index == to_index:
            continue

        adjacency[from_index].add(to_index)
        adjacency[to_index].add(from_index)

    degrees = {idx: len(neighbors) for idx, neighbors in adjacency.items()}

    root_index: int | None = None
    fact_name = ""
    fact_tables = [item for item in model.get("fact_tables", []) if isinstance(item, dict)]
    if fact_tables:
        fact_name = str(fact_tables[0].get("name", "") or "").strip()

    if fact_name:
        fact_key = _name_key(fact_name)
        for idx, instance in enumerate(ordered):
            if _name_key(str(instance.get("semantic_name", "") or "")) == fact_key:
                root_index = idx
                break

    if root_index is None:
        for idx, instance in enumerate(ordered):
            if bool(instance.get("is_fact")):
                root_index = idx
                break

    if root_index is None:
        root_index = 0

    visited: set[int] = set()
    ordered_indexes: list[int] = []

    def _neighbor_sort_key(index: int) -> tuple[int, int, str]:
        instance = ordered[index]
        relation_name = str(
            instance.get("relation_name", "") or instance.get("caption", "") or instance.get("semantic_name", "")
        )
        return (
            -degrees.get(index, 0),
            1 if bool(instance.get("is_fact")) else 0,
            _name_key(relation_name),
        )

    def _visit(index: int) -> None:
        if index in visited:
            return
        visited.add(index)
        ordered_indexes.append(index)

        for neighbor in sorted(adjacency.get(index, set()), key=_neighbor_sort_key):
            _visit(neighbor)

    _visit(root_index)

    remaining = [idx for idx in range(len(ordered)) if idx not in visited]
    remaining.sort(
        key=lambda idx: (
            0 if bool(ordered[idx].get("is_fact")) else 1,
            -degrees.get(idx, 0),
            _name_key(
                str(
                    ordered[idx].get("relation_name", "")
                    or ordered[idx].get("caption", "")
                    or ordered[idx].get("semantic_name", "")
                )
            ),
        )
    )

    ordered_indexes.extend(remaining)
    return [ordered[index] for index in ordered_indexes]


def _apply_existing_object_ids_to_instances(
    datasource_node: ET.Element,
    table_instances: list[dict[str, Any]],
) -> None:
    existing_lookup = _build_table_object_id_lookup_from_current_object_graph(datasource_node)
    if not existing_lookup:
        return

    used_object_ids: set[str] = set()
    for instance in table_instances:
        candidates = [
            str(instance.get("relation_name", "") or "").strip(),
            str(instance.get("semantic_name", "") or "").strip(),
            _table_leaf_from_table_reference(str(instance.get("physical_table", "") or "")),
            str(instance.get("caption", "") or "").strip(),
            str(instance.get("alias", "") or "").strip(),
        ]

        selected_object_id = ""
        for candidate in candidates:
            for key in (_name_key(candidate), _semantic_name_key(candidate)):
                if not key:
                    continue
                object_id = str(existing_lookup.get(key, "") or "").strip()
                if object_id and object_id not in used_object_ids:
                    selected_object_id = object_id
                    break
            if selected_object_id:
                break

        if selected_object_id:
            instance["object_id"] = selected_object_id
            used_object_ids.add(selected_object_id)


def _normalize_connection_cols_and_metadata(
    connection_node: ET.Element,
    datasource_node: ET.Element,
    table_instances: list[dict[str, Any]],
    preferred_exposed_names: dict[tuple[str, str], str],
) -> None:
    cols_node = connection_node.find("cols")
    if cols_node is None:
        return

    maps = [child for child in list(cols_node) if child.tag == "map"]
    if not maps:
        return

    table_order: dict[str, int] = {}
    for index, instance in enumerate(table_instances):
        for candidate in (
            str(instance.get("relation_name", "") or ""),
            str(instance.get("semantic_name", "") or ""),
            str(instance.get("caption", "") or ""),
            str(instance.get("physical_table", "") or ""),
            _table_leaf_from_table_reference(str(instance.get("physical_table", "") or "")),
        ):
            key = _name_key(candidate)
            if key and key not in table_order:
                table_order[key] = index

    entries: list[dict[str, Any]] = []
    for index, map_node in enumerate(maps):
        key_raw = str(map_node.attrib.get("key", "") or "").strip()
        value_raw = str(map_node.attrib.get("value", "") or "").strip()
        old_exposed = _clean_name(key_raw)
        table_name, column_name = _parse_cols_map_table_column(value_raw)

        preferred_name = ""
        if table_name and column_name:
            preferred_name = str(
                preferred_exposed_names.get((_name_key(table_name), _name_key(column_name)), "") or ""
            ).strip()

        candidate_name = _clean_name(preferred_name or old_exposed or column_name)
        if not candidate_name:
            candidate_name = _clean_name(column_name)

        entries.append(
            {
                "index": index,
                "map_node": map_node,
                "table_name": table_name,
                "column_name": column_name,
                "old_exposed": old_exposed,
                "candidate_name": candidate_name,
                "preferred_name": _clean_name(preferred_name),
                "final_name": "",
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(_name_key(entry["candidate_name"]), []).append(entry)

    used_final_names: set[str] = set()

    def _make_unique_name(base_name: str) -> str:
        proposal = _clean_name(base_name) or "Column"
        proposal_key = _name_key(proposal)
        if proposal_key not in used_final_names:
            used_final_names.add(proposal_key)
            return proposal

        suffix = 2
        while True:
            candidate = f"{proposal} {suffix}"
            candidate_key = _name_key(candidate)
            if candidate_key not in used_final_names:
                used_final_names.add(candidate_key)
                return candidate
            suffix += 1

    for group_key, group_entries in grouped.items():
        if not group_key:
            for entry in group_entries:
                entry["final_name"] = _make_unique_name(entry["candidate_name"])
            continue

        ordered_group = sorted(
            group_entries,
            key=lambda entry: (
                0 if _name_key(entry["preferred_name"]) == group_key and entry["preferred_name"] else 1,
                table_order.get(_name_key(entry["table_name"]), 10_000),
                entry["index"],
            ),
        )

        for position, entry in enumerate(ordered_group):
            base_name = entry["candidate_name"]
            if position > 0 and entry["table_name"]:
                base_name = f"{entry['candidate_name']} ({entry['table_name']})"
            entry["final_name"] = _make_unique_name(base_name)

    old_to_new: dict[str, str] = {}
    pair_to_final: dict[tuple[str, str], str] = {}
    for entry in entries:
        final_name = str(entry.get("final_name", "") or entry.get("candidate_name", "") or "").strip()
        if not final_name:
            continue

        entry["map_node"].attrib["key"] = f"[{final_name}]"

        old_key = _name_key(entry.get("old_exposed", ""))
        if old_key:
            old_to_new[old_key] = final_name

        table_name = str(entry.get("table_name", "") or "").strip()
        column_name = str(entry.get("column_name", "") or "").strip()
        if table_name and column_name:
            pair_to_final[(_name_key(table_name), _name_key(column_name))] = final_name

    for map_node in maps:
        cols_node.remove(map_node)
    for map_node in sorted(
        maps,
        key=lambda node: (
            _name_key(_clean_name(str(node.attrib.get("key", "") or ""))),
            _name_key(str(node.attrib.get("value", "") or "")),
        ),
    ):
        cols_node.append(map_node)

    metadata_node = connection_node.find("metadata-records")
    if metadata_node is not None:
        for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
            remote_name_node = record.find("remote-name")
            parent_name_node = record.find("parent-name")
            if remote_name_node is None or parent_name_node is None:
                continue

            remote_name = str(remote_name_node.text or "").strip()
            parent_name = _clean_name(str(parent_name_node.text or "").strip())
            final_name = pair_to_final.get((_name_key(parent_name), _name_key(remote_name)), "")
            if not final_name:
                continue

            local_name_node = record.find("local-name")
            if local_name_node is None:
                local_name_node = ET.SubElement(record, "local-name")
            local_name_node.text = f"[{final_name}]"

    for column_node in [child for child in list(datasource_node) if child.tag == "column"]:
        name_attr = str(column_node.attrib.get("name", "") or "").strip()
        datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
        if not name_attr or datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
            continue

        clean_name = _clean_name(name_attr)
        new_name = old_to_new.get(_name_key(clean_name), "")
        if new_name and _name_key(new_name) != _name_key(clean_name):
            column_node.attrib["name"] = f"[{new_name}]"


def _sync_connection_metadata_with_current_model(
    connection_node: ET.Element,
    datasource_node: ET.Element,
) -> None:
    metadata_node = connection_node.find("metadata-records")
    if metadata_node is None:
        return

    local_key_to_table_name = _build_cols_local_key_to_table_name(connection_node)
    if not local_key_to_table_name:
        for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
            metadata_node.remove(record)
        return

    table_object_id_lookup = _build_table_object_id_lookup_from_current_object_graph(datasource_node)
    seen_local_keys: set[str] = set()

    for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
        local_name_node = record.find("local-name")
        local_name = str(local_name_node.text or "").strip() if local_name_node is not None else ""
        local_key = _clean_name(local_name).lower()
        expected_table_name = local_key_to_table_name.get(local_key, "")

        if not local_key or not expected_table_name or local_key in seen_local_keys:
            metadata_node.remove(record)
            continue

        seen_local_keys.add(local_key)

        parent_name_node = record.find("parent-name")
        if parent_name_node is None:
            parent_name_node = ET.SubElement(record, "parent-name")
        parent_name_node.text = f"[{expected_table_name}]"

        object_id = table_object_id_lookup.get(_name_key(expected_table_name), "")
        object_id_node = record.find("object-id")
        if object_id:
            if object_id_node is None:
                object_id_node = ET.SubElement(record, "object-id")
            object_id_node.text = f"[{object_id}]"
        elif object_id_node is not None:
            record.remove(object_id_node)


def _build_cols_local_key_to_table_name(connection_node: ET.Element) -> dict[str, str]:
    lookup: dict[str, str] = {}
    cols_node = connection_node.find("cols")
    if cols_node is None:
        return lookup

    for map_node in [child for child in list(cols_node) if child.tag == "map"]:
        key_raw = str(map_node.attrib.get("key", "") or "").strip()
        value_raw = str(map_node.attrib.get("value", "") or "").strip()
        local_key = _clean_name(key_raw).lower()
        table_name, _ = _parse_cols_map_table_column(value_raw)
        if not local_key or not table_name:
            continue
        lookup[local_key] = table_name

    return lookup


def _tableau_ensure_exposed_columns_from_connection_metadata(datasource_node: ET.Element) -> None:
    connection_node = datasource_node.find("connection")
    if connection_node is None:
        return

    cols_node = connection_node.find("cols")
    if cols_node is None:
        return

    metadata_lookup = _tableau_build_connection_metadata_lookup(connection_node)
    if not metadata_lookup:
        return

    existing_names: set[str] = set()
    for child in list(datasource_node):
        if child.tag != "column":
            continue
        datatype = str(child.attrib.get("datatype", "") or "").strip().lower()
        name_attr = str(child.attrib.get("name", "") or "").strip()
        if not name_attr or datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
            continue
        existing_names.add(_name_key(name_attr))

    insert_at = len(list(datasource_node))
    for index, child in enumerate(list(datasource_node)):
        if child.tag in {"layout", "semantic-values", "object-graph"}:
            insert_at = index
            break

    columns_to_add: list[ET.Element] = []
    for map_node in [child for child in list(cols_node) if child.tag == "map"]:
        local_name = str(map_node.attrib.get("key", "") or "").strip()
        if not local_name:
            continue
        local_key = _name_key(local_name)
        if not local_key or local_key in existing_names:
            continue

        metadata = metadata_lookup.get(local_key)
        if metadata is None:
            continue

        columns_to_add.append(_tableau_build_exposed_column_from_metadata(local_name, metadata))
        existing_names.add(local_key)

    for offset, column_node in enumerate(columns_to_add):
        datasource_node.insert(insert_at + offset, column_node)


def _tableau_build_connection_metadata_lookup(
    connection_node: ET.Element,
) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    metadata_node = connection_node.find("metadata-records")
    if metadata_node is None:
        return lookup

    for record in [child for child in list(metadata_node) if child.tag == "metadata-record"]:
        local_name = str(record.findtext("local-name", "") or "").strip()
        if not local_name:
            continue

        lookup[_name_key(local_name)] = {
            "local_name": local_name,
            "local_type": str(record.findtext("local-type", "") or "").strip().lower(),
            "aggregation": str(record.findtext("aggregation", "") or "").strip(),
            "remote_name": str(record.findtext("remote-name", "") or "").strip(),
            "remote_alias": str(record.findtext("remote-alias", "") or "").strip(),
        }

    return lookup


def _tableau_build_exposed_column_from_metadata(
    local_name: str,
    metadata: dict[str, str],
) -> ET.Element:
    clean_local_name = _clean_name(local_name)
    local_type = str(metadata.get("local_type", "") or "").strip().lower()
    remote_name = str(metadata.get("remote_name", "") or "").strip()
    remote_alias = str(metadata.get("remote_alias", "") or "").strip()
    datatype, role, semantic_type = _tableau_infer_exposed_column_traits(
        local_name=clean_local_name,
        local_type=local_type,
        remote_name=remote_name,
    )

    attrs: dict[str, str] = {
        "name": local_name,
        "datatype": datatype,
        "role": role,
        "type": semantic_type,
    }

    if role == "dimension" and datatype in {"integer", "real"}:
        attrs["aggregation"] = "Sum"

    caption = ""
    if " (" not in clean_local_name:
        caption = remote_alias or remote_name
    if caption:
        attrs["caption"] = _tableau_titleize_identifier(caption)

    return ET.Element("column", attrib=attrs)


def _tableau_infer_exposed_column_traits(
    local_name: str,
    local_type: str,
    remote_name: str,
) -> tuple[str, str, str]:
    normalized_local_name = _clean_name(local_name)
    normalized_remote_name = _clean_name(remote_name)
    dimension_like = _tableau_is_dimension_like_field_name(normalized_local_name) or _tableau_is_dimension_like_field_name(
        normalized_remote_name
    )

    if local_type in {"string"}:
        return "string", "dimension", "nominal"
    if local_type in {"boolean"}:
        return "boolean", "dimension", "nominal"
    if local_type in {"date", "datetime"}:
        return local_type, "dimension", "ordinal"
    if local_type == "integer":
        if dimension_like:
            return "integer", "dimension", "ordinal"
        return "integer", "measure", "quantitative"
    if local_type == "real":
        if dimension_like:
            return "real", "dimension", "ordinal"
        return "real", "measure", "quantitative"

    return "string", "dimension", "nominal"


def _tableau_is_dimension_like_field_name(value: str) -> bool:
    normalized = _name_key(value)
    if not normalized:
        return False
    return (
        normalized.endswith("key")
        or normalized.endswith("id")
        or normalized.endswith("code")
        or normalized.startswith("is")
    )


def _tableau_titleize_identifier(value: str) -> str:
    text = _clean_name(value)
    if not text:
        return ""

    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+", text)
    if not parts:
        return text
    return " ".join(parts)


def _build_table_object_id_lookup_from_current_object_graph(datasource_node: ET.Element) -> dict[str, str]:
    lookup: dict[str, str] = {}

    object_graph = datasource_node.find("object-graph")
    if object_graph is None:
        return lookup

    objects_node = object_graph.find("objects")
    if objects_node is None:
        return lookup

    for object_node in [child for child in list(objects_node) if child.tag == "object"]:
        object_id = str(object_node.attrib.get("id", "") or "").strip()
        if not object_id:
            continue

        caption = str(object_node.attrib.get("caption", "") or "").strip()
        caption_key = _name_key(caption)
        caption_semantic_key = _semantic_name_key(caption)
        if caption_key:
            lookup.setdefault(caption_key, object_id)
        if caption_semantic_key:
            lookup.setdefault(caption_semantic_key, object_id)
        if " (" in caption:
            base_caption = caption.split(" (", 1)[0].strip()
            base_caption_key = _name_key(base_caption)
            base_caption_semantic_key = _semantic_name_key(base_caption)
            if base_caption_key:
                lookup.setdefault(base_caption_key, object_id)
            if base_caption_semantic_key:
                lookup.setdefault(base_caption_semantic_key, object_id)

        relation_node = object_node.find("properties/relation")
        if relation_node is None:
            continue

        relation_name = str(relation_node.attrib.get("name", "") or "").strip()
        relation_name_key = _name_key(relation_name)
        relation_name_semantic_key = _semantic_name_key(relation_name)
        if relation_name_key:
            lookup.setdefault(relation_name_key, object_id)
        if relation_name_semantic_key:
            lookup.setdefault(relation_name_semantic_key, object_id)

        table_ref = str(relation_node.attrib.get("table", "") or "").strip()
        table_leaf = _table_leaf_from_table_reference(table_ref)
        table_leaf_key = _name_key(table_leaf)
        table_leaf_semantic_key = _semantic_name_key(table_leaf)
        if table_leaf_key:
            lookup.setdefault(table_leaf_key, object_id)
        if table_leaf_semantic_key:
            lookup.setdefault(table_leaf_semantic_key, object_id)

    return lookup


def _table_leaf_from_table_reference(table_ref: str) -> str:
    tokens = re.findall(r"\[([^\]]+)\]", table_ref or "")
    if tokens:
        return tokens[-1].strip()

    cleaned = _clean_name(table_ref)
    parts = [part.strip() for part in cleaned.split(".") if part.strip()]
    if not parts:
        return ""
    return parts[-1]


def _build_table_instances_from_model(model: dict[str, Any]) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []

    fact_name = ""
    fact_tables = [item for item in model.get("fact_tables", []) if isinstance(item, dict)]
    if fact_tables:
        fact_name = str(fact_tables[0].get("name", "") or "").strip()
        if fact_name:
            instances.append(
                {
                    "semantic_name": fact_name,
                    "physical_table": fact_name,
                    "alias": "",
                    "role": "",
                    "is_fact": True,
                }
            )

    for group_name in ("direct_dimensions", "snowflake_dimensions"):
        for dimension in _normalize_dimension_list(model.get(group_name, [])):
            semantic_name = str(dimension.get("name", "") or "").strip()
            physical_table = str(dimension.get("physical_table", "") or semantic_name).strip()
            if not semantic_name or not physical_table:
                continue
            instances.append(
                {
                    "semantic_name": semantic_name,
                    "physical_table": physical_table,
                    "alias": str(dimension.get("alias", "") or "").strip(),
                    "role": str(dimension.get("semantic_role", "") or "").strip(),
                    "is_fact": False,
                }
            )

    known_semantic_keys = {_name_key(str(item.get("semantic_name", ""))) for item in instances}
    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        for side in ("from", "to"):
            table_name = str(relationship.get(f"{side}_table", "") or "").strip()
            if not table_name:
                continue
            key = _name_key(table_name)
            if key and key in known_semantic_keys:
                continue
            instances.append(
                {
                    "semantic_name": table_name,
                    "physical_table": table_name,
                    "alias": str(relationship.get(f"{side}_alias", "") or "").strip(),
                    "role": "",
                    "is_fact": _same_name(table_name, fact_name),
                }
            )
            if key:
                known_semantic_keys.add(key)

    display_used: dict[str, int] = {}
    physical_leaf_counts: dict[str, int] = {}
    for instance in instances:
        semantic_name = str(instance.get("semantic_name", "") or "").strip()
        parsed_physical, parsed_role = _split_dimension_name_role(semantic_name)
        physical = str(instance.get("physical_table", "") or parsed_physical or semantic_name).strip()

        leaf = _clean_name(physical).split(".")[-1] if physical else _clean_name(parsed_physical)
        leaf_key = _name_key(leaf)
        if leaf_key:
            physical_leaf_counts[leaf_key] = physical_leaf_counts.get(leaf_key, 0) + 1

        role = str(instance.get("role", "") or parsed_role).strip()
        alias = str(instance.get("alias", "") or "").strip()

        caption = leaf or semantic_name or "Table"
        if role and not instance.get("is_fact"):
            caption = f"{caption} ({role})"
        elif alias and not instance.get("is_fact") and _name_key(alias) != _name_key(caption):
            caption = f"{caption} ({alias})"

        count = display_used.get(caption.lower(), 0) + 1
        display_used[caption.lower()] = count
        if count > 1:
            caption = f"{caption} {count}"

        instance["caption"] = caption
        instance["physical_leaf"] = leaf
        instance["object_id"] = _make_table_object_id(f"{semantic_name}|{physical}|{caption}")

    for instance in instances:
        caption = str(instance.get("caption", "") or "Table")
        physical_leaf = str(instance.get("physical_leaf", "") or "").strip()
        physical_leaf_key = _name_key(physical_leaf)

        relation_name = physical_leaf or caption
        # Keep relation names aligned with cols/map table references when unique.
        if physical_leaf_key and physical_leaf_counts.get(physical_leaf_key, 0) > 1:
            relation_name = caption

        # Keep object captions aligned with relation names for robust Tableau field resolution.
        instance["caption"] = relation_name
        instance["relation_name"] = relation_name
        semantic_name = str(instance.get("semantic_name", "") or "").strip()
        physical = str(instance.get("physical_table", "") or "").strip()
        instance["object_id"] = _make_table_object_id(f"{semantic_name}|{physical}|{relation_name}")
        instance.pop("physical_leaf", None)

    return instances


def _make_table_object_id(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "", _clean_name(value).split("|")[0])
    base = base or "Table"
    digest = hashlib.md5(value.encode("utf-8")).hexdigest().upper()
    return f"{base}_{digest}"


def _replace_relation_collection(
    connection_node: ET.Element,
    named_connection_name: str,
    table_instances: list[dict[str, Any]],
    schema_hint: str,
    provider_class: str,
) -> None:
    for child in list(connection_node):
        if child.tag == "relation":
            connection_node.remove(child)

    relation_collection = ET.Element("relation", attrib={"type": "collection"})
    for instance in table_instances:
        caption = str(instance.get("caption", "") or "Table")
        relation_name = str(instance.get("relation_name", "") or caption)
        physical = str(instance.get("physical_table", "") or caption)
        ET.SubElement(
            relation_collection,
            "relation",
            attrib={
                "connection": named_connection_name,
                "name": relation_name,
                "table": _normalize_table_reference_for_tableau(physical, schema_hint, provider_class),
                "type": "table",
            },
        )

    children = list(connection_node)
    insert_at = len(children)
    for index, child in enumerate(children):
        if child.tag == "named-connections":
            insert_at = index + 1
            break
        if child.tag == "cols":
            insert_at = index
            break
    connection_node.insert(insert_at, relation_collection)


def _normalize_table_reference_for_tableau(table_name: str, schema_hint: str, provider_class: str) -> str:
    parts = [_clean_name(part) for part in str(table_name or "").split(".") if _clean_name(part)]
    if not parts:
        return "[UnknownTable]"

    normalized_provider = str(provider_class or "").strip().lower()
    if len(parts) == 1:
        if normalized_provider == "sqlserver":
            schema = _clean_name(schema_hint) or "dbo"
            return f"[{schema}].[{parts[0]}]"
        return f"[{parts[0]}]"

    return ".".join(f"[{part}]" for part in parts)


def _rebuild_object_graph(
    datasource_node: ET.Element,
    table_instances: list[dict[str, Any]],
    named_connection_name: str,
    model: dict[str, Any],
    schema_hint: str,
    provider_class: str,
) -> None:
    object_graph = datasource_node.find("object-graph")
    if object_graph is None:
        object_graph = ET.SubElement(datasource_node, "object-graph")

    objects_node = object_graph.find("objects")
    if objects_node is None:
        objects_node = ET.SubElement(object_graph, "objects")
    relationships_node = object_graph.find("relationships")
    if relationships_node is None:
        relationships_node = ET.SubElement(object_graph, "relationships")

    for child in list(objects_node):
        objects_node.remove(child)
    for child in list(relationships_node):
        relationships_node.remove(child)

    semantic_map: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, dict[str, Any]] = {}
    physical_map: dict[str, dict[str, Any]] = {}
    object_table_by_id: dict[str, str] = {}
    fact_instance: dict[str, Any] | None = None

    connection_node = datasource_node.find("connection")
    exposed_lookup = _build_exposed_key_lookup_from_cols(connection_node)
    relationship_nodes: list[tuple[tuple[int, str, str, str, str], ET.Element]] = []

    for instance in table_instances:
        caption = str(instance.get("caption", "") or "Table")
        relation_name = str(instance.get("relation_name", "") or caption)
        object_id = str(instance.get("object_id", "") or _make_table_object_id(caption))
        instance["object_id"] = object_id

        table_ref = _normalize_table_reference_for_tableau(
            str(instance.get("physical_table", "") or caption),
            schema_hint,
            provider_class,
        )

        object_node = ET.SubElement(objects_node, "object", attrib={"caption": caption, "id": object_id})
        properties_node = ET.SubElement(object_node, "properties", attrib={"context": ""})
        ET.SubElement(
            properties_node,
            "relation",
            attrib={
                "connection": named_connection_name,
                "name": relation_name,
                "table": table_ref,
                "type": "table",
            },
        )
        object_table_by_id[object_id] = str(instance.get("physical_table", "") or caption)

        semantic_key = _name_key(str(instance.get("semantic_name", "") or ""))
        physical_key = _name_key(str(instance.get("physical_table", "") or ""))
        alias_key = _name_key(str(instance.get("alias", "") or ""))

        if semantic_key and semantic_key not in semantic_map:
            semantic_map[semantic_key] = instance
        if alias_key:
            alias_map[alias_key] = instance
        if physical_key and physical_key not in physical_map:
            physical_map[physical_key] = instance
        if instance.get("is_fact") and fact_instance is None:
            fact_instance = instance

    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue

        from_instance = _resolve_instance_for_relationship_side(
            table_name=str(relationship.get("from_table", "") or ""),
            alias=str(relationship.get("from_alias", "") or ""),
            semantic_map=semantic_map,
            alias_map=alias_map,
            physical_map=physical_map,
        )
        to_instance = _resolve_instance_for_relationship_side(
            table_name=str(relationship.get("to_table", "") or ""),
            alias=str(relationship.get("to_alias", "") or ""),
            semantic_map=semantic_map,
            alias_map=alias_map,
            physical_map=physical_map,
        )
        if from_instance is None or to_instance is None:
            continue

        from_col, to_col = _resolve_join_columns_for_relationship(relationship)
        if not from_col or not to_col:
            continue

        cardinality = str(relationship.get("cardinality", "") or "").strip().lower()
        relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()

        from_instance, to_instance, from_col, to_col, cardinality = _orient_relationship_instance_sides(
            from_instance=from_instance,
            to_instance=to_instance,
            from_col=from_col,
            to_col=to_col,
            cardinality=cardinality,
            fact_instance=fact_instance,
        )

        from_object_id = str(from_instance.get("object_id", "") or "")
        to_object_id = str(to_instance.get("object_id", "") or "")
        from_exposed = _resolve_exposed_relationship_column(
            exposed_lookup=exposed_lookup,
            object_table_by_id=object_table_by_id,
            object_id=from_object_id,
            column_name=from_col,
        )
        to_exposed = _resolve_exposed_relationship_column(
            exposed_lookup=exposed_lookup,
            object_table_by_id=object_table_by_id,
            object_id=to_object_id,
            column_name=to_col,
        )
        if not from_exposed:
            from_exposed = _clean_name(from_col)
        if not to_exposed:
            to_exposed = _clean_name(to_col)

        rel_node = ET.Element("relationship")
        expression_node = ET.SubElement(rel_node, "expression", attrib={"op": "="})
        ET.SubElement(expression_node, "expression", attrib={"op": f"[{from_exposed}]"})
        ET.SubElement(expression_node, "expression", attrib={"op": f"[{to_exposed}]"})

        first_attrs = {"object-id": str(from_instance.get("object_id", ""))}
        second_attrs = {"object-id": str(to_instance.get("object_id", ""))}

        first_is_fact_like = _is_fact_like_instance(from_instance)
        second_is_fact_like = _is_fact_like_instance(to_instance)
        if from_instance is fact_instance:
            first_is_fact_like = True
        if to_instance is fact_instance:
            second_is_fact_like = True

        _set_relationship_endpoint_attributes(
            first_attrs=first_attrs,
            second_attrs=second_attrs,
            cardinality=cardinality,
            relation_type=relation_type,
            first_is_fact_like=first_is_fact_like,
            second_is_fact_like=second_is_fact_like,
        )

        ET.SubElement(rel_node, "first-end-point", attrib=first_attrs)
        ET.SubElement(rel_node, "second-end-point", attrib=second_attrs)

        relationship_nodes.append(
            (
                (
                    0 if first_is_fact_like else 1,
                    _name_key(from_exposed),
                    _name_key(to_exposed),
                    _name_key(from_object_id),
                    _name_key(to_object_id),
                ),
                rel_node,
            )
        )

    for _, rel_node in sorted(relationship_nodes, key=lambda item: item[0]):
        relationships_node.append(rel_node)


def _resolve_instance_for_relationship_side(
    table_name: str,
    alias: str,
    semantic_map: dict[str, dict[str, Any]],
    alias_map: dict[str, dict[str, Any]],
    physical_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    alias_key = _name_key(alias)
    if alias_key and alias_key in alias_map:
        return alias_map[alias_key]

    table_key = _name_key(table_name)
    if table_key and table_key in semantic_map:
        return semantic_map[table_key]
    if table_key and table_key in physical_map:
        return physical_map[table_key]
    return None


def _resolve_join_columns_for_relationship(relationship: dict[str, Any]) -> tuple[str, str]:
    join_condition = str(relationship.get("join_condition", "") or "")
    pairs = _extract_join_pairs(join_condition)
    if not pairs:
        return "", ""

    from_alias = str(relationship.get("from_alias", "") or "").strip().lower()
    to_alias = str(relationship.get("to_alias", "") or "").strip().lower()

    for pair in pairs:
        left_alias = pair["left_alias"].lower()
        right_alias = pair["right_alias"].lower()
        if from_alias and to_alias and left_alias == from_alias and right_alias == to_alias:
            return pair["left_column"], pair["right_column"]
        if from_alias and to_alias and left_alias == to_alias and right_alias == from_alias:
            return pair["right_column"], pair["left_column"]

    if from_alias:
        for pair in pairs:
            if pair["left_alias"].lower() == from_alias:
                return pair["left_column"], pair["right_column"]
            if pair["right_alias"].lower() == from_alias:
                return pair["right_column"], pair["left_column"]

    first_pair = pairs[0]
    return first_pair["left_column"], first_pair["right_column"]


def _invert_cardinality(cardinality: str) -> str:
    normalized = str(cardinality or "").strip().lower()
    if normalized == "many-to-one":
        return "one-to-many"
    if normalized == "one-to-many":
        return "many-to-one"
    return normalized


def _orient_relationship_instance_sides(
    from_instance: dict[str, Any],
    to_instance: dict[str, Any],
    from_col: str,
    to_col: str,
    cardinality: str,
    fact_instance: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    should_swap = False

    if to_instance is fact_instance and from_instance is not fact_instance:
        should_swap = True
    else:
        from_is_primary_fact = from_instance is fact_instance
        to_is_primary_fact = to_instance is fact_instance

        # For non-primary fact relationships, place the dimension side first.
        if not from_is_primary_fact and not to_is_primary_fact:
            from_is_fact_like = _is_fact_like_instance(from_instance)
            to_is_fact_like = _is_fact_like_instance(to_instance)
            if from_is_fact_like and not to_is_fact_like:
                should_swap = True

    if not should_swap:
        return from_instance, to_instance, from_col, to_col, str(cardinality or "").strip().lower()

    return (
        to_instance,
        from_instance,
        to_col,
        from_col,
        _invert_cardinality(cardinality),
    )


def _is_fact_like_table_name(table_name: str) -> bool:
    key = _name_key(table_name)
    return bool(key) and key.startswith("fact")


def _is_fact_like_instance(instance: dict[str, Any] | None) -> bool:
    if not isinstance(instance, dict):
        return False
    if bool(instance.get("is_fact")):
        return True
    semantic_name = str(instance.get("semantic_name", "") or "")
    physical_name = str(instance.get("physical_table", "") or "")
    alias = str(instance.get("alias", "") or "")
    return (
        _is_fact_like_table_name(semantic_name)
        or _is_fact_like_table_name(physical_name)
        or _is_fact_like_table_name(alias)
    )


def _is_fact_like_object_id(object_id: str, object_table_by_id: dict[str, str]) -> bool:
    table_name = object_table_by_id.get(object_id, "")
    return _is_fact_like_table_name(table_name)


def _set_relationship_endpoint_attributes(
    first_attrs: dict[str, str],
    second_attrs: dict[str, str],
    cardinality: str,
    relation_type: str,
    first_is_fact_like: bool,
    second_is_fact_like: bool,
) -> None:
    def _set_unique(attrs: dict[str, str]) -> None:
        attrs["unique-key"] = "true"
        attrs["is-db-set-unique-key"] = "true"

    def _set_guaranteed(attrs: dict[str, str]) -> None:
        attrs["guaranteed-value"] = "true"
        attrs["is-db-set-guaranteed-value"] = "true"

    if relation_type == "dimension_to_dimension":
        if cardinality == "one-to-one":
            _set_unique(first_attrs)
            _set_unique(second_attrs)
        elif cardinality == "one-to-many":
            _set_unique(first_attrs)
        elif cardinality == "many-to-one":
            _set_unique(second_attrs)
        return

    if cardinality in {"one-to-one", "one-to-many"}:
        _set_unique(first_attrs)
    if cardinality in {"one-to-one", "many-to-one"}:
        _set_unique(second_attrs)

    if cardinality == "many-to-one":
        _set_guaranteed(first_attrs)
    elif cardinality == "one-to-many":
        _set_guaranteed(second_attrs)

    if cardinality == "" and relation_type == "fact_to_dimension":
        if first_is_fact_like and not second_is_fact_like:
            _set_guaranteed(first_attrs)
            _set_unique(second_attrs)
        elif second_is_fact_like and not first_is_fact_like:
            _set_guaranteed(second_attrs)
            _set_unique(first_attrs)
        else:
            _set_guaranteed(first_attrs)
            _set_unique(second_attrs)

    if "guaranteed-value" not in first_attrs and "guaranteed-value" not in second_attrs:
        if first_is_fact_like and not second_is_fact_like:
            _set_guaranteed(first_attrs)
        elif second_is_fact_like and not first_is_fact_like:
            _set_guaranteed(second_attrs)

    if "unique-key" not in first_attrs and "unique-key" not in second_attrs:
        if first_is_fact_like and not second_is_fact_like:
            _set_unique(second_attrs)
        elif second_is_fact_like and not first_is_fact_like:
            _set_unique(first_attrs)


def _update_existing_object_graph_relationships(datasource_node: ET.Element, model: dict[str, Any]) -> bool:
    object_graph = datasource_node.find("object-graph")
    if object_graph is None:
        return False

    objects_node = object_graph.find("objects")
    if objects_node is None:
        return False

    relationships_node = object_graph.find("relationships")

    object_lookup, object_table_by_id = _build_object_graph_indexes(objects_node)
    if not object_lookup:
        return _sync_single_object_graph_relation_from_connection(datasource_node)

    connection_node = datasource_node.find("connection")
    exposed_lookup = _build_exposed_key_lookup_from_cols(connection_node)

    fact_object_ids: set[str] = set()
    for fact in model.get("fact_tables", []):
        if not isinstance(fact, dict):
            continue
        fact_name = str(fact.get("name", "") or "").strip()
        if not fact_name:
            continue
        object_id = _resolve_object_id_for_relationship_side(
            table_name=fact_name,
            alias="",
            object_lookup=object_lookup,
        )
        if object_id:
            fact_object_ids.add(object_id)

    if not fact_object_ids:
        for relationship in model.get("relationships", []):
            if not isinstance(relationship, dict):
                continue
            relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()
            if relation_type != "fact_to_dimension":
                continue
            object_id = _resolve_object_id_for_relationship_side(
                table_name=str(relationship.get("from_table", "") or ""),
                alias=str(relationship.get("from_alias", "") or ""),
                object_lookup=object_lookup,
            )
            if object_id:
                fact_object_ids.add(object_id)

    new_relationship_nodes: list[ET.Element] = []
    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue

        from_object_id = _resolve_object_id_for_relationship_side(
            table_name=str(relationship.get("from_table", "") or ""),
            alias=str(relationship.get("from_alias", "") or ""),
            object_lookup=object_lookup,
        )
        to_object_id = _resolve_object_id_for_relationship_side(
            table_name=str(relationship.get("to_table", "") or ""),
            alias=str(relationship.get("to_alias", "") or ""),
            object_lookup=object_lookup,
        )
        if not from_object_id or not to_object_id:
            continue

        from_col, to_col = _resolve_join_columns_for_relationship(relationship)
        if not from_col or not to_col:
            continue

        if to_object_id in fact_object_ids and from_object_id not in fact_object_ids:
            from_object_id, to_object_id = to_object_id, from_object_id
            from_col, to_col = to_col, from_col

        from_exposed = _resolve_exposed_relationship_column(
            exposed_lookup=exposed_lookup,
            object_table_by_id=object_table_by_id,
            object_id=from_object_id,
            column_name=from_col,
        )
        to_exposed = _resolve_exposed_relationship_column(
            exposed_lookup=exposed_lookup,
            object_table_by_id=object_table_by_id,
            object_id=to_object_id,
            column_name=to_col,
        )
        if not from_exposed or not to_exposed:
            continue

        cardinality = str(relationship.get("cardinality", "") or "").strip().lower()
        relation_type = str(relationship.get("relationship_type", "") or "").strip().lower()

        rel_node = ET.Element("relationship")
        expression_node = ET.SubElement(rel_node, "expression", attrib={"op": "="})
        ET.SubElement(expression_node, "expression", attrib={"op": f"[{from_exposed}]"})
        ET.SubElement(expression_node, "expression", attrib={"op": f"[{to_exposed}]"})

        first_attrs = {"object-id": from_object_id}
        second_attrs = {"object-id": to_object_id}

        first_is_fact_like = (
            from_object_id in fact_object_ids
            or _is_fact_like_object_id(from_object_id, object_table_by_id)
        )
        second_is_fact_like = (
            to_object_id in fact_object_ids
            or _is_fact_like_object_id(to_object_id, object_table_by_id)
        )

        _set_relationship_endpoint_attributes(
            first_attrs=first_attrs,
            second_attrs=second_attrs,
            cardinality=cardinality,
            relation_type=relation_type,
            first_is_fact_like=first_is_fact_like,
            second_is_fact_like=second_is_fact_like,
        )

        ET.SubElement(rel_node, "first-end-point", attrib=first_attrs)
        ET.SubElement(rel_node, "second-end-point", attrib=second_attrs)
        new_relationship_nodes.append(rel_node)

    if not new_relationship_nodes:
        return _sync_single_object_graph_relation_from_connection(datasource_node)

    if relationships_node is None:
        relationships_node = ET.SubElement(object_graph, "relationships")

    for child in list(relationships_node):
        relationships_node.remove(child)
    for rel_node in new_relationship_nodes:
        relationships_node.append(rel_node)
    return True


def _sync_single_object_graph_relation_from_connection(datasource_node: ET.Element) -> bool:
    object_graph = datasource_node.find("object-graph")
    if object_graph is None:
        return False

    objects_node = object_graph.find("objects")
    if objects_node is None:
        return False

    object_nodes = [child for child in list(objects_node) if child.tag == "object"]
    if len(object_nodes) != 1:
        return False

    connection_node = datasource_node.find("connection")
    if connection_node is None:
        return False

    connection_relation = None
    for child in list(connection_node):
        if child.tag == "relation":
            connection_relation = child
            break
    if connection_relation is None:
        return False

    object_node = object_nodes[0]
    properties_node = object_node.find("properties")
    if properties_node is None:
        properties_node = ET.SubElement(object_node, "properties", attrib={"context": ""})

    relation_node = properties_node.find("relation")
    cloned_relation = copy.deepcopy(connection_relation)
    if relation_node is None:
        properties_node.append(cloned_relation)
    else:
        relation_index = list(properties_node).index(relation_node)
        properties_node.remove(relation_node)
        properties_node.insert(relation_index, cloned_relation)

    return True


def _build_object_graph_indexes(objects_node: ET.Element) -> tuple[dict[str, str], dict[str, str]]:
    object_lookup: dict[str, str] = {}
    object_table_by_id: dict[str, str] = {}

    for object_node in [child for child in list(objects_node) if child.tag == "object"]:
        object_id = str(object_node.attrib.get("id", "") or "").strip()
        if not object_id:
            continue

        caption = str(object_node.attrib.get("caption", "") or "").strip()
        if caption:
            caption_key = _name_key(caption)
            if caption_key:
                object_lookup.setdefault(caption_key, object_id)
            semantic_caption_key = _semantic_name_key(caption)
            if semantic_caption_key:
                object_lookup.setdefault(semantic_caption_key, object_id)
            if " (" in caption:
                base_caption = caption.split(" (", 1)[0].strip()
                base_key = _name_key(base_caption)
                if base_key:
                    object_lookup.setdefault(base_key, object_id)

        relation_node = object_node.find("properties/relation")
        relation_name = ""
        table_leaf = ""
        if relation_node is not None:
            relation_name = str(relation_node.attrib.get("name", "") or "").strip()
            table_ref = str(relation_node.attrib.get("table", "") or "").strip()
            tokens = re.findall(r"\[([^\]]+)\]", table_ref)
            if tokens:
                table_leaf = tokens[-1].strip()
            elif table_ref:
                parts = [part.strip() for part in _clean_name(table_ref).split(".") if part.strip()]
                if parts:
                    table_leaf = parts[-1]

        for candidate in (relation_name, table_leaf):
            candidate_key = _name_key(candidate)
            if candidate_key:
                object_lookup.setdefault(candidate_key, object_id)
            semantic_candidate_key = _semantic_name_key(candidate)
            if semantic_candidate_key:
                object_lookup.setdefault(semantic_candidate_key, object_id)

        object_table_by_id[object_id] = table_leaf or relation_name or caption

    return object_lookup, object_table_by_id


def _resolve_object_id_for_relationship_side(
    table_name: str,
    alias: str,
    object_lookup: dict[str, str],
) -> str:
    candidates: list[str] = []
    for raw in (alias, table_name):
        text = str(raw or "").strip()
        if not text:
            continue
        candidates.append(_name_key(text))
        candidates.append(_semantic_name_key(text))
        if " (" in text:
            candidates.append(_name_key(text.split(" (", 1)[0].strip()))

    for candidate in candidates:
        if candidate and candidate in object_lookup:
            return object_lookup[candidate]

    alias_key = _name_key(alias)
    if alias_key:
        for key, object_id in object_lookup.items():
            if alias_key == key:
                return object_id
        for key, object_id in object_lookup.items():
            if alias_key in key:
                return object_id

    return ""


def _build_exposed_key_lookup_from_cols(connection_node: ET.Element | None) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    if connection_node is None:
        return lookup

    cols_node = connection_node.find("cols")
    if cols_node is None:
        return lookup

    for map_node in [child for child in list(cols_node) if child.tag == "map"]:
        key_raw = str(map_node.attrib.get("key", "") or "").strip()
        value_raw = str(map_node.attrib.get("value", "") or "").strip()
        exposed_name = _clean_name(key_raw)
        table_name, column_name = _parse_cols_map_table_column(value_raw)
        if not exposed_name or not table_name or not column_name:
            continue
        lookup[(_name_key(table_name), _name_key(column_name))] = exposed_name

    return lookup


def _parse_cols_map_table_column(value_raw: str) -> tuple[str, str]:
    tokens = re.findall(r"\[([^\]]+)\]", value_raw or "")
    if len(tokens) >= 2:
        return tokens[-2].strip(), tokens[-1].strip()

    cleaned = _clean_name(value_raw)
    parts = [part.strip() for part in cleaned.split(".") if part.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", ""


def _resolve_exposed_relationship_column(
    exposed_lookup: dict[tuple[str, str], str],
    object_table_by_id: dict[str, str],
    object_id: str,
    column_name: str,
) -> str:
    clean_column = _clean_name(column_name)
    parts = [part.strip() for part in clean_column.split(".") if part.strip()]
    base_column = parts[-1] if parts else clean_column.strip()
    if not base_column:
        return ""

    table_name = str(object_table_by_id.get(object_id, "") or "").strip()
    direct_match = exposed_lookup.get((_name_key(table_name), _name_key(base_column)), "")
    if direct_match:
        return direct_match

    column_key = _name_key(base_column)
    candidates = {
        exposed_name
        for (table_key, col_key), exposed_name in exposed_lookup.items()
        if col_key == column_key
    }
    if len(candidates) == 1:
        return next(iter(candidates))

    return base_column


def _upsert_table_object_columns(datasource_node: ET.Element, table_instances: list[dict[str, Any]]) -> None:
    for child in list(datasource_node):
        if child.tag == "metadata-records":
            datasource_node.remove(child)

    for column_node in [child for child in list(datasource_node) if child.tag == "column"]:
        datatype = str(column_node.attrib.get("datatype", "") or "").strip().lower()
        name_attr = str(column_node.attrib.get("name", "") or "")
        if datatype == "table" or name_attr.startswith("[__tableau_internal_object_id__]."):
            datasource_node.remove(column_node)

    children = list(datasource_node)
    insert_at = len(children)
    for index, child in enumerate(children):
        if child.tag == "layout":
            insert_at = index
            break

    for offset, instance in enumerate(table_instances):
        caption = str(instance.get("caption", "") or "Table")
        object_id = str(instance.get("object_id", "") or _make_table_object_id(caption))
        table_column = ET.Element(
            "column",
            attrib={
                "caption": caption,
                "datatype": "table",
                "name": f"[__tableau_internal_object_id__].[{object_id}]",
                "role": "measure",
                "type": "quantitative",
            },
        )
        datasource_node.insert(insert_at + offset, table_column)


def _generate_response(
    sql_query: str,
    conversation: list[dict[str, Any]],
    llm_config_path: str,
    database_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], bool]:
    if not sql_query.strip():
        raise ValueError("SQL query must be non-empty.")

    sql_evidence_model = _heuristic_model(sql_query, conversation, apply_corrections=False)
    previous_model = _latest_assistant_model_from_conversation(conversation)
    has_follow_up = bool(_follow_up_user_messages(conversation))
    if previous_model and has_follow_up:
        sql_evidence_model = copy.deepcopy(previous_model)
    _apply_follow_up_corrections(sql_evidence_model, conversation)
    if not isinstance(database_context, dict):
        database_context = _build_current_database_context()

    llm = _load_optional_llm(llm_config_path)
    if llm is not None:
        try:
            raw = llm.chat(
                SYSTEM_PROMPT,
                _build_user_prompt(
                    sql_query,
                    conversation,
                    database_context=database_context,
                ),
            )
            payload = _parse_json_payload(raw)
            assistant_text = payload.get("assistant_response") or "Generated an updated dimensional model."
            llm_model = _normalize_model(payload.get("model", {}))
            structured_result = _merge_llm_with_sql_evidence(
                llm_model,
                sql_evidence_model,
                forced_model_type=_forced_model_type_from_conversation(conversation),
                allow_llm_schema_changes=has_follow_up,
            )
            structured_result["database_context"] = database_context
            assistant_text = _augment_assistant_response_text(
                assistant_text,
                conversation,
                previous_model,
                structured_result,
                database_context,
            )
            return assistant_text, structured_result, False
        except Exception as exc:
            # sql_evidence_model["warnings"].insert(0, f"LLM analysis failed; SQL-evidence model used. Details: {exc}")
            _refresh_model_output(sql_evidence_model)
            sql_evidence_model["database_context"] = database_context
            return (
                _augment_assistant_response_text(
                    "Built the dimensional model from SQL evidence and applied any recognized follow-up corrections.",
                    conversation,
                    previous_model,
                    sql_evidence_model,
                    database_context,
                ),
                sql_evidence_model,
                True,
            )

    # sql_evidence_model["warnings"].insert(0, "LLM config not available; SQL-evidence model used.")
    _refresh_model_output(sql_evidence_model)
    sql_evidence_model["database_context"] = database_context
    return (
        _augment_assistant_response_text(
            "Built the dimensional model from SQL evidence and applied any recognized follow-up corrections.",
            conversation,
            previous_model,
            sql_evidence_model,
            database_context,
        ),
        sql_evidence_model,
        True,
    )


def _latest_assistant_model_from_conversation(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(conversation):
        if message.get("role") != "assistant":
            continue
        structured_result = message.get("structured_result")
        if isinstance(structured_result, dict) and structured_result:
            return copy.deepcopy(structured_result)
    return {}


def _merge_llm_with_sql_evidence(
    llm_model: dict[str, Any],
    sql_evidence_model: dict[str, Any],
    forced_model_type: str = "",
    allow_llm_schema_changes: bool = False,
) -> dict[str, Any]:
    merged = copy.deepcopy(sql_evidence_model)

    known_table_keys = _known_table_keys(sql_evidence_model)
    llm_table_names = _table_names_from_model(llm_model)
    ignored_llm_tables = [name for name in llm_table_names if _name_key(name) not in known_table_keys]

    if merged.get("fact_tables") and llm_model.get("fact_tables"):
        merged_fact = merged["fact_tables"][0]
        llm_fact = llm_model["fact_tables"][0]
        if _same_name(str(llm_fact.get("name", "")), str(merged_fact.get("name", ""))):
            merged_fact["measures"] = _unique(
                _as_string_list(merged_fact.get("measures", []))
                + _as_string_list(llm_fact.get("measures", []))
            )
            llm_fact_keys = [
                key for key in _as_string_list(llm_fact.get("foreign_keys", [])) if _looks_like_key_column(key)
            ]
            merged_fact["foreign_keys"] = _unique(_as_string_list(merged_fact.get("foreign_keys", [])) + llm_fact_keys)

    llm_dimensions = _merge_dimensions(
        _normalize_dimension_list(llm_model.get("direct_dimensions", [])),
        _normalize_dimension_list(llm_model.get("snowflake_dimensions", [])),
    )
    merged["direct_dimensions"] = _enrich_dimension_attributes(merged.get("direct_dimensions", []), llm_dimensions)
    merged["snowflake_dimensions"] = _enrich_dimension_attributes(
        merged.get("snowflake_dimensions", []),
        llm_dimensions,
    )

    if allow_llm_schema_changes:
        _apply_llm_dimension_grouping(merged, llm_model, known_table_keys)
        candidate_relationships = _known_llm_relationships_for_model(merged, llm_model, known_table_keys)
        if candidate_relationships:
            merged["relationships"] = candidate_relationships
    elif not merged.get("relationships") and llm_model.get("relationships"):
        candidate_relationships = _known_llm_relationships_for_model(merged, llm_model, known_table_keys)
        if candidate_relationships:
            merged["relationships"] = candidate_relationships

    merged["assumptions"] = _unique(
        _as_string_list(merged.get("assumptions", [])) + _as_string_list(llm_model.get("assumptions", []))
    )
    merged["review_notes"] = _unique(
        _as_string_list(merged.get("review_notes", [])) + _as_string_list(llm_model.get("review_notes", []))
    )
    merged["warnings"] = _unique(
        _as_string_list(merged.get("warnings", [])) + _as_string_list(llm_model.get("warnings", []))
    )

    # if ignored_llm_tables:
    #     merged["warnings"].insert(
    #         0,
    #         "Ignored LLM-only tables not supported by SQL evidence: " + ", ".join(_unique(ignored_llm_tables)),
    #     )

    _refresh_model_output(merged, forced_model_type=forced_model_type or None)
    return merged


def _known_llm_relationships_for_model(
    base_model: dict[str, Any],
    llm_model: dict[str, Any],
    known_table_keys: set[str],
) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for relationship in llm_model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        from_table = _resolve_known_llm_table_name(
            base_model,
            str(relationship.get("from_table", "")).strip(),
            known_table_keys,
        )
        to_table = _resolve_known_llm_table_name(
            base_model,
            str(relationship.get("to_table", "")).strip(),
            known_table_keys,
        )
        if not from_table or not to_table:
            continue
        existing_relationship, reverse_match = _find_existing_relationship(base_model, from_table, to_table)
        existing_cardinality = str(existing_relationship.get("cardinality", "")).strip()
        if reverse_match and existing_cardinality:
            existing_cardinality = _reverse_cardinality(existing_cardinality)
        relationships.append(
            {
                "from_table": from_table,
                "to_table": to_table,
                "from_alias": str(relationship.get("from_alias", "")).strip(),
                "to_alias": str(relationship.get("to_alias", "")).strip(),
                "relationship_type": _normalize_relationship_type(
                    str(relationship.get("relationship_type", "") or "")
                )
                or str(existing_relationship.get("relationship_type", "")).strip(),
                "cardinality": str(relationship.get("cardinality", "")).strip() or existing_cardinality,
                "join_condition": str(relationship.get("join_condition", "")).strip()
                or str(existing_relationship.get("join_condition", "")).strip(),
            }
        )
    return _dedupe_relationships(relationships)


def _find_existing_relationship(
    base_model: dict[str, Any],
    from_table: str,
    to_table: str,
) -> tuple[dict[str, Any], bool]:
    for relationship in base_model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        direct_match = _same_name(str(relationship.get("from_table", "")), from_table) and _same_name(
            str(relationship.get("to_table", "")),
            to_table,
        )
        if direct_match:
            return relationship, False
        reverse_match = _same_name(str(relationship.get("from_table", "")), to_table) and _same_name(
            str(relationship.get("to_table", "")),
            from_table,
        )
        if reverse_match:
            return relationship, True
    return {}, False


def _apply_llm_dimension_grouping(
    base_model: dict[str, Any],
    llm_model: dict[str, Any],
    known_table_keys: set[str],
) -> None:
    direct_dimensions = _known_llm_dimensions_for_model(
        base_model,
        _normalize_dimension_list(llm_model.get("direct_dimensions", [])),
        known_table_keys,
    )
    snowflake_dimensions = _known_llm_dimensions_for_model(
        base_model,
        _normalize_dimension_list(llm_model.get("snowflake_dimensions", [])),
        known_table_keys,
    )
    if not direct_dimensions and not snowflake_dimensions:
        return

    grouped_keys = {
        _dimension_identity_key(dimension)
        for dimension in direct_dimensions + snowflake_dimensions
        if _dimension_identity_key(dimension)
    }

    for dimension in _normalize_dimension_list(base_model.get("direct_dimensions", [])):
        key = _dimension_identity_key(dimension)
        if key and key not in grouped_keys:
            direct_dimensions.append(dimension)
            grouped_keys.add(key)

    for dimension in _normalize_dimension_list(base_model.get("snowflake_dimensions", [])):
        key = _dimension_identity_key(dimension)
        if key and key not in grouped_keys:
            snowflake_dimensions.append(dimension)
            grouped_keys.add(key)

    base_model["direct_dimensions"] = _merge_dimensions(direct_dimensions)
    base_model["snowflake_dimensions"] = _merge_dimensions(snowflake_dimensions)


def _known_llm_dimensions_for_model(
    base_model: dict[str, Any],
    dimensions: list[dict[str, Any]],
    known_table_keys: set[str],
) -> list[dict[str, Any]]:
    known_dimensions: list[dict[str, Any]] = []
    for dimension in dimensions:
        resolved_name = _resolve_known_llm_table_name(
            base_model,
            str(dimension.get("name", "") or dimension.get("physical_table", "")).strip(),
            known_table_keys,
        ) or _resolve_known_llm_table_name(
            base_model,
            str(dimension.get("physical_table", "") or "").strip(),
            known_table_keys,
        )
        if not resolved_name:
            continue

        existing = _find_existing_dimension(base_model, resolved_name)
        source = existing or dimension
        known_dimensions.append(
            {
                "name": str(source.get("name", "") or resolved_name).strip(),
                "physical_table": str(source.get("physical_table", "") or dimension.get("physical_table", "") or resolved_name).strip(),
                "alias": str(source.get("alias", "") or dimension.get("alias", "")).strip(),
                "semantic_role": str(
                    dimension.get("semantic_role", "") or source.get("semantic_role", "")
                ).strip(),
                "attributes": _unique(
                    _as_string_list(source.get("attributes", []))
                    + _as_string_list(dimension.get("attributes", []))
                ),
                "natural_key": str(
                    dimension.get("natural_key", "") or source.get("natural_key", "")
                ).strip(),
            }
        )
    return _merge_dimensions(known_dimensions)


def _find_existing_dimension(base_model: dict[str, Any], table_name: str) -> dict[str, Any]:
    for dimension in _normalize_dimension_list(base_model.get("direct_dimensions", [])) + _normalize_dimension_list(
        base_model.get("snowflake_dimensions", [])
    ):
        if _same_name(str(dimension.get("name", "")), table_name) or _same_name(
            str(dimension.get("physical_table", "")),
            table_name,
        ):
            return dimension
    return {}


def _resolve_known_llm_table_name(
    base_model: dict[str, Any],
    table_name: str,
    known_table_keys: set[str],
) -> str:
    if not table_name:
        return ""
    resolved = _resolve_table_name(base_model, table_name)
    if resolved and _name_key(resolved) in known_table_keys:
        return resolved
    if _name_key(table_name) in known_table_keys:
        return _clean_name(table_name)
    parsed_physical, _parsed_role = _split_dimension_name_role(table_name)
    if parsed_physical and _name_key(parsed_physical) in known_table_keys:
        return _clean_name(parsed_physical)
    return ""


def _enrich_dimension_attributes(
    base_dimensions: list[dict[str, Any]],
    extra_dimensions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    base = _normalize_dimension_list(base_dimensions)
    extra_normalized = _normalize_dimension_list(extra_dimensions)
    extra_lookup_by_identity = {
        _dimension_identity_key(dimension): dimension
        for dimension in extra_normalized
        if _dimension_identity_key(dimension)
    }
    extra_lookup_by_name = {
        _name_key(dimension["name"]): dimension
        for dimension in extra_normalized
        if dimension.get("name")
    }

    enriched: list[dict[str, Any]] = []
    for dimension in base:
        identity_key = _dimension_identity_key(dimension)
        name_key = _name_key(dimension["name"])
        extra = extra_lookup_by_identity.get(identity_key) or extra_lookup_by_name.get(name_key)
        if not extra:
            enriched.append(dimension)
            continue
        enriched.append(
            {
                "name": dimension["name"],
                "physical_table": str(dimension.get("physical_table", "")).strip()
                or str(extra.get("physical_table", "")).strip()
                or dimension["name"],
                "alias": str(dimension.get("alias", "")).strip() or str(extra.get("alias", "")).strip(),
                "semantic_role": str(dimension.get("semantic_role", "")).strip()
                or str(extra.get("semantic_role", "")).strip(),
                "attributes": _unique(
                    _as_string_list(dimension.get("attributes", [])) + _as_string_list(extra.get("attributes", []))
                ),
                "natural_key": str(dimension.get("natural_key", "")).strip()
                or str(extra.get("natural_key", "")).strip(),
            }
        )
    return enriched


def _table_names_from_model(model: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for fact in model.get("fact_tables", []):
        if isinstance(fact, dict) and fact.get("name"):
            names.append(str(fact["name"]))
    for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])):
        names.append(dimension["name"])
    for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", [])):
        names.append(dimension["name"])
    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        from_table = str(relationship.get("from_table", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()
        if from_table:
            names.append(from_table)
        if to_table:
            names.append(to_table)
    return _unique(names)


def _known_table_keys(model: dict[str, Any]) -> set[str]:
    keys = {_name_key(name) for name in _table_names_from_model(model) if _name_key(name)}
    for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])):
        physical_key = _name_key(str(dimension.get("physical_table", "")))
        if physical_key:
            keys.add(physical_key)
    for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", [])):
        physical_key = _name_key(str(dimension.get("physical_table", "")))
        if physical_key:
            keys.add(physical_key)
    return keys


def _build_current_database_context() -> dict[str, Any]:
    datasource, dataset = _resolve_current_rdl_context()
    context = {
        "datasource_name": "",
        "dataset_name": "",
        "provider": "",
        "server": "",
        "database": "",
        "connected": False,
        "inventory_source": "rdl_context",
        "total_tables": 0,
        "available_tables": [],
        "error": "",
    }

    if isinstance(dataset, dict):
        context["dataset_name"] = str(dataset.get("name", "") or "").strip()

    if not isinstance(datasource, dict) or not datasource:
        context["error"] = "Datasource context is not available."
        return context

    context["datasource_name"] = str(datasource.get("name", "") or "").strip()
    context["provider"] = str(datasource.get("provider", "") or "").strip()

    connection_info = datasource.get("connection_info", {})
    if isinstance(connection_info, dict):
        context["server"] = str(connection_info.get("server", "") or "").strip()
        context["database"] = str(connection_info.get("database", "") or "").strip()

    inventory = _cached_datasource_inventory(datasource)
    if not inventory:
        return context

    context["server"] = str(inventory.get("server", "") or context["server"]).strip()
    context["database"] = str(inventory.get("database", "") or context["database"]).strip()
    context["connected"] = bool(inventory.get("connected", False))
    context["error"] = str(inventory.get("error", "") or "").strip()

    table_rows: list[dict[str, Any]] = []
    for table in inventory.get("tables", []) if isinstance(inventory.get("tables", []), list) else []:
        if not isinstance(table, dict):
            continue
        schema_name = str(table.get("schema", "") or "").strip()
        table_name = str(table.get("name", "") or "").strip()
        full_name = str(table.get("full_name", "") or "").strip()
        table_type = str(table.get("table_type", "") or "").strip()
        if not full_name and schema_name and table_name:
            full_name = f"[{schema_name}].[{table_name}]"
        if not table_name and not full_name:
            continue
        table_rows.append(
            {
                "schema": schema_name,
                "name": table_name,
                "full_name": full_name or table_name,
                "table_type": table_type,
                "columns": table.get("columns", []) if isinstance(table.get("columns"), list) else [],
                "primary_key": table.get("primary_key", []) if isinstance(table.get("primary_key"), list) else [],
                "foreign_keys": table.get("foreign_keys", []) if isinstance(table.get("foreign_keys"), list) else [],
            }
        )

    if table_rows:
        context["available_tables"] = table_rows
        context["total_tables"] = len(table_rows)
        context["inventory_source"] = "database_inventory"

    return context


def _cached_datasource_inventory(datasource: dict[str, Any]) -> dict[str, Any]:
    cache_key = _database_inventory_cache_key(datasource)
    if not cache_key:
        return {}

    cache = st.session_state.setdefault("sql_model_assistant_database_inventory_cache", {})
    if cache_key not in cache:
        cache[cache_key] = inspect_sqlserver_datasource_inventory(datasource)

    cached_inventory = cache.get(cache_key, {})
    return cached_inventory if isinstance(cached_inventory, dict) else {}


def _database_inventory_cache_key(datasource: dict[str, Any]) -> str:
    if not isinstance(datasource, dict) or not datasource:
        return ""

    connection_info = datasource.get("connection_info", {})
    if not isinstance(connection_info, dict):
        connection_info = {}

    payload = {
        "name": str(datasource.get("name", "") or ""),
        "provider": str(datasource.get("provider", "") or ""),
        "connection_string_hash": hashlib.md5(
            str(datasource.get("connection_string", "") or "").encode("utf-8")
        ).hexdigest(),
        "server": str(connection_info.get("server", "") or ""),
        "database": str(connection_info.get("database", "") or ""),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _database_context_for_prompt(database_context: dict[str, Any]) -> dict[str, Any]:
    available_tables = database_context.get("available_tables", [])
    table_names: list[str] = []
    if isinstance(available_tables, list):
        for item in available_tables[:200]:
            if not isinstance(item, dict):
                continue
            full_name = str(item.get("full_name", "") or "").strip()
            table_name = str(item.get("name", "") or "").strip()
            if full_name:
                table_names.append(full_name)
            elif table_name:
                table_names.append(table_name)

    return {
        "datasource_name": str(database_context.get("datasource_name", "") or "").strip(),
        "dataset_name": str(database_context.get("dataset_name", "") or "").strip(),
        "provider": str(database_context.get("provider", "") or "").strip(),
        "server": str(database_context.get("server", "") or "").strip(),
        "database": str(database_context.get("database", "") or "").strip(),
        "connected": bool(database_context.get("connected", False)),
        "total_tables": int(database_context.get("total_tables", 0) or 0),
        "available_tables": table_names,
        "error": str(database_context.get("error", "") or "").strip(),
    }


def _latest_user_message(conversation: list[dict[str, Any]]) -> str:
    for message in reversed(conversation):
        if message.get("role") == "user":
            return str(message.get("content", "") or "").strip()
    return ""


def _message_requests_database_inventory(message: str) -> bool:
    folded = _fold_correction_text(message)
    if not folded:
        return False

    table_inventory_patterns = [
        r"\ball\b.*\bavailable tables\b",
        r"\bavailable tables\b",
        r"\bshow\b.*\btables\b",
        r"\blist\b.*\btables\b",
        r"\bwhat tables\b",
        r"\btables available\b",
        r"\btables? disponibles?\b",
        r"\bmontre\b.*\btables?\b",
        r"\bliste\b.*\btables?\b",
        r"\bquelles?\s+tables?\b",
        r"\bbase de donnees\b",
        r"\bdatabase\b",
    ]
    return any(re.search(pattern, folded) for pattern in table_inventory_patterns)


def _message_requests_schema_change(message: str) -> bool:
    folded = _fold_correction_text(message)
    if not folded:
        return False

    change_patterns = [
        r"\b(?:add|create|update|modify|change|remove|delete|drop|rename|set)\b[^\n]{0,80}\b(?:relationship|relation|schema|model|dimension|fact|table|column|attribute|join|cardinality)\b",
        r"\b(?:ajoute(?:r)?|cree(?:r)?|modifie(?:r)?|change(?:r)?|supprime(?:r)?|enleve(?:r)?|renomme(?:r)?|mettre)\b[^\n]{0,80}\b(?:relationship|relation|schema|modele|dimension|fait|table|colonne|attribut|jointure|cardinalite)\b",
    ]
    return any(re.search(pattern, folded, flags=re.IGNORECASE) for pattern in change_patterns)


def _message_requests_schema_view(message: str) -> bool:
    folded = _fold_correction_text(message)
    if not folded:
        return False
    if folded.startswith("analyze this sql query"):
        return True

    view_patterns = [
        r"\b(?:show|display|give|open|view|see|what is|what are|current|latest)\b[^\n]{0,80}\b(?:model|schema|diagram|relationships|relationship|dimensions|dimension|fact tables|fact table|joins?)\b",
        r"\b(?:montre|affiche|donne|voir|ouvre|quel|quels|actuel|dernier)\b[^\n]{0,80}\b(?:modele|schema|diagramme|relations?|dimensions?|faits?|jointures?)\b",
    ]
    return any(re.search(pattern, folded, flags=re.IGNORECASE) for pattern in view_patterns)


def _assistant_display_mode(conversation: list[dict[str, Any]], structured_result: dict[str, Any]) -> str:
    if not isinstance(structured_result, dict) or not structured_result:
        return "chat"

    latest_user_message = _latest_user_message(conversation)
    if not latest_user_message:
        return "chat"
    if _message_requests_database_inventory(latest_user_message):
        return "chat"
    if _message_requests_schema_change(latest_user_message):
        return "model"
    if _message_requests_schema_view(latest_user_message):
        return "model"
    return "chat"


def _new_review_notes_since_previous_model(
    previous_model: dict[str, Any],
    current_model: dict[str, Any],
) -> list[str]:
    previous_notes = set(_as_string_list(previous_model.get("review_notes", [])) if isinstance(previous_model, dict) else [])
    return [
        note
        for note in _as_string_list(current_model.get("review_notes", []))
        if note not in previous_notes
    ]


def _augment_assistant_response_text(
    base_text: str,
    conversation: list[dict[str, Any]],
    previous_model: dict[str, Any],
    current_model: dict[str, Any],
    database_context: dict[str, Any],
) -> str:
    segments: list[str] = []
    base = str(base_text or "").strip()
    if base:
        segments.append(base)

    latest_user_message = _latest_user_message(conversation)
    if _message_requests_database_inventory(latest_user_message):
        datasource_name = str(database_context.get("datasource_name", "") or "").strip() or "Unknown datasource"
        database_name = str(database_context.get("database", "") or "").strip() or "Unknown database"
        total_tables = int(database_context.get("total_tables", 0) or 0)
        table_rows = database_context.get("available_tables", [])
        preview_names: list[str] = []
        if isinstance(table_rows, list):
            for item in table_rows[:15]:
                if not isinstance(item, dict):
                    continue
                preview_name = str(item.get("full_name", "") or item.get("name", "") or "").strip()
                if preview_name:
                    preview_names.append(preview_name)
        inventory_line = (
            f"Datasource `{datasource_name}` points to database `{database_name}` and exposes "
            f"{total_tables} table(s)."
        )
        if preview_names:
            inventory_line += " Preview: " + ", ".join(preview_names) + "."
        inventory_error = str(database_context.get("error", "") or "").strip()
        if inventory_error and not bool(database_context.get("connected", False)):
            inventory_line += f" Inventory warning: {inventory_error}."
        segments.append(inventory_line)

    new_notes = _new_review_notes_since_previous_model(previous_model, current_model)
    if new_notes:
        segments.append("Applied schema changes: " + "; ".join(new_notes[:4]))

    return "\n\n".join(segment for segment in segments if segment).strip()


def _load_optional_llm(llm_config_path: str):
    candidates: list[Path] = []
    if llm_config_path:
        raw = Path(llm_config_path).expanduser()
        candidates.append(raw)
        if not raw.is_absolute():
            candidates.append(ROOT_DIR / raw)
            candidates.append(PROJECT_ROOT / raw)
    candidates.extend([SQL_ASSISTANT_LLM_CONFIG, DEFAULT_LLM_CONFIG, FALLBACK_LLM_CONFIG])

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    for candidate in deduped:
        if not candidate.exists():
            continue
        try:
            return load_llm_from_config(candidate)
        except Exception:
            continue
    return None


def _build_user_prompt(
    sql_query: str,
    conversation: list[dict[str, Any]],
    database_context: dict[str, Any] | None = None,
) -> str:
    history_lines: list[str] = []
    for index, message in enumerate(conversation, start=1):
        history_lines.append(f"{index}. {message['role'].upper()}: {message['content']}")
        if message.get("structured_result"):
            history_lines.append(json.dumps(_prompt_ready_structured_result(message["structured_result"]), indent=2))

    history = "\n".join(history_lines) if history_lines else "1. USER: Analyze the SQL query."
    database_context_payload = _database_context_for_prompt(database_context or {})
    return (
        f"SQL query:\n{sql_query}\n\n"
        f"Datasource context:\n{json.dumps(database_context_payload, indent=2)}\n\n"
        f"Conversation history:\n{history}\n\n"
        "Return JSON only."
    )


def _prompt_ready_structured_result(structured_result: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(structured_result) if isinstance(structured_result, dict) else {}
    database_context = payload.get("database_context", {})
    if isinstance(database_context, dict):
        payload["database_context"] = _database_context_for_prompt(database_context)
    return payload


def _parse_json_payload(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        raise ValueError("Response was not valid JSON.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload = json.loads(text[start : index + 1])
                if isinstance(payload, dict):
                    return payload
                break
    raise ValueError("Response was not valid JSON.")


def _normalize_model(model: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "model_type": _normalize_model_type(str(model.get("model_type", "Unknown") or "Unknown")),
        "schema_confidence": _normalize_confidence(str(model.get("schema_confidence", "") or "")),
        "model_summary": str(model.get("model_summary", "") or "").strip(),
        "fact_tables": [],
        "direct_dimensions": [],
        "snowflake_dimensions": [],
        "relationships": [],
        "review_notes": _as_string_list(model.get("review_notes", [])),
        "assumptions": _as_string_list(model.get("assumptions", [])),
        "warnings": _as_string_list(model.get("warnings", [])),
    }

    for item in model.get("fact_tables", []):
        if not isinstance(item, dict):
            continue
        normalized["fact_tables"].append(
            {
                "name": str(item.get("name", "")).strip(),
                "measures": _as_string_list(item.get("measures", [])),
                "foreign_keys": _as_string_list(item.get("foreign_keys", [])),
            }
        )

    explicit_direct = _normalize_dimension_list(model.get("direct_dimensions", []))
    explicit_snowflake = _normalize_dimension_list(model.get("snowflake_dimensions", []))
    legacy_dimensions = _normalize_dimension_list(model.get("dimension_tables", []))
    branch_hints = _branch_dimension_names(model.get("snowflake_branches", []))
    branch_dimensions = [{"name": table, "attributes": [], "natural_key": ""} for table in branch_hints]

    if explicit_direct or explicit_snowflake:
        normalized["direct_dimensions"] = explicit_direct
        normalized["snowflake_dimensions"] = explicit_snowflake
    else:
        normalized["direct_dimensions"] = legacy_dimensions
        normalized["snowflake_dimensions"] = branch_dimensions

    for item in model.get("relationships", []):
        if not isinstance(item, dict):
            continue
        normalized["relationships"].append(
            {
                "from_table": str(item.get("from_table", "")).strip(),
                "to_table": str(item.get("to_table", "")).strip(),
                "from_alias": str(item.get("from_alias", "")).strip(),
                "to_alias": str(item.get("to_alias", "")).strip(),
                "relationship_type": _normalize_relationship_type(str(item.get("relationship_type", "") or "")),
                "cardinality": str(item.get("cardinality", "")).strip(),
                "join_condition": str(item.get("join_condition", "")).strip(),
            }
        )

    _refresh_model_output(normalized)
    return normalized


def _normalize_model_type(value: str) -> str:
    lowered = value.strip().lower()
    if lowered == "star":
        return "Star"
    if lowered == "snowflake":
        return "Snowflake"
    if lowered == "hybrid":
        return "Hybrid"
    return "Unknown"


def _normalize_confidence(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"high", "medium", "low"}:
        return lowered
    return ""


def _normalize_relationship_type(value: str) -> str:
    lowered = value.strip().lower().replace("-", "_")
    if lowered in {"fact_to_dimension", "dimension_to_dimension"}:
        return lowered
    return ""


def _split_dimension_name_role(name: str) -> tuple[str, str]:
    text = str(name or "").strip()
    match = re.match(r"^(.*?)\s*\[role:\s*([^\]]+)\](?:\s*\(\d+\))?\s*$", text, flags=re.IGNORECASE)
    if not match:
        return text, ""
    return match.group(1).strip(), match.group(2).strip()


def _normalize_dimension_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    dimensions: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            name = item.strip()
            if name:
                parsed_physical, parsed_role = _split_dimension_name_role(name)
                dimensions.append(
                    {
                        "name": name,
                        "physical_table": parsed_physical or name,
                        "alias": "",
                        "semantic_role": parsed_role,
                        "attributes": [],
                        "natural_key": "",
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        parsed_physical, parsed_role = _split_dimension_name_role(name)
        physical_table = str(item.get("physical_table", "")).strip() or parsed_physical or name
        semantic_role = str(item.get("semantic_role", "")).strip() or parsed_role
        dimensions.append(
            {
                "name": name,
                "physical_table": physical_table,
                "alias": str(item.get("alias", "")).strip(),
                "semantic_role": semantic_role,
                "attributes": _as_string_list(item.get("attributes", [])),
                "natural_key": str(item.get("natural_key", "")).strip(),
            }
        )
    return dimensions


def _dimension_identity_key(dimension: dict[str, Any]) -> str:
    alias = str(dimension.get("alias", "")).strip().lower()
    if alias:
        return f"alias:{alias}"

    physical_key = _name_key(str(dimension.get("physical_table", "")))
    role_key = _name_key(str(dimension.get("semantic_role", "")))
    if physical_key and role_key:
        return f"physical:{physical_key}|role:{role_key}"

    name = _name_key(str(dimension.get("name", "")).strip())
    if name:
        return f"name:{name}"
    return ""


def _merge_dimensions(*dimension_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for dimensions in dimension_groups:
        for dimension in dimensions:
            normalized = _normalize_dimension_list([dimension])
            if not normalized:
                continue
            candidate = normalized[0]
            name = str(candidate.get("name", "")).strip()
            if not name:
                continue
            key = _dimension_identity_key(candidate)
            if not key:
                continue
            if key not in merged:
                merged[key] = {
                    "name": name,
                    "physical_table": str(candidate.get("physical_table", "")).strip() or name,
                    "alias": str(candidate.get("alias", "")).strip(),
                    "semantic_role": str(candidate.get("semantic_role", "")).strip(),
                    "attributes": [],
                    "natural_key": "",
                }
                order.append(key)
            merged[key]["attributes"] = _unique(
                merged[key]["attributes"] + _as_string_list(candidate.get("attributes", []))
            )
            if not merged[key]["physical_table"]:
                merged[key]["physical_table"] = str(candidate.get("physical_table", "")).strip()
            if not merged[key]["alias"]:
                merged[key]["alias"] = str(candidate.get("alias", "")).strip()
            if not merged[key]["semantic_role"]:
                merged[key]["semantic_role"] = str(candidate.get("semantic_role", "")).strip()

            natural_key = str(candidate.get("natural_key", "")).strip()
            if natural_key and not merged[key]["natural_key"]:
                merged[key]["natural_key"] = natural_key
    return [merged[key] for key in order]


def _branch_dimension_names(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for branch in value:
        if not isinstance(branch, dict):
            continue
        for table_name in _as_string_list(branch.get("branch_tables", [])):
            if table_name.strip():
                names.add(table_name.strip())
    return names


def _infer_relationship_type(from_table: str, to_table: str, fact_keys: set[str]) -> str:
    if _name_key(from_table) in fact_keys or _name_key(to_table) in fact_keys:
        return "fact_to_dimension"
    return "dimension_to_dimension"


def _table_graph(relationships: list[dict[str, Any]]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for relationship in relationships:
        left_key = _name_key(str(relationship.get("from_table", "")))
        right_key = _name_key(str(relationship.get("to_table", "")))
        if not left_key or not right_key:
            continue
        graph.setdefault(left_key, set()).add(right_key)
        graph.setdefault(right_key, set()).add(left_key)
    return graph


def _table_distances(start_key: str, graph: dict[str, set[str]]) -> dict[str, int]:
    if not start_key:
        return {}
    distances: dict[str, int] = {start_key: 0}
    queue = [start_key]
    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        for neighbor in graph.get(current, set()):
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)
    return distances


def _shortest_table_path(start_key: str, target_key: str, graph: dict[str, set[str]]) -> list[str]:
    if not start_key or not target_key:
        return []
    if start_key == target_key:
        return [start_key]

    parents: dict[str, str | None] = {start_key: None}
    queue = [start_key]
    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        for neighbor in sorted(graph.get(current, set())):
            if neighbor in parents:
                continue
            parents[neighbor] = current
            if neighbor == target_key:
                break
            queue.append(neighbor)
        if target_key in parents:
            break

    if target_key not in parents:
        return []

    path: list[str] = []
    cursor: str | None = target_key
    while cursor is not None:
        path.append(cursor)
        cursor = parents.get(cursor)
    path.reverse()
    return path


def _nearest_table_key(start_key: str, candidate_keys: set[str], graph: dict[str, set[str]]) -> str:
    if not start_key or not candidate_keys:
        return ""

    distances = _table_distances(start_key, graph)
    ranked = sorted(
        ((distances[key], key) for key in candidate_keys if key in distances),
        key=lambda item: (item[0], item[1]),
    )
    if not ranked:
        return ""
    return ranked[0][1]


def _snowflake_origin_lookup(
    fact_table: str,
    direct_dimensions: list[dict[str, Any]],
    snowflake_dimensions: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, str]:
    fact_key = _name_key(fact_table)
    graph = _table_graph(relationships)
    direct_lookup = {
        _name_key(dimension["name"]): dimension["name"]
        for dimension in direct_dimensions
        if dimension.get("name")
    }
    direct_keys = set(direct_lookup.keys())

    origins: dict[str, str] = {}
    for snowflake_dimension in snowflake_dimensions:
        snowflake_name = str(snowflake_dimension.get("name", "")).strip()
        snowflake_key = _name_key(snowflake_name)
        if not snowflake_key:
            continue

        origin = ""
        if fact_key:
            path = _shortest_table_path(fact_key, snowflake_key, graph)
            for path_key in path[1:]:
                if path_key in direct_lookup:
                    origin = direct_lookup[path_key]
                    break
        if not origin:
            nearest_key = _nearest_table_key(snowflake_key, direct_keys, graph)
            if nearest_key:
                origin = direct_lookup.get(nearest_key, "")

        origins[snowflake_key] = origin

    return origins


def _classify_dimensions(
    all_dimensions: list[dict[str, Any]],
    fact_table: str,
    relationships: list[dict[str, Any]],
    direct_hints: set[str],
    snowflake_hints: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fact_key = _name_key(fact_table)
    distances = _table_distances(fact_key, _table_graph(relationships)) if fact_key else {}

    direct_dimensions: list[dict[str, Any]] = []
    snowflake_dimensions: list[dict[str, Any]] = []
    for dimension in all_dimensions:
        name = str(dimension.get("name", "")).strip()
        if not name or (fact_key and _name_key(name) == fact_key):
            continue
        key = _name_key(name)
        distance = distances.get(key)
        if key in snowflake_hints and key not in direct_hints:
            snowflake_dimensions.append(dimension)
            continue
        if key in direct_hints and key not in snowflake_hints:
            direct_dimensions.append(dimension)
            continue
        if distance is not None and distance > 1:
            snowflake_dimensions.append(dimension)
            continue
        if key in direct_hints or distance == 1 or distance is None:
            direct_dimensions.append(dimension)
            continue
        snowflake_dimensions.append(dimension)

    return _merge_dimensions(direct_dimensions), _merge_dimensions(snowflake_dimensions)


def _extract_fact_keys_from_relationship(relationship: dict[str, Any], fact_table: str) -> list[str]:
    if relationship.get("relationship_type") != "fact_to_dimension":
        return []

    pairs = _extract_join_pairs(str(relationship.get("join_condition", "")))
    if not pairs:
        return []

    if _same_name(str(relationship.get("from_table", "")), fact_table):
        return _unique([pair["left_column"] for pair in pairs if pair["left_column"]])
    if _same_name(str(relationship.get("to_table", "")), fact_table):
        return _unique([pair["right_column"] for pair in pairs if pair["right_column"]])
    return []


def _dedupe_relationships(relationships: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str, str]] = set()

    for relationship in relationships:
        from_table = str(relationship.get("from_table", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()
        from_alias = str(relationship.get("from_alias", "")).strip().lower()
        to_alias = str(relationship.get("to_alias", "")).strip().lower()
        relationship_type = str(relationship.get("relationship_type", "")).strip().lower()
        cardinality = str(relationship.get("cardinality", "")).strip().lower()
        join_condition = " ".join(str(relationship.get("join_condition", "")).split()).lower()

        forward_key = (
            _name_key(from_table),
            _name_key(to_table),
            from_alias,
            to_alias,
            relationship_type,
            cardinality,
            join_condition,
        )
        reverse_key = (
            _name_key(to_table),
            _name_key(from_table),
            to_alias,
            from_alias,
            relationship_type,
            _reverse_cardinality(cardinality),
            join_condition,
        )

        if forward_key in seen or reverse_key in seen:
            continue

        seen.add(forward_key)
        deduped.append(relationship)

    return deduped


def _refresh_model_output(model: dict[str, Any], forced_model_type: str | None = None) -> None:
    fact_tables: list[dict[str, Any]] = []
    for item in model.get("fact_tables", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        fact_tables.append(
            {
                "name": name,
                "measures": _as_string_list(item.get("measures", [])),
                "foreign_keys": _as_string_list(item.get("foreign_keys", [])),
            }
        )
    model["fact_tables"] = fact_tables

    fact_keys = {_name_key(fact["name"]) for fact in fact_tables if fact.get("name")}
    relationships: list[dict[str, Any]] = []
    for item in model.get("relationships", []):
        if not isinstance(item, dict):
            continue
        from_table = str(item.get("from_table", "")).strip()
        to_table = str(item.get("to_table", "")).strip()
        if not from_table or not to_table:
            continue
        relationship_type = _normalize_relationship_type(str(item.get("relationship_type", "") or ""))
        if not relationship_type:
            relationship_type = _infer_relationship_type(from_table, to_table, fact_keys)
        relationships.append(
            {
                "from_table": from_table,
                "to_table": to_table,
                "from_alias": str(item.get("from_alias", "")).strip(),
                "to_alias": str(item.get("to_alias", "")).strip(),
                "relationship_type": relationship_type,
                "cardinality": str(item.get("cardinality", "")).strip(),
                "join_condition": str(item.get("join_condition", "")).strip(),
            }
        )
    relationships = _dedupe_relationships(relationships)
    model["relationships"] = relationships

    direct_dimensions = _normalize_dimension_list(model.get("direct_dimensions", []))
    snowflake_dimensions = _normalize_dimension_list(model.get("snowflake_dimensions", []))
    all_dimensions = _merge_dimensions(direct_dimensions, snowflake_dimensions)
    known_dimension_name_keys = {_name_key(str(dimension.get("name", ""))) for dimension in all_dimensions}
    primary_fact = fact_tables[0]["name"] if fact_tables else ""

    for relationship in relationships:
        for table_name in (relationship["from_table"], relationship["to_table"]):
            if primary_fact and _same_name(table_name, primary_fact):
                continue
            name_key = _name_key(table_name)
            if name_key and name_key in known_dimension_name_keys:
                continue
            parsed_physical, parsed_role = _split_dimension_name_role(table_name)
            all_dimensions = _merge_dimensions(
                all_dimensions,
                [
                    {
                        "name": table_name,
                        "physical_table": parsed_physical or table_name,
                        "alias": "",
                        "semantic_role": parsed_role,
                        "attributes": [],
                        "natural_key": "",
                    }
                ],
            )
            if name_key:
                known_dimension_name_keys.add(name_key)

    direct_hints = {_name_key(dimension["name"]) for dimension in direct_dimensions}
    snowflake_hints = {_name_key(dimension["name"]) for dimension in snowflake_dimensions}
    classified_direct, classified_snowflake = _classify_dimensions(
        all_dimensions=all_dimensions,
        fact_table=primary_fact,
        relationships=relationships,
        direct_hints=direct_hints,
        snowflake_hints=snowflake_hints,
    )
    model["direct_dimensions"] = classified_direct
    model["snowflake_dimensions"] = classified_snowflake

    for fact in model["fact_tables"]:
        derived_keys: list[str] = []
        for relationship in relationships:
            derived_keys.extend(_extract_fact_keys_from_relationship(relationship, fact["name"]))
        fact["foreign_keys"] = _unique(derived_keys) or _unique(fact.get("foreign_keys", []))

    derived_type = _derive_model_type(
        fact_table=primary_fact,
        relationships=relationships,
        direct_dimensions=model["direct_dimensions"],
        snowflake_dimensions=model["snowflake_dimensions"],
    )
    if forced_model_type:
        model["model_type"] = _normalize_model_type(forced_model_type)
    elif derived_type != "Unknown":
        model["model_type"] = derived_type
    else:
        model["model_type"] = _normalize_model_type(str(model.get("model_type", "Unknown") or "Unknown"))

    model["schema_confidence"] = _derive_schema_confidence(model)
    model["model_summary"] = _build_model_summary(model)
    existing_notes = _as_string_list(model.get("review_notes", []))
    model["review_notes"] = _unique(existing_notes + _derived_review_notes(model))
    model["assumptions"] = _as_string_list(model.get("assumptions", []))
    model["warnings"] = _as_string_list(model.get("warnings", []))


def _derive_model_type(
    fact_table: str,
    relationships: list[dict[str, Any]],
    direct_dimensions: list[dict[str, Any]],
    snowflake_dimensions: list[dict[str, Any]],
) -> str:
    if not fact_table:
        return "Unknown"
    has_fact_to_dimension = any(
        relationship.get("relationship_type") == "fact_to_dimension" for relationship in relationships
    )
    has_dimension_to_dimension = any(
        relationship.get("relationship_type") == "dimension_to_dimension" for relationship in relationships
    )
    has_snowflake = bool(snowflake_dimensions) or has_dimension_to_dimension
    has_direct = bool(direct_dimensions)

    if has_fact_to_dimension and not has_snowflake:
        return "Star"
    if has_dimension_to_dimension and has_fact_to_dimension:
        return "Snowflake"
    if has_snowflake and has_direct:
        return "Hybrid"
    if has_snowflake:
        return "Snowflake"
    if has_fact_to_dimension:
        return "Star"
    return "Unknown"


def _derive_schema_confidence(model: dict[str, Any]) -> str:
    score = 0
    if model.get("fact_tables"):
        score += 2
    if model.get("relationships"):
        score += 2
    if any(relationship.get("join_condition") for relationship in model.get("relationships", [])):
        score += 1
    if any(
        relationship.get("relationship_type") == "fact_to_dimension"
        for relationship in model.get("relationships", [])
    ):
        score += 1
    if model.get("warnings"):
        score -= 2
    if not model.get("direct_dimensions") and not model.get("snowflake_dimensions"):
        score -= 1

    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _build_model_summary(model: dict[str, Any]) -> str:
    fact_name = (
        str(model["fact_tables"][0].get("name", "")).split(".")[-1]
        if model.get("fact_tables")
        else "an unknown fact table"
    )
    direct_count = len(model.get("direct_dimensions", []))
    snowflake_count = len(model.get("snowflake_dimensions", []))
    return (
        f"{model.get('model_type', 'Unknown')} model centered on {fact_name} with "
        f"{direct_count} direct dimensions and {snowflake_count} snowflake dimensions."
    )


def _derived_review_notes(model: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not model.get("relationships"):
        notes.append("No joins were parsed; verify the SQL join conditions.")
    if model.get("snowflake_dimensions"):
        notes.append("Dimension-to-dimension paths were detected and classified as snowflake dimensions.")
    all_dimensions = _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
        model.get("snowflake_dimensions", [])
    )
    by_physical: dict[str, dict[str, Any]] = {}
    for dimension in all_dimensions:
        physical_label = str(dimension.get("physical_table", "")).strip() or str(dimension.get("name", "")).strip()
        physical_key = _name_key(physical_label)
        if not physical_key:
            continue
        role = str(dimension.get("semantic_role", "")).strip() or str(dimension.get("alias", "")).strip()
        bucket = by_physical.setdefault(physical_key, {"label": physical_label, "roles": set()})
        bucket["roles"].add(role or str(dimension.get("name", "")))
    role_playing_tables = [bucket["label"] for bucket in by_physical.values() if len(bucket["roles"]) > 1]
    if role_playing_tables:
        notes.append(
            "Role-playing dimensions detected on physical tables: " + ", ".join(_unique(role_playing_tables))
        )
    if model.get("fact_tables"):
        fact = model["fact_tables"][0]
        if model.get("direct_dimensions") and not fact.get("foreign_keys"):
            notes.append("Fact foreign keys could not be extracted from join predicates.")
    if model.get("model_type") == "Unknown":
        notes.append("Schema type could not be confidently determined from the available SQL structure.")
    return notes


def _format_role_name(value: str) -> str:
    token = _clean_name(value).strip()
    if not token:
        return ""
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", token) if part]
    if len(parts) > 1:
        return "".join(part[:1].upper() + part[1:] for part in parts)
    return token[:1].upper() + token[1:]


def _infer_semantic_role_from_fk(fact_fk: str, alias: str, physical_table: str) -> str:
    fk = _clean_name(fact_fk).strip()
    if fk:
        role = re.sub(r"(?i)(?:_|)(id|key|code|number|num)$", "", fk).strip("_ ")
        role_name = _format_role_name(role)
        if role_name:
            return role_name

    alias_role = _format_role_name(alias)
    if len(alias_role) > 2:
        return alias_role

    physical = _clean_name(physical_table).split(".")[-1]
    physical = re.sub(r"(?i)^dim_?", "", physical)
    return _format_role_name(physical)


def _fact_side_foreign_keys_for_alias(
    dimension_alias: str,
    joins: list[dict[str, str]],
    fact_aliases: set[str],
) -> list[str]:
    alias_key = dimension_alias.lower()
    keys: list[str] = []
    for join in joins:
        for pair in _extract_join_pairs(join.get("condition", "")):
            left_alias = pair["left_alias"].lower()
            right_alias = pair["right_alias"].lower()
            if left_alias in fact_aliases and right_alias == alias_key:
                keys.append(pair["left_column"])
            elif right_alias in fact_aliases and left_alias == alias_key:
                keys.append(pair["right_column"])
    return _unique(keys)


def _join_paths_for_alias(dimension_alias: str, joins: list[dict[str, str]]) -> list[str]:
    alias_key = dimension_alias.lower()
    paths: list[str] = []
    for join in joins:
        condition = " ".join(str(join.get("condition", "")).split())
        if not condition:
            continue
        pairs = _extract_join_pairs(condition)
        if any(pair["left_alias"].lower() == alias_key or pair["right_alias"].lower() == alias_key for pair in pairs):
            paths.append(condition)
    return _unique(paths)


def _build_semantic_dimensions(
    table_refs: list[tuple[str, str]],
    alias_to_table: dict[str, str],
    joins: list[dict[str, str]],
    select_columns: list[dict[str, Any]],
    fact_table: str,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    fact_aliases = {alias.lower() for table, alias in table_refs if _same_name(table, fact_table)}
    candidates: list[dict[str, Any]] = []
    for table, alias in table_refs:
        if _same_name(table, fact_table):
            continue

        alias_key = alias.lower()
        attributes = [
            column["output_name"]
            for column in select_columns
            if not column["is_measure"] and str(column.get("table_alias", "")).lower() == alias_key
        ]
        fact_foreign_keys = _fact_side_foreign_keys_for_alias(alias, joins, fact_aliases)
        natural_key = _dimension_key_from_joins(
            table_aliases={alias},
            joins=joins,
            alias_to_table=alias_to_table,
            fact_table=fact_table,
        )
        semantic_role = _infer_semantic_role_from_fk(
            fact_foreign_keys[0] if fact_foreign_keys else "",
            alias=alias,
            physical_table=table,
        )
        candidates.append(
            {
                "physical_table": table,
                "alias": alias,
                "semantic_role": semantic_role,
                "fact_foreign_keys": fact_foreign_keys,
                "join_paths": _join_paths_for_alias(alias, joins),
                "attributes": _unique(attributes),
                "natural_key": natural_key,
            }
        )

    by_physical_table: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_physical_table.setdefault(_name_key(candidate["physical_table"]), []).append(candidate)

    semantic_dimensions: list[dict[str, Any]] = []
    alias_to_semantic_name: dict[str, str] = {}
    used_names: set[str] = set()

    for group in by_physical_table.values():
        signatures = {
            (
                tuple(_as_string_list(item.get("fact_foreign_keys", []))),
                tuple(_as_string_list(item.get("join_paths", []))),
            )
            for item in group
        }
        is_role_playing = len(group) > 1 and len(signatures) > 1

        if not is_role_playing:
            merged_attributes: list[str] = []
            merged_natural_key = ""
            for item in group:
                merged_attributes.extend(_as_string_list(item.get("attributes", [])))
                if not merged_natural_key:
                    merged_natural_key = str(item.get("natural_key", "")).strip()

            lead = group[0]
            semantic_name = str(lead["physical_table"])
            semantic_dimensions.append(
                {
                    "name": semantic_name,
                    "physical_table": str(lead["physical_table"]),
                    "alias": str(lead["alias"]),
                    "semantic_role": str(lead.get("semantic_role", "")),
                    "attributes": _unique(merged_attributes),
                    "natural_key": merged_natural_key,
                }
            )
            for item in group:
                alias_to_semantic_name[str(item["alias"]).lower()] = semantic_name
            used_names.add(_name_key(semantic_name))
            continue

        for item in group:
            role = str(item.get("semantic_role", "")).strip() or _format_role_name(str(item.get("alias", "")))
            semantic_name = f"{item['physical_table']} [role: {role}]"
            base_name = semantic_name
            suffix = 2
            while _name_key(semantic_name) in used_names:
                semantic_name = f"{base_name} ({suffix})"
                suffix += 1

            semantic_dimensions.append(
                {
                    "name": semantic_name,
                    "physical_table": str(item["physical_table"]),
                    "alias": str(item["alias"]),
                    "semantic_role": role,
                    "attributes": _as_string_list(item.get("attributes", [])),
                    "natural_key": str(item.get("natural_key", "")).strip(),
                }
            )
            alias_to_semantic_name[str(item["alias"]).lower()] = semantic_name
            used_names.add(_name_key(semantic_name))

    return _merge_dimensions(semantic_dimensions), alias_to_semantic_name


def _apply_semantic_names_to_relationships(
    relationships: list[dict[str, str]],
    alias_to_semantic_name: dict[str, str],
    fact_aliases: set[str],
    fact_table: str,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        from_alias = str(relationship.get("from_alias", "")).strip()
        to_alias = str(relationship.get("to_alias", "")).strip()

        from_table = str(relationship.get("from_table", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()

        if from_alias.lower() in fact_aliases:
            from_table = fact_table
        elif from_alias.lower() in alias_to_semantic_name:
            from_table = alias_to_semantic_name[from_alias.lower()]

        if to_alias.lower() in fact_aliases:
            to_table = fact_table
        elif to_alias.lower() in alias_to_semantic_name:
            to_table = alias_to_semantic_name[to_alias.lower()]

        enriched = {
            "from_table": from_table,
            "to_table": to_table,
            "relationship_type": str(relationship.get("relationship_type", "")).strip(),
            "cardinality": str(relationship.get("cardinality", "")).strip(),
            "join_condition": str(relationship.get("join_condition", "")).strip(),
        }
        if from_alias:
            enriched["from_alias"] = from_alias
        if to_alias:
            enriched["to_alias"] = to_alias
        output.append(enriched)
    return output


def _heuristic_model(
    sql_query: str,
    conversation: list[dict[str, Any]],
    apply_corrections: bool = True,
) -> dict[str, Any]:
    cleaned_sql = _sanitize_sql(sql_query)
    cte_primary_tables = _extract_cte_primary_tables(cleaned_sql)
    raw_table_refs = _extract_table_refs(cleaned_sql)
    table_refs = _resolve_table_refs(raw_table_refs, cte_primary_tables)
    joins = _extract_joins(cleaned_sql)
    select_columns = _extract_select_columns(cleaned_sql)
    group_by = _extract_group_by(cleaned_sql)

    if not table_refs:
        return _normalize_model(
            {
                "model_type": "Unknown",
                "schema_confidence": "low",
                "model_summary": "Unable to infer a dimensional model because source tables were not detected.",
                "review_notes": ["The parser could not identify tables from FROM/JOIN clauses."],
                "assumptions": ["The SQL parser could not confidently identify source tables."],
                "warnings": ["Use a configured LLM endpoint for richer modeling output."],
            }
        )

    alias_to_table = _build_alias_to_table_map(table_refs)
    tables = _unique([table for table, _alias in table_refs])
    fact_table = _pick_fact_table(tables, select_columns, table_refs, joins)
    fact_aliases = {alias.lower() for table, alias in table_refs if _same_name(table, fact_table)}
    semantic_dimensions, alias_to_semantic_name = _build_semantic_dimensions(
        table_refs=table_refs,
        alias_to_table=alias_to_table,
        joins=joins,
        select_columns=select_columns,
        fact_table=fact_table,
    )

    relationships: list[dict[str, Any]] = []
    for join in joins:
        relationship = _build_relationship_from_join(
            join=join,
            alias_to_table=alias_to_table,
            fact_table=fact_table,
            group_by=group_by,
            select_columns=select_columns,
        )
        if relationship:
            relationships.append(relationship)
    relationships = _apply_semantic_names_to_relationships(
        relationships=relationships,
        alias_to_semantic_name=alias_to_semantic_name,
        fact_aliases=fact_aliases,
        fact_table=fact_table,
    )

    fact_measures = [
        column["output_name"]
        for column in select_columns
        if column["is_measure"]
        and _same_name(_resolve_alias_table(alias_to_table, column["table_alias"]), fact_table)
    ]
    if not fact_measures:
        fact_measures = [column["output_name"] for column in select_columns if column["is_measure"]]

    fact_foreign_keys: list[str] = []
    for relationship in relationships:
        fact_foreign_keys.extend(_extract_fact_keys_from_relationship(relationship, fact_table))

    fact_tables = [
        {
            "name": fact_table,
            "measures": _unique(fact_measures),
            "foreign_keys": _unique(fact_foreign_keys),
        }
    ]

    direct_dimensions, snowflake_dimensions = _classify_dimensions(
        all_dimensions=semantic_dimensions,
        fact_table=fact_table,
        relationships=relationships,
        direct_hints=set(),
        snowflake_hints=set(),
    )

    model = _normalize_model(
        {
            "fact_tables": fact_tables,
            "direct_dimensions": direct_dimensions,
            "snowflake_dimensions": snowflake_dimensions,
            "relationships": relationships,
            "assumptions": [
                "Fact and dimension roles were inferred from table names, aggregate usage, and join paths.",
            ],
            "warnings": [],
        }
    )

    if apply_corrections:
        _apply_follow_up_corrections(model, conversation)
    return model


def _extract_table_refs(sql_query: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"\b(?:from|(?:(?:inner|left|right|full|cross)(?:\s+outer)?\s+)?join)\s+([A-Za-z0-9_\[\]\.]+)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
        flags=re.IGNORECASE,
    )
    table_refs: list[tuple[str, str]] = []
    for raw_table, raw_alias in pattern.findall(sql_query):
        table = _clean_name(raw_table)
        if not table:
            continue
        alias = raw_alias.strip() if raw_alias else table.split(".")[-1]
        if (table, alias) not in table_refs:
            table_refs.append((table, alias))
    return table_refs


def _extract_joins(sql_query: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"\b(?:(inner|left|right|full|cross)(?:\s+outer)?\s+)?join\s+([A-Za-z0-9_\[\]\.]+)"
        r"(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?\s+on\s+"
        r"(.*?)(?=\b(?:(?:inner|left|right|full|cross)(?:\s+outer)?\s+)?join\b|\bwhere\b|\bgroup\s+by\b|\border\s+by\b|\bhaving\b|\bqualify\b|\bunion\b|$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    joins = []
    for raw_join_type, raw_table, raw_alias, condition in pattern.findall(sql_query):
        joins.append(
            {
                "table": _clean_name(raw_table),
                "alias": raw_alias.strip() if raw_alias else _clean_name(raw_table).split(".")[-1],
                "join_type": (raw_join_type or "inner").strip().lower(),
                "condition": " ".join(condition.split()),
            }
        )
    return joins


def _extract_join_pairs(condition: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
        flags=re.IGNORECASE,
    )
    pairs: list[dict[str, str]] = []
    for match in pattern.finditer(condition):
        pairs.append(
            {
                "left_alias": match.group(1),
                "left_column": _clean_name(match.group(2)),
                "right_alias": match.group(3),
                "right_column": _clean_name(match.group(4)),
            }
        )
    return pairs


def _build_relationship_from_join(
    join: dict[str, str],
    alias_to_table: dict[str, str],
    fact_table: str,
    group_by: list[str],
    select_columns: list[dict[str, Any]],
) -> dict[str, str] | None:
    condition = join.get("condition", "")
    pairs = _extract_join_pairs(condition)
    join_alias = join.get("alias", "")
    join_type = join.get("join_type", "inner")

    left_alias = ""
    right_alias = ""
    for pair in pairs:
        if join_alias and join_alias.lower() in {pair["left_alias"].lower(), pair["right_alias"].lower()}:
            if pair["left_alias"].lower() == join_alias.lower():
                left_alias, right_alias = pair["right_alias"], pair["left_alias"]
            else:
                left_alias, right_alias = pair["left_alias"], pair["right_alias"]
            break

    if not left_alias or not right_alias:
        if pairs:
            left_alias, right_alias = pairs[0]["left_alias"], pairs[0]["right_alias"]
        else:
            left_alias, right_alias = _aliases_from_join(condition)

    left_table = _resolve_alias_table(alias_to_table, left_alias)
    right_table = _resolve_alias_table(alias_to_table, right_alias)
    if not left_table or not right_table:
        return None

    from_table, to_table = left_table, right_table
    relationship_type = "dimension_to_dimension"
    if _same_name(left_table, fact_table) and not _same_name(right_table, fact_table):
        relationship_type = "fact_to_dimension"
    elif _same_name(right_table, fact_table) and not _same_name(left_table, fact_table):
        relationship_type = "fact_to_dimension"

    cardinality = _infer_join_cardinality(
        from_table=from_table,
        to_table=to_table,
        left_alias=left_alias,
        right_alias=right_alias,
        join_pairs=pairs,
        fact_table=fact_table,
        join_type=join_type,
        group_by=group_by,
        select_columns=select_columns,
    )

    return {
        "from_table": from_table,
        "to_table": to_table,
        "from_alias": left_alias,
        "to_alias": right_alias,
        "relationship_type": relationship_type,
        "cardinality": cardinality,
        "join_condition": condition,
    }


def _dimension_key_from_joins(
    table_aliases: set[str],
    joins: list[dict[str, str]],
    alias_to_table: dict[str, str],
    fact_table: str,
) -> str:
    alias_keys = {alias.lower() for alias in table_aliases}
    candidates: list[tuple[int, str]] = []
    for join in joins:
        for pair in _extract_join_pairs(join["condition"]):
            if pair["left_alias"].lower() in alias_keys:
                other_table = _resolve_alias_table(alias_to_table, pair["right_alias"])
                priority = 0 if _same_name(other_table, fact_table) else 1
                candidates.append((priority, pair["left_column"]))
            if pair["right_alias"].lower() in alias_keys:
                other_table = _resolve_alias_table(alias_to_table, pair["left_alias"])
                priority = 0 if _same_name(other_table, fact_table) else 1
                candidates.append((priority, pair["right_column"]))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _extract_select_columns(sql_query: str) -> list[dict[str, Any]]:
    main_query = _extract_main_query(sql_query)
    select_section = _extract_top_level_clause(main_query, "select", ["from"])
    if not select_section:
        return []

    columns = []
    for chunk in _split_sql_list(select_section):
        expression = " ".join(chunk.split())
        alias_match = re.search(r"\bas\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)$", expression, flags=re.IGNORECASE)
        if not alias_match:
            alias_match = re.search(r"\)\s+(\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)$", expression)
        output_name = _clean_name(alias_match.group(1)) if alias_match else expression
        column_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)", expression)
        table_alias = column_match.group(1) if column_match else ""
        is_measure = any(re.search(fr"\b{func}\s*\(", expression, flags=re.IGNORECASE) for func in AGG_FUNCS)
        columns.append(
            {
                "expression": expression,
                "output_name": output_name,
                "table_alias": table_alias,
                "is_measure": is_measure,
            }
        )
    return columns


def _extract_group_by(sql_query: str) -> list[str]:
    main_query = _extract_main_query(sql_query)
    group_section = _extract_top_level_clause(main_query, "group by", ["having", "order by", "qualify", "union"])
    if not group_section:
        return []
    return [" ".join(chunk.split()) for chunk in _split_sql_list(group_section)]


def _split_sql_list(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _pick_fact_table(
    tables: list[str],
    select_columns: list[dict[str, Any]],
    table_refs: list[tuple[str, str]],
    joins: list[dict[str, str]],
) -> str:
    if not tables:
        return ""

    alias_to_table = _build_alias_to_table_map(table_refs)
    measure_tables = {
        _resolve_alias_table(alias_to_table, column["table_alias"])
        for column in select_columns
        if column["is_measure"] and column["table_alias"]
    }

    adjacency: dict[str, set[str]] = {_name_key(table): set() for table in tables}
    for join in joins:
        pairs = _extract_join_pairs(join.get("condition", ""))
        if pairs:
            first_pair = pairs[0]
            left_table = _resolve_alias_table(alias_to_table, first_pair["left_alias"])
            right_table = _resolve_alias_table(alias_to_table, first_pair["right_alias"])
        else:
            left_alias, right_alias = _aliases_from_join(join.get("condition", ""))
            left_table = _resolve_alias_table(alias_to_table, left_alias)
            right_table = _resolve_alias_table(alias_to_table, right_alias)
        if not left_table or not right_table:
            continue
        left_key = _name_key(left_table)
        right_key = _name_key(right_table)
        if not left_key or not right_key or left_key == right_key:
            continue
        adjacency.setdefault(left_key, set()).add(right_key)
        adjacency.setdefault(right_key, set()).add(left_key)

    best_score = None
    best_table = tables[0]

    for index, table in enumerate(tables):
        score = 0
        normalized = table.lower()
        if any(hint in normalized for hint in FACT_HINTS):
            score += 4
        if any(hint in normalized for hint in DIM_HINTS):
            score -= 3
        if any(_same_name(table, measure_table) for measure_table in measure_tables if measure_table):
            score += 3
        score += len(adjacency.get(_name_key(table), set()))
        if index == 0:
            score += 1
        if best_score is None or score > best_score:
            best_score = score
            best_table = table
    return best_table


def _sanitize_sql(sql_query: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql_query or "", flags=re.DOTALL)
    no_line = re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)
    return " ".join(no_line.split())


def _resolve_table_refs(
    table_refs: list[tuple[str, str]],
    cte_primary_tables: dict[str, str],
) -> list[tuple[str, str]]:
    resolved: list[tuple[str, str]] = []
    for table, alias in table_refs:
        replacement = cte_primary_tables.get(_name_key(table), table)
        candidate = (replacement, alias)
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _extract_cte_primary_tables(sql_query: str) -> dict[str, str]:
    text = _sanitize_sql(sql_query)
    if not re.match(r"^with\b", text, flags=re.IGNORECASE):
        return {}

    mapping: dict[str, str] = {}
    index = len("with")
    length = len(text)

    while index < length:
        index = _skip_whitespace(text, index)
        cte_name, index = _read_sql_identifier(text, index)
        if not cte_name:
            break

        index = _skip_whitespace(text, index)
        if index < length and text[index] == "(":
            _columns_block, index = _read_parenthesized_block(text, index)
            index = _skip_whitespace(text, index)

        as_match = re.match(r"as\b", text[index:], flags=re.IGNORECASE)
        if not as_match:
            break
        index += len(as_match.group(0))
        index = _skip_whitespace(text, index)

        if index >= length or text[index] != "(":
            break

        cte_body, index = _read_parenthesized_block(text, index)
        cte_refs = _extract_table_refs(cte_body)
        cte_joins = _extract_joins(cte_body)
        cte_select = _extract_select_columns(cte_body)
        cte_tables = _unique([table for table, _alias in cte_refs])
        if cte_tables:
            mapping[_name_key(cte_name)] = _pick_fact_table(cte_tables, cte_select, cte_refs, cte_joins)

        index = _skip_whitespace(text, index)
        if index < length and text[index] == ",":
            index += 1
            continue
        break

    return mapping


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _read_sql_identifier(text: str, index: int) -> tuple[str, int]:
    if index >= len(text):
        return "", index

    if text[index] == "[":
        end = text.find("]", index + 1)
        if end < 0:
            return "", index
        return _clean_name(text[index + 1 : end]), end + 1

    if text[index] == '"':
        end = text.find('"', index + 1)
        if end < 0:
            return "", index
        return _clean_name(text[index + 1 : end]), end + 1

    match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
    if not match:
        return "", index
    return match.group(0), index + len(match.group(0))


def _read_parenthesized_block(text: str, start_index: int) -> tuple[str, int]:
    if start_index >= len(text) or text[start_index] != "(":
        return "", start_index

    depth = 0
    in_single = False
    in_double = False
    in_bracket = False
    index = start_index

    while index < len(text):
        char = text[index]

        if in_single:
            if char == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                in_single = False
            index += 1
            continue
        if in_double:
            if char == '"':
                in_double = False
            index += 1
            continue
        if in_bracket:
            if char == "]":
                in_bracket = False
            index += 1
            continue

        if char == "'":
            in_single = True
            index += 1
            continue
        if char == '"':
            in_double = True
            index += 1
            continue
        if char == "[":
            in_bracket = True
            index += 1
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start_index + 1 : index], index + 1
        index += 1

    return text[start_index + 1 :], len(text)


def _extract_main_query(sql_query: str) -> str:
    cleaned = _sanitize_sql(sql_query)
    start, _matched = _find_top_level_phrase(cleaned, ["select"])
    if start < 0:
        return cleaned
    return cleaned[start:]


def _extract_top_level_clause(sql_query: str, start_phrase: str, end_phrases: list[str]) -> str:
    start, _matched = _find_top_level_phrase(sql_query, [start_phrase])
    if start < 0:
        return ""

    content_start = start + len(start_phrase)
    end, _end_match = _find_top_level_phrase(sql_query, end_phrases, start_index=content_start)
    if end < 0:
        end = len(sql_query)
    return sql_query[content_start:end].strip()


def _find_top_level_phrase(sql_query: str, phrases: list[str], start_index: int = 0) -> tuple[int, str]:
    text = sql_query or ""
    lowered = text.lower()
    normalized_phrases = sorted({phrase.lower().strip() for phrase in phrases if phrase and phrase.strip()}, key=len, reverse=True)
    if not normalized_phrases:
        return -1, ""

    depth = 0
    in_single = False
    in_double = False
    in_bracket = False
    index = max(0, start_index)

    while index < len(lowered):
        char = lowered[index]

        if in_single:
            if char == "'":
                if index + 1 < len(lowered) and lowered[index + 1] == "'":
                    index += 2
                    continue
                in_single = False
            index += 1
            continue
        if in_double:
            if char == '"':
                in_double = False
            index += 1
            continue
        if in_bracket:
            if char == "]":
                in_bracket = False
            index += 1
            continue

        if char == "'":
            in_single = True
            index += 1
            continue
        if char == '"':
            in_double = True
            index += 1
            continue
        if char == "[":
            in_bracket = True
            index += 1
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue

        if depth == 0:
            for phrase in normalized_phrases:
                if _phrase_matches_at(lowered, index, phrase):
                    return index, phrase

        index += 1

    return -1, ""


def _phrase_matches_at(text: str, index: int, phrase: str) -> bool:
    if not text.startswith(phrase, index):
        return False

    before = text[index - 1] if index > 0 else " "
    after_index = index + len(phrase)
    after = text[after_index] if after_index < len(text) else " "

    if (before.isalnum() or before == "_") or (after.isalnum() or after == "_"):
        return False
    return True


def _build_alias_to_table_map(table_refs: list[tuple[str, str]]) -> dict[str, str]:
    alias_to_table: dict[str, str] = {}
    for table, alias in table_refs:
        if table and alias:
            alias_to_table[alias] = table
            alias_to_table[alias.lower()] = table
        if table:
            base = table.split(".")[-1]
            alias_to_table.setdefault(base, table)
            alias_to_table.setdefault(base.lower(), table)
    return alias_to_table


def _resolve_alias_table(alias_to_table: dict[str, str], alias: str) -> str:
    value = alias_to_table.get(alias, "")
    if value:
        return value
    return alias_to_table.get(alias.lower(), "")


def _infer_join_cardinality(
    from_table: str,
    to_table: str,
    left_alias: str,
    right_alias: str,
    join_pairs: list[dict[str, str]],
    fact_table: str,
    join_type: str,
    group_by: list[str],
    select_columns: list[dict[str, Any]],
) -> str:
    del join_type

    from_columns = _relationship_side_columns(left_alias, join_pairs)
    to_columns = _relationship_side_columns(right_alias, join_pairs)

    from_unique = _is_likely_unique_side(from_table, from_columns, fact_table, group_by, select_columns)
    to_unique = _is_likely_unique_side(to_table, to_columns, fact_table, group_by, select_columns)

    if from_unique and to_unique:
        return "one-to-one"
    if from_unique and not to_unique:
        return "one-to-many"
    if not from_unique and to_unique:
        return "many-to-one"

    if _same_name(from_table, fact_table) and not _same_name(to_table, fact_table):
        return "many-to-one"
    if _same_name(to_table, fact_table) and not _same_name(from_table, fact_table):
        return "one-to-many"
    return "many-to-many"


def _relationship_side_columns(alias: str, join_pairs: list[dict[str, str]]) -> list[str]:
    if not alias:
        return []
    alias_key = alias.lower()
    columns: list[str] = []
    for pair in join_pairs:
        if pair["left_alias"].lower() == alias_key:
            columns.append(pair["left_column"])
        if pair["right_alias"].lower() == alias_key:
            columns.append(pair["right_column"])
    return _unique(columns)


def _is_likely_unique_side(
    table_name: str,
    join_columns: list[str],
    fact_table: str,
    group_by: list[str],
    select_columns: list[dict[str, Any]],
) -> bool:
    if not join_columns:
        return False

    score = 0
    table_key = _name_key(table_name)
    semantic_table_key = _semantic_name_key(table_name)
    is_fact_side = _same_name(table_name, fact_table)

    if any(hint in table_key for hint in DIM_HINTS):
        score += 1
    if any(hint in table_key for hint in FACT_HINTS):
        score -= 1
    if is_fact_side:
        score -= 2

    grouped_columns = {_group_column_name(expression) for expression in group_by}
    grouped_columns.discard("")

    for column in join_columns:
        col_key = _name_key(column)
        if _looks_like_key_column(col_key):
            entity_key = _column_entity_key(col_key)
            if entity_key and semantic_table_key and entity_key != semantic_table_key:
                score -= 1
            else:
                score += 2
        if any(hint in col_key for hint in NON_KEY_COLUMN_HINTS):
            score -= 1
        if not is_fact_side and col_key in grouped_columns:
            score += 1

    selected_non_measure = {
        _name_key(column.get("output_name", ""))
        for column in select_columns
        if isinstance(column, dict) and not column.get("is_measure")
    }
    if not is_fact_side and any(_name_key(column) in selected_non_measure for column in join_columns):
        score += 1

    return score >= 2


def _group_column_name(expression: str) -> str:
    cleaned = " ".join((expression or "").split())
    match = re.match(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z0-9_\[\]]+)$", cleaned)
    if not match:
        return ""
    return _name_key(match.group(1))


def _aliases_from_join(condition: str) -> tuple[str, str]:
    match = re.search(
        r"([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z0-9_\[\]]+\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z0-9_\[\]]+",
        condition,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _fact_key_from_join(condition: str, table_refs: list[tuple[str, str]], fact_table: str) -> str:
    alias_to_table = {alias: table for table, alias in table_refs}
    match = re.search(
        r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
        condition,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    if alias_to_table.get(match.group(1), "") == fact_table:
        return _clean_name(match.group(2))
    if alias_to_table.get(match.group(3), "") == fact_table:
        return _clean_name(match.group(4))
    return ""


def _dimension_key_from_join(condition: str, aliases: set[str]) -> str:
    match = re.search(
        r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_\[\]]+)",
        condition,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    if match.group(1) in aliases:
        return _clean_name(match.group(2))
    if match.group(3) in aliases:
        return _clean_name(match.group(4))
    return ""


def _follow_up_user_messages(conversation: list[dict[str, Any]]) -> list[str]:
    messages: list[str] = []
    for item in conversation:
        if item.get("role") != "user" or not item.get("content"):
            continue
        content = str(item["content"]).strip()
        if content.lower().startswith("analyze this sql query"):
            continue
        messages.append(content)
    return messages


def _fold_correction_text(value: str) -> str:
    replacements = str.maketrans(
        {
            "à": "a",
            "â": "a",
            "ä": "a",
            "é": "e",
            "è": "e",
            "ê": "e",
            "ë": "e",
            "î": "i",
            "ï": "i",
            "ô": "o",
            "ö": "o",
            "ù": "u",
            "û": "u",
            "ü": "u",
            "ç": "c",
            "À": "A",
            "Â": "A",
            "Ä": "A",
            "É": "E",
            "È": "E",
            "Ê": "E",
            "Ë": "E",
            "Î": "I",
            "Ï": "I",
            "Ô": "O",
            "Ö": "O",
            "Ù": "U",
            "Û": "U",
            "Ü": "U",
            "Ç": "C",
        }
    )
    return value.translate(replacements).lower()


def _normalize_correction_model_type(value: str) -> str:
    token = _fold_correction_text(value).strip().lower()
    if token in {"star", "etoile"}:
        return "Star"
    if token in {"snowflake", "flocon"}:
        return "Snowflake"
    if token in {"hybrid", "hybride"}:
        return "Hybrid"
    return ""


def _forced_model_type_from_conversation(conversation: list[dict[str, Any]]) -> str:
    merged = "\n".join(_follow_up_user_messages(conversation))
    if not merged:
        return ""
    folded = _fold_correction_text(merged)
    model_type_pattern = r"(star|snowflake|hybrid|etoile|flocon|hybride)"
    trigger_pattern = (
        r"(?:it is|it's|should be|schema is|schema should be|"
        r"c est|c'est|schema est|schema doit etre|schema devrait etre|"
        r"modele est|modele doit etre|modele devrait etre|doit etre|devrait etre)"
    )
    forced_model_type = ""
    for match in re.finditer(fr"\b{trigger_pattern}\s+(?:un\s+|une\s+|en\s+)?{model_type_pattern}\b", folded):
        normalized = _normalize_correction_model_type(match.group(1))
        if normalized:
            forced_model_type = normalized
    return forced_model_type


def _normalize_correction_cardinality(value: str) -> str:
    token = _fold_correction_text(value)
    token = re.sub(r"\s+", " ", token.replace("_", "-")).strip()
    compact = token.replace(" ", "").replace(":", "-")

    if compact in {"one-to-one", "1-1", "un-a-un", "un-vers-un"}:
        return "one-to-one"
    if compact in {"one-to-many", "1-n", "1-many", "un-a-plusieurs", "un-vers-plusieurs"}:
        return "one-to-many"
    if compact in {"many-to-one", "n-1", "many-1", "plusieurs-a-un", "plusieurs-vers-un"}:
        return "many-to-one"
    if compact in {"many-to-many", "n-n", "many-many", "plusieurs-a-plusieurs"}:
        return "many-to-many"
    return ""


def _normalize_correction_relationship_type(value: str) -> str:
    folded = _fold_correction_text(value)
    if "dimension" in folded and ("dimension" in folded.split("dimension", 1)[1] or "flocon" in folded):
        return "dimension_to_dimension"
    if "fact" in folded or "fait" in folded:
        return "fact_to_dimension"
    return _normalize_relationship_type(value.replace(" ", "_").replace("-", "_"))


def _apply_follow_up_corrections(model: dict[str, Any], conversation: list[dict[str, Any]]) -> None:
    user_messages = _follow_up_user_messages(conversation)
    if not user_messages:
        return

    merged = "\n".join(user_messages)
    folded = _fold_correction_text(merged)
    forced_model_type = _forced_model_type_from_conversation(conversation)
    applied_notes: list[str] = []
    handled_relationship_pairs: set[tuple[str, str]] = set()

    if forced_model_type:
        applied_notes.append(f"User correction applied: schema type forced to {forced_model_type}.")

    table_token = r"([A-Za-z0-9_\.\[\]]+)"
    identifier_token = r"[A-Za-z0-9_\.\[\]]+"
    cardinality_token = (
        r"(one-to-one|one-to-many|many-to-one|many-to-many|"
        r"1\s*[:\-]\s*1|1\s*[:\-]\s*n|n\s*[:\-]\s*1|n\s*[:\-]\s*n|"
        r"un\s+a\s+un|un\s+a\s+plusieurs|plusieurs\s+a\s+un|plusieurs\s+a\s+plusieurs|"
        r"un\s+vers\s+un|un\s+vers\s+plusieurs|plusieurs\s+vers\s+un)"
    )
    relationship_type_token = (
        r"(fact[-_\s]?to[-_\s]?dimension|dimension[-_\s]?to[-_\s]?dimension|"
        r"fact\s+a\s+dimension|fait\s+a\s+dimension|dimension\s+a\s+dimension|dimension\s+vers\s+dimension)"
    )

    for relation_remove_match in re.finditer(
        fr"\b(?:remove|delete|drop|supprime(?:r)?|enleve(?:r)?)\b[^\n]{{0,80}}?"
        fr"\b(?:relationship|relation|lien)\b[^\n]{{0,120}}?"
        fr"(?:"
        fr"\b(?:between|entre)\s+(?P<between_left>{identifier_token})\s+"
        fr"(?:and|et)\s+(?P<between_right>{identifier_token})"
        fr"|"
        fr"\b(?:from|de)\s+(?P<from_left>{identifier_token})\s+"
        fr"(?:to|vers|a)\s+(?P<from_right>{identifier_token})"
        fr")",
        folded,
        flags=re.IGNORECASE,
    ):
        left_name = _clean_correction_identifier(
            relation_remove_match.group("between_left") or relation_remove_match.group("from_left")
        )
        right_name = _clean_correction_identifier(
            relation_remove_match.group("between_right") or relation_remove_match.group("from_right")
        )
        if _remove_relationship_correction(model, left_name, right_name):
            handled_relationship_pairs.add(_relationship_pair_key(left_name, right_name))
            applied_notes.append(f"User correction applied: removed relationship between {left_name} and {right_name}.")

    for relation_change_match in re.finditer(
        fr"\b(?:add|create|set|update|modify|change|keep|ajoute(?:r)?|cree(?:r)?|modifie(?:r)?|change(?:r)?|garde(?:r)?|mettre)\b"
        fr"[^\n]{{0,80}}?\b(?:relationship|relation|lien)\b[^\n]{{0,160}}?\b(?:between|entre)\s+"
        fr"(?P<left>{identifier_token})\s+(?:and|et)\s+(?P<right>{identifier_token})(?P<tail>[^\n]{{0,260}})",
        folded,
        flags=re.IGNORECASE,
    ):
        left_name = relation_change_match.group("left")
        right_name = relation_change_match.group("right")
        tail = str(relation_change_match.group("tail") or "")
        pair_key = _relationship_pair_key(left_name, right_name)

        cardinality_match = re.search(cardinality_token, tail, flags=re.IGNORECASE)
        normalized_cardinality = (
            _normalize_correction_cardinality(cardinality_match.group(1)) if cardinality_match else ""
        )
        relationship_type_match = re.search(relationship_type_token, tail, flags=re.IGNORECASE)
        normalized_relationship_type = (
            _normalize_correction_relationship_type(relationship_type_match.group(1))
            if relationship_type_match
            else ""
        )
        left_column, right_column = _extract_relationship_columns_from_text(tail, left_name, right_name)

        _apply_relationship_correction(
            model=model,
            left_name=left_name,
            right_name=right_name,
            cardinality=normalized_cardinality,
            relationship_type=normalized_relationship_type,
            left_column=left_column,
            right_column=right_column,
        )
        handled_relationship_pairs.add(pair_key)

        note_parts = [f"User correction applied: relationship ensured between {left_name} and {right_name}"]
        if left_column and right_column:
            note_parts.append(f"using {left_column} = {right_column}")
        if normalized_cardinality:
            note_parts.append(f"cardinality {normalized_cardinality}")
        if normalized_relationship_type:
            note_parts.append(f"type {normalized_relationship_type}")
        applied_notes.append(", ".join(note_parts) + ".")

    for relation_match in re.finditer(
        fr"{table_token}\s+(?:to|->|vers|a|avec)\s+{table_token}[^\n]{{0,160}}?\b{cardinality_token}\b",
        folded,
    ):
        left_name, right_name, cardinality = relation_match.groups()
        if _invalid_relationship_endpoint_name(left_name) or _invalid_relationship_endpoint_name(right_name):
            continue
        if _relationship_pair_key(left_name, right_name) in handled_relationship_pairs:
            continue
        normalized_cardinality = _normalize_correction_cardinality(cardinality)
        if not normalized_cardinality:
            continue
        _apply_relationship_correction(
            model=model,
            left_name=left_name,
            right_name=right_name,
            cardinality=normalized_cardinality,
            relationship_type="",
        )
        applied_notes.append(
            f"User correction applied: {left_name} to {right_name} cardinality set to {normalized_cardinality}."
        )

    for relation_type_match in re.finditer(
        fr"{table_token}\s+(?:to|->|vers|a|avec)\s+{table_token}[^\n]{{0,160}}?\b("
        r"fact[-_\s]?to[-_\s]?dimension|dimension[-_\s]?to[-_\s]?dimension|"
        r"fact\s+a\s+dimension|fait\s+a\s+dimension|dimension\s+a\s+dimension|dimension\s+vers\s+dimension"
        r")\b",
        folded,
    ):
        left_name, right_name, relation_type = relation_type_match.groups()
        if _invalid_relationship_endpoint_name(left_name) or _invalid_relationship_endpoint_name(right_name):
            continue
        if _relationship_pair_key(left_name, right_name) in handled_relationship_pairs:
            continue
        normalized_relation_type = _normalize_correction_relationship_type(relation_type)
        if normalized_relation_type:
            _apply_relationship_correction(
                model=model,
                left_name=left_name,
                right_name=right_name,
                cardinality="",
                relationship_type=normalized_relation_type,
            )
            applied_notes.append(
                f"User correction applied: {left_name} to {right_name} relationship type set to {normalized_relation_type}."
            )

    fact_match = re.search(
        fr"\b(?:fact table|table de fait|table fact|table des faits)\b[^\n]{{0,120}}?\b"
        fr"(?:is|should be|est|doit etre|devrait etre)\s+{table_token}",
        folded,
        flags=re.IGNORECASE,
    )
    if fact_match:
        fact_name = _resolve_table_name(model, fact_match.group(1)) or fact_match.group(1).strip()
        if model.get("fact_tables"):
            model["fact_tables"][0]["name"] = fact_name
        else:
            model["fact_tables"] = [{"name": fact_name, "measures": [], "foreign_keys": []}]
        model["direct_dimensions"] = [
            dimension
            for dimension in _normalize_dimension_list(model.get("direct_dimensions", []))
            if not _same_name(dimension["name"], fact_name)
        ]
        model["snowflake_dimensions"] = [
            dimension
            for dimension in _normalize_dimension_list(model.get("snowflake_dimensions", []))
            if not _same_name(dimension["name"], fact_name)
        ]
        applied_notes.append(f"User correction applied: fact table set to {fact_name}.")

    for classification_match in re.finditer(
        fr"{table_token}\s+(?:is|should be|est|doit etre|devrait etre).{{0,80}}?\b"
        r"(directe?|snowflake|flocon)\s+dimension\b",
        folded,
        flags=re.IGNORECASE,
    ):
        table_name, target_group = classification_match.groups()
        normalized_group = "snowflake" if target_group in {"snowflake", "flocon"} else "direct"
        _move_dimension_between_groups(model, table_name, normalized_group)
        applied_notes.append(f"User correction applied: {table_name} moved to {normalized_group} dimensions.")

    for classification_match in re.finditer(
        fr"{table_token}\s+(?:is|should be|est|doit etre|devrait etre).{{0,80}}?\bdimension\s+"
        r"(directe?|snowflake|flocon)\b",
        folded,
        flags=re.IGNORECASE,
    ):
        table_name, target_group = classification_match.groups()
        normalized_group = "snowflake" if target_group in {"snowflake", "flocon"} else "direct"
        _move_dimension_between_groups(model, table_name, normalized_group)
        applied_notes.append(f"User correction applied: {table_name} moved to {normalized_group} dimensions.")

    for link_match in re.finditer(
        fr"\b(?:relation|lien|join|link)\b[^\n]{{0,80}}?\b(?:entre|between)\s+{table_token}\s+(?:et|and)\s+{table_token}",
        folded,
        flags=re.IGNORECASE,
    ):
        left_name, right_name = link_match.groups()
        if _invalid_relationship_endpoint_name(left_name) or _invalid_relationship_endpoint_name(right_name):
            continue
        if _relationship_pair_key(left_name, right_name) in handled_relationship_pairs:
            continue
        _apply_relationship_correction(
            model=model,
            left_name=left_name,
            right_name=right_name,
            cardinality="",
            relationship_type="",
        )
        applied_notes.append(f"User correction applied: relationship link kept between {left_name} and {right_name}.")

    for link_match in re.finditer(
        fr"{table_token}[^\n]{{0,80}}?\b(?:relie|reliee|connecte|connected|linked)\b[^\n]{{0,80}}?"
        fr"\b(?:a|to|vers)\s+{table_token}",
        folded,
        flags=re.IGNORECASE,
    ):
        left_name, right_name = link_match.groups()
        if _invalid_relationship_endpoint_name(left_name) or _invalid_relationship_endpoint_name(right_name):
            continue
        if _relationship_pair_key(left_name, right_name) in handled_relationship_pairs:
            continue
        _apply_relationship_correction(
            model=model,
            left_name=left_name,
            right_name=right_name,
            cardinality="",
            relationship_type="",
        )
        applied_notes.append(f"User correction applied: relationship link kept between {left_name} and {right_name}.")

    fk_match = re.search(
        fr"\b(?:foreign keys?|cles? etrangeres?)\b(?:\s+(?:for|pour)\s+{table_token})?[^\n]{{0,80}}?"
        fr"\b(?:are|is|should be|sont|est|doit etre|devrait etre)\s+([A-Za-z0-9_\.\[\], ]+)",
        folded,
        flags=re.IGNORECASE,
    )
    if fk_match and model.get("fact_tables"):
        groups = fk_match.groups()
        target_table = groups[0] if len(groups) > 1 else ""
        key_list = groups[-1]
        fact_name = model["fact_tables"][0]["name"]
        if not target_table or _same_name(target_table, fact_name):
            keys = [
                _clean_name(token.strip())
                for token in re.split(r",| and | et ", key_list, flags=re.IGNORECASE)
                if token.strip()
            ]
            if keys:
                model["fact_tables"][0]["foreign_keys"] = _unique(keys)
                applied_notes.append(
                    "User correction applied: fact foreign keys set to " + ", ".join(_unique(keys)) + "."
                )

    if applied_notes:
        model["review_notes"] = _unique(_as_string_list(model.get("review_notes", [])) + applied_notes)
    _sync_relationship_types_with_current_tables(model)
    _refresh_model_output(model, forced_model_type=forced_model_type)


def _clean_correction_identifier(value: str) -> str:
    return _clean_name(str(value or "")).strip().strip(".,;:")


def _resolve_table_name(model: dict[str, Any], candidate: str) -> str:
    lookup_name = _clean_correction_identifier(candidate)
    if not lookup_name:
        return ""
    known_names: list[str] = []
    dimension_alias_map: dict[str, str] = {}
    for fact in model.get("fact_tables", []):
        if isinstance(fact, dict) and fact.get("name"):
            known_names.append(str(fact["name"]))
    for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
        model.get("snowflake_dimensions", [])
    ):
        known_names.append(dimension["name"])
        physical_table = str(dimension.get("physical_table", "")).strip()
        alias = str(dimension.get("alias", "")).strip()
        if physical_table:
            known_names.append(physical_table)
        if alias:
            dimension_alias_map[alias.lower()] = dimension["name"]
    for relationship in model.get("relationships", []):
        if isinstance(relationship, dict):
            known_names.extend(
                [
                    str(relationship.get("from_table", "")),
                    str(relationship.get("to_table", "")),
                ]
            )
            from_alias = str(relationship.get("from_alias", "")).strip()
            to_alias = str(relationship.get("to_alias", "")).strip()
            if from_alias:
                dimension_alias_map.setdefault(from_alias.lower(), str(relationship.get("from_table", "")))
            if to_alias:
                dimension_alias_map.setdefault(to_alias.lower(), str(relationship.get("to_table", "")))
    if lookup_name.lower() in dimension_alias_map:
        return dimension_alias_map[lookup_name.lower()]
    for name in known_names:
        if _same_name(name, lookup_name):
            return name
    return ""


def _apply_relationship_correction(
    model: dict[str, Any],
    left_name: str,
    right_name: str,
    cardinality: str,
    relationship_type: str,
    left_column: str = "",
    right_column: str = "",
) -> None:
    left_name = _clean_correction_identifier(left_name)
    right_name = _clean_correction_identifier(right_name)
    if _invalid_relationship_endpoint_name(left_name) or _invalid_relationship_endpoint_name(right_name):
        return

    relationships = model.get("relationships", [])
    if not isinstance(relationships, list):
        relationships = []
        model["relationships"] = relationships

    matched = False
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        direct_match = _same_name(str(relationship.get("from_table", "")), left_name) and _same_name(
            str(relationship.get("to_table", "")), right_name
        )
        reverse_match = _same_name(str(relationship.get("from_table", "")), right_name) and _same_name(
            str(relationship.get("to_table", "")), left_name
        )
        if not direct_match and not reverse_match:
            continue
        if cardinality:
            relationship["cardinality"] = cardinality if direct_match else _reverse_cardinality(cardinality)
        if left_column and right_column:
            if direct_match:
                relationship["join_condition"] = _build_relationship_join_condition(
                    model,
                    str(relationship.get("from_table", "") or left_name),
                    left_column,
                    str(relationship.get("to_table", "") or right_name),
                    right_column,
                )
            else:
                relationship["join_condition"] = _build_relationship_join_condition(
                    model,
                    str(relationship.get("from_table", "") or right_name),
                    right_column,
                    str(relationship.get("to_table", "") or left_name),
                    left_column,
                )
        if relationship_type:
            relationship["relationship_type"] = relationship_type
            relationship["_user_corrected_relationship_type"] = True
        matched = True
        break

    if matched:
        return

    resolved_left = _resolve_table_name(model, left_name) or _clean_name(left_name)
    resolved_right = _resolve_table_name(model, right_name) or _clean_name(right_name)
    fact_keys = {_name_key(fact.get("name", "")) for fact in model.get("fact_tables", []) if isinstance(fact, dict)}
    inferred_type = relationship_type or _infer_relationship_type(resolved_left, resolved_right, fact_keys)
    model["relationships"].append(
        {
            "from_table": resolved_left,
            "to_table": resolved_right,
            "relationship_type": inferred_type,
            "cardinality": cardinality,
            "join_condition": _build_relationship_join_condition(
                model,
                resolved_left,
                left_column,
                resolved_right,
                right_column,
            )
            if left_column and right_column
            else "",
            "_user_corrected_relationship_type": bool(relationship_type),
        }
    )


def _relationship_pair_key(left_name: str, right_name: str) -> tuple[str, str]:
    left_key = _name_key(_clean_correction_identifier(left_name))
    right_key = _name_key(_clean_correction_identifier(right_name))
    return tuple(sorted([left_key, right_key]))


def _remove_relationship_correction(model: dict[str, Any], left_name: str, right_name: str) -> bool:
    left_name = _clean_correction_identifier(left_name)
    right_name = _clean_correction_identifier(right_name)
    relationships = model.get("relationships", [])
    if not isinstance(relationships, list):
        return False

    filtered_relationships = []
    removed = False
    for relationship in relationships:
        if not isinstance(relationship, dict):
            filtered_relationships.append(relationship)
            continue
        direct_match = _same_name(str(relationship.get("from_table", "")), left_name) and _same_name(
            str(relationship.get("to_table", "")), right_name
        )
        reverse_match = _same_name(str(relationship.get("from_table", "")), right_name) and _same_name(
            str(relationship.get("to_table", "")), left_name
        )
        if direct_match or reverse_match:
            removed = True
            continue
        filtered_relationships.append(relationship)

    if removed:
        model["relationships"] = filtered_relationships
    return removed


def _invalid_relationship_endpoint_name(value: str) -> bool:
    token = _fold_correction_text(_clean_correction_identifier(value)).strip().lower()
    if not token:
        return True
    return token in {
        "add",
        "create",
        "set",
        "update",
        "modify",
        "change",
        "keep",
        "relationship",
        "relation",
        "link",
        "lien",
    }


def _extract_relationship_columns_from_text(
    text: str,
    left_name: str,
    right_name: str,
) -> tuple[str, str]:
    identifier_token = r"[A-Za-z0-9_\.\[\]]+"
    match = re.search(
        fr"(?:columns?|colonnes?|keys?|cles?)\s+(?P<left>{identifier_token})\s*(?:=|and|et|to|vers)\s*(?P<right>{identifier_token})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""

    left_column = _normalize_relationship_column_name(match.group("left"), left_name)
    right_column = _normalize_relationship_column_name(match.group("right"), right_name)
    return left_column, right_column


def _normalize_relationship_column_name(value: str, table_name: str) -> str:
    cleaned = _clean_name(value)
    if not cleaned:
        return ""
    parts = [part.strip() for part in cleaned.split(".") if part.strip()]
    if len(parts) >= 2 and _same_name(parts[-2], table_name):
        return parts[-1]
    return parts[-1] if parts else cleaned


def _build_relationship_join_condition(
    model: dict[str, Any],
    left_table: str,
    left_column: str,
    right_table: str,
    right_column: str,
) -> str:
    left_token = _relationship_join_token(model, left_table)
    right_token = _relationship_join_token(model, right_table)
    left_col = _clean_name(left_column)
    right_col = _clean_name(right_column)
    if not left_token or not right_token or not left_col or not right_col:
        return ""
    return f"{left_token}.{left_col} = {right_token}.{right_col}"


def _relationship_join_token(model: dict[str, Any], table_name: str) -> str:
    resolved_name = _resolve_table_name(model, table_name) or table_name

    for dimension in _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
        model.get("snowflake_dimensions", [])
    ):
        if not _same_name(str(dimension.get("name", "") or ""), resolved_name):
            continue
        alias = str(dimension.get("alias", "") or "").strip()
        if alias:
            return alias
        physical_table = str(dimension.get("physical_table", "") or "").strip()
        leaf = _table_leaf_from_table_reference(physical_table or resolved_name)
        return _clean_name(leaf)

    clean_name = _clean_name(resolved_name)
    leaf = _table_leaf_from_table_reference(clean_name)
    return _clean_name(leaf)


def _move_dimension_between_groups(model: dict[str, Any], table_name: str, target_group: str) -> None:
    resolved_name = _resolve_table_name(model, table_name) or _clean_name(table_name)
    direct = _normalize_dimension_list(model.get("direct_dimensions", []))
    snowflake = _normalize_dimension_list(model.get("snowflake_dimensions", []))

    existing = None
    for dimension in direct + snowflake:
        if _same_name(dimension["name"], resolved_name):
            existing = dimension
            break
    if existing is None:
        existing = {
            "name": resolved_name,
            "physical_table": resolved_name,
            "alias": "",
            "semantic_role": "",
            "attributes": [],
            "natural_key": "",
        }

    direct = [dimension for dimension in direct if not _same_name(dimension["name"], resolved_name)]
    snowflake = [dimension for dimension in snowflake if not _same_name(dimension["name"], resolved_name)]

    if target_group == "snowflake":
        snowflake.append(existing)
    else:
        direct.append(existing)

    model["direct_dimensions"] = _merge_dimensions(direct)
    model["snowflake_dimensions"] = _merge_dimensions(snowflake)


def _sync_relationship_types_with_current_tables(model: dict[str, Any]) -> None:
    relationships = model.get("relationships", [])
    if not isinstance(relationships, list):
        return

    fact_keys = {
        _name_key(str(fact.get("name", "") or ""))
        for fact in model.get("fact_tables", [])
        if isinstance(fact, dict)
    }
    dimension_keys = {
        _name_key(str(dimension.get("name", "") or ""))
        for dimension in _normalize_dimension_list(model.get("direct_dimensions", []))
        + _normalize_dimension_list(model.get("snowflake_dimensions", []))
    }

    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        if bool(relationship.get("_user_corrected_relationship_type")):
            continue
        from_key = _name_key(str(relationship.get("from_table", "") or ""))
        to_key = _name_key(str(relationship.get("to_table", "") or ""))
        if from_key in fact_keys or to_key in fact_keys:
            relationship["relationship_type"] = "fact_to_dimension"
        elif from_key in dimension_keys and to_key in dimension_keys:
            relationship["relationship_type"] = "dimension_to_dimension"


def _reverse_cardinality(cardinality: str) -> str:
    lowered = cardinality.strip().lower()
    if lowered == "one-to-many":
        return "many-to-one"
    if lowered == "many-to-one":
        return "one-to-many"
    return lowered


def _build_schema_flow_payload(model: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(model) if isinstance(model, dict) else {}
    payload["schema_metadata"] = _build_schema_flow_metadata(payload)
    return payload


def _build_schema_flow_metadata(model: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "source": "model",
        "tables": _schema_flow_fallback_table_metadata(model),
        "warnings": [],
    }

    try:
        data_source, dataset = _resolve_current_rdl_context()
        if not data_source or not dataset:
            return metadata

        table_instances = _build_table_instances_from_model(model)
        db_catalog = _schema_flow_cached_db_catalog(
            data_source=data_source,
            dataset=dataset,
            table_instances=table_instances,
        )
        catalog_tables = _schema_flow_catalog_table_metadata(
            model=model,
            table_instances=table_instances,
            db_catalog=db_catalog,
        )
        if catalog_tables:
            metadata["tables"] = _schema_flow_merge_table_metadata(metadata["tables"], catalog_tables)
            metadata["source"] = "database_catalog"
    except Exception as exc:
        # metadata["warnings"].append(str(exc))
        pass

    return metadata


def _schema_flow_cached_db_catalog(
    data_source: dict[str, Any],
    dataset: dict[str, Any],
    table_instances: list[dict[str, Any]],
) -> dict[str, Any]:
    cache_key = _schema_flow_catalog_cache_key(
        data_source=data_source,
        dataset=dataset,
        table_instances=table_instances,
    )
    cache = st.session_state.setdefault("sql_model_assistant_schema_flow_catalog_cache", {})
    if cache_key not in cache:
        cache[cache_key] = _build_db_catalog_for_twb_generation(
            data_source=data_source,
            dataset=dataset,
            table_instances=table_instances,
        ) or {}
    cached_catalog = cache.get(cache_key, {})
    return cached_catalog if isinstance(cached_catalog, dict) else {}


def _schema_flow_catalog_cache_key(
    data_source: dict[str, Any],
    dataset: dict[str, Any],
    table_instances: list[dict[str, Any]],
) -> str:
    connection_info = data_source.get("connection_info", {}) if isinstance(data_source, dict) else {}
    if not isinstance(connection_info, dict):
        connection_info = {}

    payload = {
        "datasource": str(data_source.get("name", "") or ""),
        "provider": str(data_source.get("provider", "") or ""),
        "server": str(connection_info.get("server", "") or ""),
        "database": str(connection_info.get("database", "") or ""),
        "dataset": str(dataset.get("name", "") or ""),
        "query_hash": hashlib.md5(str(dataset.get("query", "") or "").encode("utf-8")).hexdigest(),
        "tables": sorted(
            _unique(
                [
                    str(instance.get("physical_table", "") or "")
                    for instance in table_instances
                    if isinstance(instance, dict)
                ]
            )
        ),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _schema_flow_fallback_table_metadata(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}

    for fact in [item for item in model.get("fact_tables", []) if isinstance(item, dict)]:
        fact_name = str(fact.get("name", "") or "").strip()
        if not fact_name:
            continue
        measures = _as_string_list(fact.get("measures", []))
        foreign_keys = _as_string_list(fact.get("foreign_keys", []))
        columns = [
            {"name": key, "data_type": "", "is_nullable": None, "semantic_role": "foreign_key"}
            for key in foreign_keys
        ] + [
            {"name": measure, "data_type": "", "is_nullable": None, "semantic_role": "measure"}
            for measure in measures
        ]
        table_payload = {
            "name": fact_name,
            "full_name": fact_name,
            "columns": columns,
            "primary_key": [],
            "foreign_keys": [{"column": key, "ref_schema": "", "ref_table": "", "ref_column": ""} for key in foreign_keys],
            "measures": measures,
            "attributes": [],
            "source": "model",
        }
        _schema_flow_register_table_metadata(tables, [fact_name], table_payload)

    dimensions = _normalize_dimension_list(model.get("direct_dimensions", [])) + _normalize_dimension_list(
        model.get("snowflake_dimensions", [])
    )
    for dimension in dimensions:
        name = str(dimension.get("name", "") or "").strip()
        physical_table = str(dimension.get("physical_table", "") or name).strip()
        alias = str(dimension.get("alias", "") or "").strip()
        natural_key = str(dimension.get("natural_key", "") or "").strip()
        attributes = _as_string_list(dimension.get("attributes", []))
        column_names = _unique(([natural_key] if natural_key else []) + attributes)
        table_payload = {
            "name": physical_table or name,
            "full_name": physical_table or name,
            "columns": [
                {
                    "name": column_name,
                    "data_type": "",
                    "is_nullable": None,
                    "semantic_role": "primary_key" if natural_key and _same_name(column_name, natural_key) else "attribute",
                }
                for column_name in column_names
            ],
            "primary_key": [natural_key] if natural_key else [],
            "foreign_keys": [],
            "attributes": attributes,
            "measures": [],
            "source": "model",
        }
        _schema_flow_register_table_metadata(tables, [name, physical_table, alias], table_payload)

    return tables


def _schema_flow_catalog_table_metadata(
    model: dict[str, Any],
    table_instances: list[dict[str, Any]],
    db_catalog: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    lookup = _schema_flow_catalog_lookup(db_catalog)
    if not lookup:
        return {}

    metadata: dict[str, dict[str, Any]] = {}
    for instance in table_instances:
        candidates = _schema_flow_instance_names(instance)
        catalog_payload = _schema_flow_first_catalog_match(candidates, lookup)
        if not catalog_payload:
            continue
        enriched_payload = copy.deepcopy(catalog_payload)
        enriched_payload["source"] = "database_catalog"
        _schema_flow_register_table_metadata(metadata, candidates, enriched_payload)

    for relationship in model.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        for table_name in [
            str(relationship.get("from_table", "") or ""),
            str(relationship.get("to_table", "") or ""),
        ]:
            catalog_payload = _schema_flow_first_catalog_match([table_name], lookup)
            if catalog_payload:
                enriched_payload = copy.deepcopy(catalog_payload)
                enriched_payload["source"] = "database_catalog"
                _schema_flow_register_table_metadata(metadata, [table_name], enriched_payload)

    return metadata


def _schema_flow_catalog_lookup(db_catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    datasources = db_catalog.get("datasources", []) if isinstance(db_catalog, dict) else []
    if not isinstance(datasources, list):
        return lookup

    for datasource in datasources:
        if not isinstance(datasource, dict):
            continue
        tables = datasource.get("tables", [])
        if not isinstance(tables, list):
            continue
        for table in tables:
            if not isinstance(table, dict):
                continue
            payload = _schema_flow_catalog_payload(table)
            names = [
                str(table.get("name", "") or ""),
                str(table.get("full_name", "") or ""),
                _table_leaf_from_table_reference(str(table.get("full_name", "") or "")),
            ]
            schema_name = str(table.get("schema", "") or "").strip()
            table_name = str(table.get("name", "") or "").strip()
            if schema_name and table_name:
                names.append(f"{schema_name}.{table_name}")
            _schema_flow_register_table_metadata(lookup, names, payload)

    return lookup


def _schema_flow_catalog_payload(table: dict[str, Any]) -> dict[str, Any]:
    columns: list[dict[str, Any]] = []
    for column in table.get("columns", []) if isinstance(table.get("columns", []), list) else []:
        if not isinstance(column, dict):
            continue
        nullable_value = column.get("is_nullable")
        is_nullable = nullable_value if isinstance(nullable_value, bool) else None
        columns.append(
            {
                "name": str(column.get("name", "") or "").strip(),
                "data_type": str(column.get("data_type", "") or "").strip(),
                "is_nullable": is_nullable,
            }
        )

    foreign_keys: list[dict[str, str]] = []
    for foreign_key in table.get("foreign_keys", []) if isinstance(table.get("foreign_keys", []), list) else []:
        if not isinstance(foreign_key, dict):
            continue
        foreign_keys.append(
            {
                "column": str(foreign_key.get("column", "") or "").strip(),
                "ref_schema": str(foreign_key.get("ref_schema", "") or "").strip(),
                "ref_table": str(foreign_key.get("ref_table", "") or "").strip(),
                "ref_column": str(foreign_key.get("ref_column", "") or "").strip(),
            }
        )

    return {
        "schema": str(table.get("schema", "") or "").strip(),
        "name": str(table.get("name", "") or "").strip(),
        "full_name": str(table.get("full_name", "") or "").strip(),
        "columns": [column for column in columns if column["name"]],
        "primary_key": _as_string_list(table.get("primary_key", [])),
        "foreign_keys": [foreign_key for foreign_key in foreign_keys if foreign_key["column"]],
    }


def _schema_flow_merge_table_metadata(
    fallback_tables: dict[str, dict[str, Any]],
    catalog_tables: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = copy.deepcopy(fallback_tables)
    for key, catalog_payload in catalog_tables.items():
        existing = merged.get(key, {})
        next_payload = copy.deepcopy(catalog_payload)
        if existing:
            for preserved_key in ("attributes", "measures"):
                if existing.get(preserved_key) and not next_payload.get(preserved_key):
                    next_payload[preserved_key] = existing[preserved_key]
        merged[key] = next_payload
    return merged


def _schema_flow_register_table_metadata(
    target: dict[str, dict[str, Any]],
    names: list[str],
    payload: dict[str, Any],
) -> None:
    for name in names:
        key = _schema_flow_table_key(name)
        if key:
            target[key] = payload


def _schema_flow_first_catalog_match(
    names: list[str],
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    for name in names:
        key = _schema_flow_table_key(name)
        if key and key in lookup:
            return lookup[key]
        leaf_key = _schema_flow_table_key(_table_leaf_from_table_reference(name))
        if leaf_key and leaf_key in lookup:
            return lookup[leaf_key]
    return {}


def _schema_flow_instance_names(instance: dict[str, Any]) -> list[str]:
    names = [
        str(instance.get("semantic_name", "") or ""),
        str(instance.get("physical_table", "") or ""),
        str(instance.get("alias", "") or ""),
        str(instance.get("caption", "") or ""),
        str(instance.get("relation_name", "") or ""),
    ]
    physical_leaf = _table_leaf_from_table_reference(str(instance.get("physical_table", "") or ""))
    if physical_leaf:
        names.append(physical_leaf)
    return _unique([name for name in names if name])


def _schema_flow_table_key(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", _clean_name(str(value or "")).lower())
    return cleaned.strip("_")


def _schema_flow_details_for_names(
    table_metadata: dict[str, dict[str, Any]],
    names: list[str],
) -> dict[str, Any]:
    if not isinstance(table_metadata, dict):
        return {}
    details = _schema_flow_first_catalog_match([name for name in names if name], table_metadata)
    return details if isinstance(details, dict) else {}


def _schema_flow_details_for_dimension(
    table_metadata: dict[str, dict[str, Any]],
    dimension: dict[str, Any],
) -> dict[str, Any]:
    return _schema_flow_details_for_names(
        table_metadata,
        [
            str(dimension.get("name", "") or ""),
            str(dimension.get("physical_table", "") or ""),
            str(dimension.get("alias", "") or ""),
        ],
    )


def _schema_flow_details_for_fact(
    table_metadata: dict[str, dict[str, Any]],
    fact: dict[str, Any],
) -> dict[str, Any]:
    return _schema_flow_details_for_names(table_metadata, [str(fact.get("name", "") or "")])


def _schema_flow_column_specs(table_details: dict[str, Any]) -> list[dict[str, str]]:
    columns = table_details.get("columns", []) if isinstance(table_details, dict) else []
    if not isinstance(columns, list):
        return []

    primary_keys = {_name_key(key) for key in _as_string_list(table_details.get("primary_key", []))}
    foreign_key_lookup = _schema_flow_foreign_key_lookup(table_details)
    rows: list[dict[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        column_name = str(column.get("name", "") or "").strip()
        if not column_name:
            continue
        column_key = _name_key(column_name)
        key_tags: list[str] = []
        if column_key in primary_keys:
            key_tags.append("PK")
        if column_key in foreign_key_lookup:
            key_tags.append("FK")
        nullable = column.get("is_nullable")
        rows.append(
            {
                "Column": column_name,
                "Type": str(column.get("data_type", "") or "").strip() or "-",
                "Key": ", ".join(key_tags) or "ATTR",
                "Nullable": "Yes" if nullable is True else "No" if nullable is False else "-",
                "Reference": foreign_key_lookup.get(column_key, ""),
            }
        )
    return rows


def _schema_flow_attribute_specs(
    table_details: dict[str, Any],
    fallback_attributes: list[str],
) -> list[dict[str, str]]:
    rows = _schema_flow_column_specs(table_details)
    if rows:
        attribute_rows = [row for row in rows if "PK" not in row["Key"] and "FK" not in row["Key"]]
        return attribute_rows

    return [
        {
            "Column": attribute,
            "Type": "-",
            "Key": "ATTR",
            "Nullable": "-",
            "Reference": "",
        }
        for attribute in fallback_attributes
        if attribute
    ]


def _schema_flow_foreign_key_lookup(table_details: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in _schema_flow_foreign_key_rows(table_details):
        lookup[_name_key(row["Column"])] = row["Reference"]
    return lookup


def _schema_flow_foreign_key_rows(table_details: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    foreign_keys = table_details.get("foreign_keys", []) if isinstance(table_details, dict) else []
    if not isinstance(foreign_keys, list):
        return rows

    for foreign_key in foreign_keys:
        if not isinstance(foreign_key, dict):
            continue
        column = str(foreign_key.get("column", "") or "").strip()
        if not column:
            continue
        reference = ".".join(
            [
                part
                for part in [
                    str(foreign_key.get("ref_schema", "") or "").strip(),
                    str(foreign_key.get("ref_table", "") or "").strip(),
                    str(foreign_key.get("ref_column", "") or "").strip(),
                ]
                if part
            ]
        )
        rows.append({"Column": column, "Reference": reference})

    return rows


def _schema_flow_primary_key_label(table_details: dict[str, Any], fallback: str) -> str:
    primary_keys = _as_string_list(table_details.get("primary_key", [])) if isinstance(table_details, dict) else []
    return ", ".join(primary_keys) or str(fallback or "").strip() or "-"


def _render_schema_flow_columns_table(rows: list[dict[str, str]], empty_message: str) -> None:
    if not rows:
        st.caption(empty_message)
        return
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_structured_result(model: dict[str, Any], render_key_suffix: str = "") -> None:
    if not model:
        return

    fact_tables = [fact for fact in model.get("fact_tables", []) if isinstance(fact, dict)]
    primary_fact = str(fact_tables[0].get("name", "")).strip() if fact_tables else ""
    direct_dimensions = _normalize_dimension_list(model.get("direct_dimensions", []))
    snowflake_dimensions = _normalize_dimension_list(model.get("snowflake_dimensions", []))
    relationships = [item for item in model.get("relationships", []) if isinstance(item, dict)]
    database_context = model.get("database_context", {}) if isinstance(model.get("database_context", {}), dict) else {}
    snowflake_origins = _snowflake_origin_lookup(
        fact_table=primary_fact,
        direct_dimensions=direct_dimensions,
        snowflake_dimensions=snowflake_dimensions,
        relationships=relationships,
    )

    model_col, confidence_col, dimensions_col, relationships_col = st.columns(4)
    with model_col:
        st.metric("Model Type", str(model.get("model_type", "Unknown")))
    with confidence_col:
        confidence = str(model.get("schema_confidence", "low") or "low").strip().lower()
        st.metric("Confidence", confidence.title())
    with dimensions_col:
        st.metric("Dimensions", str(len(direct_dimensions) + len(snowflake_dimensions)))
    with relationships_col:
        st.metric("Relationships", str(len(relationships)))

    if model.get("model_summary"):
        st.markdown(
            f"<div class='sqlma-callout'>{html.escape(str(model['model_summary']))}</div>",
            unsafe_allow_html=True,
        )

    schema_flow_payload: dict[str, Any] = {}
    schema_flow_table_metadata: dict[str, dict[str, Any]] = {}
    if fact_tables or direct_dimensions or snowflake_dimensions or relationships:
        schema_flow_payload = _build_schema_flow_payload(model)
        schema_metadata = schema_flow_payload.get("schema_metadata", {})
        if isinstance(schema_metadata, dict) and isinstance(schema_metadata.get("tables"), dict):
            schema_flow_table_metadata = schema_metadata["tables"]
        st.markdown('<div class="sqlma-section-label">Schema Diagram</div>', unsafe_allow_html=True)
        schema_flow_key = hashlib.md5(
            json.dumps(schema_flow_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        render_schema_flow(
            schema=schema_flow_payload,
            height=640,
            key=f"sql_model_assistant_schema_flow_{schema_flow_key}_{_tableau_safe_name(render_key_suffix)}",
            schema_signature=schema_flow_key,
        )

    facts_tab, dimensions_tab, relationships_tab, database_tab, notes_tab = st.tabs(
        ["Facts", "Dimensions", "Relations", "Database", "Notes"]
    )

    with facts_tab:
        if not fact_tables:
            st.write("No fact tables identified.")
        else:
            st.dataframe(
                [
                    {
                        "Table": fact.get("name", ""),
                        "Measures": len(_as_string_list(fact.get("measures", []))),
                        "Foreign Keys": len(
                            _schema_flow_foreign_key_lookup(
                                _schema_flow_details_for_fact(schema_flow_table_metadata, fact)
                            )
                        )
                        or len(_as_string_list(fact.get("foreign_keys", []))),
                        "Columns": len(
                            _schema_flow_column_specs(
                                _schema_flow_details_for_fact(schema_flow_table_metadata, fact)
                            )
                        ),
                    }
                    for fact in fact_tables
                ],
                width="stretch",
                hide_index=True,
            )
            for fact in fact_tables:
                fact_name = str(fact.get("name", "")).strip() or "Fact Table"
                fact_details = _schema_flow_details_for_fact(schema_flow_table_metadata, fact)
                with st.expander(f"{fact_name} details", expanded=False):
                    st.markdown("**Measures**")
                    _render_text_list(_as_string_list(fact.get("measures", [])), "No measures identified.")
                    st.markdown("**Foreign keys**")
                    fk_rows = _schema_flow_foreign_key_rows(fact_details)
                    if fk_rows:
                        _render_text_list(
                            [
                                f"{row['Column']} -> {row['Reference']}" if row["Reference"] else row["Column"]
                                for row in fk_rows
                            ],
                            "No foreign keys identified.",
                        )
                    else:
                        _render_text_list(_as_string_list(fact.get("foreign_keys", [])), "No foreign keys identified.")
                    st.markdown("**Catalog columns**")
                    _render_schema_flow_columns_table(
                        _schema_flow_column_specs(fact_details),
                        "No database column metadata available.",
                    )

    with dimensions_tab:
        direct_col, snowflake_col = st.columns(2)
        with direct_col:
            st.markdown("**Direct dimensions**")
            if direct_dimensions:
                st.dataframe(
                    [
                        {
                            "Dimension": dimension["name"],
                            "Physical Table": dimension.get("physical_table", "") or dimension["name"],
                            "Alias": dimension.get("alias", "") or "-",
                            "Semantic Role": dimension.get("semantic_role", "") or "-",
                            "Natural Key": _schema_flow_primary_key_label(
                                _schema_flow_details_for_dimension(schema_flow_table_metadata, dimension),
                                str(dimension.get("natural_key", "") or ""),
                            ),
                            "Attributes": len(
                                _schema_flow_attribute_specs(
                                    _schema_flow_details_for_dimension(schema_flow_table_metadata, dimension),
                                    _as_string_list(dimension.get("attributes", [])),
                                )
                            ),
                        }
                        for dimension in direct_dimensions
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.write("No direct dimensions identified.")
        with snowflake_col:
            st.markdown("**Snowflake dimensions**")
            if snowflake_dimensions:
                st.dataframe(
                    [
                        {
                            "Dimension": dimension["name"],
                            "Physical Table": dimension.get("physical_table", "") or dimension["name"],
                            "Alias": dimension.get("alias", "") or "-",
                            "Semantic Role": dimension.get("semantic_role", "") or "-",
                            "Origin (Star)": snowflake_origins.get(_name_key(dimension["name"]), "") or "-",
                            "Natural Key": _schema_flow_primary_key_label(
                                _schema_flow_details_for_dimension(schema_flow_table_metadata, dimension),
                                str(dimension.get("natural_key", "") or ""),
                            ),
                            "Attributes": len(
                                _schema_flow_attribute_specs(
                                    _schema_flow_details_for_dimension(schema_flow_table_metadata, dimension),
                                    _as_string_list(dimension.get("attributes", [])),
                                )
                            ),
                        }
                        for dimension in snowflake_dimensions
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.write("No snowflake dimensions identified.")

        all_dimensions = [("Direct", dimension) for dimension in direct_dimensions] + [
            ("Snowflake", dimension) for dimension in snowflake_dimensions
        ]
        if all_dimensions:
            with st.expander("Dimension attributes", expanded=False):
                for dimension_type, dimension in all_dimensions:
                    st.markdown(f"**{dimension['name']}** ({dimension_type})")
                    st.caption(
                        "Physical table: "
                        f"{dimension.get('physical_table', '') or dimension['name']} | "
                        f"Alias: {dimension.get('alias', '') or '-'} | "
                        f"Role: {dimension.get('semantic_role', '') or '-'}"
                    )
                    if dimension_type == "Snowflake":
                        origin = snowflake_origins.get(_name_key(dimension["name"]), "")
                        st.caption(f"Branch origin table (star schema): {origin or 'Unknown'}")
                    dimension_details = _schema_flow_details_for_dimension(schema_flow_table_metadata, dimension)
                    _render_schema_flow_columns_table(
                        _schema_flow_attribute_specs(
                            dimension_details,
                            _as_string_list(dimension.get("attributes", [])),
                        ),
                        "No attributes identified.",
                    )

    with relationships_tab:
        if not relationships:
            st.write("No relationships identified.")
        else:
            st.dataframe(
                [
                    {
                        "From": relationship.get("from_table", ""),
                        "From Alias": relationship.get("from_alias", "") or "-",
                        "To": relationship.get("to_table", ""),
                        "To Alias": relationship.get("to_alias", "") or "-",
                        "Type": relationship.get("relationship_type", "") or "unknown",
                        "Cardinality": relationship.get("cardinality", "") or "unknown",
                    }
                    for relationship in relationships
                ],
                width="stretch",
                hide_index=True,
            )
            with st.expander("Join conditions", expanded=False):
                for relationship in relationships:
                    label = f"{relationship.get('from_table', '')} -> {relationship.get('to_table', '')}"
                    condition = str(relationship.get("join_condition", "")).strip()
                    if condition:
                        st.markdown(f"**{label}**")
                        st.code(condition, language="sql")
                    else:
                        st.caption(f"{label}: no SQL join predicate captured.")

    with database_tab:
        datasource_name = str(database_context.get("datasource_name", "") or "").strip()
        dataset_name = str(database_context.get("dataset_name", "") or "").strip()
        database_name = str(database_context.get("database", "") or "").strip()
        server_name = str(database_context.get("server", "") or "").strip()
        provider_name = str(database_context.get("provider", "") or "").strip()
        inventory_error = str(database_context.get("error", "") or "").strip()
        available_tables = database_context.get("available_tables", [])
        total_tables = int(database_context.get("total_tables", 0) or 0)

        db_col, ds_col, server_col, tables_col = st.columns(4)
        with db_col:
            st.metric("Database", database_name or "Unknown")
        with ds_col:
            st.metric("Datasource", datasource_name or "Unknown")
        with server_col:
            st.metric("Server", server_name or "Unknown")
        with tables_col:
            st.metric("Available Tables", str(total_tables))

        if provider_name or dataset_name:
            st.caption(
                f"Provider: {provider_name or 'Unknown'} | "
                f"Dataset: {dataset_name or 'Unknown'}"
            )

        if inventory_error:
            st.warning(inventory_error)

        if isinstance(available_tables, list) and available_tables:
            st.dataframe(
                [
                    {
                        "Schema": str(item.get("schema", "") or "").strip() or "-",
                        "Table": str(item.get("name", "") or "").strip()
                        or str(item.get("full_name", "") or "").strip(),
                        "Full Name": str(item.get("full_name", "") or "").strip()
                        or str(item.get("name", "") or "").strip(),
                        "Type": str(item.get("table_type", "") or "").strip() or "-",
                    }
                    for item in available_tables
                    if isinstance(item, dict)
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.write("No database table inventory available for the current datasource.")

    with notes_tab:
        review_col, assumptions_col, warnings_col = st.columns(3)
        with review_col:
            st.markdown("**Review**")
            _render_text_list(_as_string_list(model.get("review_notes", [])), "None")
        with assumptions_col:
            st.markdown("**Assumptions**")
            _render_text_list(_as_string_list(model.get("assumptions", [])), "None")
        with warnings_col:
            st.markdown("**Warnings**")
            _render_text_list(_as_string_list(model.get("warnings", [])), "None")


def _render_text_list(items: list[str], empty_message: str) -> None:
    if not items:
        st.caption(empty_message)
        return
    for item in items:
        st.write(f"- {item}")


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _looks_like_key_column(column_name: str) -> bool:
    key = _name_key(column_name)
    if not key:
        return False
    if key in {"id", "key"}:
        return True
    return any(key.endswith(suffix) for suffix in UNIQUE_KEY_SUFFIX_HINTS)


def _column_entity_key(column_name: str) -> str:
    key = _name_key(column_name)
    if not key:
        return ""
    for suffix in sorted(UNIQUE_KEY_SUFFIX_HINTS, key=len, reverse=True):
        if not key.endswith(suffix):
            continue
        prefix = key[: -len(suffix)].rstrip("_")
        if prefix:
            return prefix
    return ""


def _clean_name(value: str) -> str:
    return value.replace("[", "").replace("]", "").strip()


def _name_key(value: str) -> str:
    cleaned = _clean_name(value).lower()
    return cleaned.split(".")[-1] if cleaned else ""


def _semantic_name_key(value: str) -> str:
    return re.sub(r"^(dim|fact|fct)_?", "", _name_key(value))


def _same_name(left: str, right: str) -> bool:
    left_key = _name_key(left)
    right_key = _name_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    return _semantic_name_key(left) == _semantic_name_key(right)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


if __name__ == "__main__":
    main()
