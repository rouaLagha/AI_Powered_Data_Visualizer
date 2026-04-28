import React, { useCallback, useEffect, useMemo, useRef } from "react";

const STREAMLIT_READY = "streamlit:componentReady";
const STREAMLIT_RENDER = "streamlit:render";

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  return String(value || "").trim();
}

function tableKey(value) {
  return text(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function tableHasBackendShape(schema) {
  return (
    toArray(schema?.fact_tables).length > 0 ||
    toArray(schema?.direct_dimensions).length > 0 ||
    toArray(schema?.snowflake_dimensions).length > 0
  );
}

function naturalKey(columns) {
  const values = toArray(columns).map((column) => text(column?.name || column));
  return values.find((column) => /(^id$|_id$|key$)/i.test(column)) || values[0] || "";
}

function relationshipType(relationship, factNames) {
  const fromIsFact = factNames.has(text(relationship.fromTable || relationship.from_table));
  const toIsFact = factNames.has(text(relationship.toTable || relationship.to_table));
  return fromIsFact || toIsFact ? "fact_to_dimension" : "dimension_to_dimension";
}

function joinCondition(relationship) {
  const explicit = text(relationship.join_condition);
  if (explicit) return explicit;

  const fromTable = text(relationship.fromTable || relationship.from_table);
  const toTable = text(relationship.toTable || relationship.to_table);
  const fromColumn = text(relationship.fromColumn || relationship.from_column);
  const toColumn = text(relationship.toColumn || relationship.to_column);
  if (!fromTable || !toTable || !fromColumn || !toColumn) return "";
  return `${fromTable}.${fromColumn} = ${toTable}.${toColumn}`;
}

function schemaFromUiModel(model) {
  const tables = toArray(model?.tables);
  const relationships = toArray(model?.relationships);
  const factNames = new Set(
    tables.filter((table) => table.type === "fact").map((table) => text(table.name)).filter(Boolean),
  );

  const snowflakeNames = new Set();
  relationships.forEach((relationship) => {
    if (relationshipType(relationship, factNames) === "dimension_to_dimension") {
      const target = text(relationship.toTable || relationship.to_table);
      if (target) snowflakeNames.add(target);
    }
  });

  const factTables = tables
    .filter((table) => table.type === "fact")
    .map((table) => ({
      name: table.name,
      grain: table.grain || "Report dataset grain",
      foreign_keys: toArray(table.columns).filter((column) => /key|id/i.test(text(column))),
      measures: toArray(table.measures).length
        ? table.measures
        : toArray(model?.measures)
            .filter((measure) => measure.sourceTable === table.name)
            .map((measure) => measure.name),
    }));

  const dimensionForTable = (table) => ({
    name: table.name,
    natural_key: naturalKey(table.columns),
    attributes: toArray(table.columns),
  });

  const directDimensions = tables
    .filter((table) => table.type !== "fact" && !snowflakeNames.has(text(table.name)))
    .map(dimensionForTable);

  const snowflakeDimensions = tables
    .filter((table) => table.type !== "fact" && snowflakeNames.has(text(table.name)))
    .map(dimensionForTable);

  const normalizedRelationships = relationships.map((relationship) => ({
    from_table: relationship.fromTable || relationship.from_table || "",
    to_table: relationship.toTable || relationship.to_table || "",
    relationship_type: relationshipType(relationship, factNames),
    cardinality: relationship.cardinality || "1:N",
    join_condition: joinCondition(relationship),
  }));

  const metadataTables = {};
  tables.forEach((table) => {
    const key = tableKey(table.name);
    if (!key) return;
    metadataTables[key] = {
      columns: toArray(table.columns).map((column) => ({ name: text(column?.name || column) })),
      primary_key: naturalKey(table.columns) ? [naturalKey(table.columns)] : [],
      foreign_keys: toArray(table.columns)
        .filter((column) => /key|id/i.test(text(column?.name || column)))
        .map((column) => ({ column: text(column?.name || column) })),
    };
  });

  return {
    model_type: model?.schemaType || "Dimensional Model",
    fact_tables: factTables,
    direct_dimensions: directDimensions,
    snowflake_dimensions: snowflakeDimensions,
    relationships: normalizedRelationships,
    schema_metadata: {
      tables: metadataTables,
    },
  };
}

function schemaForFlow(backendModel, uiModel) {
  if (backendModel && typeof backendModel === "object" && tableHasBackendShape(backendModel)) {
    return backendModel;
  }
  return schemaFromUiModel(uiModel);
}

function safeSignature(schema) {
  try {
    return JSON.stringify(schema);
  } catch {
    return String(Date.now());
  }
}

export default function SchemaFlowFrame({ backendModel, model, height = 620 }) {
  const iframeRef = useRef(null);
  const schema = useMemo(() => schemaForFlow(backendModel, model), [backendModel, model]);
  const schemaSignature = useMemo(() => safeSignature(schema), [schema]);

  const postSchema = useCallback(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target) return;
    target.postMessage(
      {
        type: STREAMLIT_RENDER,
        args: {
          schema,
          schema_signature: schemaSignature,
          height,
        },
      },
      "*",
    );
  }, [height, schema, schemaSignature]);

  useEffect(() => {
    const timer = window.setTimeout(postSchema, 80);

    function handleMessage(event) {
      if (event.source !== iframeRef.current?.contentWindow) return;
      if (event.data?.type === STREAMLIT_READY) postSchema();
    }

    window.addEventListener("message", handleMessage);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("message", handleMessage);
    };
  }, [postSchema]);

  return (
    <div className="schema-flow-frame-wrap" style={{ "--schema-flow-height": `${height}px` }}>
      <iframe
        ref={iframeRef}
        className="schema-flow-frame"
        title="Interactive schema flow"
        src="/schema-flow/index.html"
        onLoad={postSchema}
      />
    </div>
  );
}
