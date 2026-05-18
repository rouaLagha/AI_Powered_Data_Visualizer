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

    def get_tables_and_fields(self, app_id: str) -> list[JsonDict]:
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
        self._tables_and_keys_cache: JsonDict | None = None

    def close(self) -> None:
        self._rpc.close()

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
        self._tables_and_keys_cache = None
        return handle

    def _create_session_list(self, app_id: str, definition: JsonDict) -> int:
        app_handle = self._ensure_open(app_id)
        result = self._rpc.request(app_handle, "CreateSessionObject", [definition])
        qreturn = dict(result.get("qReturn") or {})
        handle = qreturn.get("qHandle")
        if not isinstance(handle, int):
            raise ConnectionError("QIX CreateSessionObject did not return a handle.")
        return handle

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
            if str(info.get("qType") or "").lower() != "sheet":
                continue
            object_data = self._get_object_data(str(info["qId"]))
            layout = dict(object_data.get("layout") or {})
            props = dict(object_data.get("properties") or {})
            sheet = self._parse_sheet(info, layout, props, rank)
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

    def get_sheets(self, app_id: str) -> list[JsonDict]:
        self._load_objects(app_id)
        return list(self._sheets_cache or [])

    def get_visual_objects(self, app_id: str) -> list[JsonDict]:
        self._load_objects(app_id)
        visuals = list(self._visuals_cache or [])
        if not visuals:
            return visuals
        try:
            master_measures = self.get_master_measures(app_id)
        except Exception:
            master_measures = []
        return _enrich_visual_measure_expressions(visuals, master_measures)

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

    def get_tables_and_fields(self, app_id: str) -> list[JsonDict]:
        """Return tables and fields from QIX (fallback to load script parsing)."""
        qix_payload = self._get_tables_and_keys_payload(app_id)
        tables_from_qix = _extract_tables_from_tables_and_keys_payload(qix_payload)
        if tables_from_qix:
            return tables_from_qix

        # Fallback only when the engine does not expose tables/keys for this app.
        try:
            script = (self.get_load_script(app_id) or "")
        except Exception:
            script = ""

        tables: dict[str, dict] = {}

        # Helper to add table entry
        def add_table(name: str, fields: list[str] | None = None, source: str | None = None) -> None:
            key = (name or "").strip()
            if not key:
                return
            if key not in tables:
                tables[key] = {"name": key, "fields": [], "source_qvd": source or ""}
            if fields:
                # preserve order, dedupe
                for f in fields:
                    if f and f not in tables[key]["fields"]:
                        tables[key]["fields"].append(f)

        # 1) Detect explicit QVD references
        for match in re.finditer(r"\bFROM\s+\[?([^\]\s;']+\.qvd)\]?", script, flags=re.IGNORECASE):
            qvd_path = match.group(1).strip("'\"")
            name = Path(qvd_path).stem
            add_table(name, fields=None, source=qvd_path)

        # 2) Parse LOAD ... FROM/RESIDENT blocks to extract field lists
        # Matches LOAD <fields> FROM <target> ... ; or LOAD <fields> RESIDENT <table> ...;
        for load_match in re.finditer(r"\bLOAD\s+(.*?)\s+(FROM|RESIDENT)\s+([^;\n]+)", script, flags=re.IGNORECASE | re.DOTALL):
            raw_fields = load_match.group(1)
            op = load_match.group(2).upper()
            target = load_match.group(3).strip()

            # clean fields: remove parentheses and qualifiers, split by comma
            # handle "Field as Alias" and "Field (something)"
            field_tokens = []
            for part in re.split(r",(?=(?:[^'\"]*['\"][^'\"]*['\"])*[^'\"]*$)", raw_fields):
                token = part.strip()
                if not token:
                    continue
                # remove trailing qualifiers like "AS Alias" or "(something)"
                token = re.sub(r"\s+AS\s+.+$", "", token, flags=re.IGNORECASE).strip()
                token = re.sub(r"\(.*?\)", "", token).strip()
                # if token contains space, take last part (e.g., "Table.Field" -> "Field")
                token = token.split()[-1]
                token = token.strip('"\'')
                if token:
                    field_tokens.append(token)

            # choose table name: if FROM references a qvd path, use its stem
            table_name = ""
            qvd_in_target = re.search(r"([\w\-/\\.]+\.qvd)", target, flags=re.IGNORECASE)
            if qvd_in_target:
                table_name = Path(qvd_in_target.group(1)).stem
            else:
                # RESIDENT target gives table name directly
                if op == "RESIDENT":
                    table_name = re.sub(r"[^A-Za-z0-9_]+", "", target.split()[0])
                else:
                    # try to find an alias AS <name> nearby
                    alias_match = re.search(r"AS\s+([A-Za-z0-9_]+)", target, flags=re.IGNORECASE)
                    if alias_match:
                        table_name = alias_match.group(1)

            if not table_name:
                # fallback: try to infer a table name from a preceding LET or statement
                continue

            add_table(table_name, fields=field_tokens, source=(qvd_in_target.group(1) if qvd_in_target else None))

        # 3) If still empty, fallback to scanning for simple table aliases (AS table)
        for alias in re.findall(r"\bAS\s+([A-Za-z0-9_]+)\b", script, flags=re.IGNORECASE):
            add_table(alias)

        # convert to list
        return list(tables.values())

    def get_table_relationships(self, app_id: str) -> list[JsonDict]:
        """Return unvalidated association candidates extracted from QIX tables/keys metadata."""
        qix_payload = self._get_tables_and_keys_payload(app_id)
        return _extract_relationships_from_tables_and_keys_payload(qix_payload)

    def _get_tables_and_keys_payload(self, app_id: str) -> JsonDict:
        if isinstance(self._tables_and_keys_cache, dict) and self._tables_and_keys_cache:
            return self._tables_and_keys_cache

        app_handle = self._ensure_open(app_id)
        q_window_size = {"qcx": 1000, "qcy": 1000}
        q_null_size = {"qcx": 0, "qcy": 0}
        candidates: list[list[Any]] = [
            [
                {
                    "qWindowSize": q_window_size,
                    "qNullSize": q_null_size,
                    "qCellHeight": 0,
                    "qSyntheticMode": False,
                    "qIncludeSysVars": False,
                }
            ],
            [{"qSyntheticMode": False, "qWindowSize": q_window_size}],
            [{"qSyntheticMode": False}],
            [q_window_size, q_null_size, 0, False, False],
            [q_window_size, q_null_size, 0, False],
        ]
        for params in candidates:
            try:
                payload = self._rpc.request(app_handle, "GetTablesAndKeys", params)
                if isinstance(payload, dict) and payload:
                    self._tables_and_keys_cache = payload
                    return payload
            except Exception:
                continue

        self._tables_and_keys_cache = {}
        return {}

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
        measures = _dedupe_measures(
            [
                *_measures_from_hypercube_props(props),
                *_measures_from_hypercube_layout(layout),
            ]
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
            measures = _dedupe_measures(measures)

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
        tables_and_fields = client.get_tables_and_fields(app_id)
        get_table_relationships = getattr(client, "get_table_relationships", None)
        association_candidates: list[JsonDict] = []
        if callable(get_table_relationships):
            try:
                association_candidates = get_table_relationships(app_id)
            except Exception as exc:
                connection_warnings.append(f"QIX relationship extraction warning: {exc}")
                association_candidates = []

        master_measures = client.get_master_measures(app_id)
        master_dimensions = client.get_master_dimensions(app_id)
        variables = client.get_variables(app_id)

        # Build a simple summary for UI metrics
        sheet_count = len(sheets or [])
        visual_count = len(visual_objects or [])
        measure_count = len(master_measures or [])
        dimension_count = len(master_dimensions or [])
        # count distinct filter fields from visuals (list objects and filter panes)
        filter_fields = set()
        for vis in visual_objects or []:
            for f in (vis.get("filters") or []):
                if isinstance(f, dict):
                    filter_fields.add(str(f.get("field") or f.get("name") or ""))
                else:
                    filter_fields.add(str(f or ""))
        filter_count = len([f for f in filter_fields if f])

        semantic_tables = [
            {
                "name": t.get("name") or "",
                "fields": t.get("fields") or [],
                "source_qvd": t.get("source_qvd") or "",
            }
            for t in (tables_and_fields or [])
            if isinstance(t, dict)
        ]

        summary = {
            "sheet_count": sheet_count,
            "visual_count": visual_count,
            "measure_count": measure_count,
            "dimension_count": dimension_count,
            "filter_count": filter_count,
        }

        semantic_model = {
            "tables": semantic_tables,
            "relationships": [],
            "association_candidates": association_candidates,
            "connections": connections,
        }

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
            "master_dimensions": master_dimensions,
            "master_measures": master_measures,
            "variables": variables,
            "tables_and_fields": tables_and_fields,
            "summary": summary,
            "semantic_model": semantic_model,
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


def _dedupe_measures(items: list[JsonDict]) -> list[JsonDict]:
    deduped: list[JsonDict] = []
    seen: dict[tuple[str, str], int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        library_id = str(item.get("library_id") or "").strip().lower()
        expression = str(item.get("expression") or "").strip().lower()
        label = str(item.get("label") or "").strip().lower()
        if library_id:
            key = ("library", library_id)
        elif expression:
            key = ("expression", expression)
        elif label:
            key = ("label", label)
        else:
            continue
        if key in seen:
            existing_index = seen[key]
            existing = deduped[existing_index]
            existing_expression = str(existing.get("expression") or "").strip()
            if not existing_expression and expression:
                merged = dict(existing)
                merged.update({k: v for k, v in item.items() if v not in (None, "")})
                deduped[existing_index] = merged
            continue
        if not expression and label:
            if any(str(existing.get("label") or "").strip().lower() == label for existing in deduped):
                continue
        if expression and label:
            label_key = ("label", label)
            if label_key in seen:
                existing_index = seen[label_key]
                existing = deduped[existing_index]
                if not str(existing.get("expression") or "").strip():
                    merged = dict(existing)
                    merged.update({k: v for k, v in item.items() if v not in (None, "")})
                    deduped[existing_index] = merged
                    seen[key] = existing_index
                    continue
        seen[key] = len(deduped)
        if label and ("label", label) not in seen and not expression:
            seen[("label", label)] = len(deduped)
        deduped.append(item)
    return deduped


def _enrich_visual_measure_expressions(visuals: list[JsonDict], master_measures: list[JsonDict]) -> list[JsonDict]:
    by_id: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for measure in master_measures:
        if not isinstance(measure, dict):
            continue
        expression = str(measure.get("expression") or "").strip()
        if not expression:
            continue
        measure_id = str(measure.get("id") or "").strip().lower()
        title = str(measure.get("title") or "").strip().lower()
        if measure_id:
            by_id[measure_id] = expression
        if title and title not in by_title:
            by_title[title] = expression

    output: list[JsonDict] = []
    for visual in visuals:
        if not isinstance(visual, dict):
            continue
        enriched_visual = dict(visual)
        enriched_measures: list[JsonDict] = []
        for measure in _as_list(visual.get("measures")):
            if not isinstance(measure, dict):
                continue
            enriched_measure = dict(measure)
            expression = str(enriched_measure.get("expression") or "").strip()
            if not expression:
                library_id = str(enriched_measure.get("library_id") or "").strip().lower()
                label = str(enriched_measure.get("label") or "").strip().lower()
                if library_id and library_id in by_id:
                    enriched_measure["expression"] = by_id[library_id]
                elif label and label in by_title:
                    enriched_measure["expression"] = by_title[label]
            enriched_measures.append(enriched_measure)
        enriched_visual["measures"] = _dedupe_measures(enriched_measures)
        output.append(enriched_visual)
    return output


def _tables_and_keys_container(payload: JsonDict) -> JsonDict:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("qtr"), dict):
        return dict(payload.get("qtr") or {})
    return payload


def _extract_tables_from_tables_and_keys_payload(payload: JsonDict) -> list[JsonDict]:
    container = _tables_and_keys_container(payload)
    tables_raw = _as_list(container.get("qTables") or container.get("tables"))
    tables: list[JsonDict] = []
    for table in tables_raw:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("qName") or table.get("name") or table.get("tableName") or "").strip()
        if not table_name:
            continue
        fields: list[str] = []
        for field in _as_list(
            table.get("qFields")
            or table.get("fields")
            or table.get("qFieldNames")
            or table.get("fieldNames")
        ):
            if isinstance(field, dict):
                field_name = str(field.get("qName") or field.get("name") or field.get("fieldName") or "").strip()
            else:
                field_name = str(field or "").strip()
            if field_name and field_name not in fields:
                fields.append(field_name)
        tables.append({"name": table_name, "fields": fields, "source_qvd": ""})
    return tables


