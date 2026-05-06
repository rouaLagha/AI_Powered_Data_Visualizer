from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET

try:
    import winreg  # type: ignore[attr-defined]
except Exception:
    winreg = None


SQL_TABLE_REF_RE = re.compile(
    r"\b(?:from|join)\s+((?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*)){0,2})",
    flags=re.IGNORECASE,
)
SQL_TABLE_ALIAS_RE = re.compile(
    r"\b(?:from|join)\s+((?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*)){0,2})(?:\s+(?:as\s+)?([A-Za-z_][\w$]*))?",
    flags=re.IGNORECASE,
)
SQL_QUALIFIED_COL_RE = re.compile(
    r"((?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*)){1,2})",
    flags=re.IGNORECASE,
)
SQL_JOIN_CLAUSE_RE = re.compile(
    r"\b(?:(inner|left|right|full|cross)\s+)?join\s+"
    r"((?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*)){0,2})"
    r"(?:\s+(?:as\s+)?([A-Za-z_][\w$]*))?\s+on\s+"
    r"(.+?)(?=\b(?:(?:inner|left|right|full|cross)\s+)?join\b|\bwhere\b|\bgroup\b|\border\b|\bhaving\b|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
FIELD_EXPR_REF_RE = re.compile(r"Fields!([A-Za-z0-9_]+)\.Value", flags=re.IGNORECASE)
PARAM_EXPR_REF_RE = re.compile(r"Parameters!([A-Za-z0-9_]+)\.Value", flags=re.IGNORECASE)
SUM_FIELD_CALL_RE = re.compile(
    r"sum\s*\(\s*Fields!(?P<field>[A-Za-z0-9_]+)\.Value\s*\)",
    flags=re.IGNORECASE,
)
SUM_DIFF_EXPR_RE = re.compile(
    r"sum\s*\(\s*Fields!(?P<left>[A-Za-z0-9_]+)\.Value\s*\)\s*"
    r"-\s*sum\s*\(\s*Fields!(?P<right>[A-Za-z0-9_]+)\.Value\s*\)",
    flags=re.IGNORECASE,
)
SUM_DIFF_RATIO_EXPR_RE = re.compile(
    r"\(\s*sum\s*\(\s*Fields!(?P<left>[A-Za-z0-9_]+)\.Value\s*\)\s*"
    r"-\s*sum\s*\(\s*Fields!(?P<right>[A-Za-z0-9_]+)\.Value\s*\)\s*\)\s*"
    r"/\s*sum\s*\(\s*Fields!(?P<denominator>[A-Za-z0-9_]+)\.Value\s*\)",
    flags=re.IGNORECASE,
)
SUM_RATIO_EXPR_RE = re.compile(
    r"sum\s*\(\s*Fields!(?P<left>[A-Za-z0-9_]+)\.Value\s*\)\s*"
    r"/\s*sum\s*\(\s*Fields!(?P<right>[A-Za-z0-9_]+)\.Value\s*\)",
    flags=re.IGNORECASE,
)
AGGREGATE_EXPR_HINT_RE = re.compile(r"\b(sum|avg|average|count|min|max|format|iif)\s*\(", flags=re.IGNORECASE)
SHELF_FIELD_REF_RE = re.compile(
    r"\[[^\]]+\]\.\[(?P<derivation>[^:\]]+):(?P<field>[^:\]]+):[^\]]+\]",
    flags=re.IGNORECASE,
)


def validate_twb_xml_basic(xml_content: str) -> None:
    """Basic structural checks before producing .twb file."""
    root = ET.fromstring(xml_content)
    if _local_name(root.tag) != "workbook":
        raise ValueError("Generated XML root must be <workbook>")

    for attr in ["version", "source-build"]:
        if attr not in root.attrib:
            raise ValueError(f"Missing required workbook attribute: {attr}")


def write_twb_file(xml_content: str, output_twb_path: str | Path) -> Path:
    validate_twb_xml_basic(xml_content)
    output_twb_path = Path(output_twb_path)
    output_twb_path.parent.mkdir(parents=True, exist_ok=True)
    output_twb_path.write_text(xml_content, encoding="utf-8")
    return output_twb_path


def inject_datasource_connections(
    xml_content: str,
    data_sources: list[dict],
    data_sets: list[dict] | None = None,
    db_catalog: dict | None = None,
) -> str:
    """Inject RDL datasource connection metadata into TWB datasource nodes."""
    root = ET.fromstring(xml_content)
    if _local_name(root.tag) != "workbook":
        raise ValueError("Generated XML root must be <workbook>")

    _strip_namespaces(root)
    data_sets = data_sets if isinstance(data_sets, list) else []

    datasources_node = root.find("datasources")
    if datasources_node is None:
        datasources_node = ET.SubElement(root, "datasources")

    if not data_sources and data_sets:
        inferred_name = data_sets[0].get("data_source_name") if isinstance(data_sets[0], dict) else None
        if not isinstance(inferred_name, str) or not inferred_name.strip():
            inferred_name = "DataSource_1"
        data_sources = [{"name": inferred_name}]

    for index, ds in enumerate(data_sources, start=1):
        ds_name = ds.get("name") or f"DataSource_{index}"
        provider_raw = ds.get("provider") or ""
        provider = provider_raw.lower()
        conn_string = ds.get("connection_string") or ""
        parsed_conn = _parse_connection_string(conn_string)
        provider_class = _provider_to_tableau_class(provider, conn_string)
        if provider_class == "snowflake":
            parsed_conn = _enrich_snowflake_connection_details(parsed_conn)
            parsed_conn = _normalize_snowflake_connection_identifiers(parsed_conn)
            if not parsed_conn.get("server"):
                # DSN-only sources without resolved host cannot use Tableau's native Snowflake connector.
                provider_class = "genericodbc"
        security_type = (ds.get("security_type") or "").lower()
        credential_retrieval = (ds.get("credential_retrieval") or "").lower()
        windows_credentials = ds.get("windows_credentials")
        user_name = ds.get("user_name")

        datasource_node = _find_or_create_datasource(datasources_node, ds_name)
        if "caption" not in datasource_node.attrib:
            datasource_node.set("caption", ds_name)
        datasource_node.attrib.setdefault("inline", "true")
        datasource_node.attrib.setdefault("hasconnection", "true")
        datasource_node.attrib.setdefault("version", "18.1")

        connection_node = _find_direct_child(datasource_node, "connection")
        if connection_node is None:
            connection_node = ET.SubElement(datasource_node, "connection")

        aliases_node = _find_direct_child(datasource_node, "aliases")
        if aliases_node is None:
            aliases_node = ET.SubElement(datasource_node, "aliases")
        aliases_node.attrib["enabled"] = "yes"

        _ensure_datasource_connection_before_aliases(datasource_node)

        keep_federated = _looks_federated_connection(connection_node)
        prefer_federated = _should_prefer_federated_connection(provider_class, parsed_conn)
        connection_class = "federated" if (keep_federated or prefer_federated) else provider_class

        connection_node.set("class", connection_class)
        if "rdl-connect-string" in connection_node.attrib:
            del connection_node.attrib["rdl-connect-string"]

        target_connection = connection_node
        named_connection_name = ""
        if connection_class == "federated":
            target_connection, named_connection_name = _ensure_federated_named_connection(
                connection_node=connection_node,
                datasource_name=ds_name,
                provider_class=provider_class,
                server_name=parsed_conn.get("server") or "",
            )
            for attr in [
                "server",
                "dbname",
                "port",
                "schema",
                "warehouse",
                "role",
                "authenticator",
                "authentication",
                "username",
                "odbc-connect-string-extras",
            ]:
                connection_node.attrib.pop(attr, None)

        if parsed_conn.get("server"):
            target_connection.set("server", parsed_conn["server"])
        if parsed_conn.get("dbname"):
            target_connection.set("dbname", parsed_conn["dbname"])
        if parsed_conn.get("port"):
            target_connection.set("port", parsed_conn["port"])
        if provider_class == "snowflake":
            target_connection.set("odbc-connect-string-extras", "")
            target_connection.set("max-varchar-size", "")
            target_connection.set("one-time-sql", "")
            target_connection.set("temp-table-detection", "optimized")
        if provider_class == "snowflake" and parsed_conn.get("schema"):
            target_connection.set("schema", parsed_conn["schema"])
        if provider_class == "snowflake" and parsed_conn.get("warehouse"):
            target_connection.set("warehouse", parsed_conn["warehouse"])
        if provider_class == "snowflake" and parsed_conn.get("role"):
            target_connection.set("role", parsed_conn["role"])
            target_connection.set("service", parsed_conn["role"])
        if provider_class == "snowflake" and parsed_conn.get("authenticator"):
            target_connection.set("authenticator", parsed_conn["authenticator"])
        if provider_class != "snowflake" and parsed_conn.get("odbc_connect_string_extras"):
            target_connection.set("odbc-connect-string-extras", parsed_conn["odbc_connect_string_extras"])

        authentication = _map_tableau_authentication(
            security_type=security_type,
            credential_retrieval=credential_retrieval,
            windows_credentials=windows_credentials,
            connection_string=conn_string,
            user_name=user_name,
            provider_class=provider_class,
        )
        if authentication:
            if provider_class == "snowflake" and authentication == "username-password":
                target_connection.set("authentication", "Username Password")
            else:
                target_connection.set("authentication", authentication)
        resolved_user_name = ""
        if isinstance(user_name, str) and user_name.strip():
            resolved_user_name = user_name.strip()
        elif isinstance(parsed_conn.get("username"), str) and parsed_conn["username"].strip():
            resolved_user_name = parsed_conn["username"].strip()
        if authentication == "username-password" and resolved_user_name:
            target_connection.set("username", resolved_user_name)

        related_sets = _datasets_for_datasource(
            data_sets,
            datasource_name=ds_name,
            allow_unbound_fallback=index == 1,
        )

        catalog_source = _find_catalog_datasource(db_catalog, ds_name)
        catalog_table_refs = _catalog_table_references(catalog_source)
        catalog_join_specs = _catalog_join_specs(catalog_source)

        rdl_table_refs = _extract_table_references_from_datasets(related_sets) if connection_class == "federated" else []
        rdl_join_specs = _extract_join_specs_from_datasets(related_sets) if connection_class == "federated" else []

        # Respect RDL SQL first; use catalog only as fallback when RDL doesn't provide join graph.
        table_refs = rdl_table_refs[:] if rdl_table_refs else catalog_table_refs[:]
        join_specs = rdl_join_specs[:] if rdl_join_specs else catalog_join_specs[:]

        if rdl_join_specs and table_refs:
            join_tables = _join_spec_table_names(rdl_join_specs)
            filtered_refs = [
                ref
                for ref in table_refs
                if _relation_name_from_table_reference(ref) in join_tables
            ]
            if filtered_refs:
                table_refs = filtered_refs
        has_relation = False

        if connection_class == "federated" and table_refs:
            relation_connection = named_connection_name or _default_named_connection_name(provider_class, ds_name)
            _align_federated_relations_to_tables(
                connection_node=connection_node,
                relation_connection_name=relation_connection,
                table_refs=table_refs,
                join_specs=join_specs,
                provider_class=provider_class,
                default_dbname=parsed_conn.get("dbname") or "",
                default_schema=parsed_conn.get("schema") or "",
            )
            has_relation = True
        elif _find_direct_child(connection_node, "relation") is not None:
            has_relation = True

        if not has_relation:
            for dataset in related_sets:
                query = dataset.get("query") if isinstance(dataset, dict) else None
                if not isinstance(query, str) or not query.strip():
                    continue
                relation_name = dataset.get("name") if isinstance(dataset, dict) else None
                if not isinstance(relation_name, str) or not relation_name.strip():
                    relation_name = "DataSet1"
                rel = ET.SubElement(connection_node, "relation", attrib={"name": relation_name, "type": "text"})
                rel.text = _sanitize_relation_sql(query)
                break

        if catalog_source:
            # Strict mode: when DB catalog exists, datasource columns come only from DB metadata.
            _upsert_datasource_columns_from_catalog(
                datasource_node=datasource_node,
                catalog_source=catalog_source,
                table_refs=table_refs,
                prune_existing=True,
            )
        else:
            _upsert_datasource_columns_from_datasets(
                datasource_node,
                related_sets,
                prune_existing=True,
            )
        if connection_class == "federated":
            if catalog_source:
                _upsert_cols_map_from_catalog(
                    datasource_node=datasource_node,
                    connection_node=connection_node,
                    catalog_source=catalog_source,
                    table_refs=table_refs,
                )
                _upsert_metadata_records_from_catalog(
                    datasource_node=datasource_node,
                    connection_node=connection_node,
                    catalog_source=catalog_source,
                    table_refs=table_refs,
                )
            else:
                _upsert_cols_map_from_datasets(
                    datasource_node=datasource_node,
                    connection_node=connection_node,
                    data_sets=related_sets,
                    table_refs=table_refs,
                )

    return ET.tostring(root, encoding="unicode")


def _looks_federated_connection(connection_node: ET.Element) -> bool:
    if (connection_node.attrib.get("class") or "").strip().lower() == "federated":
        return True
    if _find_direct_child(connection_node, "named-connections") is not None:
        return True
    for rel in [c for c in list(connection_node) if _local_name(c.tag) == "relation"]:
        if (rel.attrib.get("type") or "").strip().lower() == "collection":
            return True
    return False


def _should_prefer_federated_connection(provider_class: str, parsed_conn: dict[str, str]) -> bool:
    if provider_class == "snowflake":
        return bool(parsed_conn.get("server") or parsed_conn.get("dbname") or parsed_conn.get("schema"))
    if provider_class != "sqlserver":
        return False
    return bool(parsed_conn.get("server") or parsed_conn.get("dbname"))


def _ensure_federated_named_connection(
    connection_node: ET.Element,
    datasource_name: str,
    provider_class: str,
    server_name: str,
) -> tuple[ET.Element, str]:
    named_connections = _find_direct_child(connection_node, "named-connections")
    if named_connections is None:
        named_connections = ET.SubElement(connection_node, "named-connections")

    named_connection = _find_direct_child(named_connections, "named-connection")
    default_name = _default_named_connection_name(provider_class, datasource_name)
    if named_connection is None:
        named_connection = ET.SubElement(
            named_connections,
            "named-connection",
            attrib={
                "name": default_name,
                "caption": server_name or datasource_name,
            },
        )

    if not named_connection.attrib.get("name"):
        named_connection.attrib["name"] = default_name
    if not named_connection.attrib.get("caption"):
        named_connection.attrib["caption"] = server_name or datasource_name

    inner_connection = _find_direct_child(named_connection, "connection")
    if inner_connection is None:
        inner_connection = ET.SubElement(named_connection, "connection")
    inner_connection.attrib["class"] = provider_class

    return inner_connection, named_connection.attrib.get("name", default_name)


def _default_named_connection_name(provider_class: str, datasource_name: str) -> str:
    clean_name = _clean_identifier_token(datasource_name)
    return f"{provider_class}.{clean_name}"


def _clean_identifier_token(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "", (value or "").strip().lower())
    return text or "datasource"


def _extract_table_references_from_datasets(data_sets: list[dict]) -> list[str]:
    found: list[str] = []
    for ds in data_sets:
        if not isinstance(ds, dict):
            continue
        query = ds.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        for table_ref in _extract_table_references_from_sql(query):
            if table_ref not in found:
                found.append(table_ref)
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


def _extract_table_alias_map(sql: str) -> dict[str, str]:
    reserved_aliases = {
        "on",
        "where",
        "group",
        "order",
        "inner",
        "left",
        "right",
        "full",
        "cross",
        "join",
        "having",
        "union",
    }
    alias_map: dict[str, str] = {}

    for match in SQL_TABLE_ALIAS_RE.finditer(sql or ""):
        table_ref = (match.group(1) or "").strip()
        alias = (match.group(2) or "").strip()
        if not table_ref:
            continue

        table_name = _relation_name_from_table_reference(table_ref)
        if table_name:
            alias_map[table_name.lower()] = table_name

        if alias:
            alias_lower = alias.lower()
            if alias_lower not in reserved_aliases:
                alias_map[alias_lower] = table_name

    return alias_map


def _extract_select_projection_specs(sql: str) -> dict[str, dict[str, str | None]]:
    select_clause = _extract_top_level_select_clause(sql)
    if not select_clause:
        return {}

    table_alias_map = _extract_table_alias_map(sql)
    specs: dict[str, dict[str, str | None]] = {}

    for item in _split_top_level_csv(select_clause):
        expr, alias = _split_expression_alias(item)
        source_table, source_field = _extract_source_column_info(expr, table_alias_map)

        if not alias and source_field:
            alias = source_field
        if not alias:
            continue

        if not source_field:
            source_field = alias

        specs[alias.lower()] = {
            "alias": alias,
            "source_field": source_field,
            "source_table": source_table,
        }

    return specs


def _extract_top_level_select_clause(sql: str) -> str:
    text = sql or ""
    m = re.search(r"\bselect\b", text, flags=re.IGNORECASE)
    if m is None:
        return ""

    start = m.end()
    depth = 0
    in_single = False
    in_double = False
    in_bracket = False
    i = start

    while i < len(text):
        ch = text[i]

        if in_single:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue

        if in_bracket:
            if ch == "]":
                in_bracket = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue

        if depth == 0 and text[i : i + 4].lower() == "from":
            prev_ok = i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
            next_ok = i + 4 >= len(text) or not (text[i + 4].isalnum() or text[i + 4] == "_")
            if prev_ok and next_ok:
                return text[start:i]

        i += 1

    return ""


def _split_top_level_csv(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    in_bracket = False
    start = 0

    for i, ch in enumerate(text):
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if in_bracket:
            if ch == "]":
                in_bracket = False
            continue

        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "[":
            in_bracket = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue

        if ch == "," and depth == 0:
            piece = text[start:i].strip()
            if piece:
                items.append(piece)
            start = i + 1

    tail = text[start:].strip()
    if tail:
        items.append(tail)

    return items


def _split_expression_alias(item: str) -> tuple[str, str | None]:
    text = (item or "").strip()
    if not text:
        return "", None

    m_as = re.match(
        r"(?is)(.+?)\s+as\s+(\[[^\]]+\]|\"[^\"]+\"|`[^`]+`|[A-Za-z_][\w$]*)\s*$",
        text,
    )
    if m_as is not None:
        expr = m_as.group(1).strip()
        alias = _clean_sql_identifier(m_as.group(2))
        return expr, alias or None

    m_no_as = re.match(
        r"(?is)(.+?)\s+(\[[^\]]+\]|\"[^\"]+\"|`[^`]+`|[A-Za-z_][\w$]*)\s*$",
        text,
    )
    if m_no_as is not None:
        expr = m_no_as.group(1).strip()
        alias = _clean_sql_identifier(m_no_as.group(2))
        if expr and alias and ("." in expr or ")" in expr or "(" in expr):
            return expr, alias

    return text, None


def _extract_source_column_info(expr: str, table_alias_map: dict[str, str]) -> tuple[str | None, str | None]:
    matches = list(SQL_QUALIFIED_COL_RE.finditer(expr or ""))
    if not matches:
        return None, None

    token = matches[-1].group(1)
    parts = [p for p in token.split(".") if p]
    if len(parts) < 2:
        return None, None

    table_token = _clean_sql_identifier(parts[-2])
    field_token = _clean_sql_identifier(parts[-1])
    if not field_token:
        return None, None

    resolved_table = table_alias_map.get(table_token.lower(), table_token) if table_token else None
    return resolved_table or None, field_token


def _clean_sql_identifier(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]") and len(text) >= 2:
        text = text[1:-1]
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1]
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        text = text[1:-1]
    return text.strip()


def _normalize_snowflake_identifier_token(value: str) -> str:
    """Return Snowflake-safe identifier token casing for unquoted names."""
    token = _clean_sql_identifier(value)
    if not token:
        return ""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token):
        return token.upper()
    return token


def _normalize_snowflake_connection_identifiers(parsed_conn: dict[str, str]) -> dict[str, str]:
    normalized = dict(parsed_conn)
    for key in ["dbname", "schema", "warehouse", "role"]:
        if normalized.get(key):
            normalized[key] = _normalize_snowflake_identifier_token(normalized[key])
    return normalized


def _collect_dataset_field_specs(data_sets: list[dict]) -> list[dict[str, str | None]]:
    specs: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for ds in data_sets:
        if not isinstance(ds, dict):
            continue

        query = ds.get("query")
        projection_specs = _extract_select_projection_specs(query) if isinstance(query, str) else {}

        fields = ds.get("fields") if isinstance(ds.get("fields"), list) else []
        if fields:
            for field in fields:
                if not isinstance(field, dict):
                    continue
                alias = field.get("name") or field.get("data_field")
                if not isinstance(alias, str) or not alias.strip():
                    continue
                alias_clean = alias.strip()

                projection = projection_specs.get(alias_clean.lower(), {})
                source_field = projection.get("source_field") if isinstance(projection, dict) else None
                source_table = projection.get("source_table") if isinstance(projection, dict) else None
                physical_name = source_field.strip() if isinstance(source_field, str) and source_field.strip() else alias_clean
                caption = None

                key = physical_name.lower()
                if key in seen:
                    continue
                seen.add(key)
                specs.append(
                    {
                        "name": physical_name,
                        "caption": caption,
                        "table": source_table.strip() if isinstance(source_table, str) and source_table.strip() else None,
                    }
                )
            continue

        for alias_lower, projection in projection_specs.items():
            alias = projection.get("alias") if isinstance(projection, dict) else None
            source_field = projection.get("source_field") if isinstance(projection, dict) else None
            source_table = projection.get("source_table") if isinstance(projection, dict) else None

            if isinstance(source_field, str) and source_field.strip():
                physical_name = source_field.strip()
            elif isinstance(alias, str) and alias.strip():
                physical_name = alias.strip()
            else:
                continue

            caption = None

            key = physical_name.lower()
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                {
                    "name": physical_name,
                    "caption": caption,
                    "table": source_table.strip() if isinstance(source_table, str) and source_table.strip() else None,
                }
            )

    return specs


def _normalize_table_reference(
    table_ref: str,
    provider_class: str = "",
    default_dbname: str = "",
    default_schema: str = "",
) -> str:
    clean_parts = [_clean_sql_identifier(p) for p in (table_ref or "").split(".") if p.strip()]
    clean_parts = [p for p in clean_parts if p]
    if not clean_parts:
        return "[UnknownTable]"

    provider = (provider_class or "").strip().lower()
    dbname = _clean_sql_identifier(default_dbname)
    schema = _clean_sql_identifier(default_schema)

    if provider == "snowflake":
        clean_parts = [_normalize_snowflake_identifier_token(p) for p in clean_parts]
        dbname = _normalize_snowflake_identifier_token(dbname)
        schema = _normalize_snowflake_identifier_token(schema)

        if len(clean_parts) >= 3:
            tail = clean_parts[-3:]
            return ".".join(f"[{part}]" for part in tail)

        if len(clean_parts) == 2:
            first, second = clean_parts
            if dbname and first.lower() != dbname.lower():
                return f"[{dbname}].[{first}].[{second}]"
            return f"[{first}].[{second}]"

        table_only = clean_parts[0]
        if dbname and schema:
            return f"[{dbname}].[{schema}].[{table_only}]"
        if schema:
            return f"[{schema}].[{table_only}]"
        if dbname:
            return f"[{dbname}].[{table_only}]"
        return f"[{table_only}]"

    normalized = [f"[{part}]" for part in clean_parts]
    if len(normalized) == 1:
        # SQL Server fallback: unqualified table names are commonly in dbo schema.
        return f"[dbo].{normalized[0]}"
    return ".".join(normalized)


def _relation_name_from_table_reference(table_ref: str) -> str:
    parts = [p.strip() for p in (table_ref or "").split(".") if p.strip()]
    if not parts:
        return "Table"
    leaf = parts[-1]
    if leaf.startswith("[") and leaf.endswith("]"):
        leaf = leaf[1:-1]
    return leaf.strip() or "Table"


def _align_federated_relations_to_tables(
    connection_node: ET.Element,
    relation_connection_name: str,
    table_refs: list[str],
    join_specs: list[dict[str, str]] | None = None,
    provider_class: str = "",
    default_dbname: str = "",
    default_schema: str = "",
) -> None:
    for child in list(connection_node):
        if _local_name(child.tag) == "relation":
            connection_node.remove(child)

    table_items: list[tuple[str, str]] = []
    table_by_name: dict[str, str] = {}
    for table_ref in table_refs[:24]:
        name = _relation_name_from_table_reference(table_ref)
        norm = _normalize_table_reference(
            table_ref=table_ref,
            provider_class=provider_class,
            default_dbname=default_dbname,
            default_schema=default_schema,
        )
        if name not in table_by_name:
            table_items.append((name, norm))
            table_by_name[name] = norm

    join_tree = _build_binary_join_relation_tree(
        relation_connection_name=relation_connection_name,
        table_items=table_items,
        table_by_name=table_by_name,
        join_specs=join_specs or [],
    )
    if join_tree is not None:
        connection_node.append(join_tree)
        return

    # Fallback when no valid join graph can be built.
    relation_collection = ET.SubElement(connection_node, "relation", attrib={"type": "collection"})
    for name, norm in table_items:
        ET.SubElement(
            relation_collection,
            "relation",
            attrib={
                "connection": relation_connection_name,
                "name": name,
                "table": norm,
                "type": "table",
            },
        )


def _build_binary_join_relation_tree(
    relation_connection_name: str,
    table_items: list[tuple[str, str]],
    table_by_name: dict[str, str],
    join_specs: list[dict[str, str]],
) -> ET.Element | None:
    filtered_specs: list[dict[str, str]] = []
    for spec in join_specs:
        if not isinstance(spec, dict):
            continue
        left_table = spec.get("left_table") if isinstance(spec.get("left_table"), str) else ""
        right_table = spec.get("right_table") if isinstance(spec.get("right_table"), str) else ""
        left_col = spec.get("left_col") if isinstance(spec.get("left_col"), str) else ""
        right_col = spec.get("right_col") if isinstance(spec.get("right_col"), str) else ""
        if not (left_table and right_table and left_col and right_col):
            continue
        if left_table not in table_by_name or right_table not in table_by_name:
            continue
        filtered_specs.append(spec)

    if not filtered_specs:
        return None

    first = filtered_specs[0]
    remaining_specs = filtered_specs[1:]

    left_table = first["left_table"]
    right_table = first["right_table"]
    first_clause_spec = _orient_join_clause_spec(
        source_spec=first,
        left_table=left_table,
        right_table=right_table,
    )

    current = ET.Element(
        "relation",
        attrib={
            "type": "join",
            "join": first_clause_spec.get("join", "inner") or "inner",
        },
    )
    _append_join_clause(current, first_clause_spec)
    current.append(
        _build_table_relation_node(
            relation_connection_name=relation_connection_name,
            table_name=left_table,
            table_norm=table_by_name[left_table],
        )
    )
    current.append(
        _build_table_relation_node(
            relation_connection_name=relation_connection_name,
            table_name=right_table,
            table_norm=table_by_name[right_table],
        )
    )

    included_tables: set[str] = {left_table, right_table}

    pending = remaining_specs[:]
    while pending:
        progressed = False
        next_pending: list[dict[str, str]] = []

        for spec in pending:
            lt = spec["left_table"]
            rt = spec["right_table"]

            next_table = ""
            existing_table = ""
            if lt in included_tables and rt not in included_tables:
                existing_table = lt
                next_table = rt
            elif rt in included_tables and lt not in included_tables:
                existing_table = rt
                next_table = lt

            if not next_table or not existing_table:
                next_pending.append(spec)
                continue

            clause_spec = _orient_join_clause_spec(
                source_spec=spec,
                left_table=existing_table,
                right_table=next_table,
            )

            next_parent = ET.Element(
                "relation",
                attrib={
                    "type": "join",
                    "join": clause_spec.get("join", "inner") or "inner",
                },
            )
            _append_join_clause(next_parent, clause_spec)
            next_parent.append(current)
            next_parent.append(
                _build_table_relation_node(
                    relation_connection_name=relation_connection_name,
                    table_name=next_table,
                    table_norm=table_by_name[next_table],
                )
            )
            current = next_parent
            included_tables.add(next_table)
            progressed = True

        if not progressed:
            break
        pending = next_pending

    all_tables = [name for name, _ in table_items]
    if any(name not in included_tables for name in all_tables):
        return None

    return current


def _orient_join_clause_spec(source_spec: dict[str, str], left_table: str, right_table: str) -> dict[str, str]:
    left_col = source_spec.get("left_col", "") if isinstance(source_spec, dict) else ""
    right_col = source_spec.get("right_col", "") if isinstance(source_spec, dict) else ""
    spec_left_table = source_spec.get("left_table", "") if isinstance(source_spec, dict) else ""
    spec_right_table = source_spec.get("right_table", "") if isinstance(source_spec, dict) else ""

    if left_table == spec_left_table and right_table == spec_right_table:
        oriented_left_col = left_col
        oriented_right_col = right_col
    elif left_table == spec_right_table and right_table == spec_left_table:
        oriented_left_col = right_col
        oriented_right_col = left_col
    else:
        oriented_left_col = left_col
        oriented_right_col = right_col

    join_type = source_spec.get("join", "inner") if isinstance(source_spec, dict) else "inner"
    return {
        "join": join_type if isinstance(join_type, str) and join_type.strip() else "inner",
        "left_table": left_table,
        "left_col": oriented_left_col,
        "right_table": right_table,
        "right_col": oriented_right_col,
    }


