from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Protocol


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class QlikEngineClientConfig:
    endpoint_url: str = "ws://localhost:4848/app"
    apps_dir: str = ""
    user_directory: str = ""
    user_id: str = ""
    session_cookie: str = ""
    request_timeout_seconds: float = 30.0


class QlikEngineApiClient(Protocol):
    client_name: str
    extraction_mode: str

    def open_app(self, app_id: str) -> JsonDict:
        ...

    def get_load_script(self, app_id: str) -> str:
        ...

    def get_sheets(self, app_id: str) -> list[JsonDict]:
        ...

    def get_visual_objects(self, app_id: str) -> list[JsonDict]:
        ...

    def get_master_dimensions(self, app_id: str) -> list[JsonDict]:
        ...

    def get_master_measures(self, app_id: str) -> list[JsonDict]:
        ...

    def get_variables(self, app_id: str) -> list[JsonDict]:
        ...


def default_qlik_desktop_apps_dir() -> Path:
    return Path.home() / "Documents" / "Qlik" / "Sense" / "Apps"


def import_qvf_to_qlik(qvf_path: str | Path, apps_dir: str | Path | None = None) -> str:
    """Import a QVF into Qlik Sense Desktop's Apps folder and return qDocName.

    This function does not parse or inspect the QVF content. For Qlik Sense
    Desktop, placing the app file in the Apps directory is the import step that
    makes it openable by the local engine through OpenDoc.
    """
    source = Path(str(qvf_path)).expanduser()
    if source.suffix.lower() != ".qvf":
        raise ValueError(f"Qlik source must be a .qvf file: {qvf_path}")
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"QVF file not found: {source}")

    apps_root = Path(str(apps_dir)).expanduser() if apps_dir else default_qlik_desktop_apps_dir()
    apps_root.mkdir(parents=True, exist_ok=True)

    source_resolved = source.resolve()
    apps_resolved = apps_root.resolve()
    if source_resolved.parent == apps_resolved:
        return source_resolved.name

    target = apps_resolved / source_resolved.name
    if target.exists() and target.resolve() != source_resolved:
        digest = hashlib.sha1(str(source_resolved).encode("utf-8")).hexdigest()[:8]
        target = apps_resolved / f"{source_resolved.stem}_{digest}{source_resolved.suffix}"

    if not target.exists():
        shutil.copy2(source_resolved, target)
    return target.name


class QixJsonRpcClient:
    def __init__(self, config: QlikEngineClientConfig):
        self.config = config
        self._connection: Any | None = None
        self._next_id = 1

    def connect(self) -> None:
        if self._connection is not None:
            return
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise ImportError(
                "The real QIX client requires the 'websockets' package. "
                "Install backend requirements with: pip install -r backend/requirements.txt"
            ) from exc

        self._connection = connect(
            self._global_endpoint_url(),
            additional_headers=self._headers(),
            open_timeout=self.config.request_timeout_seconds,
            ping_interval=None,
            max_size=None,
            proxy=None,
        )

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.close()
        finally:
            self._connection = None

    def request(self, handle: int, method: str, params: list[Any] | dict[str, Any] | None = None) -> JsonDict:
        self.connect()
        assert self._connection is not None

        request_id = self._next_id
        self._next_id += 1
        payload: JsonDict = {
            "jsonrpc": "2.0",
            "id": request_id,
            "handle": handle,
            "method": method,
            "params": params or [],
        }
        self._connection.send(json.dumps(payload, ensure_ascii=True))

        deadline = time.monotonic() + self.config.request_timeout_seconds
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            if remaining <= 0.1 and time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for QIX response to {method}.")
            raw_message = self._connection.recv(timeout=remaining)
            message = json.loads(str(raw_message))
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                error = message["error"]
                details = error if isinstance(error, str) else error.get("message") or error
                raise ConnectionError(f"QIX method {method} failed: {details}")
            return dict(message.get("result") or {})

    def _global_endpoint_url(self) -> str:
        url = (self.config.endpoint_url or "ws://localhost:4848/app").strip()
        if not url:
            url = "ws://localhost:4848/app"
        return url.rstrip("/") + "/"

    def _headers(self) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        if self.config.user_directory and self.config.user_id:
            headers.append(
                (
                    "X-Qlik-User",
                    f"UserDirectory={self.config.user_directory}; UserId={self.config.user_id}",
                )
            )
        if self.config.session_cookie:
            headers.append(("Cookie", self.config.session_cookie))
        return headers