def _extract_relationships_from_tables_and_keys_payload(payload: JsonDict) -> list[JsonDict]:
    """Extract unvalidated association candidates from QIX tables/keys metadata."""
    container = _tables_and_keys_container(payload)
    tables_raw = _as_list(container.get("qTables") or container.get("tables"))
    table_names = _table_names_from_tables_payload(tables_raw)

    keys_raw = _as_list(
        container.get("qKeys")
        or container.get("keys")
        or container.get("qKeyInfos")
        or container.get("keyInfos")
    )
    associations: list[JsonDict] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    # Extract ONLY from QIX keys (no fallback). Qlik associations are not
    # relational joins yet, so keep them as validation candidates.
    for key_entry in keys_raw:
        if not isinstance(key_entry, dict):
            continue

        key_name = str(key_entry.get("qKeyName") or key_entry.get("key") or key_entry.get("name") or "").strip()
        endpoints = _extract_key_entry_endpoints(key_entry=key_entry, known_table_names=table_names, default_field_name=key_name)
        endpoints = [
            (table_name, field_name)
            for table_name, field_name in endpoints
            if not _is_technical_table(table_name) and not _is_technical_field(field_name)
        ]

        unique_endpoints: list[tuple[str, str]] = []
        endpoint_keys: set[tuple[str, str]] = set()
        for table_name, field_name in endpoints:
            endpoint_key = (table_name.strip().lower(), field_name.strip().lower())
            if endpoint_key in endpoint_keys:
                continue
            endpoint_keys.add(endpoint_key)
            unique_endpoints.append((table_name, field_name))
        endpoints = unique_endpoints

        if len({table_name.lower() for table_name, _ in endpoints}) < 2:
            continue

        field_names = [field_name for _, field_name in endpoints if field_name]
        field_name = _association_display_field(key_name, field_names)
        tables = sorted({table_name for table_name, _ in endpoints}, key=lambda item: item.lower())
        assoc_key = (field_name.lower(), tuple(table.lower() for table in tables))
        if assoc_key in seen:
            continue
        seen.add(assoc_key)
        associations.append(
            {
                "field": field_name,
                "tables": tables,
                "association_type": "qlik_association",
                "cardinality": "unknown",
                "requires_validation": True,
                "source": "qix_get_tables_and_keys",
                "key_name": key_name,
                "endpoints": [{"table": table, "field": field} for table, field in endpoints],
            }
        )
    return associations