def _append_join_clause(join_node: ET.Element, spec: dict[str, str]) -> None:
    clause = ET.SubElement(join_node, "clause", attrib={"type": "join"})
    eq_expr = ET.SubElement(clause, "expression", attrib={"op": "="})
    ET.SubElement(
        eq_expr,
        "expression",
        attrib={"op": f"[{spec['left_table']}].[{spec['left_col']}]"},
    )
    ET.SubElement(
        eq_expr,
        "expression",
        attrib={"op": f"[{spec['right_table']}].[{spec['right_col']}]"},
    )


def _build_table_relation_node(
    relation_connection_name: str,
    table_name: str,
    table_norm: str,
) -> ET.Element:
    return ET.Element(
        "relation",
        attrib={
            "connection": relation_connection_name,
            "name": table_name,
            "table": table_norm,
            "type": "table",
        },
    )


def _extract_join_specs_from_datasets(data_sets: list[dict]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    for ds in data_sets:
        if not isinstance(ds, dict):
            continue
        query = ds.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        specs.extend(_extract_join_specs_from_sql(query))
    return specs


def _join_spec_table_names(join_specs: list[dict[str, str]]) -> set[str]:
    names: set[str] = set()
    for spec in join_specs:
        if not isinstance(spec, dict):
            continue
        left_table = spec.get("left_table") if isinstance(spec.get("left_table"), str) else ""
        right_table = spec.get("right_table") if isinstance(spec.get("right_table"), str) else ""
        if left_table:
            names.add(left_table)
        if right_table:
            names.add(right_table)
    return names


def _extract_join_specs_from_sql(sql: str) -> list[dict[str, str]]:
    alias_map = _extract_table_alias_map(sql)
    specs: list[dict[str, str]] = []

    for match in SQL_JOIN_CLAUSE_RE.finditer(sql or ""):
        join_kw = (match.group(1) or "inner").strip().lower()
        if join_kw == "cross":
            continue
        on_clause = (match.group(4) or "").strip()
        if not on_clause:
            continue

        qualified = [m.group(1) for m in SQL_QUALIFIED_COL_RE.finditer(on_clause)]
        if len(qualified) < 2:
            continue

        left_table, left_col = _resolve_qualified_column(qualified[0], alias_map)
        right_table, right_col = _resolve_qualified_column(qualified[1], alias_map)
        if not left_table or not right_table or not left_col or not right_col:
            continue

        specs.append(
            {
                "join": join_kw,
                "left_table": left_table,
                "left_col": left_col,
                "right_table": right_table,
                "right_col": right_col,
            }
        )

    return specs


def _resolve_qualified_column(token: str, alias_map: dict[str, str]) -> tuple[str | None, str | None]:
    parts = [p for p in (token or "").split(".") if p]
    if len(parts) < 2:
        return None, None

    table_token = _clean_sql_identifier(parts[-2])
    column_token = _clean_sql_identifier(parts[-1])
    if not table_token or not column_token:
        return None, None

    resolved_table = alias_map.get(table_token.lower(), table_token)
    return resolved_table, column_token


def _upsert_cols_map_from_datasets(
    datasource_node: ET.Element,
    connection_node: ET.Element,
    data_sets: list[dict],
    table_refs: list[str],
) -> None:
    field_specs = _collect_dataset_field_specs(data_sets)
    if not field_specs:
        return

    cols_node = _find_direct_child(connection_node, "cols")
    if cols_node is None:
        legacy_cols = _find_direct_child(datasource_node, "cols")
        if legacy_cols is not None:
            datasource_node.remove(legacy_cols)
            connection_node.append(legacy_cols)
            cols_node = legacy_cols

    if cols_node is None:
        cols_node = ET.SubElement(connection_node, "cols")

    allowed: dict[str, dict[str, str | None]] = {}
    for spec in field_specs:
        name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        clean = name.strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in allowed:
            allowed[key] = spec

    existing_by_key: dict[str, ET.Element] = {}
    for child in list(cols_node):
        if _local_name(child.tag) != "map":
            cols_node.remove(child)
            continue
        map_key = _clean_bracketed_name(child.attrib.get("key", "")).strip().lower()
        if not map_key or map_key not in allowed:
            cols_node.remove(child)
            continue
        if map_key not in existing_by_key:
            existing_by_key[map_key] = child

    default_table_name = _relation_name_from_table_reference(table_refs[0]) if len(table_refs) == 1 else ""
    for key, spec in allowed.items():
        original_name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        if not original_name:
            continue
        existing = existing_by_key.get(key)
        map_table = spec.get("table") if isinstance(spec.get("table"), str) else ""
        table_name = map_table.strip() if isinstance(map_table, str) else ""
        if not table_name:
            table_name = default_table_name

        if existing is None:
            value = f"[{table_name}].[{original_name}]" if table_name else f"[{original_name}]"
            ET.SubElement(
                cols_node,
                "map",
                attrib={
                    "key": f"[{original_name}]",
                    "value": value,
                },
            )
            continue

        existing.attrib["key"] = f"[{original_name}]"
        if table_name and not (existing.attrib.get("value") or "").strip():
            existing.attrib["value"] = f"[{table_name}].[{original_name}]"


def _datasets_for_datasource(
    data_sets: list[dict],
    datasource_name: str,
    allow_unbound_fallback: bool,
) -> list[dict]:
    exact: list[dict] = []
    unbound: list[dict] = []
    target = (datasource_name or "").strip().lower()

    for item in data_sets:
        if not isinstance(item, dict):
            continue
        ds_name = item.get("data_source_name")
        if isinstance(ds_name, str) and ds_name.strip():
            if ds_name.strip().lower() == target:
                exact.append(item)
            continue
        unbound.append(item)

    if exact:
        return exact
    if allow_unbound_fallback:
        return unbound
    return []


def _find_catalog_datasource(db_catalog: dict | None, datasource_name: str) -> dict | None:
    if not isinstance(db_catalog, dict):
        return None
    sources = db_catalog.get("datasources")
    if not isinstance(sources, list):
        return None

    target = (datasource_name or "").strip().lower()
    for item in sources:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip().lower() == target:
            return item if _catalog_source_has_column_metadata(item) else None
    return None


def _catalog_source_has_column_metadata(catalog_source: dict | None) -> bool:
    if not isinstance(catalog_source, dict):
        return False
    tables = catalog_source.get("tables")
    if not isinstance(tables, list) or not tables:
        return False
    for table in tables:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns")
        if isinstance(columns, list) and columns:
            return True
    return False


def _catalog_table_references(catalog_source: dict | None) -> list[str]:
    if not isinstance(catalog_source, dict):
        return []
    tables = catalog_source.get("tables")
    if not isinstance(tables, list):
        return []

    refs: list[str] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        full_name = table.get("full_name")
        if isinstance(full_name, str) and full_name.strip():
            refs.append(full_name.strip())
            continue

        schema_name = table.get("schema")
        table_name = table.get("name")
        if isinstance(table_name, str) and table_name.strip():
            if isinstance(schema_name, str) and schema_name.strip():
                refs.append(f"[{schema_name.strip()}].[{table_name.strip()}]")
            else:
                refs.append(f"[{table_name.strip()}]")

    return refs


def _catalog_join_specs(catalog_source: dict | None) -> list[dict[str, str]]:
    if not isinstance(catalog_source, dict):
        return []
    join_specs = catalog_source.get("join_specs")
    if not isinstance(join_specs, list):
        return []

    out: list[dict[str, str]] = []
    for spec in join_specs:
        if not isinstance(spec, dict):
            continue
        left_table = spec.get("left_table")
        right_table = spec.get("right_table")
        left_col = spec.get("left_col")
        right_col = spec.get("right_col")
        join_type = spec.get("join")
        if not all(isinstance(v, str) and v.strip() for v in [left_table, right_table, left_col, right_col]):
            continue
        out.append(
            {
                "join": join_type.strip() if isinstance(join_type, str) and join_type.strip() else "inner",
                "left_table": left_table.strip(),
                "left_col": left_col.strip(),
                "right_table": right_table.strip(),
                "right_col": right_col.strip(),
            }
        )

    return out


def _datasource_column_insert_index(datasource_node: ET.Element) -> int:
    """Return the insertion index where datasource <column> nodes remain schema-valid."""
    anchor_tags = {
        "layout",
        "style",
        "semantic-values",
        "object-graph",
    }

    children = list(datasource_node)
    for index, child in enumerate(children):
        if _local_name(child.tag) in anchor_tags:
            return index
    return len(children)


def _insert_datasource_column(datasource_node: ET.Element, attrs: dict[str, str]) -> ET.Element:
    column_node = ET.Element("column", attrib=attrs)
    insert_at = _datasource_column_insert_index(datasource_node)
    datasource_node.insert(insert_at, column_node)
    return column_node


def _normalize_datasource_column_order(datasource_node: ET.Element) -> None:
    """Move all direct datasource <column> nodes before layout/semantic-values/object-graph."""
    columns = [child for child in list(datasource_node) if _local_name(child.tag) == "column"]
    if not columns:
        return

    for column in columns:
        datasource_node.remove(column)

    insert_at = _datasource_column_insert_index(datasource_node)
    for offset, column in enumerate(columns):
        datasource_node.insert(insert_at + offset, column)


def _catalog_table_name_key(value: str) -> str:
    return _clean_bracketed_name(str(value or "")).strip().lower()


def _ordered_catalog_tables(catalog_source: dict, table_refs: list[str] | None = None) -> list[dict]:
    tables = catalog_source.get("tables") if isinstance(catalog_source, dict) else None
    if not isinstance(tables, list):
        return []

    refs = table_refs if isinstance(table_refs, list) else []
    by_name: dict[str, dict] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name")
        if not isinstance(table_name, str) or not table_name.strip():
            continue
        key = _catalog_table_name_key(table_name)
        by_name.setdefault(key, table)

    ordered: list[dict] = []
    seen: set[str] = set()
    for table_ref in refs:
        if not isinstance(table_ref, str) or not table_ref.strip():
            continue
        relation_name = _relation_name_from_table_reference(table_ref)
        key = _catalog_table_name_key(relation_name)
        table = by_name.get(key)
        if table is None or key in seen:
            continue
        ordered.append(table)
        seen.add(key)

    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name")
        if not isinstance(table_name, str) or not table_name.strip():
            continue
        key = _catalog_table_name_key(table_name)
        if key in seen:
            continue
        ordered.append(table)
        seen.add(key)

    return ordered


def _build_catalog_exposed_column_specs(
    catalog_source: dict,
    table_refs: list[str] | None = None,
) -> list[dict[str, object]]:
    ordered_tables = _ordered_catalog_tables(catalog_source, table_refs)
    if not ordered_tables:
        return []

    duplicate_counts: dict[str, int] = {}
    for table in ordered_tables:
        columns = table.get("columns") if isinstance(table, dict) else None
        if not isinstance(columns, list):
            continue
        for column in columns:
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            if not isinstance(col_name, str) or not col_name.strip():
                continue
            key = col_name.strip().lower()
            duplicate_counts[key] = duplicate_counts.get(key, 0) + 1

    seen_per_name: dict[str, int] = {}
    specs: list[dict[str, object]] = []
    ordinal = 0

    for table in ordered_tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name")
        columns = table.get("columns")
        if not isinstance(table_name, str) or not table_name.strip() or not isinstance(columns, list):
            continue
        table_clean = table_name.strip()

        for column in columns:
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            if not isinstance(col_name, str) or not col_name.strip():
                continue

            base_name = col_name.strip()
            key = base_name.lower()
            occurrence = seen_per_name.get(key, 0) + 1
            seen_per_name[key] = occurrence

            exposed_name = base_name
            if duplicate_counts.get(key, 0) > 1 and occurrence > 1:
                exposed_name = f"{base_name} ({table_clean})"

            specs.append(
                {
                    "table_name": table_clean,
                    "column_name": base_name,
                    "exposed_name": exposed_name,
                    "data_type": column.get("data_type"),
                    "is_nullable": bool(column.get("is_nullable", True)),
                    "ordinal": ordinal,
                }
            )
            ordinal += 1

    return specs


def _upsert_datasource_columns_from_catalog(
    datasource_node: ET.Element,
    catalog_source: dict,
    table_refs: list[str] | None = None,
    prune_existing: bool = True,
) -> None:
    specs = _build_catalog_exposed_column_specs(catalog_source, table_refs)
    if not specs:
        return

    allowed_names = {
        str(spec.get("exposed_name") or "").strip().lower()
        for spec in specs
        if str(spec.get("exposed_name") or "").strip()
    }

    existing_by_name: dict[str, ET.Element] = {}
    for child in [c for c in list(datasource_node) if _local_name(c.tag) == "column"]:
        raw_name = str(child.attrib.get("name", "") or "")
        is_table_object_column = raw_name.strip().lower().startswith("[__tableau_internal_object_id__].")
        clean_name = _clean_bracketed_name(child.attrib.get("name", "")).strip().lower()
        if prune_existing and clean_name and clean_name not in allowed_names and not is_table_object_column:
            datasource_node.remove(child)
            continue
        if clean_name and clean_name not in existing_by_name:
            existing_by_name[clean_name] = child

    for spec in specs:
        exposed_name = str(spec.get("exposed_name") or "").strip()
        base_name = str(spec.get("column_name") or exposed_name).strip()
        if not exposed_name or not base_name:
            continue

        ordinal = spec.get("ordinal")
        index = int(ordinal) if isinstance(ordinal, int) else 0
        role, datatype, ctype = _infer_tableau_type_from_sql(
            column_name=base_name,
            sql_data_type=spec.get("data_type"),
            index=index,
        )

        attrs = {
            "name": f"[{exposed_name}]",
            "role": role,
            "datatype": datatype,
            "type": ctype,
        }

        existing = existing_by_name.get(exposed_name.lower())
        if existing is None:
            _insert_datasource_column(datasource_node, attrs)
            continue

        for key, value in attrs.items():
            existing.attrib[key] = value

    _normalize_datasource_column_order(datasource_node)


def _upsert_cols_map_from_catalog(
    datasource_node: ET.Element,
    connection_node: ET.Element,
    catalog_source: dict,
    table_refs: list[str],
) -> None:
    specs = _build_catalog_exposed_column_specs(catalog_source, table_refs)
    if not specs:
        return

    cols_node = _find_direct_child(connection_node, "cols")
    if cols_node is None:
        legacy_cols = _find_direct_child(datasource_node, "cols")
        if legacy_cols is not None:
            datasource_node.remove(legacy_cols)
            connection_node.append(legacy_cols)
            cols_node = legacy_cols
    if cols_node is None:
        cols_node = ET.SubElement(connection_node, "cols")

    for child in list(cols_node):
        if _local_name(child.tag) == "map":
            cols_node.remove(child)

    for spec in specs:
        table_clean = str(spec.get("table_name") or "").strip()
        base = str(spec.get("column_name") or "").strip()
        exposed = str(spec.get("exposed_name") or "").strip()
        if not table_clean or not base or not exposed:
            continue

        map_key = f"[{exposed}]"
        map_value = f"[{table_clean}].[{base}]"
        ET.SubElement(cols_node, "map", attrib={"key": map_key, "value": map_value})


def _upsert_metadata_records_from_catalog(
    datasource_node: ET.Element,
    connection_node: ET.Element,
    catalog_source: dict,
    table_refs: list[str],
) -> None:
    specs = _build_catalog_exposed_column_specs(catalog_source, table_refs)
    if not specs:
        return

    allowed_local_names = {
        f"[{str(spec.get('exposed_name') or '').strip()}]".lower()
        for spec in specs
        if str(spec.get("exposed_name") or "").strip()
    }

    metadata_node = _find_direct_child(connection_node, "metadata-records")
    if metadata_node is None:
        metadata_node = ET.SubElement(connection_node, "metadata-records")

    existing_by_local_name: dict[str, ET.Element] = {}
    for record in [c for c in list(metadata_node) if _local_name(c.tag) == "metadata-record"]:
        local_name_node = _find_direct_child(record, "local-name")
        local_name = ""
        if local_name_node is not None and isinstance(local_name_node.text, str):
            local_name = local_name_node.text.strip().lower()
        if local_name and local_name not in allowed_local_names:
            metadata_node.remove(record)
            continue
        if local_name and local_name in existing_by_local_name:
            # Keep the first occurrence to avoid duplicated metadata definitions.
            metadata_node.remove(record)
            continue
        if local_name and local_name not in existing_by_local_name:
            existing_by_local_name[local_name] = record

    table_object_ids = _build_table_object_id_lookup_from_object_graph(datasource_node)

    for index, spec in enumerate(specs, start=1):
        table_name = str(spec.get("table_name") or "").strip()
        column_name = str(spec.get("column_name") or "").strip()
        exposed_name = str(spec.get("exposed_name") or "").strip()
        if not table_name or not column_name or not exposed_name:
            continue

        local_name = f"[{exposed_name}]"
        record = existing_by_local_name.get(local_name.lower())
        if record is None:
            record = ET.SubElement(metadata_node, "metadata-record", attrib={"class": "column"})
            existing_by_local_name[local_name.lower()] = record
        else:
            record.attrib["class"] = "column"

        local_type = _metadata_local_type_from_sql(spec.get("data_type"))
        remote_type = _metadata_remote_type_from_sql(spec.get("data_type"))
        object_id = table_object_ids.get(_catalog_table_name_key(table_name), "")

        _upsert_metadata_record_text(record, "remote-name", column_name)
        if remote_type:
            _upsert_metadata_record_text(record, "remote-type", remote_type)
        _upsert_metadata_record_text(record, "local-name", local_name)
        _upsert_metadata_record_text(record, "parent-name", f"[{table_name}]")
        _upsert_metadata_record_text(record, "remote-alias", column_name)
        _upsert_metadata_record_text(record, "ordinal", str(index))
        _upsert_metadata_record_text(record, "local-type", local_type)
        _upsert_metadata_record_text(record, "aggregation", _metadata_default_aggregation(local_type))
        contains_null = "true" if bool(spec.get("is_nullable", True)) else "false"
        _upsert_metadata_record_text(record, "contains-null", contains_null)
        if object_id:
            _upsert_metadata_record_text(record, "object-id", f"[{object_id}]")
        else:
            stale_object_id = _find_direct_child(record, "object-id")
            if stale_object_id is not None:
                record.remove(stale_object_id)


def _build_table_object_id_lookup_from_object_graph(datasource_node: ET.Element) -> dict[str, str]:
    lookup: dict[str, str] = {}

    object_graph = _find_direct_child(datasource_node, "object-graph")
    if object_graph is None:
        return lookup

    objects_node = _find_direct_child(object_graph, "objects")
    if objects_node is None:
        return lookup

    for object_node in [c for c in list(objects_node) if _local_name(c.tag) == "object"]:
        object_id = str(object_node.attrib.get("id", "") or "").strip()
        if not object_id:
            continue

        caption = str(object_node.attrib.get("caption", "") or "").strip()
        if caption:
            lookup.setdefault(_catalog_table_name_key(caption), object_id)

        properties = _find_direct_child(object_node, "properties")
        relation = _find_direct_child(properties, "relation") if properties is not None else None
        if relation is None:
            continue

        relation_name = str(relation.attrib.get("name", "") or "").strip()
        if relation_name:
            lookup.setdefault(_catalog_table_name_key(relation_name), object_id)

        table_ref = str(relation.attrib.get("table", "") or "").strip()
        table_leaf = _table_leaf_from_relation_reference(table_ref)
        if table_leaf:
            lookup.setdefault(_catalog_table_name_key(table_leaf), object_id)

    return lookup


def _table_leaf_from_relation_reference(table_ref: str) -> str:
    tokens = re.findall(r"\[([^\]]+)\]", table_ref or "")
    if tokens:
        return tokens[-1].strip()

    parts = [p.strip() for p in (table_ref or "").split(".") if p.strip()]
    if not parts:
        return ""
    return _clean_sql_identifier(parts[-1])


def _upsert_metadata_record_text(record: ET.Element, local_name: str, value: str) -> None:
    node = _find_direct_child(record, local_name)
    if node is None:
        node = ET.SubElement(record, local_name)
    node.text = value


def _metadata_local_type_from_sql(sql_data_type: object) -> str:
    token = str(sql_data_type or "").strip().lower()
    if token in {"bigint", "int", "smallint", "tinyint"}:
        return "integer"
    if token in {"decimal", "numeric", "float", "real", "money", "smallmoney"}:
        return "real"
    if token in {"bit", "boolean", "bool"}:
        return "boolean"
    if token in {"date"}:
        return "date"
    if token in {"datetime", "datetime2", "smalldatetime", "datetimeoffset", "time"}:
        return "datetime"
    return "string"


def _metadata_default_aggregation(local_type: str) -> str:
    token = str(local_type or "").strip().lower()
    if token in {"integer", "real"}:
        return "Sum"
    if token in {"date", "datetime"}:
        return "Year"
    return "Count"


def _metadata_remote_type_from_sql(sql_data_type: object) -> str:
    token = str(sql_data_type or "").strip().lower()
    mapping = {
        "tinyint": "17",
        "smallint": "2",
        "int": "3",
        "bigint": "-5",
        "bit": "-7",
        "bool": "-7",
        "boolean": "-7",
        "real": "5",
        "float": "5",
        "decimal": "131",
        "numeric": "131",
        "money": "131",
        "smallmoney": "131",
        "date": "7",
        "datetime": "7",
        "datetime2": "7",
        "smalldatetime": "7",
        "datetimeoffset": "7",
        "time": "7",
        "char": "130",
        "nchar": "130",
        "varchar": "130",
        "nvarchar": "130",
        "text": "130",
        "ntext": "130",
        "xml": "130",
        "uniqueidentifier": "130",
        "sysname": "130",
    }
    return mapping.get(token, "")


def _infer_tableau_type_from_sql(
    column_name: str,
    sql_data_type: object,
    index: int,
) -> tuple[str, str, str]:
    token = str(sql_data_type or "").strip().lower()
    name_token = str(column_name or "").strip()

    integer_numeric = {
        "bigint",
        "int",
        "smallint",
        "tinyint",
    }
    real_numeric = {
        "decimal",
        "numeric",
        "float",
        "real",
        "money",
        "smallmoney",
    }
    boolean = {"bit", "boolean", "bool"}
    temporal = {"date", "datetime", "datetime2", "smalldatetime", "time", "datetimeoffset"}
    text_like = {
        "char",
        "nchar",
        "varchar",
        "nvarchar",
        "text",
        "ntext",
        "xml",
        "uniqueidentifier",
        "sql_variant",
        "sysname",
    }

    if token in integer_numeric:
        if _is_key_like_column_name(name_token):
            return "dimension", "integer", "ordinal"
        if _looks_temporal_dimension_name(name_token):
            return "dimension", "integer", "ordinal"
        return "measure", "integer", "quantitative"
    if token in real_numeric:
        if _is_key_like_column_name(name_token):
            return "dimension", "real", "nominal"
        if _looks_temporal_dimension_name(name_token):
            return "dimension", "real", "ordinal"
        return "measure", "real", "quantitative"
    if token in boolean:
        return "dimension", "boolean", "nominal"
    if token in temporal:
        return "dimension", "date", "ordinal"
    if token in text_like:
        if _looks_temporal_dimension_name(name_token):
            return "dimension", "string", "ordinal"
        return "dimension", "string", "nominal"

    # Unknown SQL types are safer as string dimensions than heuristic measure detection.
    return "dimension", "string", "nominal"


def _is_key_like_column_name(column_name: str) -> bool:
    token = (column_name or "").strip().lower()
    if not token:
        return False
    if token.endswith("key") or token.endswith("id"):
        return True
    if token.startswith("key"):
        return True
    return False


def _looks_temporal_dimension_name(column_name: str) -> bool:
    token = (column_name or "").strip().lower()
    if not token:
        return False
    if any(mark in token for mark in ["sales", "amount", "price", "cost", "quota", "qty", "quantity"]):
        return False
    return any(mark in token for mark in ["year", "quarter", "semester", "month", "week", "day", "date"])


def _measure_priority_score(field_name: str) -> int:
    token = (field_name or "").strip().lower()
    score = 100
    if any(mark in token for mark in ["sales", "amount", "revenue", "cost", "price", "quota", "profit", "total"]):
        score -= 70
    if any(mark in token for mark in ["qty", "quantity", "count", "number"]):
        score -= 30

    # De-prioritize technical row-id style numeric fields that are poor visual measures.
    if any(mark in token for mark in ["line number", "linenumber", "orderline", "revisionnumber", "revision number"]):
        score += 180
    elif "number" in token and not any(
        mark in token for mark in ["quantity", "count", "amount", "price", "cost", "quota", "revenue", "profit"]
    ):
        score += 90

    # Percentage/rate discount fields are often technical KPIs; avoid using them as default shelves.
    if any(mark in token for mark in ["discount", "pct", "percent", "ratio", "rate"]):
        score += 140

    if _is_key_like_column_name(token):
        score += 120
    if _looks_temporal_dimension_name(token):
        score += 60
    return score


def _upsert_datasource_columns_from_datasets(
    datasource_node: ET.Element,
    data_sets: list[dict],
    prune_existing: bool = True,
) -> None:
    field_specs = _collect_dataset_field_specs(data_sets)
    if not field_specs:
        return

    allowed: dict[str, dict[str, str | None]] = {}
    for spec in field_specs:
        name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        clean = name.strip()
        if not clean:
            continue
        key = clean.lower()
        if key not in allowed:
            allowed[key] = spec

    existing_by_name: dict[str, ET.Element] = {}
    for child in [c for c in list(datasource_node) if _local_name(c.tag) == "column"]:
        clean_name = _clean_bracketed_name(child.attrib.get("name", "")).strip().lower()
        if prune_existing and (not clean_name or clean_name not in allowed):
            datasource_node.remove(child)
            continue
        if not clean_name:
            continue
        if clean_name not in existing_by_name:
            existing_by_name[clean_name] = child

    created: list[ET.Element] = []
    ordered_specs = field_specs[:80]
    for i, spec in enumerate(ordered_specs):
        name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        clean_name = name.strip()
        if not clean_name:
            continue
        key = clean_name.lower()
        canonical_name = allowed[key].get("name") if isinstance(allowed[key].get("name"), str) else clean_name
        caption = allowed[key].get("caption") if isinstance(allowed[key].get("caption"), str) else ""

        existing = existing_by_name.get(key)
        if existing is not None:
            existing.attrib["name"] = f"[{canonical_name}]"
            if caption.strip():
                existing.attrib["caption"] = caption.strip()
            elif "caption" in existing.attrib:
                del existing.attrib["caption"]
            created.append(existing)
            continue

        role, dtype, ctype = _infer_tableau_column_spec(name=canonical_name, index=i)
        attrs = {
            "name": f"[{canonical_name}]",
            "role": role,
            "datatype": dtype,
            "type": ctype,
        }
        if caption.strip():
            attrs["caption"] = caption.strip()
        created.append(_insert_datasource_column(datasource_node, attrs))

    has_measure = any((c.attrib.get("role") or "").lower() == "measure" for c in created)
    if not has_measure and len(created) > 1:
        created[1].attrib["role"] = "measure"
        created[1].attrib["datatype"] = "real"
        created[1].attrib["type"] = "quantitative"

    _normalize_datasource_column_order(datasource_node)


def _columns_look_placeholder(columns: list[ET.Element]) -> bool:
    names: list[str] = []
    for col in columns:
        clean = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        if clean:
            names.append(clean)
    if not names:
        return True
    return all(re.fullmatch(r"field\d+", name) for name in names)


def _infer_tableau_column_spec(name: str, index: int) -> tuple[str, str, str]:
    token = (name or "").strip().lower()
    looks_measure = any(
        marker in token
        for marker in [
            "amount",
            "total",
            "sum",
            "sales",
            "revenue",
            "cost",
            "price",
            "qty",
            "quantity",
            "count",
            "num",
            "number",
            "rate",
            "score",
            "percent",
            "pct",
        ]
    )

    if _looks_temporal_dimension_name(token):
        return "dimension", "string", "ordinal"
    if index == 0 or not looks_measure:
        return "dimension", "string", "nominal"
    return "measure", "real", "quantitative"


def inject_semantic_bindings(
    xml_content: str,
    data_model: dict,
    visual_model: dict,
    mapping: dict,
    report_parameters: list[dict] | None = None,
) -> str:
    """Align TWB workbook bindings with semantic models derived from RDL."""
    root = ET.fromstring(xml_content)
    _strip_namespaces(root)

    datasources = _find_direct_child(root, "datasources")
    if datasources is None:
        return ET.tostring(root, encoding="unicode")

    datasource_nodes = [c for c in list(datasources) if _local_name(c.tag) == "datasource"]
    if not datasource_nodes:
        return ET.tostring(root, encoding="unicode")

    default_ds_node = datasource_nodes[0]
    default_ds_name = default_ds_node.attrib.get("name", "DataSource_1")

    datasets = data_model.get("datasets", []) if isinstance(data_model, dict) else []
    if not isinstance(datasets, list):
        datasets = []

    dataset_to_datasource = _build_dataset_to_datasource_map(datasets)
    dataset_to_fields = _build_dataset_to_fields_map(data_model)
    dataset_alias_to_source, dataset_source_to_alias = _build_dataset_projection_maps(datasets)
    dataset_to_fields = _canonicalize_dataset_fields(dataset_to_fields, dataset_alias_to_source)

    first_query = datasets[0].get("query") if datasets and isinstance(datasets[0], dict) else None
    conn = _find_direct_child(default_ds_node, "connection")
    if conn is not None and isinstance(first_query, str) and first_query.strip() and _find_direct_child(conn, "relation") is None:
        rel = ET.SubElement(conn, "relation", attrib={"name": "DataSet1", "type": "text"})
        rel.text = _sanitize_relation_sql(first_query)

    field_names = _collect_global_field_names(data_model)
    field_names = _canonicalize_global_fields(field_names, dataset_alias_to_source)
    if not field_names:
        for scoped in dataset_to_fields.values():
            for field_name in scoped:
                if field_name not in field_names:
                    field_names.append(field_name)
    if not field_names:
        field_names = ["Field1", "Field2"]

    datasource_names = {
        ds.attrib.get("name", "").strip()
        for ds in datasource_nodes
        if isinstance(ds.attrib.get("name"), str) and ds.attrib.get("name", "").strip()
    }

    for ds_node in datasource_nodes:
        ds_name = ds_node.attrib.get("name", "").strip() or default_ds_name
        scoped_fields = _collect_scoped_field_names(ds_name, dataset_to_datasource, dataset_to_fields)
        if scoped_fields:
            _upsert_datasource_columns_from_datasets(
                ds_node,
                [{"fields": [{"name": f} for f in scoped_fields]}],
                prune_existing=False,
            )

        if _find_direct_child(ds_node, "layout") is None:
            ET.SubElement(
                ds_node,
                "layout",
                attrib={
                    "dim-ordering": "alphabetic",
                    "measure-ordering": "alphabetic",
                    "dim-percentage": "0.50",
                    "measure-percentage": "0.50",
                    "show-structure": "true",
                },
            )
        if _find_direct_child(ds_node, "style") is None:
            ET.SubElement(ds_node, "style")

    sheet_specs = _collect_sheet_specs(visual_model, mapping)
    sheet_specs = _expand_sheet_specs_for_regional_sales(sheet_specs, visual_model)
    normalized_report_parameters = report_parameters if isinstance(report_parameters, list) else []
    if _is_regionalsales_report(visual_model):
        _inject_regionalsales_calculated_fields(datasource_nodes, normalized_report_parameters)
    _inject_expression_measure_calculated_fields(
        datasource_nodes=datasource_nodes,
        sheet_specs=sheet_specs,
        dataset_to_datasource=dataset_to_datasource,
        dataset_alias_to_source=dataset_alias_to_source,
        default_ds_name=default_ds_name,
    )
    _inject_static_text_calculated_fields(datasource_nodes, sheet_specs, default_ds_name)

    datasource_field_roles = _build_datasource_field_role_lookup(datasource_nodes)
    datasource_field_tables = _build_datasource_field_table_lookup(datasource_nodes)
    datasource_field_names = _build_datasource_field_names_lookup(datasource_nodes)
    datasource_field_metadata = _build_datasource_field_metadata_lookup(datasource_nodes)
    parameter_filter_specs = _build_parameter_filter_specs(
        datasets=datasets,
        mapping=mapping,
        report_parameters=normalized_report_parameters,
    )
    _rebuild_worksheets_from_sheet_specs(
        root=root,
        sheet_specs=sheet_specs,
        datasource_names=datasource_names,
        default_ds_name=default_ds_name,
        dataset_to_datasource=dataset_to_datasource,
        dataset_to_fields=dataset_to_fields,
        dataset_alias_to_source=dataset_alias_to_source,
        dataset_source_to_alias=dataset_source_to_alias,
        global_field_names=field_names,
        datasource_field_roles=datasource_field_roles,
        datasource_field_tables=datasource_field_tables,
        datasource_field_names=datasource_field_names,
    )

    if parameter_filter_specs:
        worksheet_dataset_by_name = {
            str(spec.get("name", "")).strip().lower(): str(spec.get("dataset_name", "")).strip()
            for spec in sheet_specs
            if isinstance(spec, dict) and isinstance(spec.get("name"), str)
        }
        _apply_parameter_filters_to_worksheets(
            root=root,
            filter_specs=parameter_filter_specs,
            datasource_field_roles=datasource_field_roles,
            datasource_field_metadata=datasource_field_metadata,
            datasource_field_names=datasource_field_names,
            worksheet_dataset_by_name=worksheet_dataset_by_name,
            default_ds_name=default_ds_name,
        )

    required_fields = _collect_fields_used_by_workbook_views(root)
    if required_fields:
        for ds_node in datasource_nodes:
            _prune_datasource_fields_to_usage(ds_node, required_fields)

    _rebuild_dashboards_from_sheet_specs(root, sheet_specs, visual_model, sorted(datasource_names))

    return ET.tostring(root, encoding="unicode")


def _build_dataset_to_datasource_map(datasets: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in datasets:
        if not isinstance(item, dict):
            continue
        dataset_name = item.get("name")
        datasource_name = item.get("data_source_name")
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            continue
        if not isinstance(datasource_name, str) or not datasource_name.strip():
            continue
        mapping[dataset_name.strip()] = datasource_name.strip()
    return mapping


def _build_dataset_to_fields_map(data_model: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    fields = data_model.get("fields", []) if isinstance(data_model, dict) else []
    if not isinstance(fields, list):
        return result

    for item in fields:
        if not isinstance(item, dict):
            continue
        dataset_name = item.get("dataset_name")
        field_name = item.get("name") or item.get("data_field")
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            continue
        if not isinstance(field_name, str) or not field_name.strip():
            continue
        bucket = result.setdefault(dataset_name.strip(), [])
        clean_field = field_name.strip()
        if clean_field not in bucket:
            bucket.append(clean_field)

    return result


def _build_dataset_projection_maps(datasets: list[dict]) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    alias_to_source: dict[str, dict[str, str]] = {}
    source_to_alias: dict[str, dict[str, str]] = {}

    for item in datasets:
        if not isinstance(item, dict):
            continue
        dataset_name = item.get("name")
        query = item.get("query")
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            continue
        if not isinstance(query, str) or not query.strip():
            continue

        ds_name = dataset_name.strip()
        ds_alias_map = alias_to_source.setdefault(ds_name, {})
        ds_source_map = source_to_alias.setdefault(ds_name, {})

        for alias_lower, spec in _extract_select_projection_specs(query).items():
            source_field = spec.get("source_field") if isinstance(spec, dict) else None
            alias_name = spec.get("alias") if isinstance(spec, dict) else None
            if not isinstance(source_field, str) or not source_field.strip():
                continue
            canonical = source_field.strip()
            ds_alias_map[alias_lower] = canonical

            if isinstance(alias_name, str) and alias_name.strip():
                ds_source_map.setdefault(canonical.lower(), alias_name.strip())

    return alias_to_source, source_to_alias


def _canonicalize_dataset_fields(
    dataset_to_fields: dict[str, list[str]],
    dataset_alias_to_source: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    canonicalized: dict[str, list[str]] = {}

    for dataset_name, fields in dataset_to_fields.items():
        alias_map = dataset_alias_to_source.get(dataset_name, {})
        normalized: list[str] = []
        for field_name in fields:
            if not isinstance(field_name, str) or not field_name.strip():
                continue
            clean = field_name.strip()
            canonical = alias_map.get(clean.lower(), clean)
            if canonical not in normalized:
                normalized.append(canonical)
        canonicalized[dataset_name] = normalized

    return canonicalized


def _canonicalize_global_fields(
    field_names: list[str],
    dataset_alias_to_source: dict[str, dict[str, str]],
) -> list[str]:
    merged_alias_map: dict[str, str] = {}
    for alias_map in dataset_alias_to_source.values():
        for alias_lower, source_name in alias_map.items():
            if alias_lower not in merged_alias_map:
                merged_alias_map[alias_lower] = source_name

    normalized: list[str] = []
    for field_name in field_names:
        if not isinstance(field_name, str) or not field_name.strip():
            continue
        clean = field_name.strip()
        canonical = merged_alias_map.get(clean.lower(), clean)
        if canonical not in normalized:
            normalized.append(canonical)

    return normalized


def _collect_global_field_names(data_model: dict) -> list[str]:
    raw_fields = data_model.get("fields", []) if isinstance(data_model, dict) else []
    names: list[str] = []
    if not isinstance(raw_fields, list):
        return names

    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("data_field")
        if isinstance(name, str):
            clean = name.strip()
            if clean and clean not in names:
                names.append(clean)

    return names


def _collect_scoped_field_names(
    datasource_name: str,
    dataset_to_datasource: dict[str, str],
    dataset_to_fields: dict[str, list[str]],
) -> list[str]:
    collected: list[str] = []
    target = (datasource_name or "").strip().lower()
    if not target:
        return collected

    for dataset_name, ds_name in dataset_to_datasource.items():
        if ds_name.strip().lower() != target:
            continue
        for field_name in dataset_to_fields.get(dataset_name, []):
            if field_name not in collected:
                collected.append(field_name)

    return collected


def _upsert_datasource_calculated_column(
    datasource_node: ET.Element,
    field_name: str,
    role: str,
    datatype: str,
    type_value: str,
    formula: str,
    caption: str,
) -> None:
    if not isinstance(datasource_node, ET.Element):
        return
    clean_field = (field_name or "").strip()
    clean_formula = (formula or "").strip()
    if not clean_field or not clean_formula:
        return

    target = None
    for col in [c for c in list(datasource_node) if _local_name(c.tag) == "column"]:
        name = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        if name == clean_field.lower():
            target = col
            break

    if target is None:
        target = ET.SubElement(datasource_node, "column")

    target.attrib["name"] = f"[{clean_field}]"
    target.attrib["role"] = role
    target.attrib["datatype"] = datatype
    target.attrib["type"] = type_value
    target.attrib["caption"] = caption or clean_field

    calc = _find_direct_child(target, "calculation")
    if calc is None:
        calc = ET.SubElement(target, "calculation")
    calc.attrib["class"] = "tableau"
    calc.attrib["formula"] = clean_formula


def _extract_report_parameter_default(report_parameters: list[dict], parameter_name: str) -> str:
    target = (parameter_name or "").strip().lower()
    if not target:
        return ""

    for item in report_parameters:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name.strip().lower() != target:
            continue
        defaults = item.get("default_values")
        if not isinstance(defaults, list):
            return ""
        for raw in defaults:
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""

    return ""


def _collect_spec_expression_strings(spec: dict) -> list[str]:
    expressions: list[str] = []

    def _append(raw: object) -> None:
        if not isinstance(raw, str) or not raw.strip():
            return
        clean = raw.strip()
        if clean not in expressions:
            expressions.append(clean)

    if not isinstance(spec, dict):
        return expressions

    for raw in spec.get("expressions") if isinstance(spec.get("expressions"), list) else []:
        _append(raw)

    for raw in spec.get("static_text_values") if isinstance(spec.get("static_text_values"), list) else []:
        _append(raw)

    properties = spec.get("properties") if isinstance(spec.get("properties"), dict) else {}
    for raw in properties.get("text_values") if isinstance(properties.get("text_values"), list) else []:
        _append(raw)

    gauge = properties.get("gauge") if isinstance(properties.get("gauge"), dict) else {}
    for raw in gauge.get("value_expressions") if isinstance(gauge.get("value_expressions"), list) else []:
        _append(raw)

    return expressions


def _extract_sum_field_calls(expression: str) -> list[str]:
    fields: list[str] = []
    if not isinstance(expression, str):
        return fields

    for match in SUM_FIELD_CALL_RE.finditer(expression):
        field_name = match.group("field").strip()
        if field_name and field_name not in fields:
            fields.append(field_name)

    return fields


def _extract_sum_difference_pair(expressions: list[str]) -> tuple[str, str]:
    for expr in expressions if isinstance(expressions, list) else []:
        if not isinstance(expr, str):
            continue
        match = SUM_DIFF_EXPR_RE.search(expr)
        if match is None:
            continue
        left = match.group("left").strip()
        right = match.group("right").strip()
        if left and right and left.lower() != right.lower():
            return left, right

    return "", ""


def _extract_sum_difference_ratio_pair(expressions: list[str]) -> tuple[str, str]:
    for expr in expressions if isinstance(expressions, list) else []:
        if not isinstance(expr, str):
            continue
        match = SUM_DIFF_RATIO_EXPR_RE.search(expr)
        if match is None:
            continue
        left = match.group("left").strip()
        right = match.group("right").strip()
        denominator = match.group("denominator").strip()
        if left and right and denominator and right.lower() == denominator.lower():
            return left, right

    return "", ""


def _extract_sum_ratio_pair(expressions: list[str]) -> tuple[str, str]:
    for expr in expressions if isinstance(expressions, list) else []:
        if not isinstance(expr, str):
            continue
        match = SUM_RATIO_EXPR_RE.search(expr)
        if match is None:
            continue
        left = match.group("left").strip()
        right = match.group("right").strip()
        if left and right and left.lower() != right.lower():
            return left, right

    return "", ""


def _extract_gauge_measure_pair(spec: dict) -> tuple[str, str]:
    properties = spec.get("properties") if isinstance(spec, dict) and isinstance(spec.get("properties"), dict) else {}
    gauge = properties.get("gauge") if isinstance(properties.get("gauge"), dict) else {}
    value_expressions = gauge.get("value_expressions") if isinstance(gauge.get("value_expressions"), list) else []
    measures: list[str] = []
    for expr in value_expressions:
        if not isinstance(expr, str):
            continue
        refs = _extract_sum_field_calls(expr)
        if len(refs) == 1 and refs[0] not in measures:
            measures.append(refs[0])
    if len(measures) >= 2:
        return measures[0], measures[1]

    expressions = _collect_spec_expression_strings(spec)
    left, right = _extract_sum_ratio_pair(expressions)
    if left and right:
        return left, right

    return _extract_sum_difference_pair(expressions)


def _field_pascal_name(field_name: str) -> str:
    tokens = _field_name_tokens(field_name)
    if not tokens:
        return ""
    return "".join(token[:1].upper() + token[1:] for token in tokens if token)


def _is_target_like_measure_name(field_name: str) -> bool:
    token = (field_name or "").strip().lower()
    return any(mark in token for mark in ["quota", "target", "budget", "goal", "plan"])


def _derived_variance_field_name(left_field: str, right_field: str) -> str:
    left_token = _field_pascal_name(left_field) or "Measure"
    right_token = _field_pascal_name(right_field) or "Reference"
    if _is_target_like_measure_name(right_field):
        return f"{left_token}Variance"
    return f"{left_token}Minus{right_token}"


def _derived_variance_pct_field_name(left_field: str, right_field: str) -> str:
    return f"{_derived_variance_field_name(left_field, right_field)}Pct"


def _derived_progress_pct_field_name(left_field: str, right_field: str) -> str:
    left_token = _field_pascal_name(left_field) or "Measure"
    right_token = _field_pascal_name(right_field) or "Reference"
    return f"{left_token}{right_token}Pct"


def _resolve_expression_source_field(
    raw_field: str,
    alias_to_source: dict[str, str],
    available_fields: list[str],
) -> str:
    clean = (raw_field or "").strip()
    if not clean:
        return ""

    canonical = alias_to_source.get(clean.lower(), clean) if isinstance(alias_to_source, dict) else clean
    available_lookup = {
        field.strip().lower(): field.strip()
        for field in available_fields
        if isinstance(field, str) and field.strip()
    }
    if canonical.lower() in available_lookup:
        return available_lookup[canonical.lower()]
    if clean.lower() in available_lookup:
        return available_lookup[clean.lower()]

    fuzzy = _find_best_field_match(canonical, list(available_lookup.values()))
    return fuzzy or ""


def _upsert_measure_pair_calculated_fields(
    datasource_node: ET.Element,
    left_raw_field: str,
    right_raw_field: str,
    alias_to_source: dict[str, str],
    create_variance: bool = True,
    create_variance_pct: bool = True,
    create_progress_pct: bool = True,
) -> None:
    if not isinstance(datasource_node, ET.Element):
        return

    available_fields = [
        _clean_bracketed_name(col.attrib.get("name", "")).strip()
        for col in list(datasource_node)
        if _local_name(col.tag) == "column"
    ]
    left_source = _resolve_expression_source_field(left_raw_field, alias_to_source, available_fields)
    right_source = _resolve_expression_source_field(right_raw_field, alias_to_source, available_fields)
    if not left_source or not right_source or left_source.lower() == right_source.lower():
        return

    left_ref = f"[{left_source}]"
    right_ref = f"[{right_source}]"
    variance_name = _derived_variance_field_name(left_raw_field, right_raw_field)
    variance_pct_name = _derived_variance_pct_field_name(left_raw_field, right_raw_field)
    progress_pct_name = _derived_progress_pct_field_name(left_raw_field, right_raw_field)

    if create_variance:
        _upsert_datasource_calculated_column(
            datasource_node=datasource_node,
            field_name=variance_name,
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula=f"ZN({left_ref}) - ZN({right_ref})",
            caption="Variance" if _is_target_like_measure_name(right_raw_field) else f"{left_raw_field} - {right_raw_field}",
        )

    if create_variance_pct:
        _upsert_datasource_calculated_column(
            datasource_node=datasource_node,
            field_name=variance_pct_name,
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula=(
                f"IF ZN(SUM({right_ref})) = 0 THEN 0 "
                f"ELSE (ZN(SUM({left_ref})) - ZN(SUM({right_ref}))) / ZN(SUM({right_ref})) END"
            ),
            caption="Variance %" if _is_target_like_measure_name(right_raw_field) else f"{left_raw_field} - {right_raw_field} %",
        )

    if create_progress_pct:
        _upsert_datasource_calculated_column(
            datasource_node=datasource_node,
            field_name=progress_pct_name,
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula=(
                f"IF ZN(SUM({right_ref})) = 0 THEN 0 "
                f"ELSE ZN(SUM({left_ref})) / ZN(SUM({right_ref})) END"
            ),
            caption=f"{left_raw_field} / {right_raw_field}",
        )


def _inject_expression_measure_calculated_fields(
    datasource_nodes: list[ET.Element],
    sheet_specs: list[dict],
    dataset_to_datasource: dict[str, str],
    dataset_alias_to_source: dict[str, dict[str, str]],
    default_ds_name: str,
) -> None:
    if not isinstance(datasource_nodes, list) or not datasource_nodes:
        return

    datasource_by_name = {
        node.attrib.get("name", "").strip().lower(): node
        for node in datasource_nodes
        if isinstance(node, ET.Element) and isinstance(node.attrib.get("name"), str)
    }
    default_node = datasource_by_name.get((default_ds_name or "").strip().lower()) or datasource_nodes[0]
    processed: set[tuple[str, str, str]] = set()

    for spec in sheet_specs if isinstance(sheet_specs, list) else []:
        if not isinstance(spec, dict):
            continue
        dataset_name = spec.get("dataset_name") if isinstance(spec.get("dataset_name"), str) else ""
        datasource_name = dataset_to_datasource.get(dataset_name, "") if dataset_name else ""
        datasource_node = datasource_by_name.get(datasource_name.strip().lower()) if datasource_name else None
        if datasource_node is None:
            datasource_node = default_node

        ds_key = datasource_node.attrib.get("name", "").strip().lower()
        alias_to_source = dataset_alias_to_source.get(dataset_name, {}) if dataset_name else {}
        expressions = _collect_spec_expression_strings(spec)
        pairs: list[tuple[str, str, bool, bool, bool]] = []

        left, right = _extract_sum_difference_pair(expressions)
        if left and right:
            pairs.append((left, right, True, True, True))

        gauge_left, gauge_right = _extract_gauge_measure_pair(spec)
        if gauge_left and gauge_right:
            pairs.append((gauge_left, gauge_right, False, False, True))

        for left_raw, right_raw, create_variance, create_variance_pct, create_progress_pct in pairs:
            key = (ds_key, left_raw.strip().lower(), right_raw.strip().lower())
            if key in processed and not create_variance:
                continue
            processed.add(key)
            _upsert_measure_pair_calculated_fields(
                datasource_node=datasource_node,
                left_raw_field=left_raw,
                right_raw_field=right_raw,
                alias_to_source=alias_to_source,
                create_variance=create_variance,
                create_variance_pct=create_variance_pct,
                create_progress_pct=create_progress_pct,
            )


def _resolve_expression_measure_field_for_spec(
    spec: dict,
    semantic_kind: str,
    candidate_pool: list[str],
    field_roles: dict[str, str],
) -> str:
    if not isinstance(spec, dict):
        return ""

    def _available_measure(field_name: str) -> str:
        clean = (field_name or "").strip()
        if not clean:
            return ""
        if field_roles.get(clean.lower(), "") == "measure":
            return clean
        match = _find_best_field_match(clean, candidate_pool)
        if match and field_roles.get(match.lower(), "") == "measure":
            return match
        return ""

    expressions = _collect_spec_expression_strings(spec)
    kind = (semantic_kind or "").strip().lower()

    if kind == "gauge":
        left, right = _extract_gauge_measure_pair(spec)
        if left and right:
            resolved = _available_measure(_derived_progress_pct_field_name(left, right))
            if resolved:
                return resolved

    left, right = _extract_sum_difference_ratio_pair(expressions)
    if left and right:
        resolved = _available_measure(_derived_variance_pct_field_name(left, right))
        if resolved:
            return resolved

    left, right = _extract_sum_difference_pair(expressions)
    if left and right:
        resolved = _available_measure(_derived_variance_field_name(left, right))
        if resolved:
            return resolved

    left, right = _extract_sum_ratio_pair(expressions)
    if left and right:
        resolved = _available_measure(_derived_progress_pct_field_name(left, right))
        if resolved:
            return resolved

    return ""


def _select_context_dimension_field(candidate_pool: list[str], field_roles: dict[str, str]) -> str:
    dimensions = [
        field_name.strip()
        for field_name in candidate_pool
        if isinstance(field_name, str)
        and field_name.strip()
        and field_roles.get(field_name.strip().lower(), "") == "dimension"
        and not _is_key_like_column_name(field_name)
    ]
    if not dimensions:
        dimensions = [
            field_name.strip()
            for field_name in candidate_pool
            if isinstance(field_name, str)
            and field_name.strip()
            and field_roles.get(field_name.strip().lower(), "") == "dimension"
        ]

    for field_name in dimensions:
        if _looks_geographic_dimension_name(field_name):
            return field_name
    for field_name in dimensions:
        if not _looks_temporal_dimension_name(field_name):
            return field_name
    return dimensions[0] if dimensions else ""


def _static_text_field_name(worksheet_name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", (worksheet_name or "").strip()).strip("_")
    if not clean:
        clean = "StaticText"
    return f"__text_{clean}"


def _tableau_string_literal(value: str) -> str:
    text = value if isinstance(value, str) else ""
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


def _datasource_field_metadata(datasource_node: ET.Element, field_name: str) -> dict[str, str]:
    clean = (field_name or "").strip().lower()
    if not clean or not isinstance(datasource_node, ET.Element):
        return {}
    for col in [c for c in list(datasource_node) if _local_name(c.tag) == "column"]:
        name = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        if name != clean:
            continue
        return {
            "datatype": (col.attrib.get("datatype") or "").strip().lower(),
            "type": (col.attrib.get("type") or "").strip().lower(),
            "role": (col.attrib.get("role") or "").strip().lower(),
        }
    return {}


def _field_text_formula_reference(field_name: str, datasource_node: ET.Element) -> str:
    clean = (field_name or "").strip()
    if not clean:
        return '""'
    metadata = _datasource_field_metadata(datasource_node, clean)
    datatype = metadata.get("datatype", "")
    type_value = metadata.get("type", "")
    if _is_numeric_filter_datatype(datatype) or datatype in {"date", "datetime", "boolean"} or type_value == "quantitative":
        return f"STR([{clean}])"
    return f"[{clean}]"


def _is_tableau_string_literal_part(part: str) -> bool:
    return isinstance(part, str) and len(part) >= 2 and part.startswith('"') and part.endswith('"')


def _tableau_string_literal_content(part: str) -> str:
    if not _is_tableau_string_literal_part(part):
        return ""
    return part[1:-1].replace('\\"', '"')


def _append_text_formula_literal(parts: list[str], text: str) -> None:
    if not isinstance(text, str) or not text:
        return
    if parts and _is_tableau_string_literal_part(parts[-1]):
        merged = _tableau_string_literal_content(parts[-1]) + text
        parts[-1] = _tableau_string_literal(merged)
    else:
        parts.append(_tableau_string_literal(text))


def _append_text_formula_dynamic(parts: list[str], formula: str) -> None:
    clean_formula = (formula or "").strip()
    if not clean_formula:
        return
    if parts and _is_tableau_string_literal_part(parts[-1]):
        previous = _tableau_string_literal_content(parts[-1])
        if previous.endswith(":"):
            parts[-1] = _tableau_string_literal(f"{previous} ")
    parts.append(clean_formula)


def _build_text_values_tableau_formula(text_values: list, datasource_node: ET.Element) -> str:
    parts: list[str] = []
    for raw in text_values if isinstance(text_values, list) else []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw
        stripped = text.strip()
        if stripped.startswith("="):
            expr = stripped[1:].strip()
            param_match = PARAM_EXPR_REF_RE.fullmatch(expr)
            field_match = FIELD_EXPR_REF_RE.fullmatch(expr)
            global_match = re.fullmatch(r"Globals!([A-Za-z0-9_]+)", expr, flags=re.IGNORECASE)
            if param_match is not None:
                _append_text_formula_dynamic(parts, _field_text_formula_reference(param_match.group(1), datasource_node))
            elif field_match is not None:
                _append_text_formula_dynamic(parts, _field_text_formula_reference(field_match.group(1), datasource_node))
            elif global_match is not None:
                global_name = global_match.group(1).strip().lower()
                if global_name == "executiontime":
                    _append_text_formula_dynamic(parts, "STR(NOW())")
                elif global_name in {"pagenumber", "totalpages"}:
                    _append_text_formula_literal(parts, "1")
            continue

        _append_text_formula_literal(parts, text)

    return " + ".join(part for part in parts if part)


def _inject_static_text_calculated_fields(
    datasource_nodes: list[ET.Element],
    sheet_specs: list[dict],
    default_ds_name: str,
) -> None:
    if not isinstance(datasource_nodes, list) or not datasource_nodes:
        return

    datasource_by_name = {
        node.attrib.get("name", "").strip().lower(): node
        for node in datasource_nodes
        if isinstance(node, ET.Element) and isinstance(node.attrib.get("name"), str)
    }
    default_node = datasource_by_name.get((default_ds_name or "").strip().lower()) or datasource_nodes[0]

    for spec in sheet_specs if isinstance(sheet_specs, list) else []:
        if not isinstance(spec, dict):
            continue
        static_text = spec.get("static_text") if isinstance(spec.get("static_text"), str) else ""
        name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        if not static_text.strip() or not name.strip():
            continue

        ds_name = spec.get("datasource_name") if isinstance(spec.get("datasource_name"), str) else ""
        datasource_node = datasource_by_name.get(ds_name.strip().lower()) if ds_name.strip() else None
        if datasource_node is None:
            datasource_node = default_node
        text_values = spec.get("static_text_values") if isinstance(spec.get("static_text_values"), list) else []
        formula = _build_text_values_tableau_formula(text_values, datasource_node) or _tableau_string_literal(static_text)

        _upsert_datasource_calculated_column(
            datasource_node=datasource_node,
            field_name=_static_text_field_name(name),
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula=formula,
            caption=static_text[:80] or name,
        )


def _inject_regionalsales_calculated_fields(
    datasource_nodes: list[ET.Element],
    report_parameters: list[dict] | None = None,
) -> None:
    if not isinstance(datasource_nodes, list):
        return

    _ = report_parameters
    group_literal = "All"

    for ds_node in datasource_nodes:
        if not isinstance(ds_node, ET.Element):
            continue

        available_fields = {
            _clean_bracketed_name(col.attrib.get("name", "")).strip()
            for col in list(ds_node)
            if _local_name(col.tag) == "column"
        }

        group_field = ""
        if "SalesTerritoryGroup" in available_fields:
            group_field = "SalesTerritoryGroup"
        elif "SalesTerritoryRegion" in available_fields:
            group_field = "SalesTerritoryRegion"

        year_field = ""
        if "CalendarYear" in available_fields:
            year_field = "CalendarYear"
        elif "DateKey" in available_fields:
            year_field = "DateKey"
        elif "MonthKey" in available_fields:
            year_field = "MonthKey"

        if year_field == "CalendarYear":
            year_expr = "(IF MIN([CalendarYear]) = MAX([CalendarYear]) THEN STR(INT(MIN([CalendarYear]))) ELSE \"All\" END)"
        elif year_field:
            year_expr = (
                f"(IF MIN([{year_field}]) = MAX([{year_field}]) "
                f"THEN STR(INT(MIN([{year_field}]) / 10000)) ELSE \"All\" END)"
            )
        else:
            year_expr = '"All"'

        if group_field:
            group_expr = (
                f"(IF MIN([{group_field}]) = MAX([{group_field}]) "
                f"THEN MIN([{group_field}]) ELSE \"{group_literal}\" END)"
            )
        else:
            group_expr = f'"{group_literal}"'

        subtitle_formula = f'"Year: " + {year_expr} + " | Group: " + {group_expr}'

        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="SalesVariance",
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula="ZN([SalesAmount]) - ZN([SalesAmountQuota])",
            caption="Variance",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="SalesVariancePct",
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula=(
                "IF ZN(SUM([SalesAmountQuota])) = 0 THEN 0 "
                "ELSE (ZN(SUM([SalesAmount])) - ZN(SUM([SalesAmountQuota]))) / ZN(SUM([SalesAmountQuota])) END"
            ),
            caption="Variance %",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="SalesQuotaPct",
            role="measure",
            datatype="real",
            type_value="quantitative",
            formula=(
                "IF ZN(SUM([SalesAmountQuota])) = 0 THEN 0 "
                "ELSE ZN(SUM([SalesAmount])) / ZN(SUM([SalesAmountQuota])) END"
            ),
            caption="Quota %",
        )

        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="ReportLogoLabel",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"ID16313774731791"',
            caption="ReportLogo",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="TitleTileText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"#0d7b3d"',
            caption="TitleTile",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="ReportTitleText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"Regional Sales"',
            caption="ReportTitle",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="ReportSubtitleText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula=subtitle_formula,
            caption="ReportSubtitle",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="KPIHeaderText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"ORDERS     SALES     QUOTA     VARIANCE"',
            caption="KPIHeaders",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="FooterRunAtText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"Run at " + STR(NOW())',
            caption="FooterRunAt",
        )
        _upsert_datasource_calculated_column(
            datasource_node=ds_node,
            field_name="FooterPageText",
            role="dimension",
            datatype="string",
            type_value="nominal",
            formula='"Page 1 of 1"',
            caption="FooterPage",
        )

        month_name_field = ""
        if "EnglishMonthName" in available_fields:
            month_name_field = "EnglishMonthName"
        elif "Month" in available_fields:
            month_name_field = "Month"

        if month_name_field:
            month_sort_formula = (
                f"CASE LOWER([{month_name_field}]) "
                'WHEN "january" THEN 1 '
                'WHEN "february" THEN 2 '
                'WHEN "march" THEN 3 '
                'WHEN "april" THEN 4 '
                'WHEN "may" THEN 5 '
                'WHEN "june" THEN 6 '
                'WHEN "july" THEN 7 '
                'WHEN "august" THEN 8 '
                'WHEN "september" THEN 9 '
                'WHEN "october" THEN 10 '
                'WHEN "november" THEN 11 '
                'WHEN "december" THEN 12 '
                "ELSE 99 END"
            )
            _upsert_datasource_calculated_column(
                datasource_node=ds_node,
                field_name="MonthSortOrder",
                role="dimension",
                datatype="real",
                type_value="ordinal",
                formula=month_sort_formula,
                caption="MonthSortOrder",
            )


def _build_datasource_field_role_lookup(datasource_nodes: list[ET.Element]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for ds_node in datasource_nodes:
        ds_name = ds_node.attrib.get("name", "") if isinstance(ds_node, ET.Element) else ""
        if not isinstance(ds_name, str) or not ds_name.strip():
            continue

        ds_key = ds_name.strip().lower()
        role_map = lookup.setdefault(ds_key, {})

        for col in [c for c in list(ds_node) if _local_name(c.tag) == "column"]:
            field_name = _clean_bracketed_name(col.attrib.get("name", "")).strip()
            role = (col.attrib.get("role") or "").strip().lower()
            if not field_name or role not in {"dimension", "measure"}:
                continue
            role_map.setdefault(field_name.lower(), role)

    return lookup


def _build_datasource_field_table_lookup(datasource_nodes: list[ET.Element]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for ds_node in datasource_nodes:
        ds_name = ds_node.attrib.get("name", "") if isinstance(ds_node, ET.Element) else ""
        if not isinstance(ds_name, str) or not ds_name.strip():
            continue

        ds_key = ds_name.strip().lower()
        table_map = lookup.setdefault(ds_key, {})

        conn = _find_direct_child(ds_node, "connection")
        if conn is None:
            continue
        cols = _find_direct_child(conn, "cols")
        if cols is None:
            continue

        for map_node in [c for c in list(cols) if _local_name(c.tag) == "map"]:
            key_raw = map_node.attrib.get("key", "")
            val_raw = map_node.attrib.get("value", "")
            key = _clean_bracketed_name(key_raw).strip()
            if not key:
                continue

            m = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", (val_raw or "").strip())
            if m is None:
                continue
            table_name = m.group(1).strip()
            if not table_name:
                continue

            table_map.setdefault(key.lower(), table_name)

    return lookup


def _build_datasource_field_names_lookup(datasource_nodes: list[ET.Element]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = {}
    for ds_node in datasource_nodes:
        ds_name = ds_node.attrib.get("name", "") if isinstance(ds_node, ET.Element) else ""
        if not isinstance(ds_name, str) or not ds_name.strip():
            continue
        ds_key = ds_name.strip().lower()
        bucket = lookup.setdefault(ds_key, [])
        for col in [c for c in list(ds_node) if _local_name(c.tag) == "column"]:
            field_name = _clean_bracketed_name(col.attrib.get("name", "")).strip()
            if field_name and field_name not in bucket:
                bucket.append(field_name)
    return lookup


def _build_datasource_field_metadata_lookup(datasource_nodes: list[ET.Element]) -> dict[str, dict[str, dict[str, str]]]:
    lookup: dict[str, dict[str, dict[str, str]]] = {}
    for ds_node in datasource_nodes:
        ds_name = ds_node.attrib.get("name", "") if isinstance(ds_node, ET.Element) else ""
        if not isinstance(ds_name, str) or not ds_name.strip():
            continue

        ds_key = ds_name.strip().lower()
        field_map = lookup.setdefault(ds_key, {})
        for col in [c for c in list(ds_node) if _local_name(c.tag) == "column"]:
            field_name = _clean_bracketed_name(col.attrib.get("name", "")).strip()
            if not field_name:
                continue
            field_map[field_name.lower()] = {
                "name": field_name,
                "role": (col.attrib.get("role") or "").strip().lower(),
                "datatype": (col.attrib.get("datatype") or "").strip().lower(),
                "type": (col.attrib.get("type") or "").strip().lower(),
            }

    return lookup


def _build_parameter_filter_specs(
    datasets: list,
    mapping: dict,
    report_parameters: list[dict],
) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    _ = mapping

    def _add_spec(
        field_name: str,
        parameter_name: str = "",
        values: list[str] | None = None,
        dataset_name: str = "",
    ) -> None:
        clean_field = (field_name or "").strip()
        if not clean_field:
            return
        clean_parameter = (parameter_name or "").strip()
        clean_dataset = (dataset_name or "").strip()
        key = (clean_field.lower(), clean_parameter.lower(), clean_dataset.lower())
        if key in seen:
            return
        seen.add(key)
        specs.append(
            {
                "field": clean_field,
                "parameter_name": clean_parameter,
                "values": _unique_filter_values(values or []),
                "dataset_names": [clean_dataset] if clean_dataset else [],
            }
        )

    for dataset in datasets if isinstance(datasets, list) else []:
        if not isinstance(dataset, dict):
            continue
        dataset_name = dataset.get("name") if isinstance(dataset.get("name"), str) else ""

        for filter_entry in dataset.get("filters") if isinstance(dataset.get("filters"), list) else []:
            if not isinstance(filter_entry, dict):
                continue
            filter_values = filter_entry.get("values") if isinstance(filter_entry.get("values"), list) else []
            parameter_refs = filter_entry.get("parameter_references") if isinstance(filter_entry.get("parameter_references"), list) else []
            parameter_name = next((x.strip() for x in parameter_refs if isinstance(x, str) and x.strip()), "")
            values = _resolve_filter_values(filter_values, parameter_name, report_parameters)
            fields = _extract_fields_from_expressions(
                [
                    filter_entry.get("expression"),
                    *(filter_values if isinstance(filter_values, list) else []),
                ]
            )
            for field_name in fields:
                _add_spec(field_name, parameter_name=parameter_name, values=values, dataset_name=dataset_name)

        query = dataset.get("query") if isinstance(dataset.get("query"), str) else ""
        for query_parameter in dataset.get("query_parameters") if isinstance(dataset.get("query_parameters"), list) else []:
            if not isinstance(query_parameter, dict):
                continue
            parameter_refs = query_parameter.get("parameter_references") if isinstance(query_parameter.get("parameter_references"), list) else []
            value = query_parameter.get("value")
            if isinstance(value, str):
                parameter_refs = [*parameter_refs, *[m.group(1).strip() for m in PARAM_EXPR_REF_RE.finditer(value)]]
            for parameter_name in parameter_refs:
                if not isinstance(parameter_name, str) or not parameter_name.strip():
                    continue
                default_value = _extract_report_parameter_default(report_parameters, parameter_name)
                values = [default_value] if default_value else []
                for field_name in _extract_sql_parameter_filter_fields(query, parameter_name, dataset):
                    _add_spec(field_name, parameter_name=parameter_name, values=values, dataset_name=dataset_name)

    return specs


def _apply_parameter_filters_to_worksheets(
    root: ET.Element,
    filter_specs: list[dict[str, object]],
    datasource_field_roles: dict[str, dict[str, str]],
    datasource_field_metadata: dict[str, dict[str, dict[str, str]]],
    datasource_field_names: dict[str, list[str]],
    worksheet_dataset_by_name: dict[str, str],
    default_ds_name: str,
) -> None:
    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is None:
        return

    for ws in [c for c in list(worksheets) if _local_name(c.tag) == "worksheet"]:
        table = _find_direct_child(ws, "table")
        if table is None:
            continue
        view = _find_direct_child(table, "view")
        if view is None:
            view = ET.SubElement(table, "view")

        deps = _find_direct_child(view, "datasource-dependencies")
        ds_name = default_ds_name
        if deps is not None:
            bound_name = deps.attrib.get("datasource")
            if isinstance(bound_name, str) and bound_name.strip():
                ds_name = bound_name.strip()
        else:
            deps = ET.SubElement(view, "datasource-dependencies", attrib={"datasource": ds_name})

        ds_key = ds_name.lower()
        worksheet_name = ws.attrib.get("name", "")
        worksheet_dataset = (
            worksheet_dataset_by_name.get(worksheet_name.strip().lower(), "")
            if isinstance(worksheet_name, str)
            else ""
        )
        for spec in filter_specs:
            spec_dataset_names = spec.get("dataset_names") if isinstance(spec.get("dataset_names"), list) else []
            clean_spec_datasets = {
                str(item).strip().lower()
                for item in spec_dataset_names
                if isinstance(item, str) and item.strip()
            }
            if clean_spec_datasets and worksheet_dataset.strip().lower() not in clean_spec_datasets:
                continue

            field_name = _resolve_filter_field_name(spec.get("field"), datasource_field_names.get(ds_key, []))
            if not field_name:
                continue
            metadata = datasource_field_metadata.get(ds_key, {}).get(field_name.lower(), {})
            role = metadata.get("role") or datasource_field_roles.get(ds_key, {}).get(field_name.lower(), "dimension")
            if role not in {"dimension", "measure"}:
                role = "dimension"
            datatype = metadata.get("datatype", "")
            type_value = metadata.get("type", "")

            _append_dependency_binding(
                deps,
                field_name,
                role=role,
                datatype=datatype,
                type_value=type_value,
            )
            column_ref = _filter_column_reference(ds_name, field_name, role, datatype=datatype)
            if not column_ref:
                continue
            _ensure_view_filter(view, column_ref, field_name, role, spec, datatype=datatype)
            _ensure_filter_slice(view, column_ref)

        _reorder_view_children_for_tableau(view)


def _resolve_filter_values(
    raw_values: list,
    parameter_name: str,
    report_parameters: list[dict],
) -> list[str]:
    if parameter_name:
        default_value = _clean_filter_literal(_extract_report_parameter_default(report_parameters, parameter_name))
        return [default_value] if default_value else []

    values: list[str] = []
    for value in raw_values:
        if not isinstance(value, str) or not value.strip():
            continue
        if FIELD_EXPR_REF_RE.search(value) or PARAM_EXPR_REF_RE.search(value):
            continue
        clean = _clean_filter_literal(value)
        if clean and clean not in values:
            values.append(clean)
    return values


def _unique_filter_values(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = _clean_filter_literal(value) if isinstance(value, str) else ""
        if clean and clean not in out:
            out.append(clean)
    return out


def _clean_filter_literal(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        return ""
    if clean.startswith("="):
        clean = clean[1:].strip()
    if (clean.startswith('"') and clean.endswith('"')) or (clean.startswith("'") and clean.endswith("'")):
        clean = clean[1:-1].strip()
    return clean


def _is_numeric_filter_datatype(datatype: str) -> bool:
    return (datatype or "").strip().lower() in {"integer", "real", "float", "double", "decimal"}


def _is_numeric_literal(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", (value or "").strip()))


def _format_groupfilter_member(value: str, datatype: str) -> str:
    clean = _clean_filter_literal(value)
    if _is_numeric_filter_datatype(datatype) and _is_numeric_literal(clean):
        return clean
    return f'"{clean}"'


def _extract_sql_parameter_filter_fields(query: str, parameter_name: str, dataset: dict) -> list[str]:
    param = (parameter_name or "").strip().lstrip("@")
    if not isinstance(query, str) or not query.strip() or not param:
        return []

    identifier = r"(?:\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\.(?:\[[^\]]+\]|[A-Za-z_][\w$]*))*"
    patterns = [
        rf"(?P<field>{identifier})\s*(?:=|<>|!=|>=|<=|>|<)\s*@{re.escape(param)}\b",
        rf"@{re.escape(param)}\b\s*(?:=|<>|!=|>=|<=|>|<)\s*(?P<field>{identifier})",
        rf"(?P<field>{identifier})\s+in\s*\(\s*@{re.escape(param)}\b",
    ]
    dataset_fields = _dataset_field_names(dataset)
    out: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, query, flags=re.IGNORECASE):
            field_name = _clean_sql_identifier_leaf(match.group("field"))
            resolved = _resolve_filter_field_name(field_name, dataset_fields) or field_name
            if resolved and resolved not in out:
                out.append(resolved)
    return out


def _dataset_field_names(dataset: dict) -> list[str]:
    fields: list[str] = []
    for field in dataset.get("fields") if isinstance(dataset.get("fields"), list) else []:
        if not isinstance(field, dict):
            continue
        for key in ["name", "data_field"]:
            value = field.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in fields:
                fields.append(value.strip())
    return fields


def _clean_sql_identifier_leaf(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return _clean_bracketed_name(text.split(".")[-1]).strip()


def _resolve_filter_field_name(raw_field: object, available_fields: list[str]) -> str:
    if not isinstance(raw_field, str) or not raw_field.strip():
        return ""
    clean = raw_field.strip()
    if not available_fields:
        return clean
    for candidate in available_fields:
        if isinstance(candidate, str) and candidate.strip().lower() == clean.lower():
            return candidate.strip()
    leaf = _clean_sql_identifier_leaf(clean)
    for candidate in available_fields:
        if isinstance(candidate, str) and candidate.strip().lower() == leaf.lower():
            return candidate.strip()
    return ""


def _filter_column_reference(datasource_name: str, field_name: str, role: str, datatype: str = "") -> str:
    ds_name = (datasource_name or "").strip()
    clean_field = (field_name or "").strip()
    if not ds_name or not clean_field:
        return ""
    if (role or "").strip().lower() == "measure":
        return f"[{ds_name}].[sum:{clean_field}:qk]"
    if _is_numeric_filter_datatype(datatype):
        return f"[{ds_name}].[none:{clean_field}:qk]"
    return f"[{ds_name}].[none:{clean_field}:nk]"


def _ensure_view_filter(
    view_node: ET.Element,
    column_ref: str,
    field_name: str,
    role: str,
    spec: dict[str, object],
    datatype: str = "",
) -> None:
    filter_node: ET.Element | None = None
    for child in list(view_node):
        if _local_name(child.tag) == "filter" and (child.attrib.get("column") or "").strip() == column_ref:
            filter_node = child
            break
    if filter_node is None:
        filter_node = ET.SubElement(view_node, "filter")

    for child in list(filter_node):
        filter_node.remove(child)

    values = spec.get("values") if isinstance(spec.get("values"), list) else []
    clean_values = _unique_filter_values([str(value) for value in values])
    role_norm = (role or "").strip().lower()

    if role_norm == "dimension" and _is_numeric_filter_datatype(datatype):
        filter_node.attrib.clear()
        filter_node.attrib.update({"class": "quantitative", "column": column_ref, "included-values": "in-range"})
        range_values = [value for value in clean_values if _is_numeric_literal(value)]
        if range_values:
            min_value = range_values[0]
            max_value = range_values[1] if len(range_values) > 1 else range_values[0]
        else:
            filter_node.attrib["included-values"] = "in-range-or-null"
            min_value = "0"
            max_value = "9999"
        min_node = ET.SubElement(filter_node, "min")
        min_node.text = min_value
        max_node = ET.SubElement(filter_node, "max")
        max_node.text = max_value
        return

    if role_norm == "measure" and len(clean_values) >= 2:
        filter_node.attrib.clear()
        filter_node.attrib.update({"class": "quantitative", "column": column_ref, "included-values": "in-range"})
        min_node = ET.SubElement(filter_node, "min")
        min_node.text = clean_values[0]
        max_node = ET.SubElement(filter_node, "max")
        max_node.text = clean_values[1]
        return

    filter_node.attrib.clear()
    filter_node.attrib.update({"class": "categorical", "column": column_ref, "filter-group": "1"})
    level = f"[sum:{field_name}:qk]" if role_norm == "measure" else f"[none:{field_name}:nk]"
    if clean_values:
        for value in clean_values:
            ET.SubElement(
                filter_node,
                "groupfilter",
                attrib={"function": "member", "level": level, "member": _format_groupfilter_member(value, datatype)},
            )
    else:
        ET.SubElement(filter_node, "groupfilter", attrib={"function": "level-members", "level": level})


def _ensure_filter_slice(view_node: ET.Element, column_ref: str) -> None:
    slices = _find_direct_child(view_node, "slices")
    if slices is None:
        slices = ET.SubElement(view_node, "slices")
    for child in list(slices):
        if _local_name(child.tag) == "column" and isinstance(child.text, str) and child.text.strip() == column_ref:
            return
    node = ET.SubElement(slices, "column")
    node.text = column_ref


def _collect_fields_used_by_workbook_views(root: ET.Element) -> set[str]:
    used: set[str] = set()

    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is None:
        return used

    for ws in [c for c in list(worksheets) if _local_name(c.tag) == "worksheet"]:
        table = _find_direct_child(ws, "table")
        if table is None:
            continue

        rows = _find_direct_child(table, "rows")
        cols = _find_direct_child(table, "cols")
        for shelf in [rows, cols]:
            if shelf is None or not isinstance(shelf.text, str) or not shelf.text.strip():
                continue
            for match in SHELF_FIELD_REF_RE.finditer(shelf.text):
                field_name = match.group("field").strip()
                if field_name:
                    used.add(field_name)

        view = _find_direct_child(table, "view")
        if view is None:
            continue

        deps = _find_direct_child(view, "datasource-dependencies")
        if deps is not None:
            for dep in list(deps):
                dep_tag = _local_name(dep.tag)
                if dep_tag == "column":
                    field_name = _clean_bracketed_name(dep.attrib.get("name", "")).strip()
                    if field_name:
                        used.add(field_name)
                elif dep_tag == "column-instance":
                    field_name = _clean_bracketed_name(dep.attrib.get("column", "")).strip()
                    if field_name:
                        used.add(field_name)

        for elem in view.iter():
            for attr_value in elem.attrib.values():
                if not isinstance(attr_value, str) or not attr_value:
                    continue
                for match in SHELF_FIELD_REF_RE.finditer(attr_value):
                    field_name = match.group("field").strip()
                    if field_name:
                        used.add(field_name)

    return used


def _extract_tableau_formula_field_refs(formula: str) -> list[str]:
    refs: list[str] = []
    if not isinstance(formula, str) or not formula.strip():
        return refs

    for match in re.finditer(r"\[([^\[\]]+)\]", formula):
        token = match.group(1).strip()
        if not token:
            continue
        token_lower = token.lower()
        if token_lower in {"measure names", "measure values", "number of records"}:
            continue
        if ":" in token or "." in token:
            continue
        if token not in refs:
            refs.append(token)

    return refs


def _prune_datasource_fields_to_usage(datasource_node: ET.Element, required_fields: set[str]) -> None:
    if not isinstance(datasource_node, ET.Element):
        return
    if not isinstance(required_fields, set) or not required_fields:
        return

    columns = [c for c in list(datasource_node) if _local_name(c.tag) == "column"]
    if not columns:
        return

    required_lower = {
        field_name.strip().lower()
        for field_name in required_fields
        if isinstance(field_name, str) and field_name.strip()
    }
    if not required_lower:
        return

    available_lower: set[str] = set()
    for col in columns:
        field_name = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        if field_name:
            available_lower.add(field_name)

    keep_lower = {field_name for field_name in available_lower if field_name in required_lower}
    if not keep_lower:
        return

    changed = True
    while changed:
        changed = False
        for col in columns:
            col_name = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
            if not col_name or col_name not in keep_lower:
                continue
            calc = _find_direct_child(col, "calculation")
            formula = calc.attrib.get("formula", "") if calc is not None else ""
            for ref in _extract_tableau_formula_field_refs(formula):
                ref_lower = ref.lower()
                if ref_lower in available_lower and ref_lower not in keep_lower:
                    keep_lower.add(ref_lower)
                    changed = True

    for col in list(datasource_node):
        if _local_name(col.tag) != "column":
            continue
        col_name = _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        if col_name and col_name not in keep_lower:
            datasource_node.remove(col)

    conn = _find_direct_child(datasource_node, "connection")
    if conn is None:
        return

    cols = _find_direct_child(conn, "cols")
    if cols is None:
        return

    for map_node in list(cols):
        if _local_name(map_node.tag) != "map":
            cols.remove(map_node)
            continue
        map_key = _clean_bracketed_name(map_node.attrib.get("key", "")).strip().lower()
        if map_key and map_key not in keep_lower:
            cols.remove(map_node)


def _is_regionalsales_report(visual_model: dict) -> bool:
    if not isinstance(visual_model, dict):
        return False
    layout = visual_model.get("layout")
    if not isinstance(layout, dict):
        return False
    report_name = layout.get("report_name") if isinstance(layout.get("report_name"), str) else ""
    return "regionalsales" in report_name.strip().lower()


def _build_regionalsales_sheet_specs(
    mapped_dataset_by_visual: dict[str, str],
) -> list[dict]:
    dataset_name = mapped_dataset_by_visual.get("tablixregionsummary", "")
    if not dataset_name:
        for ds_name in mapped_dataset_by_visual.values():
            if isinstance(ds_name, str) and ds_name.strip():
                dataset_name = ds_name.strip()
                break
    if not dataset_name:
        dataset_name = "dsMain"

    specs: list[dict] = [
        {
            "name": "PageHeader_ReportLogo",
            "visual_type": "Image",
            "dataset_name": dataset_name,
            "fields": ["ReportLogoLabel"],
            "semantic_kind": "image",
            "template_role": "logo_placeholder",
        },
        {
            "name": "PageHeader_TitleTile",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["TitleTileText"],
            "semantic_kind": "header",
            "template_role": "static_text",
        },
        {
            "name": "PageHeader_ReportTitle",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["ReportTitleText"],
            "semantic_kind": "header",
            "template_role": "static_text",
        },
        {
            "name": "PageHeader_ReportSubtitle",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["ReportSubtitleText"],
            "semantic_kind": "header",
            "template_role": "static_text",
        },
        {
            "name": "tablixRegionSummary_SalesTerritoryRegion",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion"],
            "semantic_kind": "kpi",
            "template_role": "region_title",
        },
        {
            "name": "tablixRegionSummary_ColumnHeaders",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["KPIHeaderText"],
            "semantic_kind": "kpi",
            "template_role": "static_text",
        },
        {
            "name": "tablixRegionSummary_Quantity",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "Quantity"],
            "semantic_kind": "kpi",
            "template_role": "kpi_by_region",
        },
        {
            "name": "tablixRegionSummary_Sales",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "Sales"],
            "semantic_kind": "kpi",
            "template_role": "kpi_by_region",
        },
        {
            "name": "tablixRegionSummary_Textbox6",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "Quota"],
            "semantic_kind": "kpi",
            "template_role": "kpi_by_region",
        },
        {
            "name": "tablixRegionSummary_Textbox8",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "SalesVariance"],
            "semantic_kind": "kpi",
            "template_role": "kpi_by_region",
        },
        {
            "name": "tablixRegionSummary_GaugePanel1",
            "visual_type": "GaugePanel",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "SalesQuotaPct"],
            "semantic_kind": "gauge",
            "template_role": "gauge_by_region",
        },
        {
            "name": "tablixRegionSummary_GaugeLabel",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["SalesTerritoryRegion", "SalesVariancePct"],
            "semantic_kind": "kpi",
            "template_role": "kpi_by_region",
        },
        {
            "name": "tablixRegionSummary_Chart1",
            "visual_type": "Line",
            "dataset_name": dataset_name,
            "fields": ["MonthSortOrder", "MonthKey", "Month", "Sales", "Quota"],
            "semantic_kind": "chart",
            "template_role": "dual_line_chart",
        },
        {
            "name": "PageFooter_Footer1",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["FooterRunAtText"],
            "semantic_kind": "footer",
            "template_role": "static_text",
        },
        {
            "name": "PageFooter_Footer2",
            "visual_type": "Textbox",
            "dataset_name": dataset_name,
            "fields": ["FooterPageText"],
            "semantic_kind": "footer",
            "template_role": "static_text",
        },
    ]

    return specs


def _collect_sheet_specs(visual_model: dict, mapping: dict) -> list[dict]:
    specs: list[dict] = []
    seen: set[str] = set()

    visual_nodes: dict[str, dict] = {}
    _collect_visual_nodes(visual_model.get("visuals", []) if isinstance(visual_model, dict) else [], visual_nodes)

    mapped_dataset_by_visual: dict[str, str] = {}
    mapped_fields_by_visual: dict[str, list[str]] = {}
    visual_to_dataset = mapping.get("visual_to_dataset", []) if isinstance(mapping, dict) else []
    if isinstance(visual_to_dataset, list):
        for item in visual_to_dataset:
            if not isinstance(item, dict):
                continue
            visual_name = item.get("visual_name")
            dataset_name = item.get("dataset_name")
            if isinstance(visual_name, str) and visual_name.strip() and isinstance(dataset_name, str) and dataset_name.strip():
                mapped_dataset_by_visual[visual_name.strip().lower()] = dataset_name.strip()

    visual_to_fields = mapping.get("visual_to_fields", []) if isinstance(mapping, dict) else []
    if isinstance(visual_to_fields, list):
        for item in visual_to_fields:
            if not isinstance(item, dict):
                continue
            visual_name = item.get("visual_name")
            if not isinstance(visual_name, str) or not visual_name.strip():
                continue
            fields: list[str] = []
            for field in item.get("fields", []) if isinstance(item.get("fields"), list) else []:
                if isinstance(field, str) and field.strip() and field.strip() not in fields:
                    fields.append(field.strip())
            if fields:
                mapped_fields_by_visual[visual_name.strip().lower()] = fields

    if _is_regionalsales_report(visual_model):
        return _build_regionalsales_sheet_specs(mapped_dataset_by_visual)

    sheets = visual_model.get("sheets", []) if isinstance(visual_model, dict) else []
    if isinstance(sheets, list):
        for sheet in sheets:
            if not isinstance(sheet, dict):
                continue
            name = sheet.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().lower()
            if key in seen:
                continue
            seen.add(key)

            visual_node = visual_nodes.get(key, {})
            visual_type = sheet.get("visual_type") or visual_node.get("visual_type") or ""
            dataset_name = sheet.get("dataset_name") or mapped_dataset_by_visual.get(key) or visual_node.get("dataset_name")
            referenced_fields = _extract_fields_from_visual_node(visual_node)
            if not referenced_fields:
                referenced_fields = mapped_fields_by_visual.get(key, [])
            semantic_kind = _derive_sheet_semantic_kind(name.strip(), visual_type, visual_node)
            visual_properties = visual_node.get("properties") if isinstance(visual_node.get("properties"), dict) else {}
            visual_filters = visual_properties.get("filters") if isinstance(visual_properties.get("filters"), list) else []
            static_text = _extract_static_text_from_visual_properties(visual_properties)
            static_text_values = _extract_static_text_values_from_visual_properties(visual_properties)
            visual_expressions = visual_node.get("expressions") if isinstance(visual_node.get("expressions"), list) else []

            if _should_skip_sheet_spec(
                visual_name=name.strip(),
                visual_type=visual_type if isinstance(visual_type, str) else "",
                semantic_kind=semantic_kind,
                referenced_fields=referenced_fields,
                visual_properties=visual_properties,
            ):
                continue

            specs.append(
                {
                    "name": name.strip(),
                    "visual_type": visual_type if isinstance(visual_type, str) else "",
                    "dataset_name": dataset_name if isinstance(dataset_name, str) else "",
                    "fields": referenced_fields,
                    "semantic_kind": semantic_kind,
                    "filters": visual_filters,
                    "expressions": visual_expressions,
                    "properties": visual_properties,
                    "static_text": static_text,
                    "static_text_values": static_text_values,
                }
            )

    if not specs:
        for visual_name, visual_node in visual_nodes.items():
            if visual_name in seen:
                continue
            seen.add(visual_name)
            name = visual_node.get("name") if isinstance(visual_node.get("name"), str) else visual_name
            visual_type = visual_node.get("visual_type") if isinstance(visual_node.get("visual_type"), str) else ""
            referenced_fields = _extract_fields_from_visual_node(visual_node)
            if not referenced_fields:
                referenced_fields = mapped_fields_by_visual.get(visual_name, [])
            dataset_name = mapped_dataset_by_visual.get(visual_name, "")
            semantic_kind = _derive_sheet_semantic_kind(name, visual_type, visual_node)
            visual_properties = visual_node.get("properties") if isinstance(visual_node.get("properties"), dict) else {}
            visual_filters = visual_properties.get("filters") if isinstance(visual_properties.get("filters"), list) else []
            static_text = _extract_static_text_from_visual_properties(visual_properties)
            static_text_values = _extract_static_text_values_from_visual_properties(visual_properties)
            visual_expressions = visual_node.get("expressions") if isinstance(visual_node.get("expressions"), list) else []

            if _should_skip_sheet_spec(
                visual_name=name,
                visual_type=visual_type,
                semantic_kind=semantic_kind,
                referenced_fields=referenced_fields,
                visual_properties=visual_properties,
            ):
                continue

            specs.append(
                {
                    "name": name,
                    "visual_type": visual_type,
                    "dataset_name": dataset_name,
                    "fields": referenced_fields,
                    "semantic_kind": semantic_kind,
                    "filters": visual_filters,
                    "expressions": visual_expressions,
                    "properties": visual_properties,
                    "static_text": static_text,
                    "static_text_values": static_text_values,
                }
            )

    return specs


def _expand_sheet_specs_for_regional_sales(sheet_specs: list[dict], visual_model: dict) -> list[dict]:
    if not isinstance(sheet_specs, list) or not sheet_specs:
        return sheet_specs

    report_name = ""
    allow_template_expansion = False
    if isinstance(visual_model, dict):
        layout = visual_model.get("layout")
        if isinstance(layout, dict):
            report_name = layout.get("report_name") if isinstance(layout.get("report_name"), str) else ""
            allow_template_expansion = layout.get("allow_template_expansion") is True

    if not allow_template_expansion:
        return sheet_specs

    if "regionalsales" not in report_name.strip().lower():
        return sheet_specs

    dataset_name = ""
    for spec in sheet_specs:
        ds_name = spec.get("dataset_name") if isinstance(spec, dict) else ""
        if isinstance(ds_name, str) and ds_name.strip():
            dataset_name = ds_name.strip()
            break
    if not dataset_name:
        dataset_name = "dsMain"

    expanded: list[dict] = []
    seen: set[str] = set()
    for spec in sheet_specs:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        expanded.append(spec)

    # RegionalSales starter pack: multiple analytical worksheets derived from the same RDL dataset.
    templates = [
        ("Sales by Month", "LineChart", ["Month", "Sales"]),
        ("Quota by Month", "LineChart", ["Month", "Quota"]),
        ("Quantity by Region", "BarChart", ["SalesTerritoryRegion", "Quantity"]),
        ("Sales by Region", "BarChart", ["SalesTerritoryRegion", "Sales"]),
        ("Quota by Region", "BarChart", ["SalesTerritoryRegion", "Quota"]),
    ]

    for name, visual_type, fields in templates:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        expanded.append(
            {
                "name": name,
                "visual_type": visual_type,
                "dataset_name": dataset_name,
                "fields": fields,
            }
        )

    return expanded


def _resolve_dashboard_datasource_names(
    root: ET.Element,
    dashboard_datasource_names: list[str] | None,
) -> list[str]:
    resolved: list[str] = []
    if isinstance(dashboard_datasource_names, list):
        for ds_name in dashboard_datasource_names:
            if isinstance(ds_name, str) and ds_name.strip() and ds_name.strip() not in resolved:
                resolved.append(ds_name.strip())

    if resolved:
        return resolved

    datasource_root = _find_direct_child(root, "datasources")
    if datasource_root is None:
        return resolved

    for ds_node in [c for c in list(datasource_root) if _local_name(c.tag) == "datasource"]:
        ds_name = ds_node.attrib.get("name")
        if isinstance(ds_name, str) and ds_name.strip() and ds_name.strip() not in resolved:
            resolved.append(ds_name.strip())

    return resolved


def _rebuild_regionalsales_dashboard(
    root: ET.Element,
    worksheet_names: list[str],
    visual_model: dict,
    dashboard_datasource_names: list[str] | None,
) -> None:
    if len(worksheet_names) < 2:
        return

    dashboards = _find_direct_child(root, "dashboards")
    if dashboards is None:
        dashboards = ET.SubElement(root, "dashboards")

    for child in list(dashboards):
        dashboards.remove(child)

    report_name = ""
    if isinstance(visual_model, dict):
        layout = visual_model.get("layout")
        if isinstance(layout, dict):
            report_name = layout.get("report_name") if isinstance(layout.get("report_name"), str) else ""
    dashboard_name = f"{report_name.strip()} Dashboard" if report_name.strip() else "Dashboard 1"

    dashboard = ET.SubElement(dashboards, "dashboard", attrib={"name": dashboard_name})
    ET.SubElement(dashboard, "style")
    ET.SubElement(
        dashboard,
        "size",
        attrib={"maxheight": "900", "maxwidth": "1400", "minheight": "900", "minwidth": "1400"},
    )

    resolved_dashboard_ds = _resolve_dashboard_datasource_names(root, dashboard_datasource_names)
    dashboard_datasources = ET.SubElement(dashboard, "datasources")
    for ds_name in resolved_dashboard_ds:
        ET.SubElement(dashboard_datasources, "datasource", attrib={"name": ds_name})

    zones = ET.SubElement(dashboard, "zones")
    root_zone = ET.SubElement(
        zones,
        "zone",
        attrib={"id": "0", "type-v2": "layout-basic", "x": "0", "y": "0", "w": "100000", "h": "100000"},
    )

    existing = set(worksheet_names)
    zone_id = 1

    def _append_text_zone(
        parent_zone: ET.Element,
        current_zone_id: int,
        text: str,
        x: int,
        y: int,
        w: int,
        h: int,
        font_size: str = "",
        bold: bool = False,
    ) -> None:
        text_zone = ET.SubElement(
            parent_zone,
            "zone",
            attrib={
                "id": str(current_zone_id),
                "type-v2": "text",
                "forceUpdate": "true",
                "x": str(max(0, x)),
                "y": str(max(0, y)),
                "w": str(max(1, w)),
                "h": str(max(1, h)),
            },
        )
        formatted_text = ET.SubElement(text_zone, "formatted-text")
        run_attrib: dict[str, str] = {}
        if bold:
            run_attrib["bold"] = "true"
        if font_size:
            run_attrib["fontsize"] = font_size
        run_node = ET.SubElement(formatted_text, "run", attrib=run_attrib)
        run_node.text = text

        zone_style = ET.SubElement(text_zone, "zone-style")
        for attr, value in [
            ("border-color", "#000000"),
            ("border-style", "none"),
            ("border-width", "0"),
            ("margin", "4"),
        ]:
            ET.SubElement(zone_style, "format", attrib={"attr": attr, "value": value})

    def add_zone(name: str, x: int, y: int, w: int, h: int) -> None:
        nonlocal zone_id
        if name not in existing:
            return
        ET.SubElement(
            root_zone,
            "zone",
            attrib={
                "id": str(zone_id),
                "name": name,
                "type-v2": "worksheet",
                "show-title": "false",
                "x": str(max(0, x)),
                "y": str(max(0, y)),
                "w": str(max(1, w)),
                "h": str(max(1, h)),
            },
        )
        zone_id += 1

    def add_text(text: str, x: int, y: int, w: int, h: int, font_size: str = "", bold: bool = False) -> None:
        nonlocal zone_id
        _append_text_zone(root_zone, zone_id, text, x, y, w, h, font_size=font_size, bold=bold)
        zone_id += 1

    add_zone("PageHeader_ReportLogo", 0, 0, 14000, 18000)
    add_zone("PageHeader_TitleTile", 14000, 0, 86000, 18000)
    add_text("Regional Sales", 14000, 2200, 86000, 7000, font_size="24", bold=True)
    add_zone("PageHeader_ReportSubtitle", 16000, 9800, 80000, 5600)

    add_zone("tablixRegionSummary_SalesTerritoryRegion", 0, 18000, 100000, 7000)
    add_text("ORDERS", 0, 25000, 20000, 5000, font_size="12", bold=True)
    add_text("SALES", 20000, 25000, 20000, 5000, font_size="12", bold=True)
    add_text("QUOTA", 40000, 25000, 20000, 5000, font_size="12", bold=True)
    add_text("VARIANCE", 60000, 25000, 20000, 5000, font_size="12", bold=True)

    add_zone("tablixRegionSummary_Quantity", 0, 30000, 20000, 20000)
    add_zone("tablixRegionSummary_Sales", 20000, 30000, 20000, 20000)
    add_zone("tablixRegionSummary_Textbox6", 40000, 30000, 20000, 20000)
    add_zone("tablixRegionSummary_Textbox8", 60000, 30000, 20000, 20000)
    add_zone("tablixRegionSummary_GaugePanel1", 80000, 30000, 20000, 14500)
    add_zone("tablixRegionSummary_GaugeLabel", 80000, 44500, 20000, 5500)

    add_zone("tablixRegionSummary_Chart1", 0, 50000, 100000, 33000)

    add_zone("PageFooter_Footer1", 0, 83000, 50000, 7000)
    add_zone("PageFooter_Footer2", 50000, 83000, 50000, 7000)

    devicelayouts = ET.SubElement(dashboard, "devicelayouts")
    phone_layout = ET.SubElement(devicelayouts, "devicelayout", attrib={"name": "Phone"})
    ET.SubElement(phone_layout, "size", attrib={"maxheight": "1350", "minheight": "1350", "sizing-mode": "vscroll"})

    phone_zones = ET.SubElement(phone_layout, "zones")
    phone_root_zone = ET.SubElement(
        phone_zones,
        "zone",
        attrib={"id": "100", "type-v2": "layout-basic", "x": "0", "y": "0", "w": "100000", "h": "100000"},
    )
    phone_flow_zone = ET.SubElement(
        phone_root_zone,
        "zone",
        attrib={"id": "101", "type-v2": "layout-flow", "param": "vert", "x": "1000", "y": "1000", "w": "98000", "h": "98000"},
    )

    phone_entries: list[tuple[str, str]] = [
        ("worksheet", "PageHeader_ReportLogo"),
        ("worksheet", "PageHeader_TitleTile"),
        ("text", "Regional Sales"),
        ("worksheet", "PageHeader_ReportSubtitle"),
        ("worksheet", "tablixRegionSummary_SalesTerritoryRegion"),
        ("text", "ORDERS     SALES     QUOTA     VARIANCE"),
        ("worksheet", "tablixRegionSummary_Quantity"),
        ("worksheet", "tablixRegionSummary_Sales"),
        ("worksheet", "tablixRegionSummary_Textbox6"),
        ("worksheet", "tablixRegionSummary_Textbox8"),
        ("worksheet", "tablixRegionSummary_GaugePanel1"),
        ("worksheet", "tablixRegionSummary_GaugeLabel"),
        ("worksheet", "tablixRegionSummary_Chart1"),
        ("worksheet", "PageFooter_Footer1"),
        ("worksheet", "PageFooter_Footer2"),
    ]
    phone_items: list[tuple[str, str]] = []
    for kind, value in phone_entries:
        if kind == "worksheet":
            if value in existing:
                phone_items.append((kind, value))
        else:
            phone_items.append((kind, value))

    if not phone_items:
        phone_items = [("worksheet", name) for name in worksheet_names]

    phone_zone_id = 102
    phone_y = 1000
    phone_total_h = 98000
    phone_w = 96000
    phone_rows = len(phone_items)
    phone_cell_h = phone_total_h // phone_rows if phone_rows else phone_total_h

    for idx, (entry_kind, entry_value) in enumerate(phone_items):
        if idx < phone_rows - 1:
            zone_h = phone_cell_h
        else:
            zone_h = (1000 + phone_total_h) - phone_y

        if entry_kind == "worksheet":
            ET.SubElement(
                phone_flow_zone,
                "zone",
                attrib={
                    "id": str(phone_zone_id),
                    "name": entry_value,
                    "type-v2": "worksheet",
                    "show-title": "false",
                    "x": "1000",
                    "y": str(phone_y),
                    "w": str(phone_w),
                    "h": str(zone_h),
                },
            )
        else:
            text_size = "24" if entry_value == "Regional Sales" else "12"
            _append_text_zone(
                phone_flow_zone,
                phone_zone_id,
                entry_value,
                1000,
                phone_y,
                phone_w,
                zone_h,
                font_size=text_size,
                bold=True,
            )

        phone_y += zone_h
        phone_zone_id += 1


def _rebuild_dashboards_from_sheet_specs(
    root: ET.Element,
    sheet_specs: list[dict],
    visual_model: dict,
    dashboard_datasource_names: list[str] | None = None,
) -> None:
    worksheet_names: list[str] = []
    semantic_by_name: dict[str, str] = {}
    for spec in sheet_specs:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if isinstance(name, str) and name.strip():
            clean_name = name.strip()
            if clean_name not in worksheet_names:
                worksheet_names.append(clean_name)
            semantic_kind = spec.get("semantic_kind") if isinstance(spec.get("semantic_kind"), str) else ""
            semantic_by_name[clean_name] = semantic_kind.strip().lower()

    if _is_regionalsales_report(visual_model):
        _rebuild_regionalsales_dashboard(
            root=root,
            worksheet_names=worksheet_names,
            visual_model=visual_model,
            dashboard_datasource_names=dashboard_datasource_names,
        )
        return

    header_sheets = [name for name in worksheet_names if semantic_by_name.get(name, "") == "header"]
    footer_sheets = [name for name in worksheet_names if semantic_by_name.get(name, "") == "footer"]
    filter_sheets = [name for name in worksheet_names if semantic_by_name.get(name, "") == "filter"]
    content_sheets = [
        name
        for name in worksheet_names
        if semantic_by_name.get(name, "") not in {"header", "footer", "filter"}
    ]

    worksheet_names = header_sheets + content_sheets + filter_sheets + footer_sheets

    if len(worksheet_names) < 1:
        return

    dashboards = _find_direct_child(root, "dashboards")
    if dashboards is None:
        dashboards = ET.SubElement(root, "dashboards")

    for child in list(dashboards):
        dashboards.remove(child)

    report_name = ""
    if isinstance(visual_model, dict):
        layout = visual_model.get("layout")
        if isinstance(layout, dict):
            report_name = layout.get("report_name") if isinstance(layout.get("report_name"), str) else ""
    dashboard_name = f"{report_name.strip()} Dashboard" if report_name.strip() else "Dashboard 1"

    dashboard = ET.SubElement(dashboards, "dashboard", attrib={"name": dashboard_name})
    ET.SubElement(dashboard, "style")
    ET.SubElement(
        dashboard,
        "size",
        attrib={"maxheight": "900", "maxwidth": "1400", "minheight": "900", "minwidth": "1400"},
    )

    resolved_dashboard_ds = _resolve_dashboard_datasource_names(root, dashboard_datasource_names)

    dashboard_datasources = ET.SubElement(dashboard, "datasources")
    for ds_name in resolved_dashboard_ds:
        ET.SubElement(dashboard_datasources, "datasource", attrib={"name": ds_name})

    zones = ET.SubElement(dashboard, "zones")
    root_zone = ET.SubElement(
        zones,
        "zone",
        attrib={"id": "0", "type-v2": "layout-basic", "x": "0", "y": "0", "w": "100000", "h": "100000"},
    )

    total = len(worksheet_names)
    zone_id = 1

    # Give a clearer dashboard visual hierarchy: one hero sheet on top, supporting views below.
    if total >= 5:
        hero_h = 22000
        hero_name = content_sheets[0] if content_sheets else worksheet_names[0]
        ET.SubElement(
            root_zone,
            "zone",
            attrib={
                "id": str(zone_id),
                "name": hero_name,
                "type-v2": "worksheet",
                "show-title": "false",
                "x": "0",
                "y": "0",
                "w": "100000",
                "h": str(hero_h),
            },
        )
        zone_id += 1

        remaining = [name for name in worksheet_names if name != hero_name]
        cols = 2 if len(remaining) > 1 else 1
        rows = (len(remaining) + cols - 1) // cols if remaining else 1
        cell_w = 100000 // cols
        details_y0 = hero_h
        details_h = 100000 - details_y0
        cell_h = details_h // rows if rows else details_h

        for idx, ws_name in enumerate(remaining):
            row = idx // cols
            col = idx % cols
            x = col * cell_w
            y = details_y0 + (row * cell_h)
            w = cell_w if col < cols - 1 else 100000 - x
            h = cell_h if row < rows - 1 else 100000 - y

            ET.SubElement(
                root_zone,
                "zone",
                attrib={
                    "id": str(zone_id),
                    "name": ws_name,
                    "type-v2": "worksheet",
                    "show-title": "false",
                    "x": str(x),
                    "y": str(y),
                    "w": str(w),
                    "h": str(h),
                },
            )
            zone_id += 1
    else:
        cols = 2 if total > 1 else 1
        rows = (total + cols - 1) // cols
        cell_w = 100000 // cols
        cell_h = 100000 // rows if rows else 100000

        for idx, ws_name in enumerate(worksheet_names):
            row = idx // cols
            col = idx % cols
            x = col * cell_w
            y = row * cell_h
            w = cell_w if col < cols - 1 else 100000 - x
            h = cell_h if row < rows - 1 else 100000 - y

            ET.SubElement(
                root_zone,
                "zone",
                attrib={
                    "id": str(zone_id),
                    "name": ws_name,
                    "type-v2": "worksheet",
                    "show-title": "false",
                    "x": str(x),
                    "y": str(y),
                    "w": str(w),
                    "h": str(h),
                },
            )
            zone_id += 1

    devicelayouts = ET.SubElement(dashboard, "devicelayouts")
    phone_layout = ET.SubElement(devicelayouts, "devicelayout", attrib={"name": "Phone"})
    ET.SubElement(phone_layout, "size", attrib={"maxheight": "1350", "minheight": "1350", "sizing-mode": "vscroll"})

    phone_zones = ET.SubElement(phone_layout, "zones")
    phone_root_zone = ET.SubElement(
        phone_zones,
        "zone",
        attrib={"id": "100", "type-v2": "layout-basic", "x": "0", "y": "0", "w": "100000", "h": "100000"},
    )
    phone_flow_zone = ET.SubElement(
        phone_root_zone,
        "zone",
        attrib={"id": "101", "type-v2": "layout-flow", "param": "vert", "x": "1000", "y": "1000", "w": "98000", "h": "98000"},
    )

    phone_zone_id = 102
    phone_y = 1000
    phone_total_h = 98000
    phone_w = 96000
    phone_rows = len(worksheet_names)
    phone_cell_h = phone_total_h // phone_rows if phone_rows else phone_total_h

    for idx, ws_name in enumerate(worksheet_names):
        if idx < phone_rows - 1:
            zone_h = phone_cell_h
        else:
            zone_h = (1000 + phone_total_h) - phone_y

        ET.SubElement(
            phone_flow_zone,
            "zone",
            attrib={
                "id": str(phone_zone_id),
                "name": ws_name,
                "type-v2": "worksheet",
                "show-title": "false",
                "x": "1000",
                "y": str(phone_y),
                "w": str(phone_w),
                "h": str(zone_h),
            },
        )

        phone_y += zone_h
        phone_zone_id += 1


def _collect_visual_nodes(items: list, out: dict[str, dict]) -> None:
    if not isinstance(items, list):
        return

    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            out[name.strip().lower()] = {
                "name": name.strip(),
                "visual_type": item.get("visual_type") if isinstance(item.get("visual_type"), str) else "",
                "dataset_name": item.get("dataset_name") if isinstance(item.get("dataset_name"), str) else "",
                "expressions": item.get("expressions") if isinstance(item.get("expressions"), list) else [],
                "properties": item.get("properties") if isinstance(item.get("properties"), dict) else {},
            }
        children = item.get("children", [])
        if isinstance(children, list):
            _collect_visual_nodes(children, out)


def _extract_fields_from_expressions(expressions: list) -> list[str]:
    names: list[str] = []
    if not isinstance(expressions, list):
        return names

    for expr in expressions:
        if not isinstance(expr, str):
            continue
        for match in FIELD_EXPR_REF_RE.finditer(expr):
            field_name = match.group(1).strip()
            if field_name and field_name not in names:
                names.append(field_name)

    return names


def _extract_fields_from_visual_node(visual_node: dict) -> list[str]:
    names: list[str] = []

    def _append(field_name: str) -> None:
        clean = field_name.strip()
        if clean and clean not in names:
            names.append(clean)

    expressions = visual_node.get("expressions") if isinstance(visual_node.get("expressions"), list) else []
    for field_name in _extract_fields_from_expressions(expressions):
        _append(field_name)

    properties = visual_node.get("properties") if isinstance(visual_node.get("properties"), dict) else {}
    for field_name in properties.get("field_references") if isinstance(properties.get("field_references"), list) else []:
        if isinstance(field_name, str):
            _append(field_name)

    for filter_item in properties.get("filters") if isinstance(properties.get("filters"), list) else []:
        if not isinstance(filter_item, dict):
            continue
        candidates = [filter_item.get("expression"), *(filter_item.get("values") if isinstance(filter_item.get("values"), list) else [])]
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            for field_name in _extract_fields_from_expressions([candidate]):
                _append(field_name)

    tablix = properties.get("tablix") if isinstance(properties.get("tablix"), dict) else {}
    for cell_expr in tablix.get("cell_expressions") if isinstance(tablix.get("cell_expressions"), list) else []:
        if not isinstance(cell_expr, str):
            continue
        for field_name in _extract_fields_from_expressions([cell_expr]):
            _append(field_name)

    return names


def _extract_static_text_from_visual_properties(properties: dict) -> str:
    if not isinstance(properties, dict):
        return ""

    text_values = _extract_static_text_values_from_visual_properties(properties)
    parts: list[str] = []
    for raw in text_values:
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        if text.startswith("="):
            param_match = PARAM_EXPR_REF_RE.fullmatch(text[1:].strip())
            global_match = re.fullmatch(r"Globals!([A-Za-z0-9_]+)", text[1:].strip(), flags=re.IGNORECASE)
            if param_match is not None:
                text = f"[{param_match.group(1).strip()}]"
            elif global_match is not None:
                text = f"[{global_match.group(1).strip()}]"
            else:
                continue
        if text and text not in parts:
            parts.append(text)

    return " ".join(parts).strip()


def _extract_static_text_values_from_visual_properties(properties: dict) -> list[str]:
    if not isinstance(properties, dict):
        return []
    text_values = properties.get("text_values") if isinstance(properties.get("text_values"), list) else []
    return [value for value in text_values if isinstance(value, str) and value.strip()]


def _derive_sheet_semantic_kind(sheet_name: str, visual_type: str, visual_node: dict) -> str:
    name_hint = (sheet_name or "").strip().lower()
    type_hint = (visual_type or "").strip().lower()

    properties = visual_node.get("properties") if isinstance(visual_node.get("properties"), dict) else {}
    semantic_hint = properties.get("semantic_hint") if isinstance(properties.get("semantic_hint"), str) else ""
    if semantic_hint.strip().lower() == "static_text":
        return "static_text"

    if "filter" in type_hint or name_hint.startswith("filter"):
        return "filter"
    if "gauge" in type_hint:
        return "gauge"
    if "image" in type_hint:
        return "image"

    section_hint = (
        properties.get("container_section")
        if isinstance(properties.get("container_section"), str)
        else ""
    ).strip().lower()

    if "pageheader" in section_hint:
        return "header"
    if "pagefooter" in section_hint:
        return "footer"

    if "textbox" in type_hint or "text" in type_hint:
        if any(token in name_hint for token in ["header", "title", "subtitle"]):
            return "header"
        if any(token in name_hint for token in ["footer", "footnote"]):
            return "footer"

        if semantic_hint.strip().lower() == "kpi":
            expressions = visual_node.get("expressions") if isinstance(visual_node.get("expressions"), list) else []
            if any(AGGREGATE_EXPR_HINT_RE.search(expr) for expr in expressions if isinstance(expr, str)):
                return "kpi"

            refs = properties.get("field_references") if isinstance(properties.get("field_references"), list) else []
            if any(_looks_kpi_field_name(ref) for ref in refs if isinstance(ref, str)):
                return "kpi"
            return "text"

        expressions = visual_node.get("expressions") if isinstance(visual_node.get("expressions"), list) else []
        text_values = properties.get("text_values") if isinstance(properties.get("text_values"), list) else []
        blob = " ".join([x for x in expressions + text_values if isinstance(x, str)]).lower()
        if any(AGGREGATE_EXPR_HINT_RE.search(expr) for expr in expressions if isinstance(expr, str)):
            return "kpi"
        if "fields!" in blob and any(
            token in blob for token in ["sales", "quota", "amount", "target", "variance", "quantity", "kpi", "%"]
        ):
            return "kpi"
        if _extract_static_text_from_visual_properties(properties):
            return "static_text"
        return "text"

    if any(token in name_hint for token in ["header", "title", "subtitle"]):
        return "header"
    if any(token in name_hint for token in ["footer", "footnote"]):
        return "footer"

    return "chart"


def _should_skip_sheet_spec(
    visual_name: str,
    visual_type: str,
    semantic_kind: str,
    referenced_fields: list[str],
    visual_properties: dict,
) -> bool:
    vt = (visual_type or "").strip().lower()
    kind = (semantic_kind or "").strip().lower()
    section = (
        visual_properties.get("container_section")
        if isinstance(visual_properties.get("container_section"), str)
        else ""
    ).strip().lower()

    if vt == "rectangle":
        return True

    if kind == "filter":
        return True

    _ = visual_name  # Keep signature explicit for future naming heuristics.
    return False


def _looks_kpi_field_name(field_name: str) -> bool:
    token = (field_name or "").strip().lower()
    if not token:
        return False

    if any(mark in token for mark in ["territory", "region", "country", "state", "city", "group"]):
        return False

    if any(mark in token for mark in ["sales", "amount", "quota", "quantity", "revenue", "profit", "cost", "total", "pct", "percent", "ratio"]):
        return True

    return False


def _rebuild_worksheets_from_sheet_specs(
    root: ET.Element,
    sheet_specs: list[dict],
    datasource_names: set[str],
    default_ds_name: str,
    dataset_to_datasource: dict[str, str],
    dataset_to_fields: dict[str, list[str]],
    dataset_alias_to_source: dict[str, dict[str, str]],
    dataset_source_to_alias: dict[str, dict[str, str]],
    global_field_names: list[str],
    datasource_field_roles: dict[str, dict[str, str]],
    datasource_field_tables: dict[str, dict[str, str]],
    datasource_field_names: dict[str, list[str]],
) -> None:
    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is None:
        worksheets = ET.SubElement(root, "worksheets")

    for child in list(worksheets):
        worksheets.remove(child)

    specs = sheet_specs if sheet_specs else [{"name": "Sheet 1", "visual_type": "Chart", "dataset_name": "", "fields": []}]

    for idx, spec in enumerate(specs, start=1):
        raw_name = spec.get("name") if isinstance(spec.get("name"), str) else ""
        worksheet_name = raw_name.strip() or f"Sheet {idx}"
        visual_type = spec.get("visual_type") if isinstance(spec.get("visual_type"), str) else ""
        semantic_kind = spec.get("semantic_kind") if isinstance(spec.get("semantic_kind"), str) else ""
        template_role = spec.get("template_role") if isinstance(spec.get("template_role"), str) else ""

        dataset_name = spec.get("dataset_name") if isinstance(spec.get("dataset_name"), str) else ""
        sheet_ds_name = dataset_to_datasource.get(dataset_name, "")
        if not sheet_ds_name or sheet_ds_name not in datasource_names:
            sheet_ds_name = default_ds_name

        dataset_fields = dataset_to_fields.get(dataset_name, []) if dataset_name else []
        alias_to_source = dataset_alias_to_source.get(dataset_name, {}) if dataset_name else {}
        source_to_alias = dataset_source_to_alias.get(dataset_name, {}) if dataset_name else {}
        field_roles = datasource_field_roles.get(sheet_ds_name.strip().lower(), {})
        field_tables = datasource_field_tables.get(sheet_ds_name.strip().lower(), {})
        ds_fields = datasource_field_names.get(sheet_ds_name.strip().lower(), [])
        referenced_fields = spec.get("fields") if isinstance(spec.get("fields"), list) else []
        candidate_pool: list[str] = []
        for source in [dataset_fields, ds_fields, global_field_names]:
            for field_name in source:
                if not isinstance(field_name, str) or not field_name.strip():
                    continue
                clean = field_name.strip()
                if clean not in candidate_pool:
                    candidate_pool.append(clean)

        def _resolve_hint_field(hint_name: str) -> str:
            if not isinstance(hint_name, str) or not hint_name.strip():
                return ""
            clean_hint = hint_name.strip()
            canonical = alias_to_source.get(clean_hint.lower(), clean_hint)
            if canonical in candidate_pool:
                return canonical
            fuzzy = _find_best_field_match(canonical, candidate_pool)
            return fuzzy or canonical

        dim_field, measure_field, has_numeric_measure = _choose_sheet_dim_measure_fields(
            referenced_fields=referenced_fields,
            dataset_fields=dataset_fields,
            global_field_names=global_field_names,
            alias_to_source=alias_to_source,
            field_roles=field_roles,
            field_tables=field_tables,
            datasource_fields=ds_fields,
            visual_type=visual_type,
            worksheet_name=worksheet_name,
        )

        expression_measure_field = _resolve_expression_measure_field_for_spec(
            spec=spec,
            semantic_kind=semantic_kind,
            candidate_pool=candidate_pool,
            field_roles=field_roles,
        )
        if expression_measure_field:
            measure_field = expression_measure_field
            has_numeric_measure = True
        if semantic_kind == "gauge" and field_roles.get(dim_field.lower(), "") != "dimension":
            context_dim_field = _select_context_dimension_field(candidate_pool, field_roles)
            if context_dim_field:
                dim_field = context_dim_field

        if semantic_kind in {"kpi", "gauge"} and not expression_measure_field:
            for candidate in referenced_fields:
                if not isinstance(candidate, str):
                    continue
                clean_candidate = candidate.strip()
                if not clean_candidate:
                    continue
                if field_roles.get(clean_candidate.lower()) == "measure":
                    measure_field = clean_candidate
                    has_numeric_measure = True
                    break

        dim_caption = dim_field
        measure_caption = measure_field

        mark_class = _visual_type_to_mark_class(visual_type, semantic_kind=semantic_kind) or "Bar"
        if template_role == "gauge_by_region":
            mark_class = "Bar"
        if semantic_kind == "gauge" and not has_numeric_measure:
            mark_class = "Text"
        if not has_numeric_measure and semantic_kind not in {"gauge", "image", "filter"}:
            mark_class = "Text"

        ws = ET.SubElement(worksheets, "worksheet", attrib={"name": worksheet_name})
        ET.SubElement(ws, "layout-options")

        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        view_dss = ET.SubElement(view, "datasources")
        ET.SubElement(view_dss, "datasource", attrib={"name": sheet_ds_name})

        deps = ET.SubElement(view, "datasource-dependencies", attrib={"datasource": sheet_ds_name})
        include_measure_binding = has_numeric_measure
        if template_role in {"logo_placeholder", "static_text", "region_title"} or semantic_kind in {
            "header",
            "footer",
            "image",
            "filter",
        }:
            include_measure_binding = False
        _append_default_dependency_bindings(
            deps_node=deps,
            dim_field=dim_field,
            measure_field=measure_field,
            dim_caption=dim_caption,
            measure_caption=measure_caption,
            add_columns=True,
            add_instances=True,
            include_measure=include_measure_binding,
        )

        ET.SubElement(view, "perspectives")
        ET.SubElement(view, "aggregation", attrib={"value": "true"})

        ET.SubElement(table, "style")
        panes = ET.SubElement(table, "panes")
        pane = ET.SubElement(panes, "pane")
        _ensure_pane_view(pane)
        ET.SubElement(pane, "mark", attrib={"class": mark_class})

        rows = ET.SubElement(table, "rows")
        cols = ET.SubElement(table, "cols")
        static_text = spec.get("static_text") if isinstance(spec.get("static_text"), str) else ""
        if static_text.strip() and semantic_kind in {"header", "footer", "static_text", "text"}:
            static_field = _static_text_field_name(worksheet_name)
            _append_dependency_binding(deps, static_field, role="dimension", datatype="string", type_value="nominal")
            rows.text = ""
            cols.text = ""
            _set_text_pane_encoding(pane, sheet_ds_name, static_field, role="dimension")
            continue

        if semantic_kind == "text":
            _append_dependency_binding(deps, dim_field, role="dimension")
            rows.text = ""
            cols.text = ""
            _set_text_pane_encoding(pane, sheet_ds_name, dim_field, role="dimension")
            continue

        if template_role == "logo_placeholder":
            logo_field = dim_field
            for ref in referenced_fields:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                resolved = _resolve_hint_field(ref)
                if resolved:
                    logo_field = resolved
                    break
            _append_dependency_binding(deps, logo_field, role="dimension")
            rows.text = ""
            cols.text = f"[{sheet_ds_name}].[none:{logo_field}:nk]"
            continue

        if template_role == "static_text":
            text_field = dim_field
            for ref in referenced_fields:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                resolved = _resolve_hint_field(ref)
                if resolved:
                    text_field = resolved
                    break
            _append_dependency_binding(deps, text_field, role="dimension")
            rows.text = ""
            if text_field.strip().lower() == "reportsubtitletext":
                _promote_dependency_instance_to_attribute(deps, text_field)
                year_field = _resolve_hint_field("CalendarYear") or "CalendarYear"
                group_field = (
                    _resolve_hint_field("SalesTerritoryGroup")
                    or _resolve_hint_field("SalesTerritoryRegion")
                    or "SalesTerritoryGroup"
                )
                _append_dependency_binding(deps, year_field, role="dimension")
                _append_dependency_binding(deps, group_field, role="dimension")
                cols.text = f"[{sheet_ds_name}].[attr:{text_field}:nk]"
            else:
                cols.text = f"[{sheet_ds_name}].[none:{text_field}:nk]"
            continue

        if template_role == "region_title":
            region_field = dim_field
            for ref in referenced_fields:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                resolved = _resolve_hint_field(ref)
                if resolved:
                    region_field = resolved
                    break
            _append_dependency_binding(deps, region_field, role="dimension")
            rows.text = f"[{sheet_ds_name}].[none:{region_field}:nk]"
            cols.text = ""
            continue

        if template_role == "kpi_by_region":
            kpi_dim_field = dim_field
            kpi_measure_field = measure_field
            for ref in referenced_fields:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                resolved = _resolve_hint_field(ref)
                if not resolved:
                    continue
                role = field_roles.get(resolved.lower(), "")
                if role == "dimension" and (
                    _looks_geographic_dimension_name(resolved)
                    or "region" in resolved.lower()
                    or "territory" in resolved.lower()
                ):
                    kpi_dim_field = resolved
                elif role == "measure":
                    kpi_measure_field = resolved

            _append_dependency_binding(deps, kpi_dim_field, role="dimension")
            if field_roles.get(kpi_measure_field.lower(), "") == "measure":
                _append_dependency_binding(deps, kpi_measure_field, role="measure")

            rows.text = ""
            cols.text = ""
            if field_roles.get(kpi_measure_field.lower(), "") == "measure":
                _set_text_pane_encoding(pane, sheet_ds_name, kpi_measure_field, role="measure")
            else:
                _set_text_pane_encoding(pane, sheet_ds_name, kpi_dim_field, role="dimension")
            continue

        if template_role == "gauge_by_region":
            gauge_dim_field = _resolve_hint_field("SalesTerritoryRegion") or dim_field
            gauge_measure_field = (
                _resolve_hint_field("SalesQuotaPct")
                or _resolve_hint_field("SalesVariancePct")
                or _resolve_hint_field("Sales")
                or measure_field
            )

            _append_dependency_binding(deps, gauge_dim_field, role="dimension")
            if field_roles.get(gauge_measure_field.lower(), "") == "measure":
                _append_dependency_binding(deps, gauge_measure_field, role="measure")

            rows.text = f"[{sheet_ds_name}].[none:{gauge_dim_field}:nk]"
            cols.text = (
                f"[{sheet_ds_name}].[sum:{gauge_measure_field}:qk]"
                if field_roles.get(gauge_measure_field.lower(), "") == "measure"
                else ""
            )
            continue

        if template_role == "dual_line_chart":
            month_field = _resolve_hint_field("Month") or dim_field
            month_sort_field = _resolve_hint_field("MonthSortOrder") or _resolve_hint_field("MonthKey")
            sales_field = _resolve_hint_field("Sales") or measure_field
            quota_field = _resolve_hint_field("Quota")

            if month_sort_field and month_sort_field.lower() == month_field.lower():
                month_sort_field = ""

            _append_dependency_binding(deps, month_field, role="dimension")
            if month_sort_field:
                _append_dependency_binding(deps, month_sort_field, role="dimension")
            _append_dependency_binding(deps, sales_field, role="measure")
            if quota_field and quota_field.lower() != sales_field.lower():
                _append_dependency_binding(deps, quota_field, role="measure")

            rows_expr = f"[{sheet_ds_name}].[sum:{sales_field}:qk]"
            if quota_field and quota_field.lower() != sales_field.lower():
                rows_expr = f"{rows_expr}/[{sheet_ds_name}].[sum:{quota_field}:qk]"

            rows.text = rows_expr
            if month_sort_field:
                cols.text = (
                    f"[{sheet_ds_name}].[none:{month_sort_field}:nk]"
                    f"/[{sheet_ds_name}].[none:{month_field}:nk]"
                )
            else:
                cols.text = f"[{sheet_ds_name}].[none:{month_field}:nk]"
            continue

        if semantic_kind in {"header", "footer", "image"}:
            rows.text = ""
            cols.text = ""
            continue

        if semantic_kind == "filter":
            rows.text = ""
            cols.text = f"[{sheet_ds_name}].[none:{dim_field}:nk]"
            continue

        if semantic_kind == "kpi":
            rows.text = ""
            cols.text = ""
            if has_numeric_measure:
                _set_text_pane_encoding(pane, sheet_ds_name, measure_field, role="measure")
            else:
                _set_text_pane_encoding(pane, sheet_ds_name, dim_field, role="dimension")
            continue

        if semantic_kind == "gauge" and has_numeric_measure:
            rows.text = ""
            cols.text = ""
            _set_pie_pane_encodings(pane, sheet_ds_name, dim_field, measure_field)
            continue

        if _is_pie_mark_class(mark_class):
            rows.text = ""
            cols.text = ""
            _set_pie_pane_encodings(pane, sheet_ds_name, dim_field, measure_field)
            continue

        if _is_title_like_text_sheet(mark_class, visual_type, worksheet_name, semantic_kind):
            rows.text = ""
            cols.text = ""
            continue

        if not has_numeric_measure:
            rows.text = ""
            cols.text = f"[{sheet_ds_name}].[none:{dim_field}:nk]"
            continue

        if _prefer_horizontal_bar_layout(mark_class, worksheet_name, visual_type, dim_field):
            rows.text = f"[{sheet_ds_name}].[none:{dim_field}:nk]"
            cols.text = f"[{sheet_ds_name}].[sum:{measure_field}:qk]"
        else:
            rows.text = f"[{sheet_ds_name}].[sum:{measure_field}:qk]"
            cols.text = f"[{sheet_ds_name}].[none:{dim_field}:nk]"


def _choose_sheet_dim_measure_fields(
    referenced_fields: list,
    dataset_fields: list[str],
    global_field_names: list[str],
    alias_to_source: dict[str, str] | None = None,
    field_roles: dict[str, str] | None = None,
    field_tables: dict[str, str] | None = None,
    datasource_fields: list[str] | None = None,
    visual_type: str = "",
    worksheet_name: str = "",
) -> tuple[str, str, bool]:
    alias_map = alias_to_source if isinstance(alias_to_source, dict) else {}
    role_map = field_roles if isinstance(field_roles, dict) else {}
    table_map = field_tables if isinstance(field_tables, dict) else {}
    datasource_scoped = datasource_fields if isinstance(datasource_fields, list) else []
    scoped = [f for f in dataset_fields if isinstance(f, str) and f.strip()]
    global_scoped = [f for f in global_field_names if isinstance(f, str) and f.strip()]

    def _role_of(field_name: str) -> str:
        return role_map.get(field_name.lower(), "") if isinstance(field_name, str) else ""

    def _table_of(field_name: str) -> str:
        return table_map.get(field_name.lower(), "") if isinstance(field_name, str) else ""

    def _uniq(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                continue
            clean = item.strip()
            if clean not in out:
                out.append(clean)
        return out

    candidates: list[str] = []
    search_pool = _uniq(scoped + datasource_scoped + global_scoped)
    for raw in referenced_fields:
        if not isinstance(raw, str):
            continue
        clean = raw.strip()
        if not clean:
            continue
        canonical = alias_map.get(clean.lower(), clean)
        resolved = canonical

        if scoped and resolved not in scoped:
            fuzzy = _find_best_field_match(resolved, search_pool)
            if not fuzzy:
                continue
            resolved = fuzzy

        if resolved not in candidates:
            candidates.append(resolved)

    if not candidates:
        candidates = scoped[:] if scoped else global_scoped[:]

    if not candidates:
        return "Field1", "Field2", True

    dim_field = candidates[0]
    for candidate in candidates:
        if _role_of(candidate) == "dimension" and not _is_key_like_column_name(candidate):
            dim_field = candidate
            break
    else:
        for candidate in candidates:
            if _role_of(candidate) == "dimension":
                dim_field = candidate
                break

    visual_hint = f"{visual_type or ''} {worksheet_name or ''}".strip().lower()
    all_candidates = _uniq(candidates + scoped + datasource_scoped + global_scoped)

    if _looks_geographic_dimension_name(visual_hint):
        geo_candidates = [
            c
            for c in all_candidates
            if _role_of(c) == "dimension" and _looks_geographic_dimension_name(c)
        ]
        if geo_candidates:
            dim_field = geo_candidates[0]

    if _looks_time_series_hint(visual_hint):
        # Keep the originally resolved temporal label when already good (e.g., EnglishMonthName).
        if _is_key_like_column_name(dim_field) or not _looks_temporal_dimension_name(dim_field):
            temporal_candidates = [
                c
                for c in all_candidates
                if _role_of(c) == "dimension"
                and _looks_temporal_dimension_name(c)
                and not _is_key_like_column_name(c)
            ]
            if temporal_candidates:
                dim_field = sorted(temporal_candidates, key=_temporal_dimension_priority_score)[0]

    measure_field = dim_field
    measure_options: list[str] = []
    for candidate in candidates:
        if candidate == dim_field:
            continue
        if _role_of(candidate) == "measure":
            measure_options.append(candidate)

    if not measure_options:
        for candidate in all_candidates:
            if candidate == dim_field:
                continue
            if _role_of(candidate) == "measure":
                measure_options.append(candidate)

    hinted_measure = _pick_measure_by_visual_hint(measure_options, visual_hint)
    if hinted_measure:
        measure_field = hinted_measure

    if measure_options and not hinted_measure:
        measure_field = sorted(measure_options, key=_measure_priority_score)[0]

    has_numeric_measure = _role_of(measure_field) == "measure"

    if not has_numeric_measure:
        fallback_options: list[str] = []
        for candidate in candidates:
            if candidate == dim_field:
                continue
            if _role_of(candidate) == "measure":
                fallback_options.append(candidate)
        if not fallback_options:
            for candidate in all_candidates:
                if candidate == dim_field:
                    continue
                if _role_of(candidate) == "measure":
                    fallback_options.append(candidate)
        if fallback_options:
            fallback_hint = _pick_measure_by_visual_hint(fallback_options, visual_hint)
            measure_field = fallback_hint or sorted(fallback_options, key=_measure_priority_score)[0]
            has_numeric_measure = True

    if measure_field == dim_field and len(candidates) > 1:
        for candidate in candidates:
            if candidate != dim_field:
                measure_field = candidate
                break

    if not has_numeric_measure:
        has_numeric_measure = _role_of(measure_field) == "measure"

    return dim_field, measure_field, has_numeric_measure


def _find_best_field_match(requested_field: str, available_fields: list[str]) -> str:
    requested = (requested_field or "").strip()
    if not requested:
        return ""

    requested_tokens = _field_name_tokens(requested)
    requested_norm = "".join(requested_tokens)
    if not requested_norm:
        return ""

    best_field = ""
    best_score = -1
    for candidate in available_fields:
        if not isinstance(candidate, str):
            continue
        clean = candidate.strip()
        if not clean:
            continue

        candidate_tokens = _field_name_tokens(clean)
        candidate_norm = "".join(candidate_tokens)
        if not candidate_norm:
            continue

        score = 0
        if candidate_norm == requested_norm:
            score += 240
        if candidate_norm.startswith(requested_norm) or requested_norm.startswith(candidate_norm):
            score += 120
        if requested_norm in candidate_norm or candidate_norm in requested_norm:
            score += 90

        overlap = len(set(requested_tokens).intersection(candidate_tokens))
        score += overlap * 35

        req_token_set = set(requested_tokens)
        cand_token_set = set(candidate_tokens)
        if "sales" in req_token_set and ("amount" in cand_token_set or "sales" in cand_token_set):
            score += 25
        if "quota" in req_token_set and "quota" in cand_token_set:
            score += 30
        if "quantity" in req_token_set and ("quantity" in cand_token_set or "order" in cand_token_set):
            score += 25
        if "month" in req_token_set and ("month" in cand_token_set or "date" in cand_token_set):
            score += 20
        if "region" in req_token_set and (
            "region" in cand_token_set or "territory" in cand_token_set or "group" in cand_token_set
        ):
            score += 25

        if any(mark in cand_token_set for mark in ["line", "revision"]) and "line" not in req_token_set:
            score -= 35
        if "number" in cand_token_set and "number" not in req_token_set:
            score -= 15

        if _is_key_like_column_name(clean) and "key" not in req_token_set:
            score -= 30

        if score > best_score:
            best_score = score
            best_field = clean

    return best_field if best_score >= 40 else ""


def _field_name_tokens(field_name: str) -> list[str]:
    text = (field_name or "").strip()
    if text.startswith("[") and text.endswith("]") and len(text) >= 2:
        text = text[1:-1]
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return [tok.lower() for tok in text.split() if tok]


def _looks_geographic_dimension_name(column_name: str) -> bool:
    token = (column_name or "").strip().lower()
    if not token:
        return False
    return any(mark in token for mark in ["region", "territory", "country", "state", "city", "group"])


def _looks_time_series_hint(text: str) -> bool:
    token = (text or "").strip().lower()
    if not token:
        return False
    return any(mark in token for mark in ["month", "year", "quarter", "week", "day", "date", "trend", "timeline", "line", "spline"])


def _temporal_dimension_priority_score(field_name: str) -> int:
    token = (field_name or "").strip().lower()
    score = 100
    if "date" in token:
        score -= 35
    if "month" in token:
        score -= 25
    if "year" in token:
        score -= 15
    if "name" in token:
        score -= 10
    if "number" in token:
        score += 30
    if _is_key_like_column_name(token):
        score += 80
    return score


def _pick_measure_by_visual_hint(measure_candidates: list[str], visual_hint: str) -> str:
    if not isinstance(measure_candidates, list) or not measure_candidates:
        return ""

    hint = (visual_hint or "").strip().lower()
    if not hint:
        return ""

    scenario = ""
    if "quota" in hint or "target" in hint:
        scenario = "quota"
    elif "quantity" in hint or "qty" in hint or "volume" in hint:
        scenario = "quantity"
    elif "sales" in hint or "revenue" in hint or "amount" in hint:
        scenario = "sales"

    if not scenario:
        return ""

    best_field = ""
    best_score = -1
    for field_name in measure_candidates:
        token = (field_name or "").strip().lower()
        score = 0

        if scenario == "quota":
            if "quota" in token:
                score += 140
            if "target" in token:
                score += 90
        elif scenario == "quantity":
            if "quantity" in token:
                score += 140
            if "qty" in token:
                score += 120
            if "count" in token:
                score += 60
            if "order" in token:
                score += 45
        elif scenario == "sales":
            if "sales" in token:
                score += 140
            if "revenue" in token:
                score += 120
            if "amount" in token:
                score += 55
            if "discount" in token:
                score -= 120
            if "quota" in token:
                score -= 20

        # Secondary preference: keep better business metrics when scores tie.
        score -= _measure_priority_score(field_name)

        if score > best_score:
            best_score = score
            best_field = field_name

    if best_score <= 0:
        return ""

    return best_field


def _prefer_horizontal_bar_layout(mark_class: str, worksheet_name: str, visual_type: str, dim_field: str) -> bool:
    if not isinstance(mark_class, str) or mark_class.strip().lower() != "bar":
        return False

    hint = f"{worksheet_name or ''} {visual_type or ''}".strip().lower()
    if _looks_geographic_dimension_name(hint):
        return True

    return _looks_geographic_dimension_name(dim_field)


def normalize_generated_twb(xml_content: str) -> str:
    """Normalize generated TWB XML to a strict, schema-safe workbook core."""
    root = ET.fromstring(xml_content)
    if _local_name(root.tag) != "workbook":
        raise ValueError("Generated XML root must be <workbook>")

    _strip_namespaces(root)
    _strip_unexpected_workbook_character_data(root)
    _remove_schema_definition_nodes(root)

    _ensure_required_workbook_sections(root)
    _sanitize_datasources(root)
    _sanitize_worksheets(root)
    _sanitize_windows(root)
    _remove_disallowed_sort_nodes(root)
    _prune_to_safe_workbook_core(root)
    _reorder_workbook_children(root)

    # Force a known-working workbook header profile to avoid version-specific parser regressions.
    root.attrib["version"] = "18.1"
    root.attrib["source-build"] = "2024.2.0 (20242.24.0620.1454)"
    root.attrib["source-platform"] = "win"

    return ET.tostring(root, encoding="unicode")


def _rebuild_compat_workbook(source_root: ET.Element) -> ET.Element:
    """Create a clean workbook structure similar to known working TWB files."""
    workbook = ET.Element(
        "workbook",
        attrib={
            "version": source_root.attrib.get("version", "18.1"),
            "source-build": source_root.attrib.get("source-build", "2024.2.0 (20242.24.0620.1454)"),
            "source-platform": source_root.attrib.get("source-platform", "win"),
        },
    )

    datasources_data = _extract_datasource_payload(source_root)
    worksheets_data = _extract_worksheet_payload(source_root)

    datasources_el = ET.SubElement(workbook, "datasources")
    for ds in datasources_data:
        ds_node = ET.SubElement(
            datasources_el,
            "datasource",
            attrib={
                "name": ds["name"],
                "caption": ds["caption"],
                "inline": "true",
            },
        )

        conn_attrib = {k: v for k, v in ds["connection"].items() if v is not None}
        if "class" not in conn_attrib:
            conn_attrib["class"] = "genericodbc"
        ET.SubElement(ds_node, "connection", attrib=conn_attrib)

        ET.SubElement(ds_node, "aliases", attrib={"enabled": "yes"})
        ET.SubElement(
            ds_node,
            "layout",
            attrib={
                "dim-ordering": "alphabetic",
                "measure-ordering": "alphabetic",
                "dim-percentage": "0.50",
                "measure-percentage": "0.50",
                "show-structure": "true",
            },
        )
        ET.SubElement(ds_node, "style")

    primary_ds_name = datasources_data[0]["name"] if datasources_data else "DataSource_1"

    worksheets_el = ET.SubElement(workbook, "worksheets")
    for ws in worksheets_data:
        ws_node = ET.SubElement(worksheets_el, "worksheet", attrib={"name": ws})
        ET.SubElement(ws_node, "layout-options")

        table = ET.SubElement(ws_node, "table")
        view = ET.SubElement(table, "view")
        view_ds = ET.SubElement(view, "datasources")
        ET.SubElement(view_ds, "datasource", attrib={"name": primary_ds_name})
        ET.SubElement(view, "datasource-dependencies", attrib={"datasource": primary_ds_name})
        ET.SubElement(view, "perspectives")
        ET.SubElement(view, "aggregation", attrib={"value": "true"})

        ET.SubElement(table, "style")
        ET.SubElement(table, "panes")
        ET.SubElement(table, "rows")
        ET.SubElement(table, "cols")

    windows_el = ET.SubElement(workbook, "windows")
    for ws in worksheets_data:
        win = ET.SubElement(windows_el, "window", attrib={"name": ws, "class": "worksheet"})
        ET.SubElement(win, "cards")
        ET.SubElement(win, "viewpoint")

    return workbook


def _extract_datasource_payload(source_root: ET.Element) -> list[dict]:
    payload: list[dict] = []
    for ds in [el for el in source_root.iter() if _local_name(el.tag) == "datasource"]:
        name = ds.attrib.get("name")
        if not name:
            continue
        caption = ds.attrib.get("caption") or name
        conn = _find_direct_child(ds, "connection")
        conn_attrib = dict(conn.attrib) if conn is not None else {}
        payload.append({"name": name, "caption": caption, "connection": conn_attrib})

    if not payload:
        payload = [
            {
                "name": "DataSource_1",
                "caption": "DataSource_1",
                "connection": {"class": "genericodbc"},
            }
        ]
    return payload


def _extract_worksheet_payload(source_root: ET.Element) -> list[str]:
    names: list[str] = []
    for ws in [el for el in source_root.iter() if _local_name(el.tag) == "worksheet"]:
        name = ws.attrib.get("name")
        if name and name not in names:
            names.append(name)

    if not names:
        names = ["Sheet 1"]
    return names


def _find_or_create_datasource(datasources_node: ET.Element, datasource_name: str) -> ET.Element:
    for node in datasources_node.findall("datasource"):
        if node.attrib.get("name") == datasource_name:
            return node

    return ET.SubElement(
        datasources_node,
        "datasource",
        attrib={
            "name": datasource_name,
            "caption": datasource_name,
            "inline": "true",
        },
    )


def _looks_like_snowflake_connection(provider: str, connection_string: str) -> bool:
    provider_token = (provider or "").strip().lower()
    conn = (connection_string or "").strip().lower()

    if "snowflake" in provider_token:
        return True
    return "snowflake" in conn


def _provider_to_tableau_class(provider: str, connection_string: str = "") -> str:
    if _looks_like_snowflake_connection(provider, connection_string):
        return "snowflake"
    if "sql" in provider:
        return "sqlserver"
    if "oracle" in provider:
        return "oracle"
    if "postgres" in provider:
        return "postgres"
    if "mysql" in provider:
        return "mysql"
    return "genericodbc"


def _parse_connection_string(connection_string: str) -> dict[str, str]:
    parts = [p.strip() for p in connection_string.split(";") if p.strip()]
    kv: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        kv[key.strip().lower()] = value.strip()

    dsn = kv.get("dsn") or kv.get("odbc dsn") or ""
    account = kv.get("account") or ""
    server = kv.get("data source") or kv.get("server") or kv.get("host") or kv.get("address") or ""
    if not server and account:
        server = f"{account}.snowflakecomputing.com"
    dbname = kv.get("initial catalog") or kv.get("database") or kv.get("dbname") or kv.get("db") or ""
    schema = kv.get("schema") or kv.get("current schema") or ""
    username = kv.get("uid") or kv.get("user id") or kv.get("user") or kv.get("username") or ""
    warehouse = kv.get("warehouse") or ""
    role = kv.get("role") or ""
    authenticator = kv.get("authenticator") or ""

    port = ""
    # SQL Server style: server,1433
    if server and "," in server:
        server_part, port_part = server.split(",", 1)
        server = server_part.strip()
        if re.fullmatch(r"\d+", port_part.strip()):
            port = port_part.strip()

    if not port:
        declared_port = kv.get("port") or kv.get("tcp port")
        if isinstance(declared_port, str) and re.fullmatch(r"\d+", declared_port.strip()):
            port = declared_port.strip()

    excluded_keys = {
        "data source",
        "server",
        "address",
        "addr",
        "network address",
        "initial catalog",
        "database",
        "dbname",
        "port",
        "tcp port",
    }
    extras_parts: list[str] = []
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip().lower() in excluded_keys:
            continue
        extras_parts.append(f"{key.strip()}={value.strip()}")

    return {
        "dsn": dsn,
        "server": server,
        "dbname": dbname,
        "port": port,
        "schema": schema,
        "username": username,
        "warehouse": warehouse,
        "role": role,
        "authenticator": authenticator,
        "odbc_connect_string_extras": ";".join(extras_parts),
    }


def _enrich_snowflake_connection_details(parsed_conn: dict[str, str]) -> dict[str, str]:
    enriched = dict(parsed_conn)
    dsn_name = (enriched.get("dsn") or "").strip()
    if not dsn_name:
        return enriched

    dsn_values = _resolve_windows_odbc_dsn(dsn_name)
    if not dsn_values:
        return enriched

    for key in ["server", "dbname", "schema", "username", "warehouse", "role", "authenticator"]:
        if not enriched.get(key) and dsn_values.get(key):
            enriched[key] = dsn_values[key]

    return enriched


def _resolve_windows_odbc_dsn(dsn_name: str) -> dict[str, str]:
    if winreg is None:
        return {}

    candidate_paths = [
        fr"Software\ODBC\ODBC.INI\{dsn_name}",
        fr"Software\WOW6432Node\ODBC\ODBC.INI\{dsn_name}",
    ]
    hives = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]

    values: dict[str, str] = {}

    def _read_value(key_obj, name: str) -> str:
        try:
            raw, _ = winreg.QueryValueEx(key_obj, name)
        except OSError:
            return ""
        return str(raw).strip() if raw is not None else ""

    for hive in hives:
        for reg_path in candidate_paths:
            try:
                with winreg.OpenKey(hive, reg_path) as key_obj:
                    server = _read_value(key_obj, "SERVER") or _read_value(key_obj, "HOST")
                    account = _read_value(key_obj, "ACCOUNT")
                    if not server and account:
                        server = f"{account}.snowflakecomputing.com"

                    if server:
                        values.setdefault("server", server)
                    database = _read_value(key_obj, "DATABASE") or _read_value(key_obj, "DB")
                    if database:
                        values.setdefault("dbname", database)
                    schema = _read_value(key_obj, "SCHEMA")
                    if schema:
                        values.setdefault("schema", schema)
                    user = _read_value(key_obj, "UID") or _read_value(key_obj, "USER") or _read_value(key_obj, "USERNAME")
                    if user:
                        values.setdefault("username", user)
                    warehouse = _read_value(key_obj, "WAREHOUSE")
                    if warehouse:
                        values.setdefault("warehouse", warehouse)
                    role = _read_value(key_obj, "ROLE")
                    if role:
                        values.setdefault("role", role)
                    authenticator = _read_value(key_obj, "AUTHENTICATOR")
                    if authenticator:
                        values.setdefault("authenticator", authenticator)
            except OSError:
                continue

    return values


def _map_tableau_authentication(
    security_type: str,
    credential_retrieval: str,
    windows_credentials: bool | None,
    connection_string: str,
    user_name: str | None,
    provider_class: str = "",
) -> str:
    conn = (connection_string or "").lower()
    provider = (provider_class or "").strip().lower()

    has_conn_user = bool(re.search(r"(^|;)\s*(?:user id|uid|user|username)\s*=", conn))
    has_conn_password = bool(re.search(r"(^|;)\s*(?:pwd|password)\s*=", conn))
    has_explicit_user = bool(isinstance(user_name, str) and user_name.strip())

    # When credentials are explicitly present in the connection string, prefer username/password.
    if has_conn_user or has_conn_password or has_explicit_user:
        if credential_retrieval == "prompt":
            return "prompt"
        return "username-password"

    # Snowflake ODBC sources are credential-based in this pipeline; avoid SQL-Server SSPI defaults.
    if provider == "snowflake" or _looks_like_snowflake_connection(provider, conn):
        if credential_retrieval == "prompt":
            return "prompt"
        if credential_retrieval == "store" or has_conn_user or has_conn_password or has_explicit_user:
            return "username-password"
        return "username-password"

    if security_type == "windows":
        return "sspi"
    if security_type == "username-password":
        return "username-password"
    if security_type == "prompt":
        return "prompt"

    if credential_retrieval in {"integrated", "windows"}:
        return "sspi"
    if credential_retrieval == "store":
        return "username-password"
    if credential_retrieval == "prompt":
        return "prompt"

    if windows_credentials is True:
        return "sspi"
    if "integrated security=true" in conn or "integrated security=sspi" in conn or "trusted_connection=true" in conn:
        return "sspi"
    if has_conn_user or has_explicit_user:
        return "username-password"

    # Conservative default for SQL Server-like local environments.
    return "sspi"


def _ensure_required_workbook_sections(root: ET.Element) -> None:
    if root.find("preferences") is None:
        ET.SubElement(root, "preferences")

    if root.find("style") is None:
        ET.SubElement(root, "style")

    if root.find("windows") is None:
        windows = ET.SubElement(root, "windows")
        window = ET.SubElement(windows, "window", attrib={"name": "Window 1", "class": "worksheet"})
        ET.SubElement(window, "cards")
        ET.SubElement(window, "viewpoint")


def _sanitize_datasources(root: ET.Element) -> None:
    datasource_containers = [
        child for child in list(root) if _local_name(child.tag) == "datasources"
    ]

    datasources_node = datasource_containers[0] if datasource_containers else ET.SubElement(root, "datasources")

    # Keep full datasource XML payload to preserve relations/columns and connection settings.
    extracted_nodes: dict[str, ET.Element] = {}
    order: list[str] = []
    for container in datasource_containers or [datasources_node]:
        for ds in [c for c in list(container) if _local_name(c.tag) == "datasource"]:
            idx = len(order) + 1
            name = ds.attrib.get("name") or f"DataSource_{idx}"
            if name not in extracted_nodes:
                order.append(name)
                extracted_nodes[name] = ET.fromstring(ET.tostring(ds, encoding="unicode"))
            else:
                # Prefer the version that has richer connection payload.
                existing = extracted_nodes[name]
                old_conn = _find_direct_child(existing, "connection")
                new_conn = _find_direct_child(ds, "connection")
                old_rel = len([c for c in list(old_conn) if _local_name(c.tag) == "relation"]) if old_conn is not None else 0
                new_rel = len([c for c in list(new_conn) if _local_name(c.tag) == "relation"]) if new_conn is not None else 0
                if new_rel > old_rel:
                    extracted_nodes[name] = ET.fromstring(ET.tostring(ds, encoding="unicode"))

    if not extracted_nodes:
        fallback = ET.Element(
            "datasource",
            attrib={
                "name": "DataSource_1",
                "caption": "DataSource_1",
                "inline": "true",
                "hasconnection": "true",
            },
        )
        ET.SubElement(fallback, "connection", attrib={"class": "genericodbc"})
        extracted_nodes["DataSource_1"] = fallback
        order = ["DataSource_1"]

    for container in datasource_containers[1:]:
        root.remove(container)

    for child in list(datasources_node):
        datasources_node.remove(child)

    for name in order:
        node = extracted_nodes[name]
        node.attrib.setdefault("name", name)
        node.attrib.setdefault("caption", name)
        node.attrib.setdefault("inline", "true")
        node.attrib.setdefault("hasconnection", "true")

        conn = _find_direct_child(node, "connection")
        if conn is None:
            conn = ET.SubElement(node, "connection", attrib={"class": "genericodbc"})
        else:
            conn.attrib.setdefault("class", "genericodbc")

        # Normalize legacy invalid placement: <datasource><cols>...</cols></datasource>
        # should be nested under the datasource connection node.
        direct_cols = _find_direct_child(node, "cols")
        if direct_cols is not None:
            node.remove(direct_cols)
            conn.append(direct_cols)

        _reorder_connection_children(conn)

        # Keep datasource child order compliant with Tableau's expected content model.
        _reorder_datasource_children(node)

        for rel in [c for c in list(conn) if _local_name(c.tag) == "relation"]:
            rel_type = (rel.attrib.get("type") or "").lower()
            if rel_type == "text" and isinstance(rel.text, str) and rel.text.strip():
                rel.text = _sanitize_relation_sql(rel.text)

        datasources_node.append(node)


def _reorder_connection_children(connection_node: ET.Element) -> None:
    order = [
        "named-connections",
        "relation",
        "cols",
    ]
    rank = {name: i for i, name in enumerate(order)}

    children = list(connection_node)
    children.sort(key=lambda el: rank.get(_local_name(el.tag), 10_000))

    for child in list(connection_node):
        connection_node.remove(child)
    for child in children:
        connection_node.append(child)


def _reorder_datasource_children(datasource_node: ET.Element) -> None:
    order = [
        "repository-location",
        "connection",
        "utility-dimensions",
        "dimension",
        "overridable-settings",
        "aliases",
        "column",
        "column-instance",
        "group",
        "mapped-images",
        "drill-paths",
        "unlinked-server-hierarchies",
        "folder",
        "actions",
        "calculated-members",
        "extract",
        "layout",
        "style",
        "semantic-values",
        "date-options",
        "default-date-format",
        "default-sorts",
        "field-sort-info",
        "datasource-dependencies",
        "explainability",
        "filter",
    ]
    rank = {name: i for i, name in enumerate(order)}

    children = list(datasource_node)
    children.sort(key=lambda el: rank.get(_local_name(el.tag), 10_000))

    for child in list(datasource_node):
        datasource_node.remove(child)
    for child in children:
        datasource_node.append(child)


def _sanitize_worksheets(root: ET.Element) -> None:
    datasources_node = _find_direct_child(root, "datasources")
    first_ds_name = "DataSource_1"
    if datasources_node is not None:
        first = datasources_node.find("datasource")
        if first is not None and first.attrib.get("name"):
            first_ds_name = first.attrib["name"]

    dim_field, measure_field = _select_view_fields(datasources_node, first_ds_name)

    worksheet_containers = [
        child for child in list(root) if _local_name(child.tag) == "worksheets"
    ]
    worksheets_node = worksheet_containers[0] if worksheet_containers else None
    sheets_payload: list[dict[str, object]] = []
    for container in worksheet_containers:
        for ws in [c for c in list(container) if _local_name(c.tag) == "worksheet"]:
            if ws.attrib.get("name"):
                rows_text = ""
                cols_text = ""
                deps_datasource_name = first_ds_name
                dependency_children: list[ET.Element] = []
                view_filter_children: list[ET.Element] = []
                slice_columns: list[str] = []
                has_measure_dependency = False
                sheet_dim_field = ""
                sheet_measure_field = ""
                text_encoding_column = ""
                table_old = _find_direct_child(ws, "table")
                if table_old is not None:
                    text_encoding_column = _extract_text_encoding_column(table_old)
                    rows_old = _find_direct_child(table_old, "rows")
                    cols_old = _find_direct_child(table_old, "cols")
                    if rows_old is not None and isinstance(rows_old.text, str):
                        rows_text = rows_old.text.strip()
                    if cols_old is not None and isinstance(cols_old.text, str):
                        cols_text = cols_old.text.strip()

                    view_old = _find_direct_child(table_old, "view")
                    if view_old is not None:
                        deps_old = _find_direct_child(view_old, "datasource-dependencies")
                        if deps_old is not None:
                            bound_ds_name = deps_old.attrib.get("datasource")
                            if isinstance(bound_ds_name, str) and bound_ds_name.strip():
                                deps_datasource_name = bound_ds_name.strip()

                            for dep_child in list(deps_old):
                                dep_tag = _local_name(dep_child.tag)
                                if dep_tag in {"column", "column-instance"}:
                                    if dep_tag == "column":
                                        role = (dep_child.attrib.get("role") or "").strip().lower()
                                        if role == "measure":
                                            has_measure_dependency = True
                                    elif dep_tag == "column-instance":
                                        derivation = (dep_child.attrib.get("derivation") or "").strip().lower()
                                        if derivation in {"sum", "avg", "average", "min", "max", "count"}:
                                            has_measure_dependency = True
                                    dependency_children.append(_clone_xml_element(dep_child))

                        for view_child in list(view_old):
                            if _local_name(view_child.tag) == "filter":
                                view_filter_children.append(_clone_xml_element(view_child))

                        slices_old = _find_direct_child(view_old, "slices")
                        if slices_old is not None:
                            for slice_child in [c for c in list(slices_old) if _local_name(c.tag) == "column"]:
                                if isinstance(slice_child.text, str) and slice_child.text.strip():
                                    slice_columns.append(slice_child.text.strip())

                if dependency_children:
                    sheet_dim_field, sheet_measure_field = _extract_dim_measure_from_dependency_nodes(dependency_children)

                if not sheet_dim_field or not sheet_measure_field:
                    shelf_dim, shelf_measure = _extract_dim_measure_from_shelves(rows_text, cols_text)
                    if not sheet_dim_field:
                        sheet_dim_field = shelf_dim
                    if not sheet_measure_field:
                        sheet_measure_field = shelf_measure

                mark_class = _extract_table_mark_class(table_old)
                preserve_empty_shelves = False
                text_measure_only_shelves = False
                if table_old is not None and _is_text_mark_class(mark_class):
                    preserve_empty_shelves = not rows_text and not cols_text
                    text_measure_only_shelves = _shelves_are_measure_only(rows_text, cols_text)
                elif table_old is not None and isinstance(mark_class, str) and mark_class.strip().lower() == "shape":
                    preserve_empty_shelves = not rows_text
                sheets_payload.append(
                    {
                        "name": ws.attrib["name"],
                        "rows": rows_text,
                        "cols": cols_text,
                        "mark_class": mark_class,
                        "preserve_empty_shelves": "true" if preserve_empty_shelves else "",
                        "deps_datasource_name": deps_datasource_name,
                        "dependency_children": dependency_children,
                        "view_filter_children": view_filter_children,
                        "slice_columns": slice_columns,
                        "has_measure_dependency": "true" if has_measure_dependency else "",
                        "sheet_dim_field": sheet_dim_field,
                        "sheet_measure_field": sheet_measure_field,
                        "text_encoding_column": text_encoding_column,
                        "text_measure_only_shelves": "true" if text_measure_only_shelves else "",
                    }
                )

    if not sheets_payload:
        sheets_payload = [
            {
                "name": "Sheet 1",
                "rows": "",
                "cols": "",
                "mark_class": "Bar",
                "deps_datasource_name": first_ds_name,
                "dependency_children": [],
                "view_filter_children": [],
                "slice_columns": [],
            }
        ]

    if worksheets_node is None:
        worksheets_node = ET.SubElement(root, "worksheets")

    for container in worksheet_containers[1:]:
        root.remove(container)

    for child in list(worksheets_node):
        worksheets_node.remove(child)

    for sheet in sheets_payload:
        name = sheet["name"]
        mark_class = sheet.get("mark_class") if isinstance(sheet.get("mark_class"), str) else "Bar"
        is_pie = _is_pie_mark_class(mark_class)
        is_text = _is_text_mark_class(mark_class)
        is_shape = isinstance(mark_class, str) and mark_class.strip().lower() == "shape"
        preserve_empty_shelves = sheet.get("preserve_empty_shelves") == "true"
        has_measure_dependency = sheet.get("has_measure_dependency") == "true"
        text_measure_only_shelves = sheet.get("text_measure_only_shelves") == "true"

        deps_datasource_name = sheet.get("deps_datasource_name")
        if not isinstance(deps_datasource_name, str) or not deps_datasource_name.strip():
            deps_datasource_name = first_ds_name
        deps_datasource_name = deps_datasource_name.strip()

        sheet_dim_field = sheet.get("sheet_dim_field") if isinstance(sheet.get("sheet_dim_field"), str) else ""
        sheet_measure_field = sheet.get("sheet_measure_field") if isinstance(sheet.get("sheet_measure_field"), str) else ""
        sheet_dim_field = sheet_dim_field.strip() if sheet_dim_field.strip() else dim_field
        sheet_measure_field = sheet_measure_field.strip() if sheet_measure_field.strip() else measure_field
        text_encoding_column = sheet.get("text_encoding_column") if isinstance(sheet.get("text_encoding_column"), str) else ""

        # Tableau cartesian default: X on columns, Y on rows.
        rows_default = f"[{deps_datasource_name}].[sum:{sheet_measure_field}:qk]"
        cols_default = f"[{deps_datasource_name}].[none:{sheet_dim_field}:nk]"

        if is_text:
            # Preserve explicit text/KPI/filter shelf intent from injected semantic bindings.
            rows_text = sheet["rows"] if isinstance(sheet.get("rows"), str) else ""
            cols_text = sheet["cols"] if isinstance(sheet.get("cols"), str) else ""
            if preserve_empty_shelves or text_measure_only_shelves:
                rows_text = ""
                cols_text = ""
        elif is_shape and preserve_empty_shelves:
            rows_text = ""
            cols_text = sheet["cols"] if isinstance(sheet.get("cols"), str) else ""
        else:
            rows_text = "" if (is_pie or preserve_empty_shelves) else (sheet["rows"] or rows_default)
            cols_text = "" if (is_pie or preserve_empty_shelves) else (sheet["cols"] or cols_default)

        if not is_pie and not preserve_empty_shelves:
            if _contains_placeholder_field_reference(rows_text):
                rows_text = rows_default
            if _contains_placeholder_field_reference(cols_text):
                cols_text = cols_default

        if _is_text_mark_class(mark_class) and not has_measure_dependency and not text_encoding_column.strip():
            if not rows_text and not cols_text:
                cols_text = f"[{deps_datasource_name}].[none:{sheet_dim_field}:nk]"

        ws = ET.SubElement(worksheets_node, "worksheet", attrib={"name": name})
        ET.SubElement(ws, "layout-options")

        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        view_dss = ET.SubElement(view, "datasources")
        ET.SubElement(view_dss, "datasource", attrib={"name": deps_datasource_name})
        deps = ET.SubElement(view, "datasource-dependencies", attrib={"datasource": deps_datasource_name})

        has_dependency_columns = False
        has_dependency_instances = False
        dependency_children = sheet.get("dependency_children")
        if isinstance(dependency_children, list):
            for dep_child in dependency_children:
                if not isinstance(dep_child, ET.Element):
                    continue
                cloned = _clone_xml_element(dep_child)
                deps.append(cloned)
                dep_tag = _local_name(cloned.tag)
                if dep_tag == "column":
                    has_dependency_columns = True
                elif dep_tag == "column-instance":
                    has_dependency_instances = True

        _append_default_dependency_bindings(
            deps,
            dim_field=sheet_dim_field,
            measure_field=sheet_measure_field,
            add_columns=not has_dependency_columns,
            add_instances=not has_dependency_instances,
        )

        view_filter_children = sheet.get("view_filter_children")
        if isinstance(view_filter_children, list):
            for filter_child in view_filter_children:
                if isinstance(filter_child, ET.Element):
                    view.append(_clone_xml_element(filter_child))

        ET.SubElement(view, "perspectives")
        slice_columns = sheet.get("slice_columns")
        if isinstance(slice_columns, list) and slice_columns:
            slices = ET.SubElement(view, "slices")
            seen_slices: set[str] = set()
            for slice_column in slice_columns:
                if not isinstance(slice_column, str) or not slice_column.strip():
                    continue
                clean_slice = slice_column.strip()
                if clean_slice in seen_slices:
                    continue
                seen_slices.add(clean_slice)
                slice_node = ET.SubElement(slices, "column")
                slice_node.text = clean_slice
        ET.SubElement(view, "aggregation", attrib={"value": "true"})
        _reorder_view_children_for_tableau(view)

        ET.SubElement(table, "style")
        panes = ET.SubElement(table, "panes")
        if isinstance(mark_class, str) and mark_class:
            pane = ET.SubElement(panes, "pane")
            _ensure_pane_view(pane)
            ET.SubElement(pane, "mark", attrib={"class": mark_class})
            if is_pie:
                _set_pie_pane_encodings(pane, deps_datasource_name, sheet_dim_field, sheet_measure_field)
            elif is_text:
                if text_encoding_column.strip():
                    _set_text_pane_encoding_column(pane, text_encoding_column)
                elif has_measure_dependency and (preserve_empty_shelves or text_measure_only_shelves):
                    _set_text_pane_encoding(pane, deps_datasource_name, sheet_measure_field, role="measure")
        rows = ET.SubElement(table, "rows")
        rows.text = rows_text
        cols = ET.SubElement(table, "cols")
        cols.text = cols_text


def _append_default_dependency_bindings(
    deps_node: ET.Element,
    dim_field: str,
    measure_field: str,
    add_columns: bool,
    add_instances: bool,
    dim_caption: str | None = None,
    measure_caption: str | None = None,
    include_measure: bool = True,
) -> None:
    dim_datatype, dim_type = _infer_dimension_dependency_spec(dim_field)

    if add_columns:
        dim_label = dim_caption.strip() if isinstance(dim_caption, str) and dim_caption.strip() else dim_field
        measure_label = (
            measure_caption.strip() if isinstance(measure_caption, str) and measure_caption.strip() else measure_field
        )
        ET.SubElement(
            deps_node,
            "column",
            attrib={
                "name": f"[{dim_field}]",
                "role": "dimension",
                "datatype": dim_datatype,
                "type": dim_type,
                "caption": dim_label,
            },
        )
        if include_measure:
            ET.SubElement(
                deps_node,
                "column",
                attrib={
                    "name": f"[{measure_field}]",
                    "role": "measure",
                    "datatype": "real",
                    "type": "quantitative",
                    "caption": measure_label,
                },
            )

    if add_instances:
        ET.SubElement(
            deps_node,
            "column-instance",
            attrib={
                "column": f"[{dim_field}]",
                "derivation": "None",
                "name": f"[none:{dim_field}:nk]",
                "pivot": "key",
                "type": dim_type,
            },
        )
        if include_measure:
            ET.SubElement(
                deps_node,
                "column-instance",
                attrib={
                    "column": f"[{measure_field}]",
                    "derivation": "Sum",
                    "name": f"[sum:{measure_field}:qk]",
                    "pivot": "key",
                    "type": "quantitative",
                },
            )


def _append_dependency_binding(
    deps_node: ET.Element,
    field_name: str,
    role: str,
    datatype: str = "",
    type_value: str = "",
) -> None:
    clean = (field_name or "").strip()
    if not clean:
        return

    role_norm = (role or "").strip().lower()
    if role_norm not in {"dimension", "measure"}:
        return

    existing_columns = {
        _clean_bracketed_name(col.attrib.get("name", "")).strip().lower()
        for col in list(deps_node)
        if _local_name(col.tag) == "column"
    }
    existing_instances = {
        _clean_bracketed_name(ci.attrib.get("name", "")).strip().lower()
        for ci in list(deps_node)
        if _local_name(ci.tag) == "column-instance"
    }

    clean_lower = clean.lower()
    if clean_lower not in existing_columns:
        if role_norm == "dimension":
            inferred_datatype, inferred_type = _infer_dimension_dependency_spec(clean)
            column_datatype = datatype.strip().lower() if isinstance(datatype, str) and datatype.strip() else inferred_datatype
            column_type = type_value.strip().lower() if isinstance(type_value, str) and type_value.strip() else inferred_type
            if _is_numeric_filter_datatype(column_datatype):
                column_type = "quantitative"
            ET.SubElement(
                deps_node,
                "column",
                attrib={
                    "name": f"[{clean}]",
                    "role": "dimension",
                    "datatype": column_datatype,
                    "type": column_type,
                    "caption": clean,
                },
            )
        else:
            column_datatype = datatype.strip().lower() if isinstance(datatype, str) and datatype.strip() else "real"
            ET.SubElement(
                deps_node,
                "column",
                attrib={
                    "name": f"[{clean}]",
                    "role": "measure",
                    "datatype": column_datatype,
                    "type": "quantitative",
                    "caption": clean,
                },
            )

    if role_norm == "dimension":
        instance_suffix = "qk" if _is_numeric_filter_datatype(datatype) else "nk"
        instance_name = f"none:{clean}:{instance_suffix}"
        if instance_name.lower() not in existing_instances:
            instance_type = type_value.strip().lower() if isinstance(type_value, str) and type_value.strip() else _infer_dimension_dependency_spec(clean)[1]
            if _is_numeric_filter_datatype(datatype):
                instance_type = "quantitative"
            ET.SubElement(
                deps_node,
                "column-instance",
                attrib={
                    "column": f"[{clean}]",
                    "derivation": "None",
                    "name": f"[{instance_name}]",
                    "pivot": "key",
                    "type": instance_type,
                },
            )
    else:
        instance_name = f"sum:{clean}:qk"
        if instance_name.lower() not in existing_instances:
            ET.SubElement(
                deps_node,
                "column-instance",
                attrib={
                    "column": f"[{clean}]",
                    "derivation": "Sum",
                    "name": f"[{instance_name}]",
                    "pivot": "key",
                    "type": "quantitative",
                },
            )


def _reorder_view_children_for_tableau(view_node: ET.Element) -> None:
    if not isinstance(view_node, ET.Element):
        return

    order = [
        "datasources",
        "mapsources",
        "datasource-dependencies",
        "filter",
        "sort",
        "perspectives",
        "slices",
        "aggregation",
    ]
    children = [c for c in list(view_node) if isinstance(c, ET.Element)]
    ordered: list[ET.Element] = []
    for name in order:
        ordered.extend([c for c in children if _local_name(c.tag) == name])
    ordered.extend([c for c in children if _local_name(c.tag) not in order])

    for child in list(view_node):
        view_node.remove(child)
    for child in ordered:
        view_node.append(child)


def _ensure_subtitle_filter_context(
    view_node: ET.Element,
    datasource_name: str,
    year_field: str,
    group_field: str,
) -> None:
    if not isinstance(view_node, ET.Element):
        return

    ds_name = (datasource_name or "").strip() or "EnterData"
    year_clean = (year_field or "").strip()
    group_clean = (group_field or "").strip()
    if not year_clean or not group_clean:
        return

    year_col = f"[{ds_name}].[none:{year_clean}:qk]"
    group_col = f"[{ds_name}].[none:{group_clean}:nk]"

    year_filter: ET.Element | None = None
    group_filter: ET.Element | None = None
    for child in list(view_node):
        if _local_name(child.tag) != "filter":
            continue
        col_attr = (child.attrib.get("column") or "").strip()
        if col_attr == year_col and year_filter is None:
            year_filter = child
        elif col_attr == group_col and group_filter is None:
            group_filter = child

    if year_filter is None:
        year_filter = ET.SubElement(view_node, "filter")
    year_filter.attrib.clear()
    year_filter.attrib.update(
        {
            "column": year_col,
            "class": "quantitative",
            "included-values": "in-range-or-null",
        }
    )
    for child in list(year_filter):
        year_filter.remove(child)
    min_node = ET.SubElement(year_filter, "min")
    min_node.text = "0"
    max_node = ET.SubElement(year_filter, "max")
    max_node.text = "9999"

    if group_filter is None:
        group_filter = ET.SubElement(view_node, "filter")
    group_filter.attrib.clear()
    group_filter.attrib.update(
        {
            "class": "categorical",
            "column": group_col,
            "filter-group": "1",
        }
    )
    for child in list(group_filter):
        group_filter.remove(child)
    group_filter_node = ET.SubElement(group_filter, "groupfilter")
    group_filter_node.attrib.update(
        {
            "function": "level-members",
            "level": f"[none:{group_clean}:nk]",
        }
    )

    perspectives = _find_direct_child(view_node, "perspectives")
    if perspectives is None:
        perspectives = ET.SubElement(view_node, "perspectives")

    slices = _find_direct_child(view_node, "slices")
    if slices is None:
        slices = ET.SubElement(view_node, "slices")
    for child in list(slices):
        slices.remove(child)
    year_slice = ET.SubElement(slices, "column")
    year_slice.text = year_col
    group_slice = ET.SubElement(slices, "column")
    group_slice.text = group_col

    _reorder_view_children_for_tableau(view_node)


def _promote_dependency_instance_to_attribute(
    deps_node: ET.Element,
    field_name: str,
) -> None:
    clean = (field_name or "").strip()
    if not clean:
        return

    clean_lower = clean.lower()
    attr_name_lower = f"[attr:{clean_lower}:nk]"
    none_name_lower = f"[none:{clean_lower}:nk]"
    dim_type = _infer_dimension_dependency_spec(clean)[1]

    target_instance: ET.Element | None = None
    removable_none_instances: list[ET.Element] = []

    for child in list(deps_node):
        if _local_name(child.tag) != "column-instance":
            continue
        child_name_lower = (child.attrib.get("name") or "").strip().lower()
        if child_name_lower == attr_name_lower:
            target_instance = child
        elif child_name_lower == none_name_lower:
            removable_none_instances.append(child)

    if target_instance is None and removable_none_instances:
        target_instance = removable_none_instances.pop(0)

    if target_instance is None:
        target_instance = ET.SubElement(deps_node, "column-instance")

    target_instance.attrib.update(
        {
            "column": f"[{clean}]",
            "derivation": "Attribute",
            "name": f"[attr:{clean}:nk]",
            "pivot": "key",
            "type": dim_type,
        }
    )

    for child in removable_none_instances:
        deps_node.remove(child)


def _infer_dimension_dependency_spec(field_name: str) -> tuple[str, str]:
    token = (field_name or "").strip().lower()
    if not token:
        return "string", "nominal"

    if "sortorder" in token or ("sort" in token and "month" in token):
        return "real", "ordinal"
    if _is_key_like_column_name(token):
        return "real", "ordinal"
    if _looks_temporal_dimension_name(token):
        return "string", "ordinal"

    return "string", "nominal"


def _extract_table_mark_class(table_node: ET.Element | None) -> str:
    if table_node is None:
        return "Bar"

    panes = _find_direct_child(table_node, "panes")
    if panes is None:
        return "Bar"

    for pane in [c for c in list(panes) if _local_name(c.tag) == "pane"]:
        mark = _find_direct_child(pane, "mark")
        if mark is None:
            continue
        value = mark.attrib.get("class", "").strip()
        if value:
            return value

    return "Bar"


def _extract_text_encoding_column(table_node: ET.Element | None) -> str:
    if table_node is None:
        return ""

    panes = _find_direct_child(table_node, "panes")
    if panes is None:
        return ""

    for pane in [c for c in list(panes) if _local_name(c.tag) == "pane"]:
        encodings = _find_direct_child(pane, "encodings")
        if encodings is None:
            continue
        for encoding in [c for c in list(encodings) if _local_name(c.tag) == "text"]:
            column = encoding.attrib.get("column")
            if isinstance(column, str) and column.strip():
                return column.strip()

    return ""


def _set_table_mark_class(table_node: ET.Element, mark_class: str) -> None:
    panes = _find_direct_child(table_node, "panes")
    if panes is None:
        panes = ET.SubElement(table_node, "panes")

    pane = None
    for candidate in [c for c in list(panes) if _local_name(c.tag) == "pane"]:
        pane = candidate
        break
    if pane is None:
        pane = ET.SubElement(panes, "pane")

    _ensure_pane_view(pane)

    mark = _find_direct_child(pane, "mark")
    if mark is None:
        mark = ET.SubElement(pane, "mark")
    mark.attrib["class"] = mark_class


def _set_pie_mark_encodings(
    table_node: ET.Element,
    datasource_name: str,
    dim_field: str,
    measure_field: str,
) -> None:
    panes = _find_direct_child(table_node, "panes")
    if panes is None:
        panes = ET.SubElement(table_node, "panes")

    pane = None
    for candidate in [c for c in list(panes) if _local_name(c.tag) == "pane"]:
        pane = candidate
        break
    if pane is None:
        pane = ET.SubElement(panes, "pane")

    _ensure_pane_view(pane)
    _set_pie_pane_encodings(pane, datasource_name, dim_field, measure_field)


def _set_pie_pane_encodings(
    pane_node: ET.Element,
    datasource_name: str,
    dim_field: str,
    measure_field: str,
) -> None:
    encodings = _find_direct_child(pane_node, "encodings")
    if encodings is None:
        encodings = ET.SubElement(pane_node, "encodings")

    for child in list(encodings):
        encodings.remove(child)

    ET.SubElement(
        encodings,
        "color",
        attrib={"column": f"[{datasource_name}].[none:{dim_field}:nk]"},
    )
    ET.SubElement(
        encodings,
        "wedge-size",
        attrib={"column": f"[{datasource_name}].[sum:{measure_field}:qk]"},
    )


def _set_text_pane_encoding(
    pane_node: ET.Element,
    datasource_name: str,
    field_name: str,
    role: str,
) -> None:
    clean_field = (field_name or "").strip()
    clean_datasource = (datasource_name or "").strip()
    if not clean_field or not clean_datasource:
        return

    role_norm = (role or "").strip().lower()
    if role_norm == "measure":
        column = f"[{clean_datasource}].[sum:{clean_field}:qk]"
    else:
        column = f"[{clean_datasource}].[none:{clean_field}:nk]"
    _set_text_pane_encoding_column(pane_node, column)


def _set_text_pane_encoding_column(pane_node: ET.Element, column: str) -> None:
    clean_column = (column or "").strip()
    if not clean_column:
        return

    encodings = _find_direct_child(pane_node, "encodings")
    if encodings is None:
        encodings = ET.SubElement(pane_node, "encodings")

    for child in list(encodings):
        if _local_name(child.tag) == "text":
            encodings.remove(child)

    ET.SubElement(encodings, "text", attrib={"column": clean_column})


def _is_pie_mark_class(mark_class: str | None) -> bool:
    return isinstance(mark_class, str) and mark_class.strip().lower() == "pie"


def _is_text_mark_class(mark_class: str | None) -> bool:
    return isinstance(mark_class, str) and mark_class.strip().lower() == "text"


def _is_title_like_text_sheet(
    mark_class: str | None,
    visual_type: str,
    worksheet_name: str,
    semantic_kind: str = "",
) -> bool:
    if not _is_text_mark_class(mark_class):
        return False

    kind = (semantic_kind or "").strip().lower()
    if kind in {"kpi", "filter"}:
        return False
    if kind in {"header", "footer"}:
        return True

    vt = (visual_type or "").strip().lower()
    wn = (worksheet_name or "").strip().lower()

    # Title/textbox sheets should not be converted into text crosstabs.
    if any(token in vt for token in ["textbox", "title", "label"]):
        return True
    if any(token in wn for token in ["title", "header", "subtitle", "footer"]):
        return True

    return False


def _ensure_pane_view(pane_node: ET.Element) -> None:
    pane_view = _find_direct_child(pane_node, "view")
    if pane_view is None:
        pane_view = ET.SubElement(pane_node, "view")
    breakdown = _find_direct_child(pane_view, "breakdown")
    if breakdown is None:
        breakdown = ET.SubElement(pane_view, "breakdown")
    breakdown.attrib["value"] = breakdown.attrib.get("value") or "auto"


def _build_visual_type_lookup(visual_model: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not isinstance(visual_model, dict):
        return lookup

    for sheet in visual_model.get("sheets", []):
        if not isinstance(sheet, dict):
            continue
        name = sheet.get("name")
        visual_type = sheet.get("visual_type")
        if isinstance(name, str) and name.strip() and isinstance(visual_type, str) and visual_type.strip():
            lookup[name.strip().lower()] = visual_type.strip()

    def _collect_visuals(items: list[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            visual_type = item.get("visual_type")
            if isinstance(name, str) and name.strip() and isinstance(visual_type, str) and visual_type.strip():
                lookup[name.strip().lower()] = visual_type.strip()
            children = item.get("children", [])
            if isinstance(children, list):
                _collect_visuals(children)

    visuals = visual_model.get("visuals", [])
    if isinstance(visuals, list):
        _collect_visuals(visuals)

    return lookup


def _visual_type_to_mark_class(visual_type: str, semantic_kind: str = "") -> str:
    kind = (semantic_kind or "").strip().lower()
    if kind in {"header", "footer", "kpi", "filter", "text"}:
        return "Text"
    if kind == "image":
        return "Shape"
    if kind == "gauge":
        return "Pie"

    if not isinstance(visual_type, str):
        return ""

    normalized = visual_type.strip().lower()
    if not normalized:
        return ""

    if any(token in normalized for token in ["pie", "explodedpie", "donut", "doughnut"]):
        return "Pie"
    if "shape" in normalized:
        return "Pie"
    if any(token in normalized for token in ["line", "spline", "stepline"]):
        return "Line"
    if any(token in normalized for token in ["area", "stackedarea"]):
        return "Area"
    if any(token in normalized for token in ["scatter", "bubble", "point"]):
        return "Circle"
    if "gauge" in normalized:
        return "Pie"
    if "image" in normalized:
        return "Shape"
    if "textbox" in normalized:
        return "Text"
    if "filter" in normalized:
        return "Text"
    if any(token in normalized for token in ["text", "label", "table", "tablix"]):
        return "Text"
    if any(token in normalized for token in ["bar", "stackedbar", "column", "stackedcolumn", "histogram", "chart"]):
        return "Bar"

    return ""


def _select_view_fields(datasources_node: ET.Element | None, first_ds_name: str) -> tuple[str, str]:
    ds_node: ET.Element | None = None
    if datasources_node is not None:
        for ds in [c for c in list(datasources_node) if _local_name(c.tag) == "datasource"]:
            if ds.attrib.get("name") == first_ds_name:
                ds_node = ds
                break
        if ds_node is None:
            ds_node = _find_direct_child(datasources_node, "datasource")

    dim = "Field1"
    measure = "Field2"
    all_fields: list[str] = []
    if ds_node is not None:
        dim_candidates: list[str] = []
        measure_candidates: list[str] = []
        for col in [c for c in list(ds_node) if _local_name(c.tag) == "column"]:
            raw_name = col.attrib.get("name", "")
            clean_name = _clean_bracketed_name(raw_name)
            if not clean_name:
                continue
            all_fields.append(clean_name)
            role = (col.attrib.get("role") or "").lower()
            if role == "dimension":
                dim_candidates.append(clean_name)
            elif role == "measure":
                measure_candidates.append(clean_name)

        if dim_candidates:
            dim = dim_candidates[0]
        elif all_fields:
            dim = all_fields[0]

        if measure_candidates:
            ordered_measures = sorted(measure_candidates, key=_measure_priority_score)
            measure = ordered_measures[0]
        elif len(all_fields) > 1:
            measure = all_fields[1]
        else:
            measure = dim

    return dim, measure


def _clean_bracketed_name(raw_name: str) -> str:
    text = (raw_name or "").strip()
    if text.startswith("[") and text.endswith("]") and len(text) >= 2:
        return text[1:-1].strip()
    return text


def _contains_placeholder_field_reference(shelf_text: str) -> bool:
    if not isinstance(shelf_text, str):
        return False
    return re.search(r"\bfield\d+\b", shelf_text, flags=re.IGNORECASE) is not None


def _extract_dim_measure_from_dependency_nodes(nodes: list[ET.Element]) -> tuple[str, str]:
    dim = ""
    measure = ""

    for node in nodes:
        if not isinstance(node, ET.Element):
            continue
        tag = _local_name(node.tag)
        if tag != "column":
            continue
        role = (node.attrib.get("role") or "").strip().lower()
        clean_name = _clean_bracketed_name(node.attrib.get("name", "")).strip()
        if not clean_name:
            continue
        if role == "dimension" and not dim:
            dim = clean_name
        elif role == "measure" and not measure:
            measure = clean_name

    for node in nodes:
        if not isinstance(node, ET.Element):
            continue
        if _local_name(node.tag) != "column-instance":
            continue
        instance_name = (node.attrib.get("name") or "").strip()
        match = re.match(r"^\[(?P<derivation>[^:]+):(?P<field>[^:]+):[^\]]+\]$", instance_name)
        if match is None:
            continue
        derivation = match.group("derivation").strip().lower()
        field_name = match.group("field").strip()
        if derivation in {"none", "attr", "attribute"} and field_name and not dim:
            dim = field_name
        elif derivation in {"sum", "avg", "average", "min", "max", "count"} and field_name and not measure:
            measure = field_name

    return dim, measure


def _extract_dim_measure_from_shelves(rows_text: str, cols_text: str) -> tuple[str, str]:
    dim = ""
    measure = ""
    for shelf_text in [rows_text, cols_text]:
        if not isinstance(shelf_text, str) or not shelf_text.strip():
            continue
        for match in SHELF_FIELD_REF_RE.finditer(shelf_text):
            derivation = match.group("derivation").strip().lower()
            field_name = match.group("field").strip()
            if not field_name:
                continue
            if derivation in {"none", "attr", "attribute"} and not dim:
                dim = field_name
            elif derivation in {"sum", "avg", "average", "min", "max", "count"} and not measure:
                measure = field_name
    return dim, measure


def _shelves_are_measure_only(rows_text: str, cols_text: str) -> bool:
    derivations: list[str] = []
    for shelf_text in [rows_text, cols_text]:
        if not isinstance(shelf_text, str) or not shelf_text.strip():
            continue
        for match in SHELF_FIELD_REF_RE.finditer(shelf_text):
            derivations.append((match.group("derivation") or "").strip().lower())

    if not derivations:
        return False

    aggregate_derivations = {"sum", "avg", "average", "min", "max", "count"}
    return all(derivation in aggregate_derivations for derivation in derivations)


def _sanitize_windows(root: ET.Element) -> None:
    windows = _find_direct_child(root, "windows")
    if windows is None:
        windows = ET.SubElement(root, "windows")

    default_ds_name = "DataSource_1"

    datasources_node = _find_direct_child(root, "datasources")
    if datasources_node is not None:
        first_ds = _find_direct_child(datasources_node, "datasource")
        if first_ds is not None and first_ds.attrib.get("name"):
            default_ds_name = first_ds.attrib["name"]

    worksheet_names: list[str] = []
    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is not None:
        for ws in [c for c in list(worksheets) if _local_name(c.tag) == "worksheet"]:
            name = ws.attrib.get("name")
            if name:
                worksheet_names.append(name)

    filter_card_columns = _collect_filter_card_columns(root)

    dashboard_names: list[str] = []
    dashboards = _find_direct_child(root, "dashboards")
    if dashboards is not None:
        for dashboard in [c for c in list(dashboards) if _local_name(c.tag) == "dashboard"]:
            name = dashboard.attrib.get("name")
            if name:
                dashboard_names.append(name)

    # Rebuild windows section to guaranteed-valid worksheet/dashboard-window form.
    for child in list(windows):
        windows.remove(child)

    for name in dashboard_names:
        win = ET.SubElement(windows, "window", attrib={"name": name, "class": "dashboard"})
        viewpoints = ET.SubElement(win, "viewpoints")
        if worksheet_names:
            for ws_name in worksheet_names:
                ET.SubElement(viewpoints, "viewpoint", attrib={"name": ws_name})
        else:
            ET.SubElement(viewpoints, "viewpoint")
        ET.SubElement(win, "active", attrib={"id": "-1"})

    for name in worksheet_names or ["Sheet 1"]:
        win = ET.SubElement(windows, "window", attrib={"name": name, "class": "worksheet"})
        cards = ET.SubElement(win, "cards")
        if filter_card_columns:
            edge = ET.SubElement(cards, "edge", attrib={"name": "right"})
            strip = ET.SubElement(edge, "strip", attrib={"size": "160"})
            for column_ref in filter_card_columns:
                card_attrib = {"param": column_ref, "type": "filter"}
                if ":qk]" in column_ref:
                    card_attrib["show-domain"] = "false"
                    card_attrib["show-null-ctrls"] = "false"
                ET.SubElement(strip, "card", attrib=card_attrib)
        ET.SubElement(win, "viewpoint")


def _collect_filter_card_columns(root: ET.Element) -> list[str]:
    columns: list[str] = []
    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is None:
        return columns

    for ws in [c for c in list(worksheets) if _local_name(c.tag) == "worksheet"]:
        table = _find_direct_child(ws, "table")
        if table is None:
            continue
        view = _find_direct_child(table, "view")
        if view is None:
            continue
        for filter_node in [c for c in list(view) if _local_name(c.tag) == "filter"]:
            column_ref = filter_node.attrib.get("column")
            if isinstance(column_ref, str) and column_ref.strip() and column_ref.strip() not in columns:
                columns.append(column_ref.strip())

    return columns


def _reorder_workbook_children(root: ET.Element) -> None:
    order = [
        "document-format-change-manifest",
        "repository-location",
        "preferences",
        "style-theme",
        "style",
        "local-data",
        "datasources",
        "datasource-relationships",
        "mapsources",
        "shared-views",
        "actions",
        "worksheets",
        "dashboards",
        "windows",
        "thumbnails",
        "external",
    ]
    rank = {name: i for i, name in enumerate(order)}

    children = list(root)
    children.sort(key=lambda el: rank.get(_local_name(el.tag), 10_000))

    for child in list(root):
        root.remove(child)
    for child in children:
        root.append(child)


def _prune_to_safe_workbook_core(root: ET.Element) -> None:
    """Keep only conservative top-level sections that we actively normalize."""
    safe_tags = {
        "document-format-change-manifest",
        "repository-location",
        "preferences",
        "style-theme",
        "style",
        "local-data",
        "datasources",
        "datasource-relationships",
        "mapsources",
        "shared-views",
        "actions",
        "worksheets",
        "dashboards",
        "windows",
        "thumbnails",
        "external",
    }

    for child in list(root):
        if _local_name(child.tag) not in safe_tags:
            root.remove(child)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _strip_namespaces(root: ET.Element) -> None:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _find_direct_child(parent: ET.Element, local_name: str) -> ET.Element | None:
    for child in list(parent):
        if _local_name(child.tag) == local_name:
            return child
    return None


def _ensure_datasource_connection_before_aliases(datasource_node: ET.Element) -> None:
    connection_node = _find_direct_child(datasource_node, "connection")
    aliases_node = _find_direct_child(datasource_node, "aliases")
    if connection_node is None or aliases_node is None:
        return

    children = list(datasource_node)
    connection_index = children.index(connection_node)
    aliases_index = children.index(aliases_node)
    if connection_index < aliases_index:
        return

    datasource_node.remove(connection_node)
    aliases_index = list(datasource_node).index(aliases_node)
    datasource_node.insert(aliases_index, connection_node)


def _clone_xml_element(node: ET.Element) -> ET.Element:
    return ET.fromstring(ET.tostring(node, encoding="unicode"))


def _strip_unexpected_workbook_character_data(root: ET.Element) -> None:
    # Remove accidental prose/text injected around the root that breaks Tableau content model.
    if isinstance(root.text, str) and root.text.strip():
        root.text = None

    for element in root.iter():
        local = _local_name(element.tag)

        if element is not root and isinstance(element.text, str) and element.text.strip():
            keep_text = False
            if local in {"rows", "cols"}:
                keep_text = True
            if local == "relation" and (element.attrib.get("type") or "").strip().lower() == "text":
                keep_text = True
            if not keep_text and len(list(element)) > 0:
                element.text = None

        if isinstance(element.tail, str) and element.tail.strip():
            element.tail = None


def _sanitize_relation_sql(sql: str) -> str:
    """Make custom SQL safer for Tableau wrapping on SQL Server.

    Tableau often wraps custom SQL in a derived table. SQL Server rejects top-level
    ORDER BY in that context unless TOP/OFFSET/FOR XML is present.
    """
    text = (sql or "").strip()
    if not text:
        return text

    core = text
    # Tableau commonly wraps Custom SQL in a derived table; trailing semicolons
    # and multi-statement SQL can fail in that context on SQL Server.
    core = _strip_top_level_statement_tail(core)
    lowered = core.lower()

    if not re.search(r"\border\s+by\b", lowered):
        return text

    # Keep ORDER BY when query shape explicitly allows it in SQL Server.
    if re.search(r"\btop\s*\(", lowered) or re.search(r"\btop\s+\d+\b", lowered):
        return text
    if re.search(r"\boffset\b", lowered) or re.search(r"\bfor\s+xml\b", lowered):
        return text

    idx = _find_last_top_level_order_by(core)
    if idx < 0:
        return text

    sanitized = core[:idx].rstrip()
    return sanitized


def _strip_top_level_statement_tail(sql: str) -> str:
    """Keep only the first top-level statement and remove terminal ';'."""
    if not sql:
        return sql

    in_single = False
    in_double = False
    in_bracket = False
    depth = 0
    first_semicolon = -1

    for i, ch in enumerate(sql):
        if in_single:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    continue
                in_single = False
            continue

        if in_double:
            if ch == '"':
                in_double = False
            continue

        if in_bracket:
            if ch == "]":
                in_bracket = False
            continue

        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "[":
            in_bracket = True
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            continue

        if depth == 0 and ch == ";":
            first_semicolon = i
            break

    if first_semicolon >= 0:
        return sql[:first_semicolon].rstrip()

    return sql.rstrip().rstrip(";").rstrip()


def _find_last_top_level_order_by(sql: str) -> int:
    in_single = False
    in_double = False
    in_bracket = False
    depth = 0
    candidates: list[int] = []
    i = 0

    while i < len(sql):
        ch = sql[i]

        if in_single:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if in_double:
            if ch == '"':
                in_double = False
            i += 1
            continue

        if in_bracket:
            if ch == "]":
                in_bracket = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue

        if depth == 0 and (ch == "o" or ch == "O"):
            prev_ok = i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")
            if prev_ok:
                m = re.match(r"order\s+by\b", sql[i:], flags=re.IGNORECASE)
                if m is not None:
                    candidates.append(i)
                    i += len(m.group(0))
                    continue

        i += 1

    return candidates[-1] if candidates else -1


def _remove_disallowed_sort_nodes(root: ET.Element) -> None:
    disallowed = {
        "sort",
        "sorts",
        "shelf-sort",
        "shelf-sorts",
        "shelfsort",
        "shelfsorts",
    }

    def _prune(parent: ET.Element) -> None:
        for child in list(parent):
            if _local_name(child.tag).lower() in disallowed:
                parent.remove(child)
                continue
            _prune(child)

    _prune(root)


def _remove_schema_definition_nodes(root: ET.Element) -> None:
    """Remove XSD/schema-definition tags if they were accidentally emitted by the LLM."""
    schema_tags = {
        "schema",
        "group",
        "complextype",
        "simpletype",
        "sequence",
        "choice",
        "all",
        "attribute",
        "attributegroup",
        "restriction",
        "enumeration",
        "annotation",
        "documentation",
        "import",
        "union",
        "extension",
        "selector",
        "field",
        "unique",
    }

    def _prune(parent: ET.Element) -> None:
        for child in list(parent):
            if _local_name(child.tag).lower() in schema_tags:
                parent.remove(child)
                continue
            _prune(child)

    _prune(root)


def validate_twb_structure(xml_content: str) -> list[str]:
    """Return a list of structural issues found in generated TWB XML."""
    issues: list[str] = []
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        return [f"XML parse error: {exc}"]

    if _local_name(root.tag) != "workbook":
        return ["Root element must be <workbook>"]

    if "generated-by" in root.attrib:
        issues.append("Undeclared workbook attribute found: generated-by")

    for attr in ["version", "source-build"]:
        if attr not in root.attrib:
            issues.append(f"Missing required workbook attribute: {attr}")

    xml_lower = xml_content.lower()
    if "<sort" in xml_lower:
        issues.append("Unexpected <sort...> element detected")
    if "shelfsort" in xml_lower or "shelf-sort" in xml_lower:
        issues.append("Unexpected shelf sort element detected")

    schema_tags = {
        "schema",
        "group",
        "sequence",
        "choice",
        "all",
        "complextype",
        "simpletype",
    }
    found_schema_tokens: set[str] = set()
    for elem in root.iter():
        tag_name = str(elem.tag)
        local = _local_name(tag_name).lower()
        raw = tag_name.lower()
        if local in schema_tags or raw.startswith("xs:") or raw.startswith("{http://www.w3.org/2001/xmlschema}"):
            found_schema_tokens.add(f"<{_local_name(tag_name)}")
    for token in sorted(found_schema_tokens):
        issues.append(f"Schema-definition token found in instance XML: {token}")

    required_top_level = ["datasources", "worksheets", "windows"]
    for tag in required_top_level:
        if _find_direct_child(root, tag) is None:
            issues.append(f"Missing required top-level element: {tag}")

    worksheets = [el for el in root.iter() if _local_name(el.tag) == "worksheet"]
    if not worksheets:
        issues.append("No worksheet elements found")

    worksheet_only_nodes = {
        "layout-options",
        "table",
        "view",
        "rows",
        "cols",
        "panes",
        "marks",
        "encodings",
    }
    datasources = [el for el in root.iter() if _local_name(el.tag) == "datasource"]
    for idx, datasource in enumerate(datasources, start=1):
        ds_name = datasource.attrib.get("name", f"datasource_{idx}")
        for child in list(datasource):
            child_name = _local_name(child.tag)
            if child_name in worksheet_only_nodes:
                issues.append(
                    f"Datasource '{ds_name}' contains worksheet-only element '{child_name}'"
                )

    for idx, ws in enumerate(worksheets, start=1):
        ws_name = ws.attrib.get("name", f"worksheet_{idx}")
        if _find_direct_child(ws, "layout-options") is None and _find_direct_child(ws, "repository-location") is None:
            issues.append(f"Worksheet '{ws_name}' missing layout-options/repository-location")
        if _find_direct_child(ws, "table") is None:
            issues.append(f"Worksheet '{ws_name}' missing table")

    dashboards = [el for el in root.iter() if _local_name(el.tag) == "dashboard"]
    worksheet_names = {
        ws.attrib.get("name", "").strip()
        for ws in worksheets
        if isinstance(ws.attrib.get("name"), str) and ws.attrib.get("name", "").strip()
    }
    if worksheet_names and not dashboards:
        issues.append("Missing dashboard: at least one dashboard is required when worksheets exist")

    dashboard_zone_sheet_names: set[str] = set()
    for idx, dashboard in enumerate(dashboards, start=1):
        dashboard_name = dashboard.attrib.get("name", f"dashboard_{idx}")

        dash_datasources = _find_direct_child(dashboard, "datasources")
        if dash_datasources is None:
            issues.append(f"Dashboard '{dashboard_name}' missing datasources")
        else:
            linked_sources = [
                el
                for el in list(dash_datasources)
                if _local_name(el.tag) == "datasource" and isinstance(el.attrib.get("name"), str) and el.attrib.get("name", "").strip()
            ]
            if not linked_sources:
                issues.append(f"Dashboard '{dashboard_name}' has no linked datasource")

        dash_zones = _find_direct_child(dashboard, "zones")
        if dash_zones is None:
            issues.append(f"Dashboard '{dashboard_name}' missing zones")
        else:
            for zone in dashboard.iter():
                if _local_name(zone.tag) != "zone":
                    continue
                zone_type = (zone.attrib.get("type-v2") or "").strip().lower()
                zone_name = (zone.attrib.get("name") or "").strip()
                if zone_type == "worksheet" and zone_name:
                    dashboard_zone_sheet_names.add(zone_name)

        dash_devicelayouts = _find_direct_child(dashboard, "devicelayouts")
        if dash_devicelayouts is None:
            issues.append(f"Dashboard '{dashboard_name}' missing devicelayouts")
        else:
            devicelayout_nodes = [el for el in list(dash_devicelayouts) if _local_name(el.tag) == "devicelayout"]
            if not devicelayout_nodes:
                issues.append(f"Dashboard '{dashboard_name}' devicelayouts is empty")

    if worksheet_names:
        missing_in_dashboard = sorted(name for name in worksheet_names if name not in dashboard_zone_sheet_names)
        for worksheet_name in missing_in_dashboard:
            issues.append(
                f"Worksheet '{worksheet_name}' is not placed in any dashboard zone"
            )

    windows = [el for el in root.iter() if _local_name(el.tag) == "window"]
    for idx, window in enumerate(windows, start=1):
        win_name = window.attrib.get("name", f"window_{idx}")
        win_class = (window.attrib.get("class") or "").strip().lower()
        if win_class == "dashboard":
            if _find_direct_child(window, "viewpoints") is None:
                issues.append(f"Dashboard window '{win_name}' missing viewpoints")
            if _find_direct_child(window, "active") is None:
                issues.append(f"Dashboard window '{win_name}' missing active")
            viewpoints = _find_direct_child(window, "viewpoints")
            if viewpoints is not None:
                dashboard_viewpoints = [el for el in list(viewpoints) if _local_name(el.tag) == "viewpoint"]
                if not dashboard_viewpoints:
                    issues.append(f"Dashboard window '{win_name}' has empty viewpoints")
            if _find_direct_child(window, "cards") is not None:
                issues.append(f"Dashboard window '{win_name}' contains worksheet-only cards")
            if _find_direct_child(window, "viewpoint") is not None:
                issues.append(f"Dashboard window '{win_name}' contains worksheet-only viewpoint")
        elif win_class == "worksheet":
            if _find_direct_child(window, "cards") is None:
                issues.append(f"Worksheet window '{win_name}' missing cards")
            if _find_direct_child(window, "viewpoint") is None:
                issues.append(f"Worksheet window '{win_name}' missing viewpoint")

    for forbidden in ["worksheet-number", "datagraph", "explain-data"]:
        if any(_local_name(el.tag) == forbidden for el in root.iter()):
            issues.append(f"Forbidden element found for target profile: {forbidden}")

    for devicelayouts in [el for el in root.iter() if _local_name(el.tag) == "devicelayouts"]:
        has_devicelayout = any(_local_name(child.tag) == "devicelayout" for child in list(devicelayouts))
        if not has_devicelayout:
            issues.append("Element 'devicelayouts' must contain at least one 'devicelayout'")

    return issues
