from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from .db_introspection import (
    _open_sqlserver_connection,  # internal helper reused in pipeline scope
    _resolve_connection_overrides,  # internal helper reused in pipeline scope
)


def build_hyper_extract_from_catalog(
    output_hyper_path: str | Path,
    data_sources: list[dict[str, Any]],
    db_catalog: dict[str, Any] | None,
    max_rows_per_table: int | None = None,
) -> dict[str, Any]:
    output_hyper = Path(output_hyper_path)
    output_hyper.parent.mkdir(parents=True, exist_ok=True)

    if not isinstance(db_catalog, dict):
        return {
            "status": "skipped",
            "reason": "db_catalog is missing",
            "hyper_path": str(output_hyper),
        }

    ds_entries = db_catalog.get("datasources")
    if not isinstance(ds_entries, list):
        return {
            "status": "skipped",
            "reason": "db_catalog has no datasources",
            "hyper_path": str(output_hyper),
        }

    selected = None
    for entry in ds_entries:
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("connected")):
            continue
        tables = entry.get("tables")
        if isinstance(tables, list) and tables:
            selected = entry
            break

    if selected is None:
        return {
            "status": "skipped",
            "reason": "No connected datasource with introspected tables",
            "hyper_path": str(output_hyper),
        }

    datasource_name = str(selected.get("name") or "").strip()
    ds_meta = _find_data_source_by_name(data_sources, datasource_name)
    if ds_meta is None:
        return {
            "status": "skipped",
            "reason": f"Datasource metadata not found for '{datasource_name}'",
            "hyper_path": str(output_hyper),
        }

    connection_string = str(ds_meta.get("connection_string") or "").strip()
    if not connection_string:
        return {
            "status": "skipped",
            "reason": f"Missing connection string for datasource '{datasource_name}'",
            "hyper_path": str(output_hyper),
        }

    try:
        import pyodbc  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "pyodbc is required to read source data for hyper extract creation"
        ) from exc

    try:
        from tableauhyperapi import (
            Connection,
            CreateMode,
            HyperProcess,
            Inserter,
            Nullability,
            SqlType,
            TableDefinition,
            TableName,
            Telemetry,
        )
    except Exception as exc:
        raise RuntimeError(
            "tableauhyperapi is required for hyper extract creation. "
            "Install it with: pip install tableauhyperapi. "
            f"Current interpreter: {sys.executable}"
        ) from exc

    overrides = _resolve_connection_overrides(None, datasource_name)
    security_type = str(ds_meta.get("security_type") or "").strip().lower()
    if security_type == "windows" and "trusted_connection" not in overrides:
        overrides["trusted_connection"] = "true"

    user_name = ds_meta.get("user_name")
    if isinstance(user_name, str) and user_name.strip() and "uid" not in overrides:
        overrides["uid"] = user_name.strip()

    password = ds_meta.get("password")
    if isinstance(password, str) and "pwd" not in overrides:
        overrides["pwd"] = password

    conn = _open_sqlserver_connection(pyodbc, connection_string, overrides=overrides)
    if conn is None:
        return {
            "status": "skipped",
            "reason": "No usable SQL Server ODBC driver found for extract creation",
            "hyper_path": str(output_hyper),
        }

    tables_payload = selected.get("tables") if isinstance(selected.get("tables"), list) else []
    tables_report: list[dict[str, Any]] = []

    try:
        with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
            with Connection(
                endpoint=hyper.endpoint,
                database=str(output_hyper),
                create_mode=CreateMode.CREATE_AND_REPLACE,
            ) as hyper_conn:
                hyper_conn.catalog.create_schema_if_not_exists("Extract")

                for table in tables_payload:
                    if not isinstance(table, dict):
                        continue

                    schema_name = str(table.get("schema") or "dbo").strip() or "dbo"
                    table_name = str(table.get("name") or "").strip()
                    full_name = str(table.get("full_name") or "").strip() or f"[{schema_name}].[{table_name}]"
                    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
                    if not table_name or not columns:
                        continue

                    column_defs: list[Any] = []
                    for col in columns:
                        if not isinstance(col, dict):
                            continue
                        col_name = str(col.get("name") or "").strip()
                        if not col_name:
                            continue
                        sql_type = _sqlserver_type_to_hyper(str(col.get("data_type") or ""), SqlType)
                        nullable = bool(col.get("is_nullable", True))
                        nullability = Nullability.NULLABLE if nullable else Nullability.NOT_NULLABLE
                        column_defs.append(TableDefinition.Column(col_name, sql_type, nullability))

                    if not column_defs:
                        continue

                    table_def = TableDefinition(TableName("Extract", table_name), column_defs)
                    hyper_conn.catalog.create_table(table_def)

                    query = f"SELECT * FROM {full_name}"
                    if isinstance(max_rows_per_table, int) and max_rows_per_table > 0:
                        query = f"SELECT TOP {max_rows_per_table} * FROM {full_name}"

                    with conn.cursor() as cursor:
                        cursor.execute(query)
                        inserted = 0
                        with Inserter(hyper_conn, table_def) as inserter:
                            while True:
                                rows = cursor.fetchmany(1000)
                                if not rows:
                                    break
                                inserter.add_rows(rows)
                                inserted += len(rows)
                            inserter.execute()

                    tables_report.append(
                        {
                            "table": table_name,
                            "schema": schema_name,
                            "rows": inserted,
                            "query": query,
                        }
                    )
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "status": "created",
        "hyper_path": str(output_hyper),
        "datasource": datasource_name,
        "table_count": len(tables_report),
        "tables": tables_report,
    }


