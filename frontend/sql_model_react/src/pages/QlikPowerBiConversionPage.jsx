import React, { useEffect, useRef, useState } from "react";
import { readFileAsBase64, runQlikPowerBiMetadataJob } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";

const ARTIFACT_LABELS = {
  powerbi_intermediate_model: "Modèle intermédiaire Power BI",
  qlik_metadata: "Métadonnées Qlik brutes",
  visual_metadata_path: "Visualisations Qlik",
  connection_metadata_path: "Connexions Qlik",
  dataprep_cache_metadata_path: "Cache DataPrep QVD",
  job: "Rapport d'extraction",
  qvf_path: "QVF stocké",
};

const ARTIFACT_ORDER = [
  "powerbi_intermediate_model",
  "qlik_metadata",
  "visual_metadata_path",
  "connection_metadata_path",
  "dataprep_cache_metadata_path",
  "job",
  "qvf_path",
];

const VISUAL_TYPE_LABELS = {
  barchart: "Histogramme",
  linechart: "Courbe",
  combochart: "Combo chart",
  piechart: "Donut",
  kpi: "KPI",
  gauge: "Jauge",
  table: "Tableau",
  straighttable: "Tableau",
  "sn-table": "Tableau",
  "pivot-table": "Tableau croisé",
  filterpane: "Panneau de filtres",
  scatterplot: "Scatter plot",
  treemap: "Treemap",
  map: "Carte",
};

const METRICS = [
  { key: "sheet_count", label: "Sheets", icon: "Sh", tone: "green" },
  { key: "visual_count", label: "Visuals", icon: "Vi", tone: "blue" },
  { key: "table_count", label: "Tables", icon: "Ta", tone: "red" },
  { key: "filter_count", label: "Filtres", icon: "Fi", tone: "cyan" },
];

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function displayValue(value, fallback = "-") {
  const text = String(value || "").trim();
  return text || fallback;
}

function metricValue(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toLocaleString() : "0";
}

function formatSize(size) {
  if (!size) return "";
  return `${(size / 1024).toFixed(1)} KB`;
}

function artifactLabel(key) {
  return ARTIFACT_LABELS[key] || key.replaceAll("_", " ");
}

function artifactRows(artifacts) {
  return Object.entries(artifacts || {})
    .filter(([, artifact]) => artifact?.exists)
    .sort(([leftKey], [rightKey]) => {
      const leftIndex = ARTIFACT_ORDER.indexOf(leftKey);
      const rightIndex = ARTIFACT_ORDER.indexOf(rightKey);
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    });
}

function statusTone(result) {
  const status = String(result?.status || "").toLowerCase();
  if (status === "failed") return "red";
  if (status === "completed") return "green";
  return "gray";
}

function fieldNames(items, key = "name") {
  const names = toArray(items)
    .map((item) => (item && typeof item === "object" ? item[key] || item.label || item.field || item.name : item))
    .filter(Boolean);
  return names.length ? names.slice(0, 5).join(", ") : "-";
}

function visualTypeLabel(visual) {
  const qlikType = String(visual?.qlik_type || visual?.type || "").trim().toLowerCase();
  return VISUAL_TYPE_LABELS[qlikType] || displayValue(visual?.qlik_type || visual?.powerbi_visual_type_hint);
}

function isInternalConnection(connection) {
  const name = String(connection?.name || connection?.id || "").toLowerCase();
  return Boolean(connection?.internal) || !name || name.includes("dataprepappcache") || name.startsWith("__");
}

function firstExternalConnection(connections) {
  return toArray(connections).find((connection) => connection && !isInternalConnection(connection)) || toArray(connections)[0] || {};
}

function parseConnectionValue(connectionString, keys) {
  const entries = String(connectionString || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.split("=", 2))
    .filter((parts) => parts.length === 2)
    .reduce((acc, [key, value]) => {
      acc[key.trim().toLowerCase()] = value.trim();
      return acc;
    }, {});
  for (const key of keys) {
    const value = entries[key.toLowerCase()];
    if (value) return value;
  }
  return "";
}