class QlikEngineClient:
    client_name = "QlikEngineClient"
    extraction_mode = "qix"

    def __init__(self, config: QlikEngineClientConfig | None = None):
        self.config = config or QlikEngineClientConfig()
        self._rpc = QixJsonRpcClient(self.config)
        self._app_id = ""
        self._app_handle: int | None = None
        self._object_cache: dict[str, JsonDict] = {}
        self._sheets_cache: list[JsonDict] | None = None
        self._visuals_cache: list[JsonDict] | None = None

    def close(self) -> None:
        self._rpc.close()

    def open_app(self, app_id: str) -> JsonDict:
        handle = self._ensure_open(app_id)
        app_info: JsonDict = {
            "app_id": app_id,
            "opened": True,
            "qix_handle": handle,
            "endpoint_url": self.config.endpoint_url,
        }
        try:
            result = self._rpc.request(handle, "GetAppLayout", [])
            layout = dict(result.get("qLayout") or {})
            app_info.update(
                {
                    "title": layout.get("qTitle") or layout.get("title") or app_id,
                    "last_reload_time": layout.get("qLastReloadTime") or "",
                    "file_name": layout.get("qFileName") or app_id,
                }
            )
        except Exception as exc:
            app_info["layout_warning"] = str(exc)
        return app_info

    def get_load_script(self, app_id: str) -> str:
        handle = self._ensure_open(app_id)
        result = self._rpc.request(handle, "GetScript", [])
        return str(result.get("qScript") or "")

    def get_sheets(self, app_id: str) -> list[JsonDict]:
        self._load_objects(app_id)
        return list(self._sheets_cache or [])

    def get_visual_objects(self, app_id: str) -> list[JsonDict]:
        self._load_objects(app_id)
        return list(self._visuals_cache or [])

    def get_master_dimensions(self, app_id: str) -> list[JsonDict]:
        handle = self._create_session_list(
            app_id,
            {
                "qInfo": {"qType": "DimensionList"},
                "qDimensionListDef": {
                    "qType": "dimension",
                    "qData": {
                        "title": "/qMetaDef/title",
                        "description": "/qMetaDef/description",
                        "tags": "/qMetaDef/tags",
                        "grouping": "/qDim/qGrouping",
                        "fieldDefs": "/qDim/qFieldDefs",
                        "fieldLabels": "/qDim/qFieldLabels",
                    },
                },
            },
        )
        layout = self._object_layout_by_handle(handle)
        items = _as_list((layout.get("qDimensionList") or {}).get("qItems"))
        dimensions = []
        for item in items:
            if not isinstance(item, dict):
                continue
            data = dict(item.get("qData") or {})
            meta = dict(item.get("qMeta") or {})
            fields = _string_list(data.get("fieldDefs"))
            labels = _string_list(data.get("fieldLabels"))
            info = dict(item.get("qInfo") or {})
            title = data.get("title") or meta.get("title") or info.get("qId") or ""
            dimensions.append(
                {
                    "id": info.get("qId") or title,
                    "title": title,
                    "field": fields[0] if fields else title,
                    "fields": fields,
                    "labels": labels,
                    "description": data.get("description") or meta.get("description") or "",
                    "tags": _string_list(data.get("tags") or meta.get("tags")),
                }
            )
        return dimensions

    def get_master_measures(self, app_id: str) -> list[JsonDict]:
        handle = self._create_session_list(
            app_id,
            {
                "qInfo": {"qType": "MeasureList"},
                "qMeasureListDef": {
                    "qType": "measure",
                    "qData": {
                        "title": "/qMetaDef/title",
                        "description": "/qMetaDef/description",
                        "tags": "/qMetaDef/tags",
                        "expression": "/qMeasure/qDef",
                        "label": "/qMeasure/qLabel",
                    },
                },
            },
        )
        layout = self._object_layout_by_handle(handle)
        items = _as_list((layout.get("qMeasureList") or {}).get("qItems"))
        measures = []
        for item in items:
            if not isinstance(item, dict):
                continue
            data = dict(item.get("qData") or {})
            meta = dict(item.get("qMeta") or {})
            info = dict(item.get("qInfo") or {})
            title = data.get("title") or data.get("label") or meta.get("title") or info.get("qId") or ""
            measures.append(
                {
                    "id": info.get("qId") or title,
                    "title": title,
                    "expression": data.get("expression") or "",
                    "description": data.get("description") or meta.get("description") or "",
                    "tags": _string_list(data.get("tags") or meta.get("tags")),
                }
            )
        return measures

    def get_variables(self, app_id: str) -> list[JsonDict]:
        handle = self._create_session_list(
            app_id,
            {
                "qInfo": {"qType": "VariableList"},
                "qVariableListDef": {
                    "qType": "variable",
                    "qShowReserved": False,
                    "qShowConfig": False,
                    "qData": {
                        "name": "/qName",
                        "definition": "/qDefinition",
                        "value": "/qValue",
                        "comment": "/qComment",
                        "tags": "/qMetaDef/tags",
                    },
                },
            },
        )
        layout = self._object_layout_by_handle(handle)
        items = _as_list((layout.get("qVariableList") or {}).get("qItems"))
        variables = []
        for item in items:
            if not isinstance(item, dict):
                continue
            data = dict(item.get("qData") or {})
            info = dict(item.get("qInfo") or {})
            name = data.get("name") or item.get("qName") or info.get("qId") or ""
            variables.append(
                {
                    "name": name,
                    "definition": data.get("definition") or data.get("value") or "",
                    "comment": data.get("comment") or "",
                    "tags": _string_list(data.get("tags")),
                }
            )
        return variables

    def _ensure_open(self, app_id: str) -> int:
        if self._app_handle is not None and self._app_id == app_id:
            return self._app_handle
        result = self._rpc.request(-1, "OpenDoc", [app_id])
        qreturn = dict(result.get("qReturn") or {})
        handle = qreturn.get("qHandle")
        if not isinstance(handle, int):
            raise ConnectionError(f"QIX OpenDoc did not return an app handle for {app_id}.")
        self._app_id = app_id
        self._app_handle = handle
        self._object_cache = {}
        self._sheets_cache = None
        self._visuals_cache = None
        return handle

    def _load_objects(self, app_id: str) -> None:
        if self._sheets_cache is not None and self._visuals_cache is not None:
            return

        app_handle = self._ensure_open(app_id)
        result = self._rpc.request(app_handle, "GetAllInfos", [])
        infos = [
            dict(info)
            for info in _as_list(result.get("qInfos"))
            if isinstance(info, dict) and str(info.get("qId") or "").strip()
        ]

        sheets: list[JsonDict] = []
        child_to_sheet: dict[str, str] = {}
        for rank, info in enumerate(infos):
            qtype = str(info.get("qType") or "").lower()
            if qtype != "sheet":
                continue
            object_data = self._get_object_data(str(info["qId"]))
            layout = dict(object_data.get("layout") or {})
            sheet = self._parse_sheet(info, layout, rank)
            for child_id in sheet.get("object_ids", []):
                child_to_sheet[str(child_id)] = str(sheet["id"])
            sheets.append(sheet)

        visuals: list[JsonDict] = []
        for info in infos:
            qid = str(info.get("qId") or "")
            qtype = str(info.get("qType") or "")
            if not self._looks_like_visual_type(qtype):
                continue
            object_data = self._get_object_data(qid)
            layout = dict(object_data.get("layout") or {})
            props = dict(object_data.get("properties") or {})
            if not self._looks_like_visual_layout(qtype, layout, props):
                continue
            visual = self._parse_visual(info, layout, props, child_to_sheet.get(qid, ""))
            if visual:
                visuals.append(visual)

        self._sheets_cache = sheets
        self._visuals_cache = visuals

    def _create_session_list(self, app_id: str, definition: JsonDict) -> int:
        app_handle = self._ensure_open(app_id)
        result = self._rpc.request(app_handle, "CreateSessionObject", [definition])
        qreturn = dict(result.get("qReturn") or {})
        handle = qreturn.get("qHandle")
        if not isinstance(handle, int):
            raise ConnectionError("QIX CreateSessionObject did not return a handle.")
        return handle

    def _object_layout_by_handle(self, handle: int) -> JsonDict:
        result = self._rpc.request(handle, "GetLayout", [])
        return dict(result.get("qLayout") or {})

    def _get_object_data(self, qid: str) -> JsonDict:
        if qid in self._object_cache:
            return self._object_cache[qid]
        app_handle = self._ensure_open(self._app_id)
        result = self._rpc.request(app_handle, "GetObject", [qid])
        qreturn = dict(result.get("qReturn") or {})
        handle = qreturn.get("qHandle")
        if not isinstance(handle, int):
            return {}
        data: JsonDict = {"handle": handle}
        try:
            data["layout"] = self._object_layout_by_handle(handle)
        except Exception as exc:
            data["layout_error"] = str(exc)
            data["layout"] = {}
        try:
            prop_result = self._rpc.request(handle, "GetProperties", [])
            data["properties"] = dict(prop_result.get("qProp") or {})
        except Exception as exc:
            data["properties_error"] = str(exc)
            data["properties"] = {}
        self._object_cache[qid] = data
        return data

    def _parse_sheet(self, info: JsonDict, layout: JsonDict, rank: int) -> JsonDict:
        qid = str((layout.get("qInfo") or {}).get("qId") or info.get("qId") or "")
        meta = dict(layout.get("qMeta") or {})
        title = layout.get("title") or meta.get("title") or info.get("qId") or "Sheet"
        return {
            "id": qid,
            "title": title,
            "rank": layout.get("rank", rank),
            "object_ids": _collect_child_ids(layout),
        }

    def _parse_visual(self, info: JsonDict, layout: JsonDict, props: JsonDict, sheet_id: str) -> JsonDict:
        qid = str((layout.get("qInfo") or {}).get("qId") or info.get("qId") or "")
        if not qid:
            return {}
        qtype = str((layout.get("qInfo") or {}).get("qType") or info.get("qType") or "")
        meta = dict(layout.get("qMeta") or {})
        title = layout.get("title") or meta.get("title") or props.get("title") or qid

        dimensions = _dedupe_by_key(
            [
                *_dimensions_from_hypercube_layout(layout),
                *_dimensions_from_hypercube_props(props),
                *_dimensions_from_list_layout(layout),
                *_dimensions_from_list_props(props),
            ],
            "field",
        )
        measures = _dedupe_by_key(
            [
                *_measures_from_hypercube_props(props),
                *_measures_from_hypercube_layout(layout),
            ],
            "expression",
        )

        child_ids = _collect_child_ids(layout)
        if not dimensions and child_ids:
            for child_id in child_ids:
                child = self._get_object_data(child_id)
                child_layout = dict(child.get("layout") or {})
                child_props = dict(child.get("properties") or {})
                dimensions.extend(_dimensions_from_list_layout(child_layout))
                dimensions.extend(_dimensions_from_list_props(child_props))
                measures.extend(_measures_from_hypercube_props(child_props))
                measures.extend(_measures_from_hypercube_layout(child_layout))
            dimensions = _dedupe_by_key(dimensions, "field")
            measures = _dedupe_by_key(measures, "expression")

        return {
            "id": qid,
            "sheet_id": sheet_id,
            "title": title,
            "type": qtype,
            "dimensions": dimensions,
            "measures": measures,
            "qix": {
                "qType": qtype,
                "child_ids": child_ids,
            },
        }

    def _looks_like_visual_type(self, qtype: str) -> bool:
        normalized = (qtype or "").strip().lower()
        excluded = {
            "",
            "sheet",
            "story",
            "snapshot",
            "bookmark",
            "dimension",
            "measure",
            "variable",
            "appprops",
        }
        return normalized not in excluded

    def _looks_like_visual_layout(self, qtype: str, layout: JsonDict, props: JsonDict) -> bool:
        normalized = (qtype or "").strip().lower()
        known_visuals = {
            "barchart",
            "linechart",
            "kpi",
            "table",
            "straighttable",
            "pivot-table",
            "pivot_table",
            "filterpane",
            "piechart",
            "combochart",
            "scatterplot",
            "treemap",
            "map",
            "text-image",
        }
        return (
            normalized in known_visuals
            or bool(layout.get("qHyperCube"))
            or bool(props.get("qHyperCubeDef"))
            or bool(layout.get("qListObject"))
            or bool(props.get("qListObjectDef"))
            or bool(layout.get("qChildList"))
        )


def create_qlik_engine_client(
    config: QlikEngineClientConfig | None = None,
) -> QlikEngineApiClient:
    return QlikEngineClient(config=config)


def extract_qlik_metadata(
    client: QlikEngineApiClient,
    app_id: str,
    qvf_path: str | Path,
) -> JsonDict:
    try:
        app_info = client.open_app(app_id)
        return {
            "source": {
                "qvf_path": str(qvf_path),
                "app_id": app_id,
                "client": client.client_name,
                "extraction_mode": client.extraction_mode,
                "qvf_imported": True,
            },
            "app": app_info,
            "load_script": client.get_load_script(app_id),
            "sheets": client.get_sheets(app_id),
            "visual_objects": client.get_visual_objects(app_id),
            "master_dimensions": client.get_master_dimensions(app_id),
            "master_measures": client.get_master_measures(app_id),
            "variables": client.get_variables(app_id),
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _dimensions_from_hypercube_layout(layout: JsonDict) -> list[JsonDict]:
    hypercube = dict(layout.get("qHyperCube") or {})
    dimensions = []
    for index, dimension in enumerate(_as_list(hypercube.get("qDimensionInfo"))):
        if not isinstance(dimension, dict):
            continue
        fields = _string_list(dimension.get("qGroupFieldDefs"))
        label = dimension.get("qFallbackTitle") or (fields[0] if fields else f"Dimension {index + 1}")
        dimensions.append({"label": label, "field": fields[0] if fields else label})
    return dimensions


def _dimensions_from_hypercube_props(props: JsonDict) -> list[JsonDict]:
    hypercube = dict(props.get("qHyperCubeDef") or {})
    dimensions = []
    for index, dimension in enumerate(_as_list(hypercube.get("qDimensions"))):
        if not isinstance(dimension, dict):
            continue
        qdef = dict(dimension.get("qDef") or {})
        fields = _string_list(qdef.get("qFieldDefs"))
        labels = _string_list(qdef.get("qFieldLabels"))
        label = qdef.get("qLabel") or (labels[0] if labels else "") or (fields[0] if fields else f"Dimension {index + 1}")
        dimensions.append({"label": label, "field": fields[0] if fields else label})
    return dimensions


def _dimensions_from_list_layout(layout: JsonDict) -> list[JsonDict]:
    list_object = dict(layout.get("qListObject") or {})
    info = dict(list_object.get("qDimensionInfo") or {})
    fields = _string_list(info.get("qGroupFieldDefs"))
    label = info.get("qFallbackTitle") or (fields[0] if fields else "")
    return [{"label": label, "field": fields[0] if fields else label}] if label else []


def _dimensions_from_list_props(props: JsonDict) -> list[JsonDict]:
    list_def = dict(props.get("qListObjectDef") or {})
    qdef = dict(list_def.get("qDef") or {})
    fields = _string_list(qdef.get("qFieldDefs"))
    labels = _string_list(qdef.get("qFieldLabels"))
    label = qdef.get("qLabel") or (labels[0] if labels else "") or (fields[0] if fields else "")
    return [{"label": label, "field": fields[0] if fields else label}] if label else []


def _measures_from_hypercube_layout(layout: JsonDict) -> list[JsonDict]:
    hypercube = dict(layout.get("qHyperCube") or {})
    measures = []
    for index, measure in enumerate(_as_list(hypercube.get("qMeasureInfo"))):
        if not isinstance(measure, dict):
            continue
        label = measure.get("qFallbackTitle") or f"Measure {index + 1}"
        expression = measure.get("qDef") or ""
        measures.append({"label": label, "expression": expression})
    return measures


def _measures_from_hypercube_props(props: JsonDict) -> list[JsonDict]:
    hypercube = dict(props.get("qHyperCubeDef") or {})
    measures = []
    for index, measure in enumerate(_as_list(hypercube.get("qMeasures"))):
        if not isinstance(measure, dict):
            continue
        qdef = dict(measure.get("qDef") or {})
        expression = str(qdef.get("qDef") or "")
        label = qdef.get("qLabel") or expression or f"Measure {index + 1}"
        measures.append({"label": label, "expression": expression})
    return measures


def _collect_child_ids(layout: JsonDict) -> list[str]:
    child_ids: list[str] = []

    def walk_items(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            qid = (
                (item.get("qInfo") or {}).get("qId")
                or (item.get("qData") or {}).get("id")
                or item.get("qId")
            )
            if qid and str(qid) not in child_ids:
                child_ids.append(str(qid))
            nested = (item.get("qChildList") or {}).get("qItems")
            if isinstance(nested, list):
                walk_items(nested)

    walk_items(_as_list((layout.get("qChildList") or {}).get("qItems")))
    return child_ids


def _dedupe_by_key(items: list[JsonDict], key: str) -> list[JsonDict]:
    deduped: list[JsonDict] = []
    seen: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or item.get("label") or "").strip()
        if not value or value in seen:
            if value in seen and item.get(key) and not deduped[seen[value]].get(key):
                deduped[seen[value]] = item
            continue
        seen[value] = len(deduped)
        deduped.append(item)
    return deduped


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
