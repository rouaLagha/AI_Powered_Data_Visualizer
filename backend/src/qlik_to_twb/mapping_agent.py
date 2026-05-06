from __future__ import annotations

import json
import re
from typing import Any


JsonDict = dict[str, Any]


class QlikToTableauMappingAgent:
    def __init__(
        self,
        llm_client: Any | None = None,
        model_name: str = "gpt-5.3-codex",
    ):
        self.llm_client = llm_client
        self.model_name = model_name

    def map_model(self, intermediate_model: JsonDict) -> JsonDict:
        if self.llm_client is None:
            raise ValueError("Qlik to Tableau mapping requires an LLM client.")

        llm_mapping = self._map_with_llm(intermediate_model)
        self._validate_llm_mapping(intermediate_model, llm_mapping)

        llm_mapping.setdefault("agent", {})
        llm_mapping["agent"].update(
            {
                "name": "QlikToTableauMappingAgent",
                "model": self.model_name,
                "mode": "llm",
                "fallback_available": False,
            }
        )
        return llm_mapping

    def _map_with_llm(self, intermediate_model: JsonDict) -> JsonDict:
        system_prompt = (
            "You map real Qlik Sense/QIX metadata into a Tableau workbook mapping. "
            "Return only JSON. Do not invent sheets, visual ids, field names, or source expressions. "
            "Use Tableau-compatible visual types and calculated expressions."
        )
        user_prompt = json.dumps(
            {
                "output_contract": {
                    "agent": {"name": "QlikToTableauMappingAgent", "mode": "llm"},
                    "visual_type_map": "object mapping source Qlik visual types to Tableau visual types",
                    "expression_map": [
                        {
                            "source_expression": "original Qlik expression",
                            "tableau_expression": "Tableau expression using [Field] references",
                        }
                    ],
                    "sheets": [
                        {
                            "id": "source sheet id",
                            "name": "source sheet title",
                            "tableau_name": "Tableau dashboard/sheet title",
                            "visuals": [
                                {
                                    "id": "source visual id",
                                    "title": "source visual title",
                                    "source_type": "source Qlik type",
                                    "tableau_type": "bar_chart|line_chart|kpi|table|filter|unknown",
                                    "dimensions": [
                                        {
                                            "label": "source dimension label",
                                            "field": "source field name",
                                            "tableau_field": "[SourceFieldName]",
                                        }
                                    ],
                                    "measures": [
                                        {
                                            "label": "source measure label",
                                            "source_expression": "source Qlik expression",
                                            "tableau_expression": "Tableau expression",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
                "allowed_tableau_types": ["bar_chart", "line_chart", "kpi", "table", "filter", "unknown"],
                "intermediate_model": intermediate_model,
            },
            ensure_ascii=True,
        )
        content = self.llm_client.chat(system_prompt=system_prompt, user_prompt=user_prompt)
        return _extract_json_object(content)

    def _validate_llm_mapping(self, intermediate_model: JsonDict, mapping: JsonDict) -> None:
        if not isinstance(mapping, dict):
            raise ValueError("LLM mapping response must be a JSON object.")

        sheets = mapping.get("sheets")
        if not isinstance(sheets, list):
            raise ValueError("LLM mapping response must include a sheets array.")

        expected_visual_ids = {
            str(visual.get("id") or "").strip()
            for sheet in _as_list(intermediate_model.get("sheets"))
            if isinstance(sheet, dict)
            for visual in _as_list(sheet.get("visuals"))
            if isinstance(visual, dict) and str(visual.get("id") or "").strip()
        }
        mapped_visual_ids = {
            str(visual.get("id") or "").strip()
            for sheet in sheets
            if isinstance(sheet, dict)
            for visual in _as_list(sheet.get("visuals"))
            if isinstance(visual, dict) and str(visual.get("id") or "").strip()
        }

        missing_visual_ids = sorted(expected_visual_ids - mapped_visual_ids)
        if missing_visual_ids:
            raise ValueError(
                "LLM mapping omitted Qlik visual ids: "
                + ", ".join(missing_visual_ids[:10])
                + (" ..." if len(missing_visual_ids) > 10 else "")
            )

        invalid_types = []
        for sheet in sheets:
            if not isinstance(sheet, dict):
                invalid_types.append("<sheet>")
                continue
            for visual in _as_list(sheet.get("visuals")):
                if not isinstance(visual, dict):
                    invalid_types.append("<visual>")
                    continue
                tableau_type = str(visual.get("tableau_type") or "").strip()
                if tableau_type not in {"bar_chart", "line_chart", "kpi", "table", "filter", "unknown"}:
                    invalid_types.append(tableau_type or "<empty>")
        if invalid_types:
            raise ValueError(f"LLM mapping returned unsupported Tableau visual type(s): {', '.join(invalid_types[:10])}.")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extract_json_object(content: str) -> JsonDict:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object from the mapping agent.")
    return payload
