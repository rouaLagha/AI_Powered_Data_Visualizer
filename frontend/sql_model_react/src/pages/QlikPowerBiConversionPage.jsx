import React, { useEffect, useRef, useState } from "react";
import { generateQlikPowerBiReport, readFileAsBase64, runQlikPowerBiMetadataJob } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";
import { MdViewDay, MdInsertChart, MdTableChart, MdFilterList } from "react-icons/md";

const ARTIFACT_LABELS = {
  powerbi_intermediate_model: "Data model JSON",
  powerbi_pbip_archive: "Power BI PBIP project",
  powerbi_pbip_manifest: "PBIP generation manifest",
  powerbi_semantic_model: "Power BI semantic model",
  powerbi_report_model: "Visual elements JSON",
  powerbi_semantic_definition: "Semantic model definition",
  powerbi_report_definition: "Report definition",
  powerbi_llm_mapping: "Mapping JSON",
  qlik_metadata: "Raw Qlik metadata",
  visual_metadata_path: "Qlik visuals",
  connection_metadata_path: "Qlik connections",
  dataprep_cache_metadata_path: "DataPrep QVD cache",
  job: "Extraction report",
  qvf_path: "Stored QVF",
};

const ARTIFACT_ORDER = [
  "powerbi_intermediate_model",
  "powerbi_pbip_archive",
  "powerbi_pbip_manifest",
  "powerbi_semantic_model",
  "powerbi_report_model",
  "powerbi_semantic_definition",
  "powerbi_report_definition",
  "powerbi_llm_mapping",
  "qlik_metadata",
  "visual_metadata_path",
  "connection_metadata_path",
  "dataprep_cache_metadata_path",
  "job",
  "qvf_path",
];

const VISUAL_TYPE_LABELS = {
  barchart: "Bar chart",
  linechart: "Line chart",
  combochart: "Combo chart",
  piechart: "Donut chart",
  kpi: "KPI",
  gauge: "Gauge",
  table: "Table",
  straighttable: "Table",
  "sn-table": "Table",
  "pivot-table": "Pivot table",
  filterpane: "Filter pane",
  scatterplot: "Scatter plot",
  treemap: "Treemap",
  map: "Map",
};

