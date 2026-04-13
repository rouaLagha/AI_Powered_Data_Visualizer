from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .llm_client import LLMClient
from .prompts import (
    XML_REPAIR_SYSTEM_PROMPT,
    XML_SYSTEM_PROMPT,
    build_xml_repair_user_prompt,
    build_xml_user_prompt,
)

def generate_twb_xml(
    llm: LLMClient,
    data_model: dict,
    visual_model: dict,
    mapping: dict,
    twb_xsd_summary: dict,
    output_xml_path: str | Path,
) -> str:
    prompt = build_xml_user_prompt(data_model, visual_model, mapping, twb_xsd_summary)
    raw_response = llm.chat(XML_SYSTEM_PROMPT, prompt)
    raw_xml = _extract_workbook_xml(raw_response)
    _validate_workbook_xml(raw_xml)

    output_xml_path = Path(output_xml_path)
    output_xml_path.parent.mkdir(parents=True, exist_ok=True)
    output_xml_path.write_text(raw_xml, encoding="utf-8")

    return raw_xml


def _strip_code_fences(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def repair_twb_xml(
    llm: LLMClient,
    invalid_xml: str,
    issues: list[str],
    twb_xsd_summary: dict,
) -> str:
    prompt = build_xml_repair_user_prompt(invalid_xml, issues, twb_xsd_summary)
    raw_xml = llm.chat(XML_REPAIR_SYSTEM_PROMPT, prompt)
    raw_xml = _extract_workbook_xml(raw_xml)
    _validate_workbook_xml(raw_xml)
    return raw_xml


def _extract_workbook_xml(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Agent-2 returned empty content; expected TWB XML.")

    if text.startswith("```"):
        text = _strip_code_fences(text).strip()

    candidate = _find_parseable_workbook_fragment(text)
    if candidate is not None:
        return candidate

    start_tag = "<workbook"
    end_tag = "</workbook>"
    start = text.find(start_tag)
    end = text.rfind(end_tag)
    if start >= 0 and end >= 0 and end >= start:
        return text[start : end + len(end_tag)].strip()

    # If no explicit workbook block is found, return as-is so validator can report context.
    return text


def _find_parseable_workbook_fragment(text: str) -> str | None:
    start_tag = "<workbook"
    end_tag = "</workbook>"
    best: str | None = None
    search_from = 0

    while True:
        start = text.find(start_tag, search_from)
        if start < 0:
            break

        end_search_from = start
        while True:
            end = text.find(end_tag, end_search_from)
            if end < 0:
                break

            candidate = text[start : end + len(end_tag)].strip()
            if _is_parseable_workbook_xml(candidate):
                if best is None or len(candidate) > len(best):
                    best = candidate

            end_search_from = end + len(end_tag)

        search_from = start + len(start_tag)

    return best


def _is_parseable_workbook_xml(xml_text: str) -> bool:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return False
    return _local_name(root.tag) == "workbook"


def _validate_workbook_xml(xml_text: str) -> None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        preview = xml_text[:400].replace("\n", " ").strip()
        raise ValueError(
            "Agent-2 returned invalid XML. "
            f"Parse error: {exc}. Response preview: {preview}"
        ) from exc

    if _local_name(root.tag) != "workbook":
        preview = xml_text[:300].replace("\n", " ").strip()
        raise ValueError(
            "Agent-2 XML root is not <workbook>. "
            f"Response preview: {preview}"
        )


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag
