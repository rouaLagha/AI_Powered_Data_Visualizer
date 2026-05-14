from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any, Protocol


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class QlikEngineClientConfig:
    endpoint_url: str = "ws://localhost:4848/app"
    apps_dir: str = ""
    dataprep_cache_dir: str = ""
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

    def get_connections(self, app_id: str) -> list[JsonDict]:
        ...


def default_qlik_desktop_apps_dir() -> Path:
    return Path.home() / "Documents" / "Qlik" / "Sense" / "Apps"


def default_dataprep_cache_dir() -> Path:
    return default_qlik_desktop_apps_dir() / "DataPrepAppCache"


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

        endpoint_url = self._global_endpoint_url()
        try:
            self._connection = connect(
                endpoint_url,
                additional_headers=self._headers(),
                open_timeout=self.config.request_timeout_seconds,
                ping_interval=None,
                max_size=None,
                proxy=None,
            )
        except OSError as exc:
            raise ConnectionError(_qix_connection_error_message(endpoint_url, exc)) from exc

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

    def get_connections(self, app_id: str) -> list[JsonDict]:
        handle = self._ensure_open(app_id)
        result = self._rpc.request(handle, "GetConnections", [])
        return [
            connection
            for connection in (_normalize_qix_connection(item) for item in _connection_items(result))
            if connection
        ]

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
        infos_by_id = {str(info.get("qId") or ""): info for info in infos}
        for rank, info in enumerate(infos):
            qtype = str(info.get("qType") or "").lower()
            if qtype != "sheet":
                continue
            object_data = self._get_object_data(str(info["qId"]))
            layout = dict(object_data.get("layout") or {})
            props = dict(object_data.get("properties") or {})
            sheet = self._parse_sheet(info, layout, props, rank)
            for child_id in sheet.get("object_ids", []):
                child_to_sheet.setdefault(str(child_id), str(sheet["id"]))
            sheets.append(sheet)

        visuals: list[JsonDict] = []
        candidate_ids: list[str] = []
        for info in infos:
            qid = str(info.get("qId") or "").strip()
            if not qid:
                continue
            qtype = str(info.get("qType") or "")
            if self._looks_like_visual_type(qtype):
                candidate_ids.append(qid)

        for child_id in child_to_sheet:
            if child_id not in candidate_ids:
                candidate_ids.append(child_id)

        seen_visual_ids: set[str] = set()
        for qid in candidate_ids:
            if qid in seen_visual_ids:
                continue
            seen_visual_ids.add(qid)
            info = infos_by_id.get(qid, {"qId": qid, "qType": ""})
            qtype = str(info.get("qType") or "")
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

    def _parse_sheet(self, info: JsonDict, layout: JsonDict, props: JsonDict, rank: int) -> JsonDict:
        qid = str((layout.get("qInfo") or {}).get("qId") or info.get("qId") or "")
        meta = dict(layout.get("qMeta") or {})
        title = layout.get("title") or meta.get("title") or info.get("qId") or "Sheet"
        return {
            "id": qid,
            "title": title,
            "rank": layout.get("rank", rank),
            "object_ids": _collect_child_ids(layout, props),
        }

    def _parse_visual(self, info: JsonDict, layout: JsonDict, props: JsonDict, sheet_id: str) -> JsonDict:
        qid = str((layout.get("qInfo") or {}).get("qId") or info.get("qId") or "")
        if not qid:
            return {}
        qtype = str((layout.get("qInfo") or {}).get("qType") or info.get("qType") or "")
        meta = dict(layout.get("qMeta") or {})
        title = layout.get("title") or meta.get("title") or props.get("title") or qid
        visual_type = _visual_type(qtype=qtype, layout=layout, props=props)

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

        child_ids = _collect_child_ids(layout, props)
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
            "subtitle": layout.get("subtitle") or props.get("subtitle") or "",
            "footnote": layout.get("footnote") or props.get("footnote") or "",
            "type": visual_type,
            "source_type": qtype,
            "dimensions": dimensions,
            "measures": measures,
            "qix": {
                "qType": qtype,
                "visualization": visual_type,
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
            "boxplot",
            "bulletchart",
            "button",
            "linechart",
            "kpi",
            "gauge",
            "histogram",
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
            "container",
            "distributionplot",
            "waterfallchart",
            "mekkochart",
            "sn-table",
            "qlik-multi-kpi",
            "qlik-variable-input",
            "qlik-date-picker",
        }
        visualization = _visual_type(qtype=qtype, layout=layout, props=props).lower()
        return (
            normalized in known_visuals
            or visualization in known_visuals
            or bool(_dicts_with_key(layout, "qHyperCube"))
            or bool(_dicts_with_key(props, "qHyperCubeDef"))
            or bool(_dicts_with_key(layout, "qListObject"))
            or bool(_dicts_with_key(props, "qListObjectDef"))
            or bool(_collect_child_ids(layout, props))
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
        load_script = client.get_load_script(app_id)
        sheets = client.get_sheets(app_id)
        visual_objects = client.get_visual_objects(app_id)
        connection_warnings: list[str] = []
        qix_connections: list[JsonDict] = []
        get_connections = getattr(client, "get_connections", None)
        if callable(get_connections):
            try:
                qix_connections = get_connections(app_id)
            except Exception as exc:
                connection_warnings.append(str(exc))
        else:
            connection_warnings.append(f"{client.client_name} does not expose connection metadata extraction.")

        script_connections = _connection_references_from_load_script(load_script)
        internal_connections = [
            connection
            for connection in [*qix_connections, *script_connections]
            if _is_internal_connection_reference(connection)
        ]
        connections = _merge_connections(
            [connection for connection in qix_connections if not _is_internal_connection_reference(connection)],
            [connection for connection in script_connections if not _is_internal_connection_reference(connection)],
        )
        config = getattr(client, "config", None)
        dataprep_cache_metadata = extract_dataprep_cache_metadata(
            app_id=app_id,
            qvf_path=qvf_path,
            visual_objects=visual_objects,
            cache_dir=str(getattr(config, "dataprep_cache_dir", "") or ""),
        )
        visual_objects = _attach_dataprep_matches_to_visuals(
            visual_objects=visual_objects,
            visual_table_matches=_as_list(dataprep_cache_metadata.get("visual_table_matches")),
        )
        return {
            "source": {
                "qvf_path": str(qvf_path),
                "app_id": app_id,
                "client": client.client_name,
                "extraction_mode": client.extraction_mode,
                "qvf_imported": True,
            },
            "app": app_info,
            "load_script": load_script,
            "connections": connections,
            "internal_connections": internal_connections,
            "connection_warnings": connection_warnings,
            "dataprep_cache": dataprep_cache_metadata,
            "sheets": sheets,
            "visual_objects": visual_objects,
            "master_dimensions": client.get_master_dimensions(app_id),
            "master_measures": client.get_master_measures(app_id),
            "variables": client.get_variables(app_id),
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _dimensions_from_hypercube_layout(layout: JsonDict) -> list[JsonDict]:
    dimensions = []
    for hypercube in _dicts_with_key(layout, "qHyperCube"):
        for index, dimension in enumerate(_as_list(hypercube.get("qDimensionInfo"))):
            if not isinstance(dimension, dict):
                continue
            fields = _string_list(dimension.get("qGroupFieldDefs"))
            label = dimension.get("qFallbackTitle") or (fields[0] if fields else f"Dimension {index + 1}")
            dimensions.append(
                {
                    "label": label,
                    "field": fields[0] if fields else label,
                    "fields": fields,
                    "library_id": dimension.get("qLibraryId") or "",
                    "source": "hypercube_layout",
                }
            )
    return dimensions


def _dimensions_from_hypercube_props(props: JsonDict) -> list[JsonDict]:
    dimensions = []
    for hypercube in _dicts_with_key(props, "qHyperCubeDef"):
        for index, dimension in enumerate(_as_list(hypercube.get("qDimensions"))):
            if not isinstance(dimension, dict):
                continue
            qdef = dict(dimension.get("qDef") or {})
            fields = _string_list(qdef.get("qFieldDefs"))
            labels = _string_list(qdef.get("qFieldLabels"))
            expression = str(qdef.get("qDef") or "").strip()
            label = (
                qdef.get("qLabel")
                or (labels[0] if labels else "")
                or (fields[0] if fields else "")
                or expression
                or f"Dimension {index + 1}"
            )
            dimensions.append(
                {
                    "label": label,
                    "field": fields[0] if fields else expression or label,
                    "fields": fields,
                    "expression": expression,
                    "library_id": dimension.get("qLibraryId") or qdef.get("qLibraryId") or "",
                    "source": "hypercube_properties",
                }
            )
    return dimensions


def _dimensions_from_list_layout(layout: JsonDict) -> list[JsonDict]:
    dimensions = []
    for list_object in _dicts_with_key(layout, "qListObject"):
        info = dict(list_object.get("qDimensionInfo") or {})
        fields = _string_list(info.get("qGroupFieldDefs"))
        label = info.get("qFallbackTitle") or (fields[0] if fields else "")
        if label:
            dimensions.append(
                {
                    "label": label,
                    "field": fields[0] if fields else label,
                    "fields": fields,
                    "library_id": info.get("qLibraryId") or "",
                    "source": "list_layout",
                }
            )
    return dimensions


def _dimensions_from_list_props(props: JsonDict) -> list[JsonDict]:
    dimensions = []
    for list_def in _dicts_with_key(props, "qListObjectDef"):
        qdef = dict(list_def.get("qDef") or {})
        fields = _string_list(qdef.get("qFieldDefs"))
        labels = _string_list(qdef.get("qFieldLabels"))
        expression = str(qdef.get("qDef") or "").strip()
        label = qdef.get("qLabel") or (labels[0] if labels else "") or (fields[0] if fields else "") or expression
        if label:
            dimensions.append(
                {
                    "label": label,
                    "field": fields[0] if fields else expression or label,
                    "fields": fields,
                    "expression": expression,
                    "library_id": list_def.get("qLibraryId") or qdef.get("qLibraryId") or "",
                    "source": "list_properties",
                }
            )
    return dimensions


def _measures_from_hypercube_layout(layout: JsonDict) -> list[JsonDict]:
    measures = []
    for hypercube in _dicts_with_key(layout, "qHyperCube"):
        for index, measure in enumerate(_as_list(hypercube.get("qMeasureInfo"))):
            if not isinstance(measure, dict):
                continue
            label = measure.get("qFallbackTitle") or f"Measure {index + 1}"
            expression = str(measure.get("qDef") or "").strip()
            measures.append(
                {
                    "label": label,
                    "expression": expression,
                    "library_id": measure.get("qLibraryId") or "",
                    "source": "hypercube_layout",
                }
            )
    return measures


def _measures_from_hypercube_props(props: JsonDict) -> list[JsonDict]:
    measures = []
    for hypercube in _dicts_with_key(props, "qHyperCubeDef"):
        for index, measure in enumerate(_as_list(hypercube.get("qMeasures"))):
            if not isinstance(measure, dict):
                continue
            qdef = dict(measure.get("qDef") or {})
            expression = str(qdef.get("qDef") or "").strip()
            label = qdef.get("qLabel") or expression or f"Measure {index + 1}"
            measures.append(
                {
                    "label": label,
                    "expression": expression,
                    "library_id": measure.get("qLibraryId") or qdef.get("qLibraryId") or "",
                    "source": "hypercube_properties",
                }
            )
    return measures


def _collect_child_ids(*payloads: JsonDict) -> list[str]:
    child_ids: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in child_ids:
            child_ids.append(text)

    def walk_items(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            qinfo = item.get("qInfo") if isinstance(item.get("qInfo"), dict) else {}
            qdata = item.get("qData") if isinstance(item.get("qData"), dict) else {}
            for candidate in (qinfo.get("qId"), qdata.get("id"), item.get("qId"), item.get("id"), item.get("name")):
                add(candidate)
            nested = (item.get("qChildList") or {}).get("qItems")
            if isinstance(nested, list):
                walk_items(nested)
            cells = item.get("cells")
            if isinstance(cells, list):
                walk_cells(cells)

    def walk_cells(cells: list[Any]) -> None:
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            for candidate in (cell.get("name"), cell.get("id"), cell.get("qId")):
                add(candidate)
            nested = cell.get("children") or cell.get("cells")
            if isinstance(nested, list):
                walk_cells(nested)

    def walk_payload(value: Any) -> None:
        if isinstance(value, dict):
            child_list = value.get("qChildList")
            if isinstance(child_list, dict):
                walk_items(_as_list(child_list.get("qItems")))
            cells = value.get("cells")
            if isinstance(cells, list):
                walk_cells(cells)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    walk_payload(nested)
        elif isinstance(value, list):
            for item in value:
                walk_payload(item)

    for payload in payloads:
        walk_payload(payload)
    return child_ids


def _dicts_with_key(payload: Any, key: str) -> list[JsonDict]:
    matches: list[JsonDict] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            nested = value.get(key)
            if isinstance(nested, dict):
                matches.append(dict(nested))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return matches


def _visual_type(qtype: str, layout: JsonDict, props: JsonDict) -> str:
    for payload in (props, layout):
        for key in ("visualization", "type", "qType"):
            value = payload.get(key)
            if str(value or "").strip():
                return str(value).strip()
    return str(qtype or "").strip() or "unknown"


def extract_dataprep_cache_metadata(
    app_id: str,
    qvf_path: str | Path,
    visual_objects: list[JsonDict],
    cache_dir: str | Path = "",
) -> JsonDict:
    cache_root = Path(str(cache_dir)).expanduser() if str(cache_dir or "").strip() else default_dataprep_cache_dir()
    candidates = _dataprep_app_cache_candidates(cache_root=cache_root, app_id=app_id, qvf_path=qvf_path)
    app_cache_dir = next((candidate for candidate in candidates if candidate.exists() and candidate.is_dir()), None)

    payload: JsonDict = {
        "cache_root": str(cache_root),
        "app_cache_dir": str(app_cache_dir) if app_cache_dir else "",
        "available": bool(app_cache_dir),
        "match_strategy": "folder_name",
        "qvd_tables": [],
        "visual_table_matches": [],
        "warnings": [],
    }
    if app_cache_dir is None:
        payload["warnings"].append(
            "No DataPrepAppCache folder matched the app. QVD cache metadata is optional and may not exist until Qlik prepares data."
        )
        return payload

    qvd_tables = []
    for qvd_path in sorted(app_cache_dir.glob("*.qvd"), key=lambda path: path.name.lower()):
        table = _read_qvd_table_metadata(qvd_path)
        if table:
            qvd_tables.append(table)
        else:
            payload["warnings"].append(f"Unable to read QVD header: {qvd_path.name}")

    visual_table_matches = _match_visuals_to_qvd_tables(visual_objects=visual_objects, qvd_tables=qvd_tables)
    _attach_visual_usage_to_qvd_tables(qvd_tables=qvd_tables, visual_table_matches=visual_table_matches)
    payload["qvd_tables"] = qvd_tables
    payload["visual_table_matches"] = visual_table_matches
    return payload


def _dataprep_app_cache_candidates(cache_root: Path, app_id: str, qvf_path: str | Path) -> list[Path]:
    qvf_name = Path(str(qvf_path)).name
    names = [app_id, qvf_name]

    for value in (app_id, qvf_name):
        text = str(value or "").strip()
        suffix = Path(text).suffix
        stem = Path(text).stem
        if suffix.lower() == ".qvf" and "_" in stem:
            names.append(f"{stem.rsplit('_', 1)[0]}.qvf")
        if stem:
            names.append(stem)

    unique_names: list[str] = []
    for name in names:
        cleaned = str(name or "").strip()
        if cleaned and cleaned not in unique_names:
            unique_names.append(cleaned)
    return [cache_root / name for name in unique_names]


def _read_qvd_table_metadata(qvd_path: Path) -> JsonDict:
    marker = b"</QvdTableHeader>"
    try:
        with qvd_path.open("rb") as handle:
            raw = handle.read(2 * 1024 * 1024)
    except OSError:
        return {}

    end = raw.find(marker)
    if end < 0:
        return {}

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(raw[: end + len(marker)].decode("utf-8", errors="replace"))
    except Exception:
        return {}

    columns = []
    for field in root.findall("./Fields/QvdFieldHeader"):
        field_name = str(field.findtext("FieldName") or "").strip()
        if not field_name:
            continue
        no_of_symbols = _safe_int(field.findtext("NoOfSymbols"))
        data_type = _infer_qvd_field_type(field_name, no_of_symbols)
        role = _infer_qvd_field_role(field_name, data_type, no_of_symbols)
        columns.append(
            {
                "data_type": data_type,
                "role": role,
                "tableau_datatype": _qvd_tableau_datatype(field_name, data_type, role),
                "name": field_name,
                "no_of_symbols": no_of_symbols,
                "bit_offset": _safe_int(field.findtext("BitOffset")),
                "bit_width": _safe_int(field.findtext("BitWidth")),
                "bias": _safe_int(field.findtext("Bias")),
            }
        )

    return {
        "file_name": qvd_path.name,
        "path": str(qvd_path),
        "size_bytes": qvd_path.stat().st_size,
        "table_name": str(root.findtext("TableName") or qvd_path.stem).strip() or qvd_path.stem,
        "record_count": _safe_int(root.findtext("NoOfRecords")),
        "field_count": len(columns),
        "columns": columns,
        "fields": columns,
        "field_names": [field["name"] for field in columns],
        "create_utc_time": str(root.findtext("CreateUtcTime") or "").strip(),
        "source_create_utc_time": str(root.findtext("SourceCreateUtcTime") or "").strip(),
        "used_by_visuals": [],
    }


def _match_visuals_to_qvd_tables(visual_objects: list[JsonDict], qvd_tables: list[JsonDict]) -> list[JsonDict]:
    matches = []
    for visual in visual_objects:
        if not isinstance(visual, dict):
            continue
        visual_fields = _visual_field_names(visual)
        table_matches = []
        for table in qvd_tables:
            table_fields = {_field_key(field) for field in _as_list(table.get("field_names"))}
            overlap = sorted(visual_fields & table_fields)
            score = len(overlap) / max(1, len(visual_fields))
            if overlap:
                table_matches.append(
                    {
                        "table_name": table.get("table_name", ""),
                        "qvd_file": table.get("file_name", ""),
                        "matched_fields": overlap,
                        "matched_field_count": len(overlap),
                        "visual_field_count": len(visual_fields),
                        "score": round(score, 4),
                    }
                )
        table_matches.sort(key=lambda item: (-item["score"], -item["matched_field_count"], str(item["table_name"]).lower()))
        matches.append(
            {
                "visual_id": visual.get("id", ""),
                "visual_title": visual.get("title", ""),
                "visual_type": visual.get("type") or visual.get("source_type") or "",
                "fields": sorted(visual_fields),
                "matches": table_matches,
                "best_match": table_matches[0] if table_matches else {},
                "relationship": "field_overlap",
            }
        )
    return matches


def _attach_dataprep_matches_to_visuals(
    visual_objects: list[JsonDict],
    visual_table_matches: list[JsonDict],
) -> list[JsonDict]:
    match_by_id = {str(item.get("visual_id") or ""): item for item in visual_table_matches if isinstance(item, dict)}
    output = []
    for visual in visual_objects:
        if not isinstance(visual, dict):
            continue
        enriched = dict(visual)
        match = match_by_id.get(str(enriched.get("id") or ""))
        if match:
            enriched["data_cache_matches"] = match.get("matches", [])
            enriched["best_data_cache_match"] = match.get("best_match", {})
        output.append(enriched)
    return output


def _attach_visual_usage_to_qvd_tables(qvd_tables: list[JsonDict], visual_table_matches: list[JsonDict]) -> None:
    table_by_name = {str(table.get("table_name") or ""): table for table in qvd_tables if isinstance(table, dict)}
    for match in visual_table_matches:
        if not isinstance(match, dict):
            continue
        best_match = match.get("best_match") if isinstance(match.get("best_match"), dict) else {}
        table = table_by_name.get(str(best_match.get("table_name") or ""))
        if not table:
            continue
        table.setdefault("used_by_visuals", []).append(
            {
                "visual_id": match.get("visual_id", ""),
                "visual_title": match.get("visual_title", ""),
                "score": best_match.get("score", 0),
                "matched_fields": best_match.get("matched_fields", []),
            }
        )


def _visual_field_names(visual: JsonDict) -> set[str]:
    fields: set[str] = set()
    for dimension in _as_list(visual.get("dimensions")):
        if not isinstance(dimension, dict):
            continue
        for value in [dimension.get("field"), dimension.get("label"), *_as_list(dimension.get("fields"))]:
            key = _field_key(value)
            if key:
                fields.add(key)
        for value in _field_refs_from_expression(dimension.get("expression")):
            fields.add(value)

    for measure in _as_list(visual.get("measures")):
        if not isinstance(measure, dict):
            continue
        for value in _field_refs_from_expression(measure.get("expression")):
            fields.add(value)
        if not _field_refs_from_expression(measure.get("expression")):
            key = _field_key(measure.get("label"))
            if key:
                fields.add(key)
    return fields


def _field_refs_from_expression(expression: Any) -> set[str]:
    text = str(expression or "")
    refs = {_field_key(match) for match in re.findall(r"\[([^\]]+)\]", text)}
    for match in re.finditer(r"\b(?:sum|avg|average|count|min|max|only)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", text, flags=re.IGNORECASE):
        refs.add(_field_key(match.group(1)))
    refs.discard("")
    return refs


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().strip("[]").lower())


def _safe_int(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or "0")
    except ValueError:
        return 0


def _infer_qvd_field_type(field_name: str, no_of_symbols: int) -> str:
    normalized = field_name.lower()
    if "date" in normalized or normalized.endswith("day") or normalized.endswith("month") or normalized.endswith("year"):
        return "date_or_calendar"
    if normalized.endswith("key") or normalized.endswith("id"):
        return "key"
    if any(token in normalized for token in ("amount", "cost", "price", "qty", "quantity", "sales", "total", "discount", "tax", "freight", "profit", "margin", "value", "pct", "percent", "rate")):
        return "measure_candidate"
    if no_of_symbols and no_of_symbols <= 64:
        return "dimension_candidate"
    return "unknown"


def _infer_qvd_field_role(field_name: str, data_type: str, no_of_symbols: int) -> str:
    normalized = field_name.lower()
    if data_type in {"date_or_calendar", "key"}:
        return "dimension"
    if any(token in normalized for token in ("group", "country", "region", "image", "name", "type", "category", "label", "description")):
        return "dimension"
    if any(token in normalized for token in ("amount", "cost", "price", "qty", "quantity", "sales", "total", "discount", "tax", "freight", "profit", "margin", "value", "pct", "percent", "rate")):
        return "measure"
    if data_type == "measure_candidate":
        return "measure" if no_of_symbols and no_of_symbols > 64 else "dimension"
    return "dimension"


def _qvd_tableau_datatype(field_name: str, data_type: str, role: str) -> str:
    if role == "measure":
        return "real"
    if data_type == "date_or_calendar":
        return "date"
    if data_type == "key":
        return "integer"
    normalized = field_name.lower()
    if any(token in normalized for token in ("amount", "cost", "price", "qty", "quantity", "sales", "total", "discount", "tax", "freight", "profit", "margin", "value", "pct", "percent", "rate")):
        return "real"
    return "string"


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


def _normalize_qix_connection(item: Any) -> JsonDict:
    if not isinstance(item, dict):
        return {}

    connection = item.get("qConnection") if isinstance(item.get("qConnection"), dict) else item
    name = _first_string(connection, ("qName", "name", "qId", "id"))
    connection_type = _first_string(connection, ("qType", "type", "qConnectionType", "connection_type"))
    provider = _first_string(connection, ("qProvider", "provider", "qDBMSName", "dbms"))
    raw_connection_string = _first_string(
        connection,
        ("qConnectionString", "connection_string", "connectionString", "qConnectStatement"),
    )
    redacted_connection_string = _redact_connection_string(raw_connection_string)
    attributes = _connection_string_entries(raw_connection_string)
    username = _first_string(
        connection,
        ("qUserName", "qUserId", "qUserID", "qUser", "username", "user_name", "userId", "userID", "user", "login"),
    ) or _conn_value(
        attributes,
        ("user id", "userid", "uid", "username", "user name", "user", "login", "login id"),
    )
    has_saved_password = _has_saved_password_metadata(connection, attributes)

    return {
        "id": _first_string(connection, ("qId", "id")),
        "name": name,
        "type": connection_type,
        "provider": provider or _infer_provider_from_connection_string(raw_connection_string),
        "username": username,
        "authentication": _first_string(connection, ("qLogOn", "authentication", "auth")),
        "password": "",
        "has_saved_password": has_saved_password,
        "server": _conn_value(attributes, ("server", "host", "hostname", "data source", "address")),
        "database": _conn_value(attributes, ("database", "dbname", "dbq", "initial catalog", "catalog")),
        "connection_string": redacted_connection_string,
        "modified": _first_string(connection, ("qModifiedDate", "modified", "modified_date")),
        "source": "qix_get_connections",
        "internal": _is_internal_connection_name(name),
    }


def _qix_connection_error_message(endpoint_url: str, error: OSError) -> str:
    reason = str(error).strip() or type(error).__name__
    return (
        f"Cannot connect to Qlik Engine QIX endpoint {endpoint_url}. "
        "Start Qlik Sense Desktop and keep it running, or set qlik_endpoint to the correct QIX WebSocket URL. "
        "For Qlik Sense Desktop the default endpoint is ws://localhost:4848/app. "
        f"Original connection error: {reason}"
    )


def _connection_items(payload: JsonDict) -> list[Any]:
    for key in ("qConnections", "connections", "qItems"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    connection_list = payload.get("qConnectionList")
    if isinstance(connection_list, dict):
        for key in ("qItems", "qConnections", "items"):
            value = connection_list.get(key)
            if isinstance(value, list):
                return value
    return []


def _connection_references_from_load_script(load_script: str) -> list[JsonDict]:
    text = str(load_script or "")
    references: dict[str, JsonDict] = {}

    for match in re.finditer(
        r"\bLIB\s+CONNECT\s+TO\s+(?:'([^']+)'|\"([^\"]+)\"|\[([^\]]+)\]|([^;\r\n]+))",
        text,
        flags=re.IGNORECASE,
    ):
        name = _clean_connection_ref(match.group(1) or match.group(2) or match.group(3) or match.group(4))
        _register_script_connection_reference(references, name, "lib_connect_statement")

    for match in re.finditer(r"\blib://([^/\]\r\n]+)(?:/([^\]\r\n]*))?", text, flags=re.IGNORECASE):
        name = _clean_connection_ref(match.group(1))
        path = str(match.group(2) or "").strip()
        _register_script_connection_reference(references, name, "lib_uri_reference", path=path)

    for match in re.finditer(
        r"(?m)^\s*CONNECT\s+TO\s+(?:'([^']+)'|\"([^\"]+)\"|\[([^\]]+)\]|([^;\r\n]+))",
        text,
        flags=re.IGNORECASE,
    ):
        name = _clean_connection_ref(match.group(1) or match.group(2) or match.group(3) or match.group(4))
        _register_script_connection_reference(references, name, "connect_statement")

    return sorted(references.values(), key=lambda item: str(item.get("name") or "").lower())


def _register_script_connection_reference(
    references: dict[str, JsonDict],
    name: str,
    reference_type: str,
    path: str = "",
) -> None:
    if not name:
        return
    key = name.lower()
    entry = references.setdefault(
        key,
        {
            "id": "",
            "name": name,
            "type": "library",
            "provider": "qlik_data_connection",
            "username": "",
            "authentication": "",
            "password": "",
            "has_saved_password": False,
            "server": "",
            "database": "",
            "connection_string": "",
            "source": "load_script_reference",
            "reference_types": [],
            "reference_count": 0,
            "paths": [],
            "internal": _is_internal_connection_name(name),
        },
    )
    entry["reference_count"] = int(entry.get("reference_count") or 0) + 1
    reference_types = entry.setdefault("reference_types", [])
    if isinstance(reference_types, list) and reference_type not in reference_types:
        reference_types.append(reference_type)
    paths = entry.setdefault("paths", [])
    if path and isinstance(paths, list) and path not in paths:
        paths.append(path)


def _merge_connections(qix_connections: list[JsonDict], script_connections: list[JsonDict]) -> list[JsonDict]:
    merged: dict[str, JsonDict] = {}
    unnamed_index = 0

    for connection in qix_connections:
        if not isinstance(connection, dict):
            continue
        name = str(connection.get("name") or "").strip()
        key = name.lower() if name else f"qix:{unnamed_index}"
        unnamed_index += 1
        merged[key] = dict(connection)

    for connection in script_connections:
        if not isinstance(connection, dict):
            continue
        name = str(connection.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in merged:
            merged[key]["load_script_referenced"] = True
            merged[key]["reference_count"] = connection.get("reference_count", 0)
            merged[key]["reference_types"] = connection.get("reference_types", [])
            merged[key]["paths"] = connection.get("paths", [])
            continue
        merged[key] = dict(connection)

    return sorted(merged.values(), key=lambda item: str(item.get("name") or item.get("id") or "").lower())


def _is_internal_connection_reference(connection: JsonDict) -> bool:
    if not isinstance(connection, dict):
        return True
    if bool(connection.get("internal")):
        return True
    return _is_internal_connection_name(connection.get("name") or connection.get("id"))


def _is_internal_connection_name(value: Any) -> bool:
    name = str(value or "").strip().lower()
    return (
        not name
        or name.startswith("__")
        or name in {"dataprepappcache", "dataprep app cache", "dataprep"}
        or "dataprepappcache" in name
    )


def _clean_connection_ref(value: Any) -> str:
    text = str(value or "").strip().strip(";").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.strip("'\"")


def _first_string(payload: JsonDict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _connection_string_entries(raw_connection_string: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for part in str(raw_connection_string or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        normalized = re.sub(r"\s+", " ", key.strip().lower())
        if normalized:
            entries[normalized] = value.strip()
    return entries


def _conn_value(entries: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = entries.get(key)
        if value:
            return value
    return ""


def _has_saved_password_metadata(connection: JsonDict, entries: dict[str, str]) -> bool:
    for key, value in entries.items():
        if _is_password_key(key) and str(value or "").strip():
            return True
    for key, value in connection.items():
        if _is_password_key(key) and str(value or "").strip():
            return True
    return False


def _is_password_key(key: Any) -> bool:
    normalized = re.sub(r"\s+", " ", str(key or "").strip().lower())
    return normalized in {"password", "pwd", "pass", "qpassword", "q password"} or normalized.endswith("password")


def _redact_connection_string(raw_connection_string: str) -> str:
    sensitive_keys = {
        "password",
        "pwd",
        "pass",
        "secret",
        "token",
        "access token",
        "api key",
        "apikey",
        "client secret",
    }
    parts = []
    for part in str(raw_connection_string or "").split(";"):
        if "=" not in part:
            cleaned = part.strip()
            if cleaned:
                parts.append(cleaned)
            continue
        key, value = part.split("=", 1)
        normalized_key = re.sub(r"\s+", " ", key.strip().lower())
        safe_value = "***" if normalized_key in sensitive_keys else value.strip()
        parts.append(f"{key.strip()}={safe_value}")
    return ";".join(parts)


def _infer_provider_from_connection_string(raw_connection_string: str) -> str:
    text = str(raw_connection_string or "").lower()
    if "sql server" in text or "sqlserver" in text:
        return "sqlserver"
    if "snowflake" in text:
        return "snowflake"
    if "postgres" in text:
        return "postgresql"
    if "mysql" in text:
        return "mysql"
    if "oracle" in text:
        return "oracle"
    if "odbc" in text:
        return "odbc"
    if "oledb" in text:
        return "oledb"
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
