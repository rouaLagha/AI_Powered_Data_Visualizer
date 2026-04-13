from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET


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
FIELD_EXPR_REF_RE = re.compile(r"Fields!([A-Za-z0-9_]+)\\.Value", flags=re.IGNORECASE)


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
        provider = (ds.get("provider") or "").lower()
        provider_class = _provider_to_tableau_class(provider)
        conn_string = ds.get("connection_string") or ""
        parsed_conn = _parse_connection_string(conn_string)
        security_type = (ds.get("security_type") or "").lower()
        credential_retrieval = (ds.get("credential_retrieval") or "").lower()
        windows_credentials = ds.get("windows_credentials")
        user_name = ds.get("user_name")

        datasource_node = _find_or_create_datasource(datasources_node, ds_name)
        if "caption" not in datasource_node.attrib:
            datasource_node.set("caption", ds_name)
        datasource_node.attrib.setdefault("inline", "true")
        datasource_node.attrib.setdefault("hasconnection", "true")

        connection_node = datasource_node.find("connection")
        if connection_node is None:
            connection_node = ET.SubElement(datasource_node, "connection")

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
            for attr in ["server", "dbname", "port", "authentication", "username", "odbc-connect-string-extras"]:
                connection_node.attrib.pop(attr, None)

        if parsed_conn.get("server"):
            target_connection.set("server", parsed_conn["server"])
        if parsed_conn.get("dbname"):
            target_connection.set("dbname", parsed_conn["dbname"])
        if parsed_conn.get("port"):
            target_connection.set("port", parsed_conn["port"])
        if parsed_conn.get("odbc_connect_string_extras"):
            target_connection.set("odbc-connect-string-extras", parsed_conn["odbc_connect_string_extras"])

        authentication = _map_tableau_authentication(
            security_type=security_type,
            credential_retrieval=credential_retrieval,
            windows_credentials=windows_credentials,
            connection_string=conn_string,
            user_name=user_name,
        )
        if authentication:
            target_connection.set("authentication", authentication)
        if authentication == "username-password" and isinstance(user_name, str) and user_name.strip():
            target_connection.set("username", user_name.strip())

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
            _upsert_datasource_columns_from_catalog(datasource_node, catalog_source, prune_existing=True)
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


def _normalize_table_reference(table_ref: str) -> str:
    parts = [p.strip() for p in (table_ref or "").split(".") if p.strip()]
    normalized: list[str] = []
    for part in parts:
        clean = part[1:-1].strip() if part.startswith("[") and part.endswith("]") else part.strip("[] ")
        if clean:
            normalized.append(f"[{clean}]")
    if not normalized:
        return "[UnknownTable]"
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
) -> None:
    for child in list(connection_node):
        if _local_name(child.tag) == "relation":
            connection_node.remove(child)

    table_items: list[tuple[str, str]] = []
    table_by_name: dict[str, str] = {}
    for table_ref in table_refs[:24]:
        name = _relation_name_from_table_reference(table_ref)
        norm = _normalize_table_reference(table_ref)
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
            return item
    return None


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


def _upsert_datasource_columns_from_catalog(
    datasource_node: ET.Element,
    catalog_source: dict,
    prune_existing: bool = True,
) -> None:
    tables = catalog_source.get("tables") if isinstance(catalog_source, dict) else None
    if not isinstance(tables, list):
        return

    allowed_names: set[str] = set()
    for table in tables:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns")
        if not isinstance(columns, list):
            continue
        for column in columns:
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            if isinstance(col_name, str) and col_name.strip():
                allowed_names.add(col_name.strip().lower())

    existing_by_name: dict[str, ET.Element] = {}
    for child in [c for c in list(datasource_node) if _local_name(c.tag) == "column"]:
        clean_name = _clean_bracketed_name(child.attrib.get("name", "")).strip().lower()
        if prune_existing and clean_name and clean_name not in allowed_names:
            datasource_node.remove(child)
            continue
        if clean_name and clean_name not in existing_by_name:
            existing_by_name[clean_name] = child

    seen_names: set[str] = set(existing_by_name.keys())
    for table in tables:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns")
        if not isinstance(columns, list):
            continue

        for index, column in enumerate(columns):
            if not isinstance(column, dict):
                continue
            col_name = column.get("name")
            if not isinstance(col_name, str) or not col_name.strip():
                continue

            clean_name = col_name.strip()
            key = clean_name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            role, datatype, ctype = _infer_tableau_type_from_sql(
                column_name=clean_name,
                sql_data_type=column.get("data_type"),
                index=index,
            )

            attrs = {
                "name": f"[{clean_name}]",
                "role": role,
                "datatype": datatype,
                "type": ctype,
            }
            ET.SubElement(datasource_node, "column", attrib=attrs)