function connectionDraftFromConnection(connection) {
  return {
    serverName: displayValue(connection?.server || connection?.server_name || connection?.host || ""),
    authentication: displayValue(connection?.authentication || connection?.auth_method || ""),
    userName: displayValue(connection?.username || connection?.user_name || ""),
    password: displayValue(connection?.password || ""),
    databaseName: displayValue(connection?.database || connection?.database_name || ""),
  };
}

function tableName(table) {
  return String(table?.name || table?.table_name || "").trim();
}

function tableColumns(table) {
  const fields = toArray(table?.fields);
  if (fields.length) {
    return fields
      .map((field) => (field && typeof field === "object" ? field.name || field.field_name || field.label : field))
      .filter(Boolean);
  }
  return toArray(table?.field_names).filter(Boolean);
}

function relationshipCardinality(relationship) {
  const cardinality = String(relationship?.cardinality || "").toLowerCase();
  if (!cardinality) return "cardinalité non fournie";
  if (cardinality.includes("many_to_one")) return "* : 1";
  if (cardinality.includes("one_to_many")) return "1 : *";
  if (cardinality.includes("one_to_one")) return "1 : 1";
  if (cardinality.includes("many_to_many")) return "* : *";
  return relationship?.cardinality;
}

function selectFactTable(tables, relationships) {
  if (!tables.length || !relationships.length) return null;
  const scores = new Map();
  tables.forEach((table) => scores.set(tableName(table), 0));
  relationships.forEach((relationship) => {
    const fromTable = String(relationship.from_table || "");
    const toTable = String(relationship.to_table || "");
    scores.set(fromTable, (scores.get(fromTable) || 0) + 3);
    scores.set(toTable, (scores.get(toTable) || 0) - 1);
  });
  return [...tables].sort((left, right) => {
    const leftScore = scores.get(tableName(left)) || 0;
    const rightScore = scores.get(tableName(right)) || 0;
    if (leftScore !== rightScore) return rightScore - leftScore;
    return tableColumns(right).length - tableColumns(left).length;
  })[0];
}

function starSchemaModel(tables, relationships) {
  const factTable = selectFactTable(tables, relationships);
  if (!factTable) {
    return { factTable: null, dimensions: [] };
  }
  const factName = tableName(factTable);
  const tableByName = new Map(tables.map((table) => [tableName(table), table]));
  const related = relationships
    .filter((relationship) => relationship.from_table === factName || relationship.to_table === factName)
    .map((relationship) => {
      const dimensionName = relationship.from_table === factName ? relationship.to_table : relationship.from_table;
      return {
        table: tableByName.get(dimensionName),
        relationship,
      };
    })
    .filter((item) => item.table);
  return {
    factTable,
    dimensions: related,
  };
}

function StarTableCard({ table, role, expanded, onToggle }) {
  const columns = tableColumns(table);
  const visibleColumns = expanded ? columns : columns.slice(0, 4);
  return (
    <div className={`qpb-star-table ${role}`}>
      <button type="button" className="qpb-star-table-header" onClick={onToggle}>
        <span className="qpb-star-role">{role === "fact" ? "F" : role === "dimension" ? "D" : "Q"}</span>
        <span className="qpb-star-title">{displayValue(tableName(table), "Table")}</span>
        <span className="qpb-star-count">{columns.length}</span>
        <span className="qpb-star-chevron">{expanded ? "−" : "+"}</span>
      </button>
      <div className="qpb-star-columns">
        {visibleColumns.map((column, index) => (
          <span key={`${column}-${index}`}>{column}</span>
        ))}
        {!expanded && columns.length > visibleColumns.length && <em>+{columns.length - visibleColumns.length} colonnes</em>}
      </div>
    </div>
  );
}

