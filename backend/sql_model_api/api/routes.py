from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

JsonDict = dict[str, Any]
PostHandler = Callable[[JsonDict], JsonDict]

API_PREFIX = "/api"

GET_ENDPOINTS = {
    "/api/state",
    "/api/file",
}

POST_ENDPOINTS = {
    "/api/reset",
    "/api/rdl/parse",
    "/api/dataset/select",
    "/api/model/analyze",
    "/api/schema/validate",
    "/api/twb/generate",
    "/api/tableau/publish",
    "/api/conversion/run",
    "/api/rdl-editor/apply",
}


def dispatch_post(
    path: str,
    payload: JsonDict,
    handlers: dict[str, PostHandler],
    lock: Lock,
) -> JsonDict:
    handler = handlers.get(path)
    if handler is None:
        raise ValueError(f"Unknown endpoint: {path}")
    with lock:
        return handler(payload)