const METRICS = [
  { key: "sheet_count", label: "Sheets", iconComponent: MdViewDay, tone: "green" },
  { key: "visual_count", label: "Visuals", iconComponent: MdInsertChart, tone: "blue" },
  { key: "table_count", label: "Tables", iconComponent: MdTableChart, tone: "red" },
  { key: "filter_count", label: "Filters", iconComponent: MdFilterList, tone: "cyan" },
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

function visualTypeTone(visual) {
  const qlikType = String(visual?.qlik_type || visual?.type || "").trim().toLowerCase();
  if (qlikType.includes("kpi") || qlikType.includes("gauge")) return "metric";
  if (qlikType.includes("bar") || qlikType.includes("histogram")) return "bar";
  if (qlikType.includes("line") || qlikType.includes("combo")) return "line";
  if (qlikType.includes("table") || qlikType.includes("pivot")) return "table";
  if (qlikType.includes("filter") || qlikType.includes("list")) return "filter";
  return "default";
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

function isBusinessTable(table) {
  const name = tableName(table).toLowerCase();
  if (!name) return false;
  return (
    !name.startsWith("__") &&
    !name.startsWith("$") &&
    !name.includes("dataprepappcache") &&
    name !== "autocalendar" &&
    name !== "auto calendar"
  );
}

function uniqueBusinessTables(...groups) {
  const seen = new Set();
  const tables = [];
  groups.flatMap(toArray).forEach((table) => {
    if (!table || typeof table !== "object" || !isBusinessTable(table)) return;
    const key = tableName(table).toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    tables.push(table);
  });
  return tables;
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
          Tables extracted from QVD files only. No explicit relationship was extracted, so no cardinality is invented.
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
  const [generating, setGenerating] = useState(false);
  const [generateRequested, setGenerateRequested] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [metadataValidation, setMetadataValidation] = useState(null);
  const [expandedTables, setExpandedTables] = useState({});
  const [connectionDraft, setConnectionDraft] = useState({
    serverName: "",
    authentication: "",
    userName: "",
    password: "",
    databaseName: "",
  });

  const selectedFileLabel = qvfFile ? `${qvfFile.name}${qvfFile.size ? ` (${formatSize(qvfFile.size)})` : ""}` : "No QVF selected";
  const failed = result?.status === "failed";
  const runStatus = failed ? "Failed" : result?.status === "completed" ? "Metadata ready" : "Waiting";
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
  const businessTables = uniqueBusinessTables(
    result?.tables_and_fields,
    result?.result?.tables_and_fields,
    tables
  );
  const relationships = toArray(model?.semantic_model?.relationships);
  const connections = toArray(model?.semantic_model?.connections).length
    ? toArray(model?.semantic_model?.connections)
    : toArray(result?.connection_metadata);
  const connection = firstExternalConnection(connections);
  const artifacts = artifactRows(result?.artifacts);
  const appTitle = model?.source?.app_title || result?.app_id || "Application Qlik";
  const metadataValidated = metadataValidation?.status === "validated";
  const kpiValues = {
    ...summary,
    table_count: businessTables.length || summary.table_count || 0,
  };

  const extractionDone = Boolean(result && !failed);
  const generationArtifactKeys = new Set([
    "powerbi_pbip_project",
    "powerbi_pbip_file",
    "powerbi_pbip_archive",
    "powerbi_pbip_manifest",
    "powerbi_semantic_model",
    "powerbi_report_model",
    "powerbi_semantic_definition",
    "powerbi_report_definition",
    "powerbi_llm_mapping",
  ]);
  const hasGenerationArtifacts = artifacts.some(([key]) => generationArtifactKeys.has(key));
  const hasPowerBiGenerationResult = Boolean(
    result?.powerbi_pbip_generation ||
    result?.powerbi_pbip_project ||
    result?.powerbi_pbip_file ||
    result?.powerbi_pbip_archive ||
    result?.powerbi_report_model ||
    result?.powerbi_report_definition ||
    result?.powerbi_llm_mapping ||
    result?.result?.powerbi_pbip_generation ||
    result?.result?.powerbi_pbip_project ||
    result?.result?.powerbi_pbip_file ||
    result?.result?.powerbi_pbip_archive ||
    result?.result?.powerbi_report_model ||
    result?.result?.powerbi_report_definition ||
    result?.result?.powerbi_llm_mapping
  );
  const generationDone = generateRequested && (hasPowerBiGenerationResult || hasGenerationArtifacts);

  const pipelineStatusLabel = failed
    ? "Failed"
    : generating
      ? "Generating"
      : loading
        ? "Extracting"
        : generationDone
          ? "Completed"
          : metadataValidated
            ? "Validated"
            : extractionDone
              ? "Ready for validation"
              : qvfFile
                ? "Ready"
                : "Waiting";

  const pipelineSteps = [
    {
      label: "QVF file",
      description: qvfFile ? qvfFile.name : "No file selected",
      status: qvfFile ? "done" : "waiting",
    },
    {
      label: "Metadata extraction",
      description: loading
        ? "Extraction in progress..."
        : failed
          ? "Extraction failed"
          : extractionDone
            ? "Metadata extracted"
            : "Waiting",
      status: loading ? "running" : failed ? "error" : extractionDone ? "done" : "waiting",
    },
    {
      label: "Metadata validation",
      description: metadataValidated
        ? "Metadata validated successfully"
        : metadataValidation?.status === "failed"
          ? "Validation failed"
          : "Waiting for validation",
      status: metadataValidated ? "done" : metadataValidation?.status === "failed" ? "error" : "waiting",
    },
    {
      label: "Power BI generation",
      description: generating
        ? "Power BI report generation in progress..."
        : generationDone
          ? "Report generated"
          : "Waiting",
      status: generating ? "running" : generationDone ? "done" : "waiting",
    },
  ];

  useEffect(() => {
    setConnectionDraft(connectionDraftFromConnection(connection));
  }, [connection]);

  async function runExtraction() {
    if (!qvfFile) {
      setError("Add a QVF file before starting extraction.");
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
      setGenerateRequested(false);
      setMetadataValidation(null);
      if (response?.status === "failed") {
        setError(response.error || response.result?.error || "Qlik extraction failed.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function clearResult() {
    setResult(null);
    setGenerateRequested(false);
    setError("");
    setExpandedTables({});
    setMetadataValidation(null);
  }

  function validateMetadataJob() {
    const issues = [];
    const warnings = [];
    const visualMetadata = toArray(result?.visual_metadata || result?.result?.visual_metadata || model?.report?.visuals);
    const tableMetadata = toArray(result?.tables_and_fields || model?.semantic_model?.tables);

    if (!result || failed) {
      issues.push(result?.error || "Run a successful Qlik extraction before validation.");
    }
    if (!visualMetadata.length) {
      issues.push("No Qlik visuals were extracted.");
    }
    if (!tableMetadata.length) {
      warnings.push("No tables were detected; the Power BI report may be incomplete.");
    }
    if (!connections.length) {
      warnings.push("No external connections are shown. Credentials can be completed later.");
    }

    const nextValidation = {
      status: issues.length ? "failed" : "validated",
      issues,
      warnings,
      checkedAt: new Date().toLocaleString(),
    };
    setMetadataValidation(nextValidation);
    if (issues.length) {
      setError(issues[0]);
    } else {
      setError("");
    }
    return !issues.length;
  }

  async function generatePowerBiReport() {
    if (!result) {
      setError("Run metadata extraction before generating the Power BI report.");
      return;
    }
    if (!metadataValidated && !validateMetadataJob()) {
      return;
    }
    setGenerateRequested(true);
    setGenerating(true);
    setError("");
    try {
      const response = await generateQlikPowerBiReport({
        jobId: result?.job_id || result?.result?.job_id || "",
        jobDir: result?.job_dir || "",
      });
      setResult(response);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGenerating(false);
    }
  }

  function toggleTable(table) {
    setExpandedTables((current) => ({
      ...current,
      [table]: !current[table],
    }));
  }

  return (
    <div className="page-stack qlik-powerbi-page qpb-dashboard qpb-layout-with-sidebar">
      <aside className="qpb-pipeline-sidebar">
        <div className="qpb-pipeline-header">
          <span>Pipeline</span>
          <strong>Qlik to Power BI</strong>

          <Badge tone={failed ? "red" : generationDone ? "green" : "gray"}>
            {pipelineStatusLabel}
          </Badge>
        </div>

        <div className="qpb-pipeline-steps">
          {pipelineSteps.map((step, index) => (
            <div key={step.label} className={`qpb-pipeline-step ${step.status}`}>
              <div className="qpb-pipeline-marker">
                {step.status === "done" ? "✓" : step.status === "running" ? "…" : index + 1}
              </div>

              <div className="qpb-pipeline-text">
                <strong>{step.label}</strong>
                <span>{step.description}</span>
              </div>
            </div>
          ))}
        </div>
      </aside>

      <main className="qpb-main-content">
        <Card
          className="qpb-control-card"
          title="Qlik source"
          eyebrow={displayValue(appTitle)}
          actions={<Badge tone={statusTone(result)}>{runStatus}</Badge>}
        >
          <div className="qpb-control-grid">
            <div className="qpb-file-picker">
              <div>
                <span>Source application</span>
                <strong>{selectedFileLabel}</strong>
              </div>

              <input
                ref={inputRef}
                type="file"
                accept=".qvf"
                hidden
                onChange={(event) => setQvfFile(event.target.files?.[0] || null)}
              />

              <Button onClick={() => inputRef.current?.click()}>
                Choose QVF
              </Button>
            </div>

            <div className="qpb-control-actions">
              <Button variant={extractionDone ? "secondary" : "primary"} onClick={runExtraction} disabled={loading || generating || !qvfFile}>
                Extract
              </Button>

              <Button
                variant={extractionDone && !metadataValidated ? "primary" : "secondary"}
                onClick={validateMetadataJob}
                disabled={loading || generating || !result || failed}
              >
                Validate
              </Button>

              <Button
                variant={metadataValidated ? "primary" : "secondary"}
                onClick={generatePowerBiReport}
                disabled={loading || generating || !result || !metadataValidated}
              >
                Generate
              </Button>

              <Button onClick={clearResult} disabled={loading || generating || !result}>
                Reset
              </Button>
            </div>
          </div>

          {error && <div className="loading-banner error-banner">{error}</div>}

          {loading && (
            <div className="loading-banner">
              Extraction in progress...
            </div>
          )}

          {generating && (
            <div className="loading-banner">
              Power BI report generation in progress...
            </div>
          )}

          {metadataValidation && (
            <div className={`loading-banner ${metadataValidation.status === "failed" ? "error-banner" : ""}`}>
              {metadataValidation.status === "validated" ? "Metadata validated successfully" : "Metadata validation failed"}
              {metadataValidation.warnings?.length ? ` (${metadataValidation.warnings.length} warning(s))` : ""}
            </div>
          )}
        </Card>

        <div className="qpb-kpi-grid">
          {METRICS.map((metric) => {
            const IconComponent = metric.iconComponent;
            return (
              <div className="qpb-kpi-card" key={metric.key}>
                <span className={`qpb-kpi-icon qpb-kpi-${metric.tone}`}>
                  <IconComponent size={32} />
                </span>

                <div>
                  <span>{metric.label}</span>
                  <strong>{metricValue(kpiValues[metric.key])}</strong>
                </div>
              </div>
            );
          })}
        </div>

        <Card className="qpb-visuals-card" title="Visuals by sheet">
          {displaySheets.length ? (
            <div className="qpb-table-wrap">
              <table className="qpb-visual-table">
                <thead>
                  <tr>
                    <th>Visual</th>
                    <th>Type</th>
                    <th>Dimensions</th>
                    <th>Measures</th>
                    <th>Filters</th>
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

                      {toArray(sheet.visuals).map((visual, visualIndex) => {
                        const isKpi = visual?.visual_layout_type === "kpi_card";
                        return (
                          <tr key={visual.id || visualIndex}>
                            <td className="qpb-visual-name">
                              {isKpi ? (
                                <strong>{displayValue(visual.kpi_title || visual.title, `KPI ${visualIndex + 1}`)}</strong>
                              ) : (
                                displayValue(visual.title, `Visual ${visualIndex + 1}`)
                              )}
                            </td>
                            <td>
                              <span className={`qpb-visual-type-badge ${visualTypeTone(visual)}`}>
                                {visualTypeLabel(visual)}
                              </span>
                            </td>
                            <td>
                              {isKpi ? (
                                <span style={{ color: "#666", fontSize: "0.9em" }}>KPI</span>
                              ) : (
                                fieldNames(visual.dimensions)
                              )}
                            </td>
                            <td>{fieldNames(visual.measures)}</td>
                            <td>{isKpi ? "-" : fieldNames(visual.filters, "field")}</td>
                          </tr>
                        );
                      })}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="qpb-empty qpb-empty-table">
              <strong>No metadata extracted</strong>
              <span>
                Run extraction to display sheets, visuals, dimensions, measures, and filters.
              </span>
            </div>
          )}
        </Card>

        {result && !sheets.length && toArray(result?.visual_metadata).length > 0 && (
          <Card title="Raw visuals JSON preview" collapsible>
            <pre style={{ maxHeight: 300, overflow: "auto" }}>
              {JSON.stringify(
                result?.visual_metadata || result?.result?.visual_metadata || model?.report?.visuals || [],
                null,
                2
              )}
            </pre>
          </Card>
        )}

        <div className="qpb-bottom-grid">
          <Card className="qpb-data-model-card" title="Data model">
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
                        {relationship.from_table}.{relationship.from_column} -&gt;{" "}
                        {relationship.to_table}.{relationship.to_column}
                        {" · "}
                        {relationshipCardinality(relationship)}
                      </span>
                    ))
                  ) : (
                    <span>No explicit relationships were extracted from the QVD files.</span>
                  )}
                </div>
              </>
            ) : (
              <div className="qpb-empty compact">
                <strong>Model pending</strong>
                <span>Detected tables, fields, and relationships will appear here.</span>
              </div>
            )}
          </Card>

          <Card className="qpb-connection-card" title="Data source connection">
            <div className="qpb-connection-form">
              <label>
                Server name
                <input
                  value={connectionDraft.serverName}
                  onChange={(event) =>
                    setConnectionDraft((current) => ({ ...current, serverName: event.target.value }))
                  }
                  placeholder="Server name"
                />
              </label>

              <label>
                Authentication
                <input
                  value={connectionDraft.authentication}
                  onChange={(event) =>
                    setConnectionDraft((current) => ({ ...current, authentication: event.target.value }))
                  }
                  placeholder="Authentication"
                />
              </label>

              <label>
                User name
                <input
                  value={connectionDraft.userName}
                  onChange={(event) =>
                    setConnectionDraft((current) => ({ ...current, userName: event.target.value }))
                  }
                  placeholder="User name"
                />
              </label>

              <label>
                Password
                <input
                  type="password"
                  value={connectionDraft.password}
                  onChange={(event) =>
                    setConnectionDraft((current) => ({ ...current, password: event.target.value }))
                  }
                  placeholder="Password"
                />
              </label>

              <label>
                Database name
                <input
                  value={connectionDraft.databaseName}
                  onChange={(event) =>
                    setConnectionDraft((current) => ({ ...current, databaseName: event.target.value }))
                  }
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
      </main>
    </div>
  );
}