def rewrite_workbook_for_hyper(
    source_twb_path: str | Path,
    output_twb_path: str | Path,
    hyper_relative_path: str,
) -> dict[str, Any]:
    source_path = Path(source_twb_path)
    output_path = Path(output_twb_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    root = ET.fromstring(source_path.read_text(encoding="utf-8"))
    _strip_namespaces(root)

    updated_connections = 0
    updated_relations = 0

    for connection in root.findall(".//connection"):
        parent_tag = _local_name(connection.tag)
        current_class = (connection.attrib.get("class") or "").strip().lower()
        if current_class == "federated":
            continue

        connection.attrib.clear()
        connection.set("class", "hyper")
        connection.set("dbname", hyper_relative_path)
        updated_connections += 1

    for relation in root.findall(".//relation"):
        relation_type = (relation.attrib.get("type") or "").strip().lower()
        if relation_type != "table":
            continue

        rel_name = str(relation.attrib.get("name") or "").strip()
        if not rel_name:
            rel_name = _extract_relation_name_from_table_attr(relation.attrib.get("table"))
        if not rel_name:
            continue

        relation.set("table", f"[Extract].[{rel_name}]")
        updated_relations += 1

    output_path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")

    return {
        "status": "rewritten",
        "source_twb": str(source_path),
        "output_twb": str(output_path),
        "updated_connections": updated_connections,
        "updated_relations": updated_relations,
        "hyper_relative_path": hyper_relative_path,
    }


def build_twbx_package(
    twb_path: str | Path,
    output_twbx_path: str | Path,
    hyper_path: str | Path | None = None,
) -> dict[str, Any]:
    twb = Path(twb_path)
    hyper = Path(hyper_path) if hyper_path is not None else None
    twbx = Path(output_twbx_path)
    twbx.parent.mkdir(parents=True, exist_ok=True)

    if not twb.exists():
        raise FileNotFoundError(f"TWB file not found for packaging: {twb}")
    if hyper is not None and not hyper.exists():
        raise FileNotFoundError(f"Hyper file not found for packaging: {hyper}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        staged_twb = tmp_root / twb.name
        staged_hyper: Path | None = None
        if hyper is not None:
            staged_hyper = tmp_root / "Data" / "Extracts" / hyper.name
            staged_hyper.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(twb, staged_twb)
        if hyper is not None and staged_hyper is not None:
            shutil.copy2(hyper, staged_hyper)

        with zipfile.ZipFile(twbx, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in tmp_root.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(tmp_root))

    report: dict[str, Any] = {
        "status": "packaged",
        "twbx_path": str(twbx),
        "twb_in_package": twb.name,
    }
    if hyper is not None:
        report["hyper_in_package"] = f"Data/Extracts/{hyper.name}"
    else:
        report["package_mode"] = "twb_only"
    return report


def _find_data_source_by_name(data_sources: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = (name or "").strip().lower()
    if not target:
        return None
    for ds in data_sources:
        if not isinstance(ds, dict):
            continue
        ds_name = str(ds.get("name") or "").strip().lower()
        if ds_name == target:
            return ds
    return None


def _sqlserver_type_to_hyper(sql_type: str, sql_type_cls: Any) -> Any:
    t = (sql_type or "").strip().lower()

    if t in {"bigint"}:
        return sql_type_cls.big_int()
    if t in {"int", "integer", "smallint", "tinyint", "bit"}:
        return sql_type_cls.int()
    if t in {"float", "real"}:
        return sql_type_cls.double()
    if t in {"decimal", "numeric", "money", "smallmoney"}:
        return sql_type_cls.double()
    if t in {"date"}:
        return sql_type_cls.date()
    if t in {"datetime", "datetime2", "smalldatetime", "datetimeoffset"}:
        return sql_type_cls.timestamp()
    if t in {"time"}:
        return sql_type_cls.time()
    if t in {"binary", "varbinary", "image", "rowversion", "timestamp"}:
        return sql_type_cls.bytes()

    return sql_type_cls.text()


def _extract_relation_name_from_table_attr(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    stripped = value.strip().strip("[]")
    if not stripped:
        return ""
    parts = [part for part in stripped.split("].[") if part]
    return parts[-1] if parts else stripped


def _strip_namespaces(root: ET.Element) -> None:
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]