def _table_names_from_tables_payload(tables_raw: list[Any]) -> list[str]:
    names: list[str] = []
    for table in tables_raw:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("qName") or table.get("name") or table.get("tableName") or "").strip()
        if table_name and table_name not in names:
            names.append(table_name)
    return names


def _table_fields_from_tables_payload(tables_raw: list[Any]) -> dict[str, set[str]]:
    by_table: dict[str, set[str]] = {}
    for table in tables_raw:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("qName") or table.get("name") or table.get("tableName") or "").strip()
        if not table_name:
            continue
        fields = by_table.setdefault(table_name, set())
        for field in _as_list(
            table.get("qFields")
            or table.get("fields")
            or table.get("qFieldNames")
            or table.get("fieldNames")
        ):
            if isinstance(field, dict):
                field_name = str(field.get("qName") or field.get("name") or field.get("fieldName") or "").strip()
            else:
                field_name = str(field or "").strip()
            if field_name:
                fields.add(field_name)
    return by_table


def _extract_key_entry_endpoints(
    key_entry: JsonDict,
    known_table_names: list[str],
    default_field_name: str,
) -> list[tuple[str, str]]:
    endpoints: list[tuple[str, str]] = []

    key_fields = _as_list(
        key_entry.get("qFields")
        or key_entry.get("fields")
        or key_entry.get("qKeyFields")
        or key_entry.get("keyFields")
    )
    for key_field in key_fields:
        table_name = ""
        field_name = ""
        if isinstance(key_field, dict):
            table_name = str(
                key_field.get("qTableName")
                or key_field.get("qTable")
                or key_field.get("table")
                or key_field.get("tableName")
                or ""
            ).strip()
            field_name = str(
                key_field.get("qName")
                or key_field.get("name")
                or key_field.get("field")
                or key_field.get("fieldName")
                or ""
            ).strip()
            if not table_name:
                table_name = _table_name_from_any_identifier(
                    key_field.get("qTable"),
                    known_table_names,
                )
        elif isinstance(key_field, str):
            table_name, field_name = _parse_table_field_token(key_field)

        if table_name and not field_name:
            field_name = default_field_name
        if table_name and field_name:
            endpoints.append((table_name, field_name))

    if not endpoints and default_field_name:
        local_table_names: list[str] = []
        for table_item in _as_list(key_entry.get("qTables") or key_entry.get("tables")):
            table_name = _table_name_from_any_identifier(table_item, known_table_names)
            if table_name and table_name not in local_table_names:
                local_table_names.append(table_name)
        endpoints = [(table_name, default_field_name) for table_name in local_table_names]

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for table_name, field_name in endpoints:
        key = (table_name.strip().lower(), field_name.strip().lower())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append((table_name, field_name))
    return deduped