function StarRelation({ item, side, expandedTables, toggleTable }) {
  const name = tableName(item.table);
  return (
    <div className={`qpb-star-relation ${side}`}>
      {side === "left" && (
        <StarTableCard table={item.table} role="dimension" expanded={expandedTables[name]} onToggle={() => toggleTable(name)} />
      )}
      <div className="qpb-star-link">
        <span>{relationshipCardinality(item.relationship)}</span>
      </div>
      {side === "right" && (
        <StarTableCard table={item.table} role="dimension" expanded={expandedTables[name]} onToggle={() => toggleTable(name)} />
      )}
    </div>
  );
}

function StarSchemaDiagram({ tables, relationships, expandedTables, toggleTable }) {
  const { factTable, dimensions } = starSchemaModel(tables, relationships);
  if (!factTable || !relationships.length) {
    return (
      <div className="qpb-qvd-only-model">
        <div className="qpb-qvd-note">
          Tables extraites des fichiers QVD uniquement. Aucune relation explicite n'a été extraite, donc aucune cardinalité n'est inventée.
        </div>
        <div className="qpb-qvd-table-grid">
          {tables.map((table, index) => {
            const name = tableName(table) || `table_${index + 1}`;
            return (
              <StarTableCard
                key={name}
                table={table}
                role="qvd"
                expanded={expandedTables[name]}
                onToggle={() => toggleTable(name)}
              />
            );
          })}
        </div>
      </div>
    );
  }
  const factName = tableName(factTable);
  const midpoint = Math.ceil(dimensions.length / 2);
  const leftDimensions = dimensions.slice(0, midpoint);
  const rightDimensions = dimensions.slice(midpoint);

  return (
    <div className="qpb-star-schema">
      <div className="qpb-star-side">
        {leftDimensions.map((item) => (
          <StarRelation
            key={`left-${tableName(item.table)}`}
            item={item}
            side="left"
            expandedTables={expandedTables}
            toggleTable={toggleTable}
          />
        ))}
      </div>
      <div className="qpb-star-center">
        <div className="qpb-star-hub-label">Table de faits</div>
        <StarTableCard table={factTable} role="fact" expanded={expandedTables[factName]} onToggle={() => toggleTable(factName)} />
        <div className="qpb-star-relation-summary">
          {`${relationships.length} relation(s) explicite(s) extraite(s)`}
        </div>
      </div>
      <div className="qpb-star-side">
        {rightDimensions.map((item) => (
          <StarRelation
            key={`right-${tableName(item.table)}`}
            item={item}
            side="right"
            expandedTables={expandedTables}
            toggleTable={toggleTable}
          />
        ))}
      </div>
    </div>
  );
}

