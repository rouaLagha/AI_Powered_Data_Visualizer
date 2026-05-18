import React from "react";
import Badge from "../shared/Badge.jsx";

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  return String(value || "").trim();
}

function tableKey(value) {
  return text(value)
    .toLowerCase()
    .replace(/[\[\]]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function columnPayload(column) {
  if (typeof column === "string") {
    return { name: column, dataType: "unknown", role: "" };
  }

  const name = text(column?.name || column?.column || column?.column_name);
  return {
    name,
    dataType: text(column?.data_type || column?.datatype || column?.type) || "unknown",
    role: text(column?.semantic_role || column?.role),
    nullable: typeof column?.is_nullable === "boolean" ? column.is_nullable : null,
    maxLength: Number.isFinite(Number(column?.max_length)) ? Number(column.max_length) : null,
    numericPrecision: Number.isFinite(Number(column?.numeric_precision)) ? Number(column.numeric_precision) : null,
    numericScale: Number.isFinite(Number(column?.numeric_scale)) ? Number(column.numeric_scale) : null,
    datetimePrecision: Number.isFinite(Number(column?.datetime_precision)) ? Number(column.datetime_precision) : null,
  };
}

function foreignKeyPayload(foreignKey) {
  if (!foreignKey || typeof foreignKey !== "object") return null;
  const column = text(foreignKey.column || foreignKey.from_column || foreignKey.name);
  if (!column) return null;
  return {
    column,
    refSchema: text(foreignKey.ref_schema),
    refTable: text(foreignKey.ref_table || foreignKey.references_table),
    refColumn: text(foreignKey.ref_column || foreignKey.references_column),
  };
}

function typeLabel(column) {
  const baseType = text(column.dataType) || "unknown";
  if (column.maxLength !== null && column.maxLength !== undefined) {
    const length = column.maxLength === -1 ? "max" : column.maxLength;
    return `${baseType}(${length})`;
  }
  if (column.numericPrecision !== null && column.numericPrecision !== undefined) {
    if (column.numericScale !== null && column.numericScale !== undefined) {
      return `${baseType}(${column.numericPrecision},${column.numericScale})`;
    }
    return `${baseType}(${column.numericPrecision})`;
  }
  if (column.datetimePrecision !== null && column.datetimePrecision !== undefined) {
    return `${baseType}(${column.datetimePrecision})`;
  }
  return baseType;
}

function metadataTables(backendModel) {
  const tables = backendModel?.schema_metadata?.tables;
  if (!tables || typeof tables !== "object") return new Map();

  return new Map(
    Object.entries(tables).map(([key, table]) => {
      const fullName = text(table.full_name);
      const name = text(table.name || fullName || key);
      return [
        tableKey(fullName || name || key),
        {
          schema: text(table.schema),
          name,
          fullName: fullName || name,
          tableType: text(table.table_type || table.type),
          columns: toArray(table.columns).map(columnPayload).filter((column) => column.name),
          primaryKey: toArray(table.primary_key).map(text).filter(Boolean),
          foreignKeys: toArray(table.foreign_keys).map(foreignKeyPayload).filter(Boolean),
        },
      ];
    }),
  );
}

function modelTables(model) {
  return toArray(model?.tables).map((table) => ({
    schema: "",
    name: text(table.name),
    fullName: text(table.name),
    tableType: text(table.type) || "model table",
    columns: toArray(table.columns).map(columnPayload).filter((column) => column.name),
    primaryKey: [],
    foreignKeys: [],
  }));
}

function inventoryTables(databaseContext) {
  return toArray(databaseContext?.available_tables).map((table) => ({
    schema: text(table.schema),
    name: text(table.name || table.full_name),
    fullName: text(table.full_name || table.name),
    tableType: text(table.table_type || table.type),
    columns: toArray(table.columns).map(columnPayload).filter((column) => column.name),
    primaryKey: toArray(table.primary_key).map(text).filter(Boolean),
    foreignKeys: toArray(table.foreign_keys).map(foreignKeyPayload).filter(Boolean),
  }));
}

function databaseRows(model, backendModel, databaseContext) {
  const metadataByKey = metadataTables(backendModel);
  const rows = new Map();

  function mergeTable(table, source) {
    if (!table.name && !table.fullName) return;
    const key = tableKey(table.fullName || table.name);
    const metadata = metadataByKey.get(key) || metadataByKey.get(tableKey(table.name));
    const existing = rows.get(key) || {};
    rows.set(key, {
      ...existing,
      ...table,
      ...metadata,
      source: existing.source || source,
      columns:
        existing.source === "database" && existing.columns?.length
          ? existing.columns
          : table.columns?.length
            ? table.columns
            : metadata?.columns?.length
              ? metadata.columns
              : existing.columns || [],
      primaryKey:
        existing.source === "database" && existing.primaryKey?.length
          ? existing.primaryKey
          : table.primaryKey?.length
            ? table.primaryKey
            : metadata?.primaryKey?.length
              ? metadata.primaryKey
              : existing.primaryKey || [],
      foreignKeys:
        existing.source === "database" && existing.foreignKeys?.length
          ? existing.foreignKeys
          : table.foreignKeys?.length
            ? table.foreignKeys
            : metadata?.foreignKeys?.length
              ? metadata.foreignKeys
              : existing.foreignKeys || [],
    });
  }

  inventoryTables(databaseContext).forEach((table) => mergeTable(table, "database"));
  Array.from(metadataByKey.values()).forEach((table) => mergeTable(table, "metadata"));
  modelTables(model).forEach((table) => mergeTable(table, "model"));

  return Array.from(rows.values()).sort((left, right) =>
    (left.fullName || left.name).localeCompare(right.fullName || right.name),
  );
}

function tableTone(source, tableType) {
  if (source === "database") return "green";
  if (String(tableType || "").toLowerCase().includes("fact")) return "indigo";
  return "orange";
}

export default function DatabaseOverviewPanel({ model, backendModel, databaseContext: explicitDatabaseContext }) {
  const hasExplicitContext = explicitDatabaseContext && Object.keys(explicitDatabaseContext).length > 0;
  const databaseContext = hasExplicitContext ? explicitDatabaseContext : backendModel?.database_context || {};
  const rows = databaseRows(model, backendModel, databaseContext);
  const databaseName = text(databaseContext.database);
  const sourceLabel =
    databaseContext.inventory_source === "workbook_metadata"
      ? "workbook metadata"
      : databaseContext.connected
        ? "database inventory"
        : "model metadata";
  const columnCount = rows.reduce((count, table) => count + toArray(table.columns).length, 0);
  const primaryKeyCount = rows.reduce((count, table) => count + toArray(table.primaryKey).length, 0);
  const foreignKeyCount = rows.reduce((count, table) => count + toArray(table.foreignKeys).length, 0);
  const schemaCount = new Set(rows.map((table) => text(table.schema)).filter(Boolean)).size;
  const error = text(databaseContext.error);
  const metrics = [
    { label: "Tables", value: rows.length },
    { label: "Attributes", value: columnCount },
    { label: "Keys", value: primaryKeyCount + foreignKeyCount },
    { label: "Schemas", value: schemaCount || "-" },
  ];

  return (
    <div className="database-overview">
      <div className="database-overview-header">
        <div className="database-overview-copy">
          <div className="database-overview-title-row">
            <strong>Database overview</strong>
            <Badge tone={databaseContext.connected ? "green" : "orange"}>
              {databaseContext.connected ? "Connected" : "Metadata"}
            </Badge>
          </div>
          <span>
            {databaseName ? databaseName : "Available metadata"} from {sourceLabel}
          </span>
        </div>
        <div className="database-overview-metrics" aria-label="Database summary">
          {metrics.map((metric) => (
            <div className="database-overview-metric" key={metric.label}>
              <small>{metric.label}</small>
              <strong>{metric.value}</strong>
            </div>
          ))}
        </div>
      </div>

      <div className="database-table-list">
        {error && !databaseContext.connected && !rows.length && (
          <div className="empty-state database-empty">Database introspection unavailable: {error}</div>
        )}
        {!rows.length && !error && (
          <div className="empty-state database-empty">No database tables are available yet.</div>
        )}
        {rows.map((table) => {
          const displayName = table.fullName || table.name;
          const schema = text(table.schema);
          const hasSchemaInName = schema && displayName.toLowerCase().includes(schema.toLowerCase());
          return (
            <details className="database-table-card" key={displayName}>
              <summary>
                <span className="database-table-summary-main">
                  <span className="database-table-title">
                    <strong>{displayName}</strong>
                    {schema && !hasSchemaInName && <small className="database-schema-pill">{schema}</small>}
                  </span>
                  <small className="database-table-meta">
                    {table.columns.length} attributes
                    {table.primaryKey.length ? ` / PK: ${table.primaryKey.join(", ")}` : ""}
                    {table.foreignKeys.length ? ` / ${table.foreignKeys.length} FK` : ""}
                  </small>
                </span>
                <Badge tone={tableTone(table.source, table.tableType)}>{table.tableType || table.source}</Badge>
              </summary>

              {table.columns.length ? (
                <div className="database-columns">
                  <div className="database-column-row database-column-head">
                    <span>Attribute</span>
                    <span>Type</span>
                    <span>Keys</span>
                    <span>Null</span>
                    <span>Reference</span>
                  </div>
                  {table.columns.map((column) => {
                    const primaryKeys = new Set(table.primaryKey.map((item) => item.toLowerCase()));
                    const foreignKeysByColumn = new Map(
                      table.foreignKeys.map((foreignKey) => [foreignKey.column.toLowerCase(), foreignKey]),
                    );
                    const foreignKey = foreignKeysByColumn.get(column.name.toLowerCase());
                    const isPrimary = primaryKeys.has(column.name.toLowerCase());
                    return (
                      <div className="database-column-row" key={`${displayName}-${column.name}`}>
                        <span>{column.name}</span>
                        <code>{typeLabel(column)}</code>
                        <span className="database-key-badges">
                          {isPrimary && <Badge tone="indigo">PK</Badge>}
                          {foreignKey && <Badge tone="orange">FK</Badge>}
                          {column.role && !isPrimary && !foreignKey && <Badge tone="gray">{column.role}</Badge>}
                        </span>
                        {column.nullable !== null ? <small>{column.nullable ? "nullable" : "required"}</small> : <small>-</small>}
                        <small className="database-reference">
                          {foreignKey
                            ? `${foreignKey.refSchema ? `${foreignKey.refSchema}.` : ""}${foreignKey.refTable}.${foreignKey.refColumn}`
                            : "-"}
                        </small>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="empty-state database-empty">Column metadata is not available for this table yet.</div>
              )}
            </details>
          );
        })}
      </div>
    </div>
  );
}
