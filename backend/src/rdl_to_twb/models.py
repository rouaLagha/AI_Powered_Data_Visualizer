from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedField:
    name: str
    source: str | None = None
    data_field: str | None = None
    type_name: str | None = None


@dataclass
class ParsedDataSet:
    name: str
    query: str | None = None
    data_source_name: str | None = None
    fields: list[ParsedField] = field(default_factory=list)
    query_parameters: list[dict[str, Any]] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    sort_expressions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedDataSource:
    name: str
    connection_string: str | None = None
    provider: str | None = None
    security_type: str | None = None
    credential_retrieval: str | None = None
    windows_credentials: bool | None = None
    user_name: str | None = None
    data_source_reference: str | None = None
    provider_class: str | None = None
    connection_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedVisual:
    name: str
    visual_type: str
    dataset_name: str | None = None
    expressions: list[str] = field(default_factory=list)
    layout: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)
    children: list["ParsedVisual"] = field(default_factory=list)


@dataclass
class ParsedReport:
    report_name: str
    namespace: str
    data_sources: list[ParsedDataSource] = field(default_factory=list)
    data_sets: list[ParsedDataSet] = field(default_factory=list)
    visuals: list[ParsedVisual] = field(default_factory=list)
    report_parameters: list[dict[str, Any]] = field(default_factory=list)
    report_metadata: dict[str, Any] = field(default_factory=dict)
    report_sections: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_name": self.report_name,
            "namespace": self.namespace,
            "data_sources": [
                {
                    "name": ds.name,
                    "connection_string": ds.connection_string,
                    "provider": ds.provider,
                    "security_type": ds.security_type,
                    "credential_retrieval": ds.credential_retrieval,
                    "windows_credentials": ds.windows_credentials,
                    "user_name": ds.user_name,
                    "data_source_reference": ds.data_source_reference,
                    "provider_class": ds.provider_class,
                    "connection_info": ds.connection_info,
                }
                for ds in self.data_sources
            ],
            "data_sets": [
                {
                    "name": dataset.name,
                    "query": dataset.query,
                    "data_source_name": dataset.data_source_name,
                    "fields": [
                        {
                            "name": field.name,
                            "source": field.source,
                            "data_field": field.data_field,
                            "type_name": field.type_name,
                        }
                        for field in dataset.fields
                    ],
                    "query_parameters": dataset.query_parameters,
                    "filters": dataset.filters,
                    "sort_expressions": dataset.sort_expressions,
                }
                for dataset in self.data_sets
            ],
            "visuals": [serialize_visual(v) for v in self.visuals],
            "report_parameters": self.report_parameters,
            "report_metadata": self.report_metadata,
            "report_sections": self.report_sections,
        }


def serialize_visual(visual: ParsedVisual) -> dict[str, Any]:
    return {
        "name": visual.name,
        "visual_type": visual.visual_type,
        "dataset_name": visual.dataset_name,
        "expressions": visual.expressions,
        "layout": visual.layout,
        "properties": visual.properties,
        "children": [serialize_visual(child) for child in visual.children],
    }
