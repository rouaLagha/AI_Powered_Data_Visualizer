from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).resolve().parent / "frontend"
_schema_flow_component = components.declare_component(
    "schema_flow",
    path=str(_COMPONENT_DIR),
)


def render_schema_flow(
    schema: dict[str, Any],
    height: int = 560,
    key: str | None = None,
    schema_signature: str = "",
) -> Any:
    return _schema_flow_component(
        schema=schema or {},
        schema_signature=schema_signature,
        height=height,
        key=key,
        default=None,
    )
