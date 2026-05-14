from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.rdl_to_twb.llm_client import LLMClient, LLMConfig


SYSTEM_PROMPT = """You are an NLP patch planner for RDL reports.
You MUST output JSON only. Never output XML.
The output format must be:
{
  \"operations\": [ ... ]
}
Allowed operations and fields:
- add_filter: {\"op\":\"add_filter\", \"target_dataset\": string?, \"field\": string, \"operator\": string, \"value\": string|number|boolean, \"value_kind\": \"literal\"|\"parameter\"?}
- remove_filter: {\"op\":\"remove_filter\", \"target_dataset\": string?, \"field\": string}
- remove_query_condition: {\"op\":\"remove_query_condition\", \"target_dataset\": string?, \"condition_text\": string}
- remove_query_parameter: {\"op\":\"remove_query_parameter\", \"target_dataset\": string?, \"parameter\": string}
- remove_report_parameter: {\"op\":\"remove_report_parameter\", \"parameter\": string}
- remove_expression_references: {\"op\":\"remove_expression_references\", \"parameter\": string}
- remove_visual: {\"op\":\"remove_visual\", \"target\": string, \"visual_type\": string?, \"remove_all_matches\": boolean?}
- change_chart_type: {\"op\":\"change_chart_type\", \"target\": string, \"to\": string}
- update_title: {\"op\":\"update_title\", \"target\": string?, \"text\": string}
- update_text: {\"op\":\"update_text\", \"target\": string, \"text\": string}
Constraints:
- No prose, no markdown, no code fences.
- No comments in JSON.
- Keep operations deterministic and explicit.
- If user asks to remove a filter completely from report: output ONLY these operations in this order:
    1) remove_query_condition
    2) remove_query_parameter
    3) remove_report_parameter
    4) remove_expression_references
- For complete filter removal, do NOT output add_filter or remove_filter.
"""


def load_llm_from_config(config_path: str | Path) -> LLMClient:
    cfg_path = Path(config_path)
    if not cfg_path.exists() or not cfg_path.is_file():
        raise FileNotFoundError(f"LLM config not found: {cfg_path}")

    payload = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or "agent1" not in payload:
        raise ValueError("LLM config must contain an 'agent1' section.")

    agent1 = payload["agent1"]
    if not isinstance(agent1, dict):
        raise ValueError("LLM config 'agent1' section must be a JSON object.")

    return LLMClient(LLMConfig(**agent1))


def nlp_agent(query: str, llm: LLMClient | None = None) -> dict[str, Any]:
    query_text = query.strip()
    if not query_text:
        raise ValueError("Query must be a non-empty string.")

    deterministic_patch = _build_complete_filter_removal_patch(query_text)
    if deterministic_patch is not None:
        return deterministic_patch

    if llm is None:
        return _heuristic_patch_from_query(query_text)

    user_prompt = (
        "Convert this user request into patch JSON operations for RDL.\n"
        f"User request: {query_text}\n"
        "Return JSON only."
    )
    raw_response = llm.chat(SYSTEM_PROMPT, user_prompt)
    parsed = _parse_patch_json(raw_response)
    return parsed


def _parse_patch_json(raw_response: str) -> dict[str, Any]:
    text = raw_response.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    candidate = _extract_first_json_object(text)
    if candidate is None:
        raise ValueError("NLP agent did not return valid JSON.")

    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("NLP agent JSON root must be an object.")
    return payload


def _extract_first_json_object(text: str) -> str | None:
    start_index = text.find("{")
    if start_index < 0:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start_index, len(text)):
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
                return text[start_index : index + 1]

    return None


def _heuristic_patch_from_query(query: str) -> dict[str, Any]:
    lower = query.lower()
    operations: list[dict[str, Any]] = []

    if "filter" in lower:
        year_match = re.search(r"\b(19|20)\d{2}\b", query)
        if year_match:
            value = int(year_match.group(0))
            field = "CalendarYear"
            if "sales" in lower:
                field = "SalesAmount"
            operations.append(
                {
                    "op": "add_filter",
                    "field": field,
                    "operator": "=",
                    "value": value,
                }
            )

    if ("bar chart" in lower or "bar" in lower) and "chart" in lower:
        operations.append(
            {
                "op": "change_chart_type",
                "target": "main_chart",
                "to": "bar",
            }
        )

    if "remove" in lower and "chart" in lower:
        operations.append(
            {
                "op": "remove_visual",
                "target": "main_chart",
                "visual_type": "Chart",
            }
        )

    title_match = re.search(r"title\s+(?:to|as)\s+['\"]([^'\"]+)['\"]", query, flags=re.IGNORECASE)
    if title_match:
        operations.append(
            {
                "op": "update_title",
                "text": title_match.group(1),
            }
        )

    return {"operations": operations}


def _extract_filter_parameter_name(query: str) -> str | None:
    patterns = [
        r"remove\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+filter",
        r"remove\s+filter\s+(?:for\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        r"remove\s+(?:the\s+)?filter\s+([A-Za-z_][A-Za-z0-9_]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            token = match.group(1).strip()
            return token.lstrip("@")

    stopwords = {
        "remove",
        "the",
        "filter",
        "completely",
        "from",
        "report",
        "all",
        "entirely",
        "please",
    }
    for token in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", query):
        if token.lower() not in stopwords:
            return token.lstrip("@")

    return None


def _build_complete_filter_removal_patch(query: str) -> dict[str, Any] | None:
    if _is_calendar_ds_main_filter_removal_query(query):
        return {
            "operations": [
                {
                    "op": "remove_query_condition",
                    "target_dataset": "dsMain",
                    "condition_text": "CalendarYear = @CalendarYear",
                },
                {
                    "op": "remove_query_parameter",
                    "target_dataset": "dsMain",
                    "parameter": "@CalendarYear",
                },
                {
                    "op": "remove_report_parameter",
                    "parameter": "CalendarYear",
                },
                {
                    "op": "remove_expression_references",
                    "parameter": "CalendarYear",
                },
            ]
        }

    if not _is_complete_filter_removal_query(query):
        return None

    parameter = _extract_filter_parameter_name(query)
    if not parameter:
        return None

    return {
        "operations": [
            {
                "op": "remove_query_condition",
                "target_dataset": "dsMain",
                "condition_text": f"{parameter} = @{parameter}",
            },
            {
                "op": "remove_query_parameter",
                "target_dataset": "dsMain",
                "parameter": f"@{parameter}",
            },
            {
                "op": "remove_report_parameter",
                "parameter": parameter,
            },
            {
                "op": "remove_expression_references",
                "parameter": parameter,
            },
        ]
    }


def _is_complete_filter_removal_query(query: str) -> bool:
    lower = query.lower()
    if "remove" not in lower or "filter" not in lower:
        return False

    return any(
        phrase in lower
        for phrase in (
            "completely",
            "complete",
            "entirely",
            "from the report",
            "everywhere",
            "all",
        )
    )


def _is_calendar_ds_main_filter_removal_query(query: str) -> bool:
    lower = query.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lower)

    if "remove" not in lower or "filter" not in lower:
        return False

    has_calendar_year = "calendaryear" in compact or "calenderyear" in compact
    has_ds_main = "dsmain" in compact
    return has_calendar_year and has_ds_main
