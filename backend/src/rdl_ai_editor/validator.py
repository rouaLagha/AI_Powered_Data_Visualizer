from __future__ import annotations

from typing import Any

from .schema import (
    ADD_FILTER_OPERATORS,
    CHART_TYPE_ALIASES,
    OPERATOR_ALIASES,
    SUPPORTED_OPS,
    VALUE_KINDS,
    VISUAL_TYPES,
)


class PatchValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_patch(patch_json: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    if not isinstance(patch_json, dict):
        raise PatchValidationError(["Patch payload must be a JSON object."])

    operations = patch_json.get("operations")
    if not isinstance(operations, list) or not operations:
        raise PatchValidationError(["Patch payload must contain a non-empty 'operations' array."])

    normalized_operations: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        prefix = f"operations[{index}]"

        if not isinstance(operation, dict):
            errors.append(f"{prefix} must be a JSON object.")
            continue

        op_name = operation.get("op")
        if not isinstance(op_name, str) or not op_name.strip():
            errors.append(f"{prefix}.op is required and must be a string.")
            continue

        normalized_op_name = op_name.strip().lower()
        if normalized_op_name not in SUPPORTED_OPS:
            supported = ", ".join(sorted(SUPPORTED_OPS))
            errors.append(f"{prefix}.op='{normalized_op_name}' is not supported. Supported: {supported}.")
            continue

        try:
            if normalized_op_name == "add_filter":
                normalized_operations.append(_validate_add_filter(operation, prefix))
            elif normalized_op_name == "remove_filter":
                normalized_operations.append(_validate_remove_filter(operation, prefix))
            elif normalized_op_name == "remove_query_condition":
                normalized_operations.append(_validate_remove_query_condition(operation, prefix))
            elif normalized_op_name == "remove_query_parameter":
                normalized_operations.append(_validate_remove_query_parameter(operation, prefix))
            elif normalized_op_name == "remove_report_parameter":
                normalized_operations.append(_validate_remove_report_parameter(operation, prefix))
            elif normalized_op_name == "remove_expression_references":
                normalized_operations.append(_validate_remove_expression_references(operation, prefix))
            elif normalized_op_name == "remove_visual":
                normalized_operations.append(_validate_remove_visual(operation, prefix))
            elif normalized_op_name == "change_chart_type":
                normalized_operations.append(_validate_change_chart_type(operation, prefix))
            elif normalized_op_name == "update_title":
                normalized_operations.append(_validate_update_title(operation, prefix))
            elif normalized_op_name == "update_text":
                normalized_operations.append(_validate_update_text(operation, prefix))
        except PatchValidationError as exc:
            errors.extend(exc.errors)

    if errors:
        raise PatchValidationError(errors)

    return {"operations": normalized_operations}


def _validate_add_filter(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    field = _required_str(operation, "field", prefix)
    operator_raw = _required_str(operation, "operator", prefix)

    canonical_operator = OPERATOR_ALIASES.get(operator_raw.strip().lower())
    if canonical_operator is None and operator_raw in ADD_FILTER_OPERATORS:
        canonical_operator = operator_raw
    if canonical_operator is None:
        allowed = ", ".join(sorted(ADD_FILTER_OPERATORS))
        raise PatchValidationError([f"{prefix}.operator='{operator_raw}' is invalid. Allowed: {allowed}."])

    if "value" not in operation:
        raise PatchValidationError([f"{prefix}.value is required."])
    value = operation["value"]

    value_kind = str(operation.get("value_kind", "literal")).strip().lower()
    if value_kind not in VALUE_KINDS:
        kinds = ", ".join(sorted(VALUE_KINDS))
        raise PatchValidationError([f"{prefix}.value_kind='{value_kind}' is invalid. Allowed: {kinds}."])

    if value_kind == "literal" and not isinstance(value, (str, int, float, bool)):
        raise PatchValidationError(
            [f"{prefix}.value must be a string, number, or boolean when value_kind='literal'."]
        )

    if value_kind == "parameter" and (not isinstance(value, str) or not value.strip()):
        raise PatchValidationError([f"{prefix}.value must be a non-empty parameter name when value_kind='parameter'."])

    target_dataset = _optional_str(operation.get("target_dataset"))

    return {
        "op": "add_filter",
        "target_dataset": target_dataset,
        "field": field,
        "operator": canonical_operator,
        "value": value,
        "value_kind": value_kind,
    }


def _validate_remove_filter(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    field = _required_str(operation, "field", prefix)
    target_dataset = _optional_str(operation.get("target_dataset"))
    return {
        "op": "remove_filter",
        "target_dataset": target_dataset,
        "field": field,
    }


def _validate_remove_query_condition(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    condition_text = _required_str(operation, "condition_text", prefix)
    target_dataset = _optional_str(operation.get("target_dataset"))

    return {
        "op": "remove_query_condition",
        "target_dataset": target_dataset,
        "condition_text": condition_text,
    }


def _validate_remove_query_parameter(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    parameter = _required_str(operation, "parameter", prefix)
    target_dataset = _optional_str(operation.get("target_dataset"))
    normalized_parameter = parameter if parameter.startswith("@") else f"@{parameter}"

    return {
        "op": "remove_query_parameter",
        "target_dataset": target_dataset,
        "parameter": normalized_parameter,
    }


def _validate_remove_report_parameter(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    parameter = _required_str(operation, "parameter", prefix)
    normalized_parameter = parameter.lstrip("@")
    return {
        "op": "remove_report_parameter",
        "parameter": normalized_parameter,
    }


def _validate_remove_expression_references(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    parameter = _required_str(operation, "parameter", prefix)
    normalized_parameter = parameter.lstrip("@")
    return {
        "op": "remove_expression_references",
        "parameter": normalized_parameter,
    }


def _validate_remove_visual(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    target = _required_str(operation, "target", prefix)
    visual_type = _optional_str(operation.get("visual_type"))

    if visual_type is not None:
        visual_aliases = {item.lower(): item for item in VISUAL_TYPES}
        canonical_visual_type = visual_aliases.get(visual_type.strip().lower())
        if canonical_visual_type not in VISUAL_TYPES:
            allowed = ", ".join(sorted(VISUAL_TYPES))
            raise PatchValidationError([f"{prefix}.visual_type='{visual_type}' is invalid. Allowed: {allowed}."])
        visual_type = canonical_visual_type

    remove_all_matches = bool(operation.get("remove_all_matches", False))

    return {
        "op": "remove_visual",
        "target": target,
        "visual_type": visual_type,
        "remove_all_matches": remove_all_matches,
    }


def _validate_change_chart_type(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    target = _required_str(operation, "target", prefix)
    raw_chart_type = _required_str(operation, "to", prefix)

    canonical_chart_type = CHART_TYPE_ALIASES.get(raw_chart_type.strip().lower())
    if canonical_chart_type is None:
        allowed = ", ".join(sorted(set(CHART_TYPE_ALIASES.values())))
        raise PatchValidationError([f"{prefix}.to='{raw_chart_type}' is invalid. Allowed chart types: {allowed}."])

    return {
        "op": "change_chart_type",
        "target": target,
        "to": canonical_chart_type,
    }


def _validate_update_title(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    text = _required_str(operation, "text", prefix)
    target = _optional_str(operation.get("target")) or "ReportTitle"

    return {
        "op": "update_title",
        "target": target,
        "text": text,
    }


def _validate_update_text(operation: dict[str, Any], prefix: str) -> dict[str, Any]:
    target = _required_str(operation, "target", prefix)
    text = _required_str(operation, "text", prefix)

    return {
        "op": "update_text",
        "target": target,
        "text": text,
    }


def _required_str(operation: dict[str, Any], key: str, prefix: str) -> str:
    value = operation.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PatchValidationError([f"{prefix}.{key} is required and must be a non-empty string."])
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
