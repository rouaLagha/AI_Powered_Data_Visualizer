from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


SQL_TABLE_REF_RE = re.compile(
    r"\b(?:from|join)\s+((?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*)){0,2})",
    flags=re.IGNORECASE,
)


def build_db_catalog(
    data_sources: list[dict[str, Any]],
    data_sets: list[dict[str, Any]],
    connection_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspect SQL Server metadata for tables referenced by RDL datasets."""
    catalog: dict[str, Any] = {"datasources": []}

    for data_source in data_sources:
        if not isinstance(data_source, dict):
            continue

        ds_name = str(data_source.get("name") or "").strip()
        provider = str(data_source.get("provider") or "").strip().lower()
        conn_string = str(data_source.get("connection_string") or "").strip()

        entry: dict[str, Any] = {
            "name": ds_name,
            "provider": provider,
            "tables": [],
            "join_specs": [],
        }

        if not ds_name or "sql" not in provider or not conn_string:
            entry["connected"] = False
            entry["error"] = "Skipped (unsupported provider or missing connection string)."
            catalog["datasources"].append(entry)
            continue

        used_tables = _extract_used_tables(data_sets, ds_name)
        if not used_tables:
            entry["connected"] = False
            entry["error"] = "No used tables found in dataset SQL for this datasource."
            catalog["datasources"].append(entry)
            continue

        overrides = _resolve_connection_overrides(connection_overrides, ds_name)
        effective_overrides = dict(overrides)

        security_type = str(data_source.get("security_type") or "").strip().lower()
        if security_type == "windows" and "trusted_connection" not in effective_overrides:
            effective_overrides["trusted_connection"] = "true"

        user_name = data_source.get("user_name")
        if isinstance(user_name, str) and user_name.strip() and "uid" not in effective_overrides:
            effective_overrides["uid"] = user_name.strip()

        try:
            import pyodbc  # type: ignore
        except Exception as exc:
            entry["connected"] = False
            entry["error"] = f"pyodbc import failed: {type(exc).__name__}: {exc}"
            catalog["datasources"].append(entry)
            continue

        conn = None
        try:
            conn = _open_sqlserver_connection(pyodbc, conn_string, effective_overrides)
            if conn is None:
                entry["connected"] = False
                entry["error"] = "No usable ODBC SQL Server driver found."
                catalog["datasources"].append(entry)
                continue

            tables_payload: list[dict[str, Any]] = []
            for schema_name, table_name in used_tables:
                table_meta = _fetch_table_metadata(conn, schema_name, table_name)
                if table_meta is not None:
                    tables_payload.append(table_meta)

            entry["tables"] = tables_payload
            entry["join_specs"] = _build_join_specs_from_foreign_keys(tables_payload)
            entry["connected"] = True
        except Exception as exc:
            entry["connected"] = False
            entry["error"] = f"Introspection failed: {type(exc).__name__}: {exc}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        catalog["datasources"].append(entry)

    return catalog


def inspect_sqlserver_datasource_inventory(
    data_source: dict[str, Any],
    connection_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the accessible table inventory for a SQL Server datasource."""
    entry: dict[str, Any] = {
        "name": str(data_source.get("name") or "").strip() if isinstance(data_source, dict) else "",
        "provider": str(data_source.get("provider") or "").strip().lower() if isinstance(data_source, dict) else "",
        "server": "",
        "database": "",
        "connected": False,
        "tables": [],
    }

    if not isinstance(data_source, dict):
        entry["error"] = "Unsupported datasource payload."
        return entry

    conn_string = str(data_source.get("connection_string") or "").strip()
    if not entry["name"] or "sql" not in entry["provider"] or not conn_string:
        entry["error"] = "Skipped (unsupported provider or missing connection string)."
        return entry

    entries = _connection_string_entries(conn_string)
    entry["server"] = (
        entries.get("server")
        or entries.get("data source")
        or entries.get("addr")
        or entries.get("address")
        or ""
    )
    entry["database"] = entries.get("database") or entries.get("initial catalog") or ""

    overrides = _resolve_connection_overrides(connection_overrides, entry["name"])
    effective_overrides = dict(overrides)
    security_type = str(data_source.get("security_type") or "").strip().lower()
    if security_type == "windows" and "trusted_connection" not in effective_overrides:
        effective_overrides["trusted_connection"] = "true"

    user_name = data_source.get("user_name")
    if isinstance(user_name, str) and user_name.strip() and "uid" not in effective_overrides:
        effective_overrides["uid"] = user_name.strip()

    try:
        import pyodbc  # type: ignore
    except Exception as exc:
        entry["error"] = f"pyodbc import failed: {type(exc).__name__}: {exc}"
        return entry

    conn = None
    try:
        conn = _open_sqlserver_connection(pyodbc, conn_string, effective_overrides)
        if conn is None:
            entry["error"] = "No usable ODBC SQL Server driver found."
            return entry

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
                ORDER BY TABLE_SCHEMA, TABLE_NAME
                """
            )
            table_refs: list[tuple[str, str, str]] = []
            for row in cursor.fetchall():
                schema_name = str(row[0] or "").strip()
                table_name = str(row[1] or "").strip()
                table_type = str(row[2] or "").strip()
                if not schema_name or not table_name:
                    continue
                table_refs.append((schema_name, table_name, table_type))

        tables: list[dict[str, Any]] = []
        for schema_name, table_name, table_type in table_refs:
            table_payload = _fetch_table_metadata(conn, schema_name, table_name) or {
                "schema": schema_name,
                "name": table_name,
                "full_name": f"[{schema_name}].[{table_name}]",
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
            }
            table_payload["table_type"] = table_type
            tables.append(table_payload)

        entry["tables"] = tables
        entry["connected"] = True
        return entry
    except Exception as exc:
        entry["error"] = f"Inventory failed: {type(exc).__name__}: {exc}"
        return entry
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def inspect_tableau_workbook_inventory(
    workbook_path: str | Path,
    data_source: dict[str, Any] | None = None,
    live_error: str = "",
) -> dict[str, Any]:
    """Read table/column metadata from a TWB/TDS when live DB introspection is unavailable."""
    path = Path(workbook_path)
    entry: dict[str, Any] = {
        "name": str(data_source.get("name") or "").strip() if isinstance(data_source, dict) else "",
        "provider": str(data_source.get("provider") or "").strip().lower() if isinstance(data_source, dict) else "",
        "server": "",
        "database": "",
        "connected": False,
        "inventory_source": "workbook_metadata",
        "metadata_fallback": True,
        "tables": [],
        "warning": live_error,
    }

    if isinstance(data_source, dict):
        connection_info = data_source.get("connection_info", {})
        if isinstance(connection_info, dict):
            entry["server"] = str(connection_info.get("server", "") or "")
            entry["database"] = str(connection_info.get("database", "") or "")

    if not path.exists():
        entry["error"] = f"Workbook metadata fallback file not found: {path}"
        return entry

    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        entry["error"] = f"Workbook metadata fallback failed: {type(exc).__name__}: {exc}"
        return entry

    tables_by_name: dict[str, dict[str, Any]] = {}
    table_order: list[str] = []

    for relation in root.findall(".//relation"):
        if str(relation.attrib.get("type", "") or "").lower() != "table":
            continue
        raw_name = str(relation.attrib.get("name", "") or "").strip()
        table_attr = str(relation.attrib.get("table", "") or "").strip()
        schema_name, table_name = _split_table_identifier(table_attr)
        table_name = table_name or _clean_identifier(raw_name)
        if not table_name:
            continue
        schema_name = schema_name or "dbo"
        key = table_name.lower()
        if key not in tables_by_name:
            tables_by_name[key] = {
                "schema": schema_name,
                "name": table_name,
                "full_name": f"[{schema_name}].[{table_name}]",
                "table_type": "workbook table",
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
                "_ordinals": {},
            }
            table_order.append(key)

    for record in root.findall(".//metadata-record"):
        if str(record.attrib.get("class", "") or "").lower() != "column":
            continue
        parent_name = _clean_identifier(_xml_child_text(record, "parent-name"))
        column_name = _clean_identifier(_xml_child_text(record, "remote-name")) or _clean_identifier(
            _xml_child_text(record, "local-name")
        )
        if not parent_name or not column_name:
            continue
        key = parent_name.lower()
        if key not in tables_by_name:
            tables_by_name[key] = {
                "schema": "dbo",
                "name": parent_name,
                "full_name": f"[dbo].[{parent_name}]",
                "table_type": "workbook table",
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
                "_ordinals": {},
            }
            table_order.append(key)

        table = tables_by_name[key]
        existing = {str(col.get("name", "")).lower() for col in table["columns"] if isinstance(col, dict)}
        if column_name.lower() in existing:
            continue
        ordinal = _safe_int(_xml_child_text(record, "ordinal"))
        table["columns"].append(
            {
                "name": column_name,
                "data_type": _xml_child_text(record, "local-type") or _remote_type_label(record),
                "is_nullable": _xml_child_text(record, "contains-null").strip().lower() == "true",
                "max_length": _safe_int(_xml_child_text(record, "width")),
                "numeric_precision": _safe_int(_xml_child_text(record, "precision")),
                "numeric_scale": _safe_int(_xml_child_text(record, "scale")),
                "datetime_precision": None,
            }
        )
        table["_ordinals"][column_name.lower()] = ordinal if ordinal is not None else len(table["columns"])

    _infer_table_keys_from_workbook_metadata(tables_by_name)

    tables: list[dict[str, Any]] = []
    for key in table_order:
        table = tables_by_name[key]
        ordinals = table.pop("_ordinals", {})
        table["columns"] = sorted(
            table.get("columns", []),
            key=lambda col: ordinals.get(str(col.get("name", "")).lower(), 10_000),
        )
        tables.append(table)

    entry["tables"] = tables
    if not tables:
        entry["error"] = f"No workbook table metadata found in: {path}"
    return entry


def _extract_used_tables(data_sets: list[dict[str, Any]], datasource_name: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    ds_target = datasource_name.strip().lower()

    for item in data_sets:
        if not isinstance(item, dict):
            continue

        ds_name = str(item.get("data_source_name") or "").strip().lower()
        if ds_name and ds_name != ds_target:
            continue

        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            continue

        for table_ref in _extract_table_references_from_sql(query):
            schema_name, table_name = _split_table_reference(table_ref)
            if not table_name:
                continue
            key = (schema_name.lower(), table_name.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append((schema_name, table_name))

    return found


def _extract_table_references_from_sql(sql: str) -> list[str]:
    refs: list[str] = []
    for match in SQL_TABLE_REF_RE.finditer(sql or ""):
        candidate = (match.group(1) or "").strip().rstrip(",")
        if not candidate or candidate.startswith("("):
            continue
        if candidate not in refs:
            refs.append(candidate)
    return refs


def _split_table_reference(table_ref: str) -> tuple[str, str]:
    parts = [p.strip() for p in (table_ref or "").split(".") if p.strip()]
    clean = [_clean_identifier(p) for p in parts if _clean_identifier(p)]
    if not clean:
        return "dbo", ""
    if len(clean) == 1:
        return "dbo", clean[0]
    return clean[-2], clean[-1]


def _clean_identifier(token: str) -> str:
    value = (token or "").strip()
    if value.startswith("[") and value.endswith("]") and len(value) >= 2:
        value = value[1:-1]
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def _open_sqlserver_connection(
    pyodbc_module: Any,
    raw_connection_string: str,
    overrides: dict[str, Any] | None,
):
    drivers = [d for d in pyodbc_module.drivers() if "sql server" in d.lower()]
    if not drivers:
        return None

    errors: list[str] = []
    last_exc: Exception | None = None
    for driver_name in _ordered_sqlserver_driver_candidates(drivers):
        conn_str = _normalize_sqlserver_odbc_connection_string(raw_connection_string, driver_name, overrides)
        try:
            return pyodbc_module.connect(conn_str, timeout=15)
        except Exception as exc:
            last_exc = exc
            errors.append(f"{driver_name}: {exc}")

    detail = " | ".join(errors[-3:]) if errors else "No SQL Server ODBC connection attempt was made."
    raise RuntimeError(f"Unable to connect with available SQL Server ODBC drivers. {detail}") from last_exc


def _ordered_sqlserver_driver_candidates(drivers: list[str]) -> list[str]:
    available = {driver.lower(): driver for driver in drivers}
    ordered: list[str] = []
    for candidate in [
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 18 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]:
        driver = available.get(candidate.lower())
        if driver and driver not in ordered:
            ordered.append(driver)
    for driver in drivers:
        if driver not in ordered:
            ordered.append(driver)
    return ordered


def _resolve_connection_overrides(
    connection_overrides: dict[str, dict[str, Any]] | None,
    datasource_name: str,
) -> dict[str, Any]:
    if isinstance(connection_overrides, dict):
        key = (datasource_name or "").strip().lower()
        for name, payload in connection_overrides.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                continue
            if name.strip().lower() == key:
                return payload

    env_uid = os.getenv("RDL_DB_UID")
    env_pwd = os.getenv("RDL_DB_PWD")
    env_trusted = os.getenv("RDL_DB_TRUSTED")
    env_encrypt = os.getenv("RDL_DB_ENCRYPT")
    env_tsc = os.getenv("RDL_DB_TRUST_SERVER_CERT")

    payload: dict[str, Any] = {}
    if isinstance(env_uid, str) and env_uid.strip():
        payload["uid"] = env_uid.strip()
    if isinstance(env_pwd, str) and env_pwd.strip():
        payload["pwd"] = env_pwd
    if isinstance(env_trusted, str) and env_trusted.strip():
        payload["trusted_connection"] = env_trusted.strip()
    if isinstance(env_encrypt, str) and env_encrypt.strip():
        payload["encrypt"] = env_encrypt.strip()
    if isinstance(env_tsc, str) and env_tsc.strip():
        payload["trust_server_certificate"] = env_tsc.strip()
    return payload


def _normalize_sqlserver_odbc_connection_string(
    raw_connection_string: str,
    driver_name: str,
    overrides: dict[str, Any] | None,
) -> str:
    entries = _connection_string_entries(raw_connection_string)

    server = entries.get("server") or entries.get("data source") or entries.get("addr") or entries.get("address")
    database = entries.get("database") or entries.get("initial catalog")
    uid = entries.get("uid") or entries.get("user id") or entries.get("user")
    pwd = entries.get("pwd") or entries.get("password")

    integrated_raw = entries.get("integrated security") or entries.get("trusted_connection") or ""
    integrated = integrated_raw.strip().lower() in {"true", "sspi", "yes", "1"}

    if isinstance(overrides, dict):
        override_uid = overrides.get("uid")
        override_pwd = overrides.get("pwd")
        override_trusted = overrides.get("trusted_connection")
        override_encrypt = overrides.get("encrypt")
        override_tsc = overrides.get("trust_server_certificate")

        if isinstance(override_uid, str) and override_uid.strip():
            uid = override_uid.strip()
        if isinstance(override_pwd, str):
            pwd = override_pwd
        if isinstance(override_trusted, str) and override_trusted.strip():
            integrated = override_trusted.strip().lower() in {"true", "sspi", "yes", "1"}
        if isinstance(override_encrypt, str) and override_encrypt.strip():
            entries["encrypt"] = override_encrypt.strip()
        if isinstance(override_tsc, str) and override_tsc.strip():
            entries["trustservercertificate"] = override_tsc.strip()

    parts = [f"DRIVER={{{driver_name}}}"]
    if server:
        parts.append(f"SERVER={server}")
    if database:
        parts.append(f"DATABASE={database}")

    if integrated:
        parts.append("Trusted_Connection=Yes")
    elif uid:
        parts.append(f"UID={uid}")
        if pwd:
            parts.append(f"PWD={pwd}")

    # Preserve optional extras when present.
    for src_key, dst_key in [
        ("encrypt", "Encrypt"),
        ("trustservercertificate", "TrustServerCertificate"),
        ("applicationintent", "ApplicationIntent"),
    ]:
        value = entries.get(src_key)
        if value:
            parts.append(f"{dst_key}={value}")

    if "encrypt" not in entries:
        parts.append("Encrypt=No")
    if "trustservercertificate" not in entries:
        parts.append("TrustServerCertificate=Yes")

    return ";".join(parts)


def _connection_string_entries(raw_connection_string: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for part in (raw_connection_string or "").split(";"):
        token = part.strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        entries[key.strip().lower()] = value.strip()
    return entries


def _split_table_identifier(raw_table: str) -> tuple[str, str]:
    value = str(raw_table or "").strip()
    if not value:
        return "", ""
    parts = [_clean_identifier(part) for part in value.split(".") if _clean_identifier(part)]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return "", parts[0]
    return "", ""


def _xml_child_text(node: ET.Element, tag_name: str) -> str:
    child = node.find(tag_name)
    if child is None or child.text is None:
        return ""
    return str(child.text).strip()


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _remote_type_label(record: ET.Element) -> str:
    for attribute in record.findall("./attributes/attribute"):
        if str(attribute.attrib.get("name", "") or "").strip().lower() == "debugremotetype":
            value = str(attribute.text or "").strip().strip('"')
            if value:
                return value.lower().replace("sql_", "").replace("_", " ")
    return "unknown"


def _infer_table_keys_from_workbook_metadata(tables_by_name: dict[str, dict[str, Any]]) -> None:
    primary_key_lookup: dict[str, tuple[str, str]] = {}

    for table in tables_by_name.values():
        table_name = str(table.get("name", "") or "").strip()
        columns = table.get("columns", []) if isinstance(table.get("columns"), list) else []
        column_names = [str(col.get("name", "") or "").strip() for col in columns if isinstance(col, dict)]
        pk_candidates = _primary_key_candidates_for_table(table_name)
        primary_key = [column for column in column_names if column.lower() in pk_candidates]
        if not primary_key and table_name.lower().startswith("dim"):
            key_columns = [column for column in column_names if column.lower().endswith("key")]
            if len(key_columns) == 1:
                primary_key = key_columns
        table["primary_key"] = primary_key[:1]
        if table["primary_key"]:
            primary_key_lookup[str(table["primary_key"][0]).lower()] = (
                str(table.get("schema", "") or ""),
                table_name,
            )

    for table in tables_by_name.values():
        table_name = str(table.get("name", "") or "").strip()
        columns = table.get("columns", []) if isinstance(table.get("columns"), list) else []
        foreign_keys: list[dict[str, str]] = []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("name", "") or "").strip()
            if not column_name or not column_name.lower().endswith("key"):
                continue
            target = primary_key_lookup.get(column_name.lower())
            if target is None:
                continue
            ref_schema, ref_table = target
            if ref_table.lower() == table_name.lower():
                continue
            foreign_keys.append(
                {
                    "column": column_name,
                    "ref_schema": ref_schema,
                    "ref_table": ref_table,
                    "ref_column": column_name,
                }
            )
        table["foreign_keys"] = foreign_keys


def _primary_key_candidates_for_table(table_name: str) -> set[str]:
    clean_name = re.sub(r"[^A-Za-z0-9]+", "", str(table_name or ""))
    candidates = {f"{clean_name}key".lower()} if clean_name else set()
    for prefix in ("Dim", "Fact"):
        if clean_name.startswith(prefix) and len(clean_name) > len(prefix):
            candidates.add(f"{clean_name[len(prefix):]}key".lower())
    if clean_name.lower() == "dimdate":
        candidates.add("datekey")
    return candidates


def _fetch_table_metadata(conn: Any, schema_name: str, table_name: str) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                c.TABLE_SCHEMA,
                c.TABLE_NAME,
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.IS_NULLABLE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.DATETIME_PRECISION
            FROM INFORMATION_SCHEMA.COLUMNS c
            WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION
            """,
            schema_name,
            table_name,
        )
        rows = cursor.fetchall()
        if not rows:
            return None

        canonical_schema_name = str(rows[0][0] or schema_name).strip() or schema_name
        canonical_table_name = str(rows[0][1] or table_name).strip() or table_name
        columns = [
            {
                "name": str(row[2]),
                "data_type": str(row[3]).lower() if row[3] is not None else "",
                "is_nullable": str(row[4]).upper() == "YES",
                "max_length": int(row[5]) if row[5] is not None else None,
                "numeric_precision": int(row[6]) if row[6] is not None else None,
                "numeric_scale": int(row[7]) if row[7] is not None else None,
                "datetime_precision": int(row[8]) if row[8] is not None else None,
            }
            for row in rows
        ]

        cursor.execute(
            """
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
             AND tc.TABLE_NAME = kcu.TABLE_NAME
            WHERE tc.TABLE_SCHEMA = ?
              AND tc.TABLE_NAME = ?
              AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ORDER BY kcu.ORDINAL_POSITION
            """,
            canonical_schema_name,
            canonical_table_name,
        )
        primary_key = [str(row[0]) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                s_parent.name AS parent_schema,
                t_parent.name AS parent_table,
                c_parent.name AS parent_column,
                s_ref.name AS ref_schema,
                t_ref.name AS ref_table,
                c_ref.name AS ref_column
            FROM sys.foreign_key_columns fkc
            JOIN sys.tables t_parent ON fkc.parent_object_id = t_parent.object_id
            JOIN sys.schemas s_parent ON t_parent.schema_id = s_parent.schema_id
            JOIN sys.columns c_parent
              ON fkc.parent_object_id = c_parent.object_id
             AND fkc.parent_column_id = c_parent.column_id
            JOIN sys.tables t_ref ON fkc.referenced_object_id = t_ref.object_id
            JOIN sys.schemas s_ref ON t_ref.schema_id = s_ref.schema_id
            JOIN sys.columns c_ref
              ON fkc.referenced_object_id = c_ref.object_id
             AND fkc.referenced_column_id = c_ref.column_id
            WHERE s_parent.name = ?
              AND t_parent.name = ?
            """,
            canonical_schema_name,
            canonical_table_name,
        )
        foreign_keys = [
            {
                "column": str(row[2]),
                "ref_schema": str(row[3]),
                "ref_table": str(row[4]),
                "ref_column": str(row[5]),
            }
            for row in cursor.fetchall()
        ]

    return {
        "schema": canonical_schema_name,
        "name": canonical_table_name,
        "full_name": f"[{canonical_schema_name}].[{canonical_table_name}]",
        "columns": columns,
        "primary_key": primary_key,
        "foreign_keys": foreign_keys,
    }


def _build_join_specs_from_foreign_keys(tables_payload: list[dict[str, Any]]) -> list[dict[str, str]]:
    table_names = {str(t.get("name") or "") for t in tables_payload if isinstance(t, dict)}
    specs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for table in tables_payload:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name") or "")
        if not table_name:
            continue

        for fk in table.get("foreign_keys", []) if isinstance(table.get("foreign_keys"), list) else []:
            if not isinstance(fk, dict):
                continue
            ref_table = str(fk.get("ref_table") or "")
            left_col = str(fk.get("column") or "")
            right_col = str(fk.get("ref_column") or "")
            if not ref_table or not left_col or not right_col:
                continue
            if ref_table not in table_names:
                continue

            key = (table_name.lower(), left_col.lower(), ref_table.lower(), right_col.lower())
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "join": "inner",
                    "left_table": table_name,
                    "left_col": left_col,
                    "right_table": ref_table,
                    "right_col": right_col,
                }
            )

    return specs