def _upsert_cols_map_from_catalog(
    datasource_node: ET.Element,
    connection_node: ET.Element,
    catalog_source: dict,
    table_refs: list[str],
) -> None:
    tables = catalog_source.get("tables") if isinstance(catalog_source, dict) else None
    if not isinstance(tables, list):
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

    key_counts: dict[str, int] = {}
    for table in tables:
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
            base = col_name.strip()
            key_counts[base.lower()] = key_counts.get(base.lower(), 0) + 1

            # Keep only canonical field keys to match exposed datasource columns.
            if key_counts[base.lower()] > 1:
                continue

            map_key = f"[{base}]"

            map_value = f"[{table_clean}].[{base}]"
            ET.SubElement(cols_node, "map", attrib={"key": map_key, "value": map_value})


def _infer_tableau_type_from_sql(
    column_name: str,
    sql_data_type: object,
    index: int,
) -> tuple[str, str, str]:
    token = str(sql_data_type or "").strip().lower()
    name_token = str(column_name or "").strip()

    numeric = {
        "bigint",
        "int",
        "smallint",
        "tinyint",
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

    if token in numeric:
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
        created.append(
            ET.SubElement(
                datasource_node,
                "column",
                attrib=attrs,
            )
        )

    has_measure = any((c.attrib.get("role") or "").lower() == "measure" for c in created)
    if not has_measure and len(created) > 1:
        created[1].attrib["role"] = "measure"
        created[1].attrib["datatype"] = "real"
        created[1].attrib["type"] = "quantitative"


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

    if index == 0 or not looks_measure:
        return "dimension", "string", "nominal"
    return "measure", "real", "quantitative"


def inject_semantic_bindings(
    xml_content: str,
    data_model: dict,
    visual_model: dict,
    mapping: dict,
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
    datasource_field_roles = _build_datasource_field_role_lookup(datasource_nodes)
    datasource_field_tables = _build_datasource_field_table_lookup(datasource_nodes)
    datasource_field_names = _build_datasource_field_names_lookup(datasource_nodes)
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


def _collect_sheet_specs(visual_model: dict, mapping: dict) -> list[dict]:
    specs: list[dict] = []
    seen: set[str] = set()

    visual_nodes: dict[str, dict] = {}
    _collect_visual_nodes(visual_model.get("visuals", []) if isinstance(visual_model, dict) else [], visual_nodes)

    mapped_dataset_by_visual: dict[str, str] = {}
    visual_to_dataset = mapping.get("visual_to_dataset", []) if isinstance(mapping, dict) else []
    if isinstance(visual_to_dataset, list):
        for item in visual_to_dataset:
            if not isinstance(item, dict):
                continue
            visual_name = item.get("visual_name")
            dataset_name = item.get("dataset_name")
            if isinstance(visual_name, str) and visual_name.strip() and isinstance(dataset_name, str) and dataset_name.strip():
                mapped_dataset_by_visual[visual_name.strip()] = dataset_name.strip()

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
            dataset_name = sheet.get("dataset_name") or mapped_dataset_by_visual.get(name.strip())
            expressions = visual_node.get("expressions", []) if isinstance(visual_node, dict) else []
            referenced_fields = _extract_fields_from_expressions(expressions)

            specs.append(
                {
                    "name": name.strip(),
                    "visual_type": visual_type if isinstance(visual_type, str) else "",
                    "dataset_name": dataset_name if isinstance(dataset_name, str) else "",
                    "fields": referenced_fields,
                }
            )

    if specs:
        return specs

    for visual_name, visual_node in visual_nodes.items():
        if visual_name in seen:
            continue
        seen.add(visual_name)
        name = visual_node.get("name") if isinstance(visual_node.get("name"), str) else visual_name
        visual_type = visual_node.get("visual_type") if isinstance(visual_node.get("visual_type"), str) else ""
        expressions = visual_node.get("expressions", []) if isinstance(visual_node, dict) else []
        referenced_fields = _extract_fields_from_expressions(expressions)
        dataset_name = mapped_dataset_by_visual.get(name, "")
        specs.append(
            {
                "name": name,
                "visual_type": visual_type,
                "dataset_name": dataset_name,
                "fields": referenced_fields,
            }
        )

    return specs


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
                "expressions": item.get("expressions") if isinstance(item.get("expressions"), list) else [],
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
        dim_field, measure_field, has_numeric_measure = _choose_sheet_dim_measure_fields(
            referenced_fields=referenced_fields,
            dataset_fields=dataset_fields,
            global_field_names=global_field_names,
            alias_to_source=alias_to_source,
            field_roles=field_roles,
            field_tables=field_tables,
            datasource_fields=ds_fields,
        )
        dim_caption = dim_field
        measure_caption = measure_field

        visual_type = spec.get("visual_type") if isinstance(spec.get("visual_type"), str) else ""
        mark_class = _visual_type_to_mark_class(visual_type) or "Bar"
        if not has_numeric_measure:
            mark_class = "Text"

        ws = ET.SubElement(worksheets, "worksheet", attrib={"name": worksheet_name})
        ET.SubElement(ws, "layout-options")

        table = ET.SubElement(ws, "table")
        view = ET.SubElement(table, "view")
        view_dss = ET.SubElement(view, "datasources")
        ET.SubElement(view_dss, "datasource", attrib={"name": sheet_ds_name})

        deps = ET.SubElement(view, "datasource-dependencies", attrib={"datasource": sheet_ds_name})
        _append_default_dependency_bindings(
            deps_node=deps,
            dim_field=dim_field,
            measure_field=measure_field,
            dim_caption=dim_caption,
            measure_caption=measure_caption,
            add_columns=True,
            add_instances=True,
            include_measure=has_numeric_measure,
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

        if _is_pie_mark_class(mark_class):
            rows.text = ""
            cols.text = ""
            _set_pie_pane_encodings(pane, sheet_ds_name, dim_field, measure_field)
            continue

        if _is_title_like_text_sheet(mark_class, visual_type, worksheet_name):
            rows.text = ""
            cols.text = ""
            continue

        if not has_numeric_measure:
            rows.text = ""
            cols.text = f"[{sheet_ds_name}].[none:{dim_field}:nk]"
            continue

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
    for raw in referenced_fields:
        if not isinstance(raw, str):
            continue
        clean = raw.strip()
        if not clean:
            continue
        canonical = alias_map.get(clean.lower(), clean)
        if scoped and canonical not in scoped:
            continue
        if canonical not in candidates:
            candidates.append(canonical)

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

    measure_field = dim_field
    measure_options: list[str] = []
    for candidate in candidates:
        if candidate == dim_field:
            continue
        if _role_of(candidate) == "measure":
            measure_options.append(candidate)

    if measure_options:
        measure_field = sorted(measure_options, key=_measure_priority_score)[0]

    has_numeric_measure = _role_of(measure_field) == "measure"

    if not has_numeric_measure:
        fallback_options: list[str] = []
        for candidate in scoped + global_scoped:
            if candidate == dim_field:
                continue
            if _role_of(candidate) == "measure":
                fallback_options.append(candidate)
        if fallback_options:
            measure_field = sorted(fallback_options, key=_measure_priority_score)[0]
            has_numeric_measure = True

    if measure_field == dim_field and len(candidates) > 1:
        for candidate in candidates:
            if candidate != dim_field:
                measure_field = candidate
                break

    if not has_numeric_measure:
        has_numeric_measure = _role_of(measure_field) == "measure"

    # Collection-style physical model is sensitive to cross-table shelf fields.
    # Prefer dim/measure coming from the same mapped table when possible.
    if has_numeric_measure:
        measure_table = _table_of(measure_field)
        dim_table = _table_of(dim_field)
        all_candidates = _uniq(candidates + scoped + global_scoped + datasource_scoped)

        if measure_table and dim_table and measure_table != dim_table:
            dim_same_table = [
                c
                for c in all_candidates
                if _role_of(c) == "dimension" and _table_of(c) == measure_table and not _is_key_like_column_name(c)
            ]
            if not dim_same_table:
                dim_same_table = [
                    c for c in all_candidates if _role_of(c) == "dimension" and _table_of(c) == measure_table
                ]
            if dim_same_table:
                dim_field = dim_same_table[0]
                dim_table = _table_of(dim_field)

        if measure_table and dim_table and measure_table != dim_table:
            measure_same_table = [
                c for c in all_candidates if _role_of(c) == "measure" and _table_of(c) == dim_table
            ]
            if measure_same_table:
                measure_field = sorted(measure_same_table, key=_measure_priority_score)[0]
                has_numeric_measure = True
            else:
                has_numeric_measure = False

    return dim_field, measure_field, has_numeric_measure


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


def _provider_to_tableau_class(provider: str) -> str:
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

    server = kv.get("data source") or kv.get("server") or kv.get("address") or ""
    dbname = kv.get("initial catalog") or kv.get("database") or kv.get("dbname") or ""

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
        "server": server,
        "dbname": dbname,
        "port": port,
        "odbc_connect_string_extras": ";".join(extras_parts),
    }


def _map_tableau_authentication(
    security_type: str,
    credential_retrieval: str,
    windows_credentials: bool | None,
    connection_string: str,
    user_name: str | None,
) -> str:
    conn = (connection_string or "").lower()

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
    if "user id=" in conn or "uid=" in conn or (isinstance(user_name, str) and user_name.strip()):
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
                has_measure_dependency = False
                table_old = _find_direct_child(ws, "table")
                if table_old is not None:
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

                mark_class = _extract_table_mark_class(table_old)
                preserve_empty_shelves = False
                if table_old is not None and _is_text_mark_class(mark_class):
                    preserve_empty_shelves = not rows_text and not cols_text
                sheets_payload.append(
                    {
                        "name": ws.attrib["name"],
                        "rows": rows_text,
                        "cols": cols_text,
                        "mark_class": mark_class,
                        "preserve_empty_shelves": "true" if preserve_empty_shelves else "",
                        "deps_datasource_name": deps_datasource_name,
                        "dependency_children": dependency_children,
                        "has_measure_dependency": "true" if has_measure_dependency else "",
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
        preserve_empty_shelves = sheet.get("preserve_empty_shelves") == "true"
        has_measure_dependency = sheet.get("has_measure_dependency") == "true"

        deps_datasource_name = sheet.get("deps_datasource_name")
        if not isinstance(deps_datasource_name, str) or not deps_datasource_name.strip():
            deps_datasource_name = first_ds_name
        deps_datasource_name = deps_datasource_name.strip()

        # Tableau cartesian default: X on columns, Y on rows.
        rows_default = f"[{deps_datasource_name}].[sum:{measure_field}:qk]"
        cols_default = f"[{deps_datasource_name}].[none:{dim_field}:nk]"

        rows_text = "" if (is_pie or preserve_empty_shelves) else (sheet["rows"] or rows_default)
        cols_text = "" if (is_pie or preserve_empty_shelves) else (sheet["cols"] or cols_default)

        if not is_pie and not preserve_empty_shelves:
            if _contains_placeholder_field_reference(rows_text):
                rows_text = rows_default
            if _contains_placeholder_field_reference(cols_text):
                cols_text = cols_default

        if _is_text_mark_class(mark_class) and not has_measure_dependency:
            rows_text = ""
            if not cols_text:
                cols_text = f"[{deps_datasource_name}].[none:{dim_field}:nk]"

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
            dim_field=dim_field,
            measure_field=measure_field,
            add_columns=not has_dependency_columns,
            add_instances=not has_dependency_instances,
        )

        ET.SubElement(view, "perspectives")
        ET.SubElement(view, "aggregation", attrib={"value": "true"})

        ET.SubElement(table, "style")
        panes = ET.SubElement(table, "panes")
        if isinstance(mark_class, str) and mark_class:
            pane = ET.SubElement(panes, "pane")
            _ensure_pane_view(pane)
            ET.SubElement(pane, "mark", attrib={"class": mark_class})
            if is_pie:
                _set_pie_pane_encodings(pane, deps_datasource_name, dim_field, measure_field)
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
                "datatype": "string",
                "type": "nominal",
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
                "type": "nominal",
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


def _is_pie_mark_class(mark_class: str | None) -> bool:
    return isinstance(mark_class, str) and mark_class.strip().lower() == "pie"


def _is_text_mark_class(mark_class: str | None) -> bool:
    return isinstance(mark_class, str) and mark_class.strip().lower() == "text"


def _is_title_like_text_sheet(mark_class: str | None, visual_type: str, worksheet_name: str) -> bool:
    if not _is_text_mark_class(mark_class):
        return False

    vt = (visual_type or "").strip().lower()
    wn = (worksheet_name or "").strip().lower()

    # Title/textbox sheets should not be converted into text crosstabs.
    if any(token in vt for token in ["textbox", "title", "label"]):
        return True
    if any(token in wn for token in ["title", "header", "subtitle"]):
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


def _visual_type_to_mark_class(visual_type: str) -> str:
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
    if "textbox" in normalized:
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


def _sanitize_windows(root: ET.Element) -> None:
    windows = _find_direct_child(root, "windows")
    if windows is None:
        windows = ET.SubElement(root, "windows")

    worksheet_names: list[str] = []
    worksheets = _find_direct_child(root, "worksheets")
    if worksheets is not None:
        for ws in [c for c in list(worksheets) if _local_name(c.tag) == "worksheet"]:
            name = ws.attrib.get("name")
            if name:
                worksheet_names.append(name)

    # Rebuild windows section to guaranteed-valid worksheet-window form.
    for child in list(windows):
        windows.remove(child)

    for name in worksheet_names or ["Sheet 1"]:
        win = ET.SubElement(windows, "window", attrib={"name": name, "class": "worksheet"})
        ET.SubElement(win, "cards")
        ET.SubElement(win, "viewpoint")


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

    schema_tags = ["<group", "<sequence", "<choice", "<all", "<complexType", "<simpleType", "<xs:"]
    for token in schema_tags:
        if token.lower() in xml_lower:
            issues.append(f"Schema-definition token found in instance XML: {token}")

    required_top_level = ["datasources", "worksheets", "windows"]
    for tag in required_top_level:
        if _find_direct_child(root, tag) is None:
            issues.append(f"Missing required top-level element: {tag}")

    worksheets = [el for el in root.iter() if _local_name(el.tag) == "worksheet"]
    if not worksheets:
        issues.append("No worksheet elements found")

    for idx, ws in enumerate(worksheets, start=1):
        ws_name = ws.attrib.get("name", f"worksheet_{idx}")
        if _find_direct_child(ws, "layout-options") is None and _find_direct_child(ws, "repository-location") is None:
            issues.append(f"Worksheet '{ws_name}' missing layout-options/repository-location")
        if _find_direct_child(ws, "table") is None:
            issues.append(f"Worksheet '{ws_name}' missing table")

    for forbidden in ["simple-id", "worksheet-number", "datagraph", "explain-data"]:
        if any(_local_name(el.tag) == forbidden for el in root.iter()):
            issues.append(f"Forbidden element found for target profile: {forbidden}")

    return issues
