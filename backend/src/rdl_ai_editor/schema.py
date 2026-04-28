from __future__ import annotations

from typing import Any

SUPPORTED_OPS = {
    "add_filter",
    "remove_filter",
    "remove_query_condition",
    "remove_query_parameter",
    "remove_report_parameter",
    "remove_expression_references",
    "remove_visual",
    "change_chart_type",
    "update_title",
    "update_text",
}

VALUE_KINDS = {"literal", "parameter"}

OPERATOR_ALIASES = {
    "=": "Equal",
    "==": "Equal",
    "equal": "Equal",
    "!=": "NotEqual",
    "<>": "NotEqual",
    "notequal": "NotEqual",
    ">": "GreaterThan",
    "greaterthan": "GreaterThan",
    ">=": "GreaterThanOrEqual",
    "greaterthanorequal": "GreaterThanOrEqual",
    "<": "LessThan",
    "lessthan": "LessThan",
    "<=": "LessThanOrEqual",
    "lessthanorequal": "LessThanOrEqual",
    "like": "Like",
    "in": "In",
    "between": "Between",
}

ADD_FILTER_OPERATORS = {
    "Equal",
    "NotEqual",
    "GreaterThan",
    "GreaterThanOrEqual",
    "LessThan",
    "LessThanOrEqual",
    "Like",
    "In",
    "Between",
}

CHART_TYPE_ALIASES = {
    "bar": "Bar",
    "column": "Column",
    "line": "Line",
    "area": "Area",
    "pie": "Pie",
    "doughnut": "Doughnut",
    "scatter": "Scatter",
}

VISUAL_TYPES = {
    "Chart",
    "Tablix",
    "Textbox",
    "Rectangle",
    "GaugePanel",
    "Map",
    "Image",
    "Line",
}

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["operations"],
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["op", "field", "operator", "value"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "add_filter"},
                            "target_dataset": {"type": "string"},
                            "field": {"type": "string"},
                            "operator": {"type": "string"},
                            "value": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                ]
                            },
                            "value_kind": {
                                "type": "string",
                                "enum": ["literal", "parameter"],
                            },
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "field"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_filter"},
                            "target_dataset": {"type": "string"},
                            "field": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "condition_text"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_query_condition"},
                            "target_dataset": {"type": "string"},
                            "condition_text": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "parameter"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_query_parameter"},
                            "target_dataset": {"type": "string"},
                            "parameter": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "parameter"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_report_parameter"},
                            "parameter": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "parameter"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_expression_references"},
                            "parameter": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "target"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "remove_visual"},
                            "target": {"type": "string"},
                            "visual_type": {"type": "string"},
                            "remove_all_matches": {"type": "boolean"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "target", "to"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "change_chart_type"},
                            "target": {"type": "string"},
                            "to": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "text"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "update_title"},
                            "target": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "required": ["op", "target", "text"],
                        "additionalProperties": False,
                        "properties": {
                            "op": {"const": "update_text"},
                            "target": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                ]
            },
        }
    },
}