def _table_name_from_any_identifier(value: Any, known_table_names: list[str]) -> str:
    if isinstance(value, dict):
        return str(value.get("qName") or value.get("name") or value.get("tableName") or "").strip()

    if isinstance(value, int):
        if 0 <= value < len(known_table_names):
            return known_table_names[value]
        return ""

    text = str(value or "").strip()
    if not text:
        return ""

    if text.isdigit():
        index = int(text)
        if 0 <= index < len(known_table_names):
            return known_table_names[index]

    return text


def _parse_table_field_token(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    raw_parts = [part for part in re.split(r"\.|:", text) if part]
    parts = [part.strip().strip("[]\"'") for part in raw_parts if part.strip().strip("[]\"'")]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[0] if parts else ""


def _candidate_relationship_pairs(endpoints: list[tuple[str, str]]) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    non_synthetic = [endpoint for endpoint in endpoints if not _is_synthetic_table_name(endpoint[0])]
    source = non_synthetic if len(non_synthetic) >= 2 else endpoints

    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []
    for left_index in range(len(source)):
        for right_index in range(left_index + 1, len(source)):
            pairs.append((source[left_index], source[right_index]))
    return pairs


def _relationship_key(
    left_table: str,
    left_field: str,
    right_table: str,
    right_field: str,
) -> tuple[str, str, str, str]:
    left = (left_table.lower(), left_field.lower())
    right = (right_table.lower(), right_field.lower())
    if left <= right:
        return left[0], left[1], right[0], right[1]
    return right[0], right[1], left[0], left[1]


def _orient_relationship_endpoints(
    left_endpoint: tuple[str, str],
    right_endpoint: tuple[str, str],
) -> tuple[tuple[str, str], tuple[str, str]]:
    left_score = _endpoint_relationship_score(*left_endpoint)
    right_score = _endpoint_relationship_score(*right_endpoint)
    if left_score > right_score:
        return left_endpoint, right_endpoint
    if right_score > left_score:
        return right_endpoint, left_endpoint
    if left_endpoint[0].lower() <= right_endpoint[0].lower():
        return left_endpoint, right_endpoint
    return right_endpoint, left_endpoint


def _endpoint_relationship_score(table_name: str, field_name: str) -> int:
    table = str(table_name or "").strip().lower()
    field = str(field_name or "").strip().lower()
    score = 0
    if table.startswith("fact") or "fact" in table:
        score += 20
    if table.startswith("dim"):
        score -= 8
    if field.endswith("key") or field.endswith("id"):
        score += 6
    if "alternate" in field:
        score -= 2
    return score


def _is_synthetic_table_name(table_name: str) -> bool:
    normalized = str(table_name or "").strip().lower()
    return normalized.startswith("$syn") or normalized.startswith("syn")


def extract_relationships_from_qlik_script(load_script: str, qlik_metadata: dict | None = None) -> list[JsonDict]:
    """Extract cautious Qlik association candidates from metadata or load script.

    The output intentionally is not a Power BI/Tableau relationship. It captures
    Qlik-style shared-field associations and keeps them behind validation.
    """
    table_fields = _table_fields_from_load_script(load_script)
    source = "qlik_script_parse_unvalidated"
    if not table_fields:
        table_fields = _table_fields_from_metadata(qlik_metadata or {})
        source = "qlik_metadata_table_fields"

    return _association_candidates_from_table_fields(table_fields, source=source)


def _is_technical_table(table_name: str) -> bool:
    return str(table_name or "").strip().startswith("__")


def _is_technical_field(field_name: str) -> bool:
    return str(field_name or "").strip().startswith("__")


def _clean_qlik_identifier(value: Any) -> str:
    text = str(value or "").strip()
    while text.startswith("[") and text.endswith("]") and len(text) >= 2:
        text = text[1:-1].strip()
    return text.strip("\"'` ")


def _association_display_field(key_name: str, field_names: list[str]) -> str:
    cleaned_key = _clean_qlik_identifier(key_name)
    field_counts: dict[str, tuple[str, int]] = {}
    for field_name in field_names:
        cleaned = _clean_qlik_identifier(field_name)
        if not cleaned or _is_technical_field(cleaned):
            continue
        key = cleaned.lower()
        original, count = field_counts.get(key, (cleaned, 0))
        field_counts[key] = (original, count + 1)
    if field_counts:
        return sorted(field_counts.values(), key=lambda item: (-item[1], item[0].lower()))[0][0]
    if cleaned_key and not _is_technical_field(cleaned_key):
        return cleaned_key
    return "unknown"


def _table_fields_from_metadata(qlik_metadata: dict) -> dict[str, set[str]]:
    table_fields: dict[str, set[str]] = {}
    table_sources = [
        _as_list(qlik_metadata.get("tables_and_fields")),
        _as_list((qlik_metadata.get("semantic_model") or {}).get("tables") if isinstance(qlik_metadata.get("semantic_model"), dict) else []),
    ]
    for table_items in table_sources:
        for table in table_items:
            if not isinstance(table, dict):
                continue
            table_name = _clean_qlik_identifier(table.get("name") or table.get("table_name") or table.get("qName"))
            if not table_name or _is_technical_table(table_name) or _is_synthetic_table_name(table_name):
                continue
            fields = table_fields.setdefault(table_name, set())
            for field in _as_list(table.get("fields") or table.get("field_names") or table.get("qFields") or table.get("qFieldNames")):
                field_name = ""
                if isinstance(field, dict):
                    field_name = _clean_qlik_identifier(field.get("name") or field.get("field") or field.get("label") or field.get("qName"))
                else:
                    field_name = _clean_qlik_identifier(field)
                if field_name and not _is_technical_field(field_name):
                    fields.add(field_name)
    return {table_name: fields for table_name, fields in table_fields.items() if fields}


def _table_fields_from_load_script(load_script: str) -> dict[str, set[str]]:
    table_fields: dict[str, set[str]] = {}
    lines = str(load_script or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        table_match = re.match(r"^(?:\[([^\]]+)\]|([A-Za-z_][\w.\-]*))\s*:\s*(.*)$", line)
        if not table_match:
            index += 1
            continue

        table_name = _clean_qlik_identifier(table_match.group(1) or table_match.group(2))
        if (
            not table_name
            or _is_technical_table(table_name)
            or _is_synthetic_table_name(table_name)
            or table_name.upper() in {"LET", "SET", "DECLARE", "IF", "ENDIF", "THEN", "ELSE", "LOOP", "WHILE", "FOR"}
        ):
            index += 1
            continue

        statement_text, next_index = _first_table_statement_after_label(lines, index, table_match.group(3) or "")
        fields = _field_names_from_qlik_statement(statement_text)
        clean_fields = {
            field_name
            for field_name in fields
            if field_name and not _is_technical_field(field_name)
        }
        if clean_fields:
            table_fields[table_name] = clean_fields
        index = max(index + 1, next_index)
    return table_fields


def _first_table_statement_after_label(lines: list[str], label_index: int, label_remainder: str) -> tuple[str, int]:
    buffer: list[str] = []
    index = label_index
    if str(label_remainder or "").strip():
        buffer.append(str(label_remainder).strip())
    else:
        index += 1

    while index < len(lines):
        raw_line = str(lines[index] if index != label_index or not buffer else "")
        if index != label_index:
            stripped = raw_line.strip()
            if re.match(r"^(?:\[([^\]]+)\]|([A-Za-z_][\w.\-]*))\s*:", stripped):
                break
            if stripped and not stripped.startswith(("//", "--")):
                buffer.append(stripped)

        statement = " ".join(part for part in buffer if part).strip()
        if statement and re.search(r"\b(?:MAPPING\s+LOAD|LOAD|SELECT)\b", statement, flags=re.IGNORECASE):
            if ";" in statement:
                return statement.split(";", 1)[0], index + 1
        elif statement and ";" in statement:
            return "", index + 1
        index += 1

    return " ".join(part for part in buffer if part).strip(), index


def _field_names_from_qlik_statement(statement: str) -> set[str]:
    text = str(statement or "").strip()
    if not text:
        return set()

    statement_match = re.search(r"\b(?:MAPPING\s+LOAD|LOAD|SELECT)\b", text, flags=re.IGNORECASE)
    if not statement_match:
        return set()
    keyword = statement_match.group(0).upper()
    fields_text = text[statement_match.end():].strip()
    terminator_pattern = r"\bFROM\b|\bRESIDENT\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b"
    terminator = re.search(terminator_pattern, fields_text, flags=re.IGNORECASE)
    if terminator:
        fields_text = fields_text[: terminator.start()]
    if keyword.endswith("SELECT"):
        fields_text = re.sub(r"^\s+(DISTINCT|TOP\s+\d+)\s+", "", fields_text, flags=re.IGNORECASE)

    field_names: set[str] = set()
    for part in _split_qlik_field_list(fields_text):
        field_name = _field_name_from_qlik_field_expression(part)
        if field_name:
            field_names.add(field_name)
    return field_names


def _split_qlik_field_list(fields_text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    bracket_depth = 0
    paren_depth = 0
    quote_char = ""
    for char in str(fields_text or ""):
        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = ""
            continue
        if char in {"'", '"', "`"}:
            quote_char = char
            current.append(char)
            continue
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth:
            paren_depth -= 1
        if char == "," and bracket_depth == 0 and paren_depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _field_name_from_qlik_field_expression(expression: str) -> str:
    text = str(expression or "").strip().rstrip(";")
    if not text:
        return ""
    as_match = re.search(r"\bAS\s+(?:\[([^\]]+)\]|\"([^\"]+)\"|'([^']+)'|`([^`]+)`|([A-Za-z_][\w.\-]*))", text, flags=re.IGNORECASE)
    if as_match:
        return _clean_qlik_identifier(next((group for group in as_match.groups() if group), ""))
    bracket_match = re.match(r"^\[([^\]]+)\]$", text)
    if bracket_match:
        return _clean_qlik_identifier(bracket_match.group(1))
    quoted_match = re.match(r"^[\"'`]([^\"'`]+)[\"'`]$", text)
    if quoted_match:
        return _clean_qlik_identifier(quoted_match.group(1))
    token_match = re.match(r"([A-Za-z_][\w.\-]*)", text)
    if token_match:
        token = _clean_qlik_identifier(token_match.group(1))
        if token.upper() not in {"LOAD", "SELECT", "FROM", "RESIDENT", "WHERE"}:
            return token
    return ""


def _association_candidates_from_table_fields(table_fields: dict[str, set[str]], source: str) -> list[JsonDict]:
    field_to_tables: dict[str, list[tuple[str, str]]] = {}
    for table_name, fields in table_fields.items():
        if _is_technical_table(table_name) or _is_synthetic_table_name(table_name):
            continue
        for field_name in fields:
            clean_field = _clean_qlik_identifier(field_name)
            if clean_field and not _is_technical_field(clean_field):
                field_to_tables.setdefault(clean_field.lower(), []).append((table_name, clean_field))

    associations: list[JsonDict] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for normalized_field, table_field_pairs in sorted(field_to_tables.items()):
        tables = sorted({table_name for table_name, _ in table_field_pairs}, key=lambda item: item.lower())
        if len(tables) < 2:
            continue
        field_name = _association_display_field("", [field for _, field in table_field_pairs]) or normalized_field
        assoc_key = (field_name.lower(), tuple(table.lower() for table in tables))
        if assoc_key in seen:
            continue
        seen.add(assoc_key)
        cardinality_payload = _candidate_cardinality_payload(tables, field_name)
        associations.append(
            {
                "field": field_name,
                "tables": tables,
                "association_type": "qlik_association",
                "cardinality": "unknown",
                "requires_validation": True,
                "source": source,
                **cardinality_payload,
            }
        )
    return associations


def _candidate_cardinality_payload(tables: list[str], field_name: str) -> JsonDict:
    if len(tables) != 2:
        return {}
    orientation = _business_relationship_orientation(tables[0], tables[1], field_name)
    if not orientation:
        return {}
    from_table, to_table, cardinality, rule = orientation
    return {
        "from_table": from_table,
        "from_column": field_name,
        "to_table": to_table,
        "to_column": field_name,
        "cardinality": cardinality,
        "cardinality_rule": rule,
    }


def validate_and_convert_associations_to_relationships(
    associations: list[JsonDict],
    table_metadata: dict[str, JsonDict] | None = None
) -> list[JsonDict]:
    """Convert validated associations into proper Power BI/Tableau relationships.
    
    Takes associations with unknown cardinality and converts them to relationships
    with inferred cardinality based on table metadata (row counts, distinct values).
    
    Args:
        associations: List of associations with requires_validation=true
        table_metadata: Dict of table_name -> {record_count, fields} for cardinality inference
    
    Returns:
        List of validated relationships with inferred cardinality
    """
    relationships: list[JsonDict] = []
    
    for assoc in associations:
        if assoc.get("requires_validation") is True and assoc.get("validated") is not True:
            continue
        
        from_table = assoc.get("from_table")
        to_table = assoc.get("to_table")
        field_name = assoc.get("field") or assoc.get("from_column")
        if (not from_table or not to_table) and isinstance(assoc.get("tables"), list):
            assoc_tables = [str(table or "").strip() for table in assoc.get("tables") if str(table or "").strip()]
            if len(assoc_tables) == 2:
                from_table, to_table = _orient_table_pair(assoc_tables[0], assoc_tables[1], field_name)
        
        if not (from_table and to_table and field_name):
            continue
        
        cardinality = "unknown"
        evidence: JsonDict = {}
        if table_metadata:
            cardinality, evidence = _infer_cardinality_from_metadata(
                from_table, to_table, field_name, table_metadata
            )
        
        # Create validated relationship
        relationship = {
            "id": f"{from_table.lower()}___{field_name.lower()}___{to_table.lower()}___{field_name.lower()}",
            "from_table": from_table,
            "from_column": field_name,
            "to_table": to_table,
            "to_column": field_name,
            "cardinality": cardinality,
            "source": "validated_association",
        }
        if evidence:
            relationship["cardinality_evidence"] = evidence
        relationships.append(relationship)
    
    return relationships


def _infer_cardinality_from_metadata(
    from_table: str,
    to_table: str,
    shared_field: str,
    table_metadata: dict[str, JsonDict]
) -> tuple[str, JsonDict]:
    """Infer cardinality based on row counts and field distinctness.
    
    Returns: "one_to_one", "one_to_many", "many_to_one", "many_to_many", or "unknown"
    """
    from_meta = table_metadata.get(from_table, {})
    to_meta = table_metadata.get(to_table, {})

    from_rows = _safe_int(from_meta.get("record_count"))
    to_rows = _safe_int(to_meta.get("record_count"))
    from_distinct = _field_distinct_count(from_meta, shared_field)
    to_distinct = _field_distinct_count(to_meta, shared_field)
    evidence: JsonDict = {
        "field": shared_field,
        "from_table": {
            "name": from_table,
            "row_count": from_rows,
            "distinct_count": from_distinct,
            "has_duplicates": None,
        },
        "to_table": {
            "name": to_table,
            "row_count": to_rows,
            "distinct_count": to_distinct,
            "has_duplicates": None,
        },
    }

    rule_cardinality, rule_name = _business_cardinality_rule(from_table, to_table, shared_field)
    if rule_cardinality:
        evidence["rule"] = rule_name
        return rule_cardinality, evidence

    if not from_rows or not to_rows or from_distinct is None or to_distinct is None:
        return "unknown", evidence

    from_has_duplicates = from_rows > from_distinct
    to_has_duplicates = to_rows > to_distinct
    evidence["from_table"]["has_duplicates"] = from_has_duplicates
    evidence["to_table"]["has_duplicates"] = to_has_duplicates

    if not from_has_duplicates and not to_has_duplicates:
        return "one_to_one", evidence
    if from_has_duplicates and not to_has_duplicates:
        return "many_to_one", evidence
    if not from_has_duplicates and to_has_duplicates:
        return "one_to_many", evidence
    return "many_to_many", evidence


def _field_distinct_count(table_metadata: JsonDict, field_name: str) -> int | None:
    normalized_field = _clean_qlik_identifier(field_name).lower()
    if not normalized_field:
        return None
    field_stats = table_metadata.get("field_stats")
    if isinstance(field_stats, dict):
        stats = field_stats.get(normalized_field) or field_stats.get(field_name)
        if isinstance(stats, dict):
            value = stats.get("distinct_count")
            if value is None:
                value = stats.get("no_of_symbols")
            if value is not None:
                return _safe_int(value)
    for field in _as_list(table_metadata.get("fields")):
        if not isinstance(field, dict):
            continue
        current_name = _clean_qlik_identifier(field.get("name") or field.get("field"))
        if current_name.lower() != normalized_field:
            continue
        value = field.get("distinct_count")
        if value is None:
            value = field.get("no_of_symbols")
        if value is not None:
            return _safe_int(value)
    return None


def _orient_table_pair(table_a: str, table_b: str, shared_field: str = "") -> tuple[str, str]:
    """Determine relationship direction: fact tables are sources, dimension tables are targets.
    
    Returns (from_table, to_table) tuple.
    """
    business_orientation = _business_relationship_orientation(table_a, table_b, shared_field)
    if business_orientation:
        return business_orientation[0], business_orientation[1]

    # Tables with "Fact" in name are fact tables (sources)
    a_is_fact = 'fact' in table_a.lower()
    b_is_fact = 'fact' in table_b.lower()
    
    if a_is_fact and not b_is_fact:
        return table_a, table_b
    elif b_is_fact and not a_is_fact:
        return table_b, table_a
    
    # Tables with "Dim" in name are dimensions (targets)
    a_is_dim = 'dim' in table_a.lower()
    b_is_dim = 'dim' in table_b.lower()
    
    if a_is_dim and not b_is_dim:
        return table_b, table_a
    elif b_is_dim and not a_is_dim:
        return table_a, table_b
    
    # Default: alphabetical
    return (table_a, table_b) if table_a < table_b else (table_b, table_a)


def _business_relationship_orientation(
    table_a: str,
    table_b: str,
    shared_field: str = "",
) -> tuple[str, str, str, str] | None:
    a_role = _table_business_role(table_a)
    b_role = _table_business_role(table_b)
    if a_role == "fact" and b_role == "dim":
        return table_a, table_b, "many_to_one", "fact_to_dim"
    if a_role == "dim" and b_role == "fact":
        return table_b, table_a, "many_to_one", "fact_to_dim"
    if a_role == "dim" and b_role == "dim":
        target = _general_dimension_for_field(table_a, table_b, shared_field)
        if target == table_a:
            return table_b, table_a, "many_to_one", "detail_dim_to_general_dim"
        if target == table_b:
            return table_a, table_b, "many_to_one", "detail_dim_to_general_dim"
    return None


def _business_cardinality_rule(from_table: str, to_table: str, shared_field: str = "") -> tuple[str, str]:
    from_role = _table_business_role(from_table)
    to_role = _table_business_role(to_table)
    if from_role == "fact" and to_role == "dim":
        return "many_to_one", "fact_to_dim"
    if from_role == "dim" and to_role == "fact":
        return "one_to_many", "fact_to_dim_reversed"
    if from_role == "dim" and to_role == "dim":
        target = _general_dimension_for_field(from_table, to_table, shared_field)
        if target == to_table:
            return "many_to_one", "detail_dim_to_general_dim"
        if target == from_table:
            return "one_to_many", "detail_dim_to_general_dim_reversed"
    return "", ""


def _table_business_role(table_name: str) -> str:
    normalized = str(table_name or "").strip().lower()
    if normalized.startswith("fact") or "_fact" in normalized or "fact" in normalized:
        return "fact"
    if normalized.startswith("dim") or "_dim" in normalized or "dimension" in normalized:
        return "dim"
    return ""


def _general_dimension_for_field(table_a: str, table_b: str, shared_field: str) -> str:
    field_base = _dimension_key_base(shared_field)
    if not field_base:
        return ""
    a_score = _dimension_field_match_score(table_a, field_base)
    b_score = _dimension_field_match_score(table_b, field_base)
    if a_score > b_score:
        return table_a
    if b_score > a_score:
        return table_b
    return ""


def _dimension_key_base(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    for suffix in ("alternatekey", "key", "id", "code"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _dimension_field_match_score(table_name: str, field_base: str) -> int:
    table_base = re.sub(r"[^a-z0-9]+", "", str(table_name or "").strip().lower())
    if table_base.startswith("dim"):
        table_base = table_base[3:]
    if not table_base or not field_base:
        return 0
    if table_base == field_base:
        return 100 + len(table_base)
    if field_base.endswith(table_base):
        return 60 + len(table_base)
    if table_base.endswith(field_base):
        return 40 + len(field_base)
    if field_base in table_base or table_base in field_base:
        return 10 + min(len(field_base), len(table_base))
    return 0


def _field_can_define_association(normalized_field_name: str) -> bool:
    field = str(normalized_field_name or "").strip().lower()
    if not field:
        return False
    if field.endswith("key") or field.endswith("id"):
        return True
    if "key" in field or "code" in field or "identifier" in field:
        return True
    return False


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