export default function QlikPowerBiConversionPage() {
  const inputRef = useRef(null);
  const [qvfFile, setQvfFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [expandedTables, setExpandedTables] = useState({});
  const [connectionDraft, setConnectionDraft] = useState({
    serverName: "",
    authentication: "",
    userName: "",
    password: "",
    databaseName: "",
  });

  const selectedFileLabel = qvfFile ? `${qvfFile.name}${qvfFile.size ? ` (${formatSize(qvfFile.size)})` : ""}` : "Aucun QVF sélectionné";
  const failed = result?.status === "failed";
  const runStatus = failed ? "Échec" : result?.status === "completed" ? "Métadonnées prêtes" : "En attente";
  const model = result?.intermediate_model || result?.result?.intermediate_model_data || {};
  const summary = model?.summary || result?.summary || {};
  const sheets = toArray(model?.report?.sheets);
  // If no sheets were extracted, try to build a fallback sheet grouping from available visuals
  let displaySheets = sheets;
  if (!displaySheets.length) {
    const rawVisuals = toArray(model?.report?.visuals || result?.visual_metadata || result?.result?.visual_metadata || []);
    if (rawVisuals.length) {
      displaySheets = [
        {
          id: "__unassigned__",
          title: "Unassigned visuals",
          rank: 0,
          visuals: rawVisuals,
        },
      ];
    }
  }
  const tables = toArray(model?.semantic_model?.tables);
  const relationships = toArray(model?.semantic_model?.relationships);
  const connections = toArray(model?.semantic_model?.connections).length
    ? toArray(model?.semantic_model?.connections)
    : toArray(result?.connection_metadata);
  const connection = firstExternalConnection(connections);
  const artifacts = artifactRows(result?.artifacts);
  const appTitle = model?.source?.app_title || result?.app_id || "Application Qlik";

  useEffect(() => {
    setConnectionDraft(connectionDraftFromConnection(connection));
  }, [connection]);

  async function runExtraction() {
    if (!qvfFile) {
      setError("Ajoutez un fichier QVF avant de lancer l'extraction.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const contentBase64 = await readFileAsBase64(qvfFile);
      const response = await runQlikPowerBiMetadataJob({
        fileName: qvfFile.name,
        contentBase64,
      });
      setResult(response);
      if (response?.status === "failed") {
        setError(response.error || response.result?.error || "L'extraction Qlik a échoué.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function clearResult() {
    setResult(null);
    setError("");
    setExpandedTables({});
  }

  function toggleTable(table) {
    setExpandedTables((current) => ({
      ...current,
      [table]: !current[table],
    }));
  }

  return (
    <div className="page-stack qlik-powerbi-page qpb-dashboard">
      <Card className="qpb-control-card" title="Source Qlik" eyebrow={displayValue(appTitle)} actions={<Badge tone={statusTone(result)}>{runStatus}</Badge>}>
        <div className="qpb-control-grid">
          <div className="qpb-file-picker">
            <div>
              <span>Application source</span>
              <strong>{selectedFileLabel}</strong>
            </div>
            <input ref={inputRef} type="file" accept=".qvf" hidden onChange={(event) => setQvfFile(event.target.files?.[0] || null)} />
            <Button onClick={() => inputRef.current?.click()}>Choisir QVF</Button>
          </div>
          <div className="qpb-control-actions">
            <Button variant="primary" onClick={runExtraction} disabled={loading || !qvfFile}>
              Extraire
            </Button>
            <Button onClick={clearResult} disabled={loading || !result}>
              Réinitialiser
            </Button>
          </div>
        </div>

        {error && <div className="loading-banner error-banner">{error}</div>}
        {loading && <div className="loading-banner">Extraction Qlik Engine en cours...</div>}
      </Card>

      <div className="qpb-kpi-grid">
        {METRICS.map((metric) => (
          <div className="qpb-kpi-card" key={metric.key}>
            <span className={`qpb-kpi-icon qpb-kpi-${metric.tone}`}>{metric.icon}</span>
            <div>
              <span>{metric.label}</span>
              <strong>{metricValue(summary[metric.key])}</strong>
            </div>
          </div>
        ))}
      </div>

      <Card className="qpb-visuals-card" title="Visualisations par sheet">
        {displaySheets.length ? (
          <div className="qpb-table-wrap">
            <table className="qpb-visual-table">
              <thead>
                <tr>
                  <th>Visual</th>
                  <th>Type</th>
                  <th>Dimensions</th>
                  <th>Mesures</th>
                  <th>Filtres</th>
                </tr>
              </thead>
              <tbody>
                {displaySheets.map((sheet, sheetIndex) => (
                  <React.Fragment key={sheet.id || sheetIndex}>
                    <tr className="qpb-sheet-row">
                      <td colSpan="5">
                        <span>⌄</span>
                        <strong>{displayValue(sheet.title, `Sheet ${sheetIndex + 1}`)}</strong>
                      </td>
                    </tr>
                    {toArray(sheet.visuals).map((visual, visualIndex) => (
                      <tr key={visual.id || visualIndex}>
                        <td className="qpb-visual-name">{displayValue(visual.title, `Visual ${visualIndex + 1}`)}</td>
                        <td>{visualTypeLabel(visual)}</td>
                        <td>{fieldNames(visual.dimensions)}</td>
                        <td>{fieldNames(visual.measures)}</td>
                        <td>{fieldNames(visual.filters, "field")}</td>
                      </tr>
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="qpb-empty qpb-empty-table">
            <strong>Aucune métadonnée extraite</strong>
            <span>Lancez l'extraction pour afficher les sheets, visualisations, dimensions, mesures et filtres.</span>
          </div>
        )}
      </Card>

      {/* Debug / fallback JSON view to aid inspection when visuals are missing or incomplete */}
      {result && (!sheets.length && toArray(result?.visual_metadata).length > 0) && (
        <Card title="Aperçu JSON des visualisations brutes" collapsible>
          <pre style={{ maxHeight: 300, overflow: "auto" }}>{JSON.stringify(result?.visual_metadata || result?.result?.visual_metadata || model?.report?.visuals || [], null, 2)}</pre>
        </Card>
      )}

      <Card title="Tables" collapsible>
        {toArray(result?.tables_and_fields).map((table) => (
          <div key={tableName(table)} className="qpb-item">
            <h4>{tableName(table)}</h4>
            <p>{fieldNames(table.fields)}</p>
          </div>
        ))}
      </Card>

      <div className="qpb-bottom-grid">
        <Card className="qpb-data-model-card" title="Modèle de données">
          {tables.length ? (
            <>
              <StarSchemaDiagram
                tables={result?.tables_and_fields || tables}
                relationships={relationships}
                expandedTables={expandedTables}
                toggleTable={toggleTable}
              />
              <div className="qpb-relationship-list">
                {relationships.length ? (
                  relationships.slice(0, 8).map((relationship, index) => (
                    <span key={relationship.id || index}>
                      {relationship.from_table}.{relationship.from_column} -&gt; {relationship.to_table}.{relationship.to_column}
                      {" · "}
                      {relationshipCardinality(relationship)}
                    </span>
                  ))
                ) : (
                  <span>Aucune relation explicite extraite depuis les fichiers QVD.</span>
                )}
              </div>
            </>
          ) : (
            <div className="qpb-empty compact">
              <strong>Modèle en attente</strong>
              <span>Les tables, champs et relations détectées apparaîtront ici.</span>
            </div>
          )}
        </Card>

        <Card className="qpb-connection-card" title="Connexion à la source de données">
            <div className="qpb-connection-form">
              <label>
                Server Name
                <input
                  value={connectionDraft.serverName}
                  onChange={(event) => setConnectionDraft((current) => ({ ...current, serverName: event.target.value }))}
                  placeholder="Server name"
                />
              </label>
              <label>
                Authentication
                <input
                  value={connectionDraft.authentication}
                  onChange={(event) => setConnectionDraft((current) => ({ ...current, authentication: event.target.value }))}
                  placeholder="Authentication"
                />
              </label>
              <label>
                User Name
                <input
                  value={connectionDraft.userName}
                  onChange={(event) => setConnectionDraft((current) => ({ ...current, userName: event.target.value }))}
                  placeholder="User name"
                />
              </label>
              <label>
                Password
                <input
                  type="password"
                  value={connectionDraft.password}
                  onChange={(event) => setConnectionDraft((current) => ({ ...current, password: event.target.value }))}
                  placeholder="Password"
                />
              </label>
              <label>
                Database Name
                <input
                  value={connectionDraft.databaseName}
                  onChange={(event) => setConnectionDraft((current) => ({ ...current, databaseName: event.target.value }))}
                  placeholder="Database name"
                />
              </label>
            </div>
        </Card>
      </div>

      {artifacts.length > 0 && (
        <div className="qpb-output-strip">
          {artifacts.map(([key, artifact]) => (
            <a key={key} href={artifact.download_url}>
              {artifactLabel(key)}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
