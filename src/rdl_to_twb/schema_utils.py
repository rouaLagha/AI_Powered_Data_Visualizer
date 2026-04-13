from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def summarize_xsd_elements(xsd_path: str | Path, max_elements: int = 120) -> dict:
    """Extract a compact element/attribute summary for prompt grounding."""
    xsd_path = Path(xsd_path)
    root = ET.parse(xsd_path).getroot()

    elements: list[dict[str, str | None]] = []
    for el in root.iter():
        if _local_name(el.tag) != "element":
            continue
        elements.append(
            {
                "name": el.attrib.get("name"),
                "type": el.attrib.get("type"),
                "minOccurs": el.attrib.get("minOccurs"),
                "maxOccurs": el.attrib.get("maxOccurs"),
            }
        )

    attributes: list[str] = []
    for attr in root.iter():
        if _local_name(attr.tag) == "attribute" and "name" in attr.attrib:
            attributes.append(attr.attrib["name"])

    element_names = sorted({e["name"] for e in elements if e.get("name")})

    return {
        "schema_file": xsd_path.name,
        "element_count": len(elements),
        "attribute_count": len(attributes),
        "element_names": element_names,
        "sample_elements": elements[:max_elements],
        "sample_attributes": attributes[:max_elements],
    }
