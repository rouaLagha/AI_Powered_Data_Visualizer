import React, { useRef, useState } from "react";
import { readFileAsBase64, runQlikConversionJob, runQlikMetadataJob } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";

const ARTIFACT_LABELS = {
  qlik_metadata: "Qlik metadata JSON",
  qvf_path: "Uploaded QVF",
  job: "Job report",
  intermediate_model: "Intermediate model",
  tableau_mapping: "Tableau mapping",
  twb: "Generated TWB",
  twb_draft_xml: "LLM TWB draft XML",
  twb_validated_xml: "Validated TWB XML",
  twb_validation_report: "TWB validation report",
  pipeline_trace: "Pipeline trace",
  visual_metadata_path: "Visual metadata JSON",
  connection_metadata_path: "Connection metadata JSON",
  dataprep_cache_metadata_path: "DataPrep QVD cache JSON",
};

const ARTIFACT_ORDER = [
  "qlik_metadata",
  "visual_metadata_path",
  "connection_metadata_path",
  "dataprep_cache_metadata_path",
  "twb",
  "twb_draft_xml",
  "twb_validated_xml",
  "twb_validation_report",
  "tableau_mapping",
  "intermediate_model",
  "pipeline_trace",
  "job",
  "qvf_path",
];

function artifactRows(artifacts) {
  return Object.entries(artifacts || {})
    .filter(([, artifact]) => artifact?.exists)
    .sort(([leftKey], [rightKey]) => {
      const leftIndex = ARTIFACT_ORDER.indexOf(leftKey);
      const rightIndex = ARTIFACT_ORDER.indexOf(rightKey);
      return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
    });
}

function artifactLabel(key) {
  return ARTIFACT_LABELS[key] || key.replaceAll("_", " ");
}

function formatSize(size) {
  if (!size) return "";
  return `${(size / 1024).toFixed(1)} KB`;
}

function statusTone(result) {
  const status = String(result?.status || "").toLowerCase();
  if (status === "failed") return "red";
  if (status === "completed") return "green";
  return result?.ok ? "indigo" : "gray";
}

function displayValue(value, fallback = "-") {
  const text = String(value || "").trim();
  return text || fallback;
}

function sheetLabel(visual, index) {
  // Prefer an explicit sheet name when available, else construct a friendly label
  if (visual?.sheet_name) return String(visual.sheet_name).trim();
  if (visual?.sheet_title) return String(visual.sheet_title).trim();
  if (visual?.sheet_id) return `Sheet ${visual.sheet_id}`;
  // fallback to using visual title or a generic label
  return visual?.title ? `${visual.title}` : `Unassigned`;
}

function firstArray(...values) {
  for (const value of values) {
    if (Array.isArray(value)) return value;
  }
  return [];
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function fieldList(items, primaryKey, fallbackKey = "label") {
  const names = toArray(items)
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      return item[primaryKey] || item[fallbackKey] || "";
    })
    .filter(Boolean);
  return names.length ? names.slice(0, 4).join(", ") : "No fields detected";
}

function connectionSubtitle(connection) {
  const parts = [
    connection?.type,
    connection?.provider,
    connection?.source === "load_script_reference" ? "load script" : connection?.source === "qix_get_connections" ? "QIX" : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" / ") : "Connection metadata";
}

function formatNumber(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return "0";
  return numericValue.toLocaleString();
}

function isInternalQlikName(value) {
  const name = String(value || "").trim().toLowerCase();
  return !name || name.startsWith("__") || name === "dataprepappcache" || name.includes("dataprepappcache");
}

function isInternalConnection(connection) {
  return Boolean(connection?.internal) || isInternalQlikName(connection?.name || connection?.id);
}

function uniqueQvdTables(...groups) {
  const seen = new Set();
  const tables = [];
  groups.flatMap(toArray).forEach((table) => {
    if (!table || typeof table !== "object") return;
    const key = table.path || table.file_name || table.table_name || JSON.stringify(table);
    if (seen.has(key)) return;
    seen.add(key);
    tables.push(table);
  });
  return tables;
}

function connectionKey(connection, index) {
  return String(connection?.id || connection?.name || `connection-${index}`);
}

function connectionStringPreview(connectionString) {
  const parts = String(connectionString || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .filter((part) => {
      const key = part.split("=", 1)[0].trim().toLowerCase();
      return !["password", "pwd", "pass"].includes(key) && !key.endsWith("password");
    });
  return parts.join(";");
}

export default function QlikConversionPage() {
  const inputRef = useRef(null);
  const [qvfFile, setQvfFile] = useState(null);
  const [jobsRoot, setJobsRoot] = useState("output/qlik_jobs");
  const [jobId, setJobId] = useState("");
  const [qlikEndpoint, setQlikEndpoint] = useState("ws://localhost:4848/app");
  const [qlikAppsDir, setQlikAppsDir] = useState("");
  const [dataprepCacheDir, setDataprepCacheDir] = useState("");
  const [configPath, setConfigPath] = useState("backend/config/llm_config.json");
  const [metadataValidation, setMetadataValidation] = useState(null);
  const [connectionUsers, setConnectionUsers] = useState({});
  const [connectionPasswords, setConnectionPasswords] = useState({});
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function runMetadataJob() {
    if (!qvfFile) {
      setError("Upload a QVF file before starting the metadata job.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const contentBase64 = await readFileAsBase64(qvfFile);
      const response = await runQlikMetadataJob({
        fileName: qvfFile.name,
        contentBase64,
        jobsRoot,
        jobId,
        qlikEndpoint,
        qlikAppsDir,
        dataprepCacheDir,
      });
      setResult(response);
      setMetadataValidation(null);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  const artifacts = artifactRows(result?.artifacts);
  const summary = result?.summary || {};
  const failed = result?.status === "failed";
  const visualMetadata = firstArray(result?.visual_metadata, result?.result?.visual_metadata);
  const rawConnectionMetadata = firstArray(result?.connection_metadata, result?.result?.connection_metadata);
  const connectionMetadata = rawConnectionMetadata.filter((connection) => !isInternalConnection(connection));
  const connectionWarnings = firstArray(result?.connection_warnings, result?.result?.connection_warnings);
  const dataprepCacheMetadata = result?.dataprep_cache_metadata || result?.result?.dataprep_cache_metadata || {};
  const qvdTables = uniqueQvdTables(dataprepCacheMetadata?.qvd_tables, dataprepCacheMetadata?.internal_qvd_tables);
  const matchedVisualCount = visualMetadata.filter((visual) => visual?.best_data_cache_match?.table_name).length;
  const selectedFileLabel = qvfFile ? `${qvfFile.name}${qvfFile.size ? ` (${formatSize(qvfFile.size)})` : ""}` : "No QVF selected";
  const runStatus = failed ? "Failed" : result?.ok ? "Completed" : "QVF upload";
  const detailRows = [
    ["App ID", result?.app_id || result?.result?.app_id],
    ["QIX client", result?.client || result?.result?.client],
    ["Extraction mode", result?.extraction_mode || result?.result?.extraction_mode],
    ["Job folder", result?.job_dir],
    ["Output folder", result?.output_dir || result?.result?.output_dir],
  ];
  const metadataValidated = metadataValidation?.status === "validated";

  function validateMetadataJob() {
    const issues = [];
    const warnings = [];
    if (!result || failed) {
      issues.push(result?.error || "Run metadata extraction successfully before validation.");
    }
    if (!visualMetadata.length) {
      issues.push("No visual metadata was extracted.");
    }
    if (!summary.sheet_count && !visualMetadata.some((visual) => visual?.sheet_id)) {
      warnings.push("No sheet metadata was extracted; visuals may be grouped as unassigned.");
    }
    if (!qvdTables.length) {
      warnings.push("No DataPrep QVD table was found. The report can still be generated from QIX metadata.");
    }
    if (!connectionMetadata.length) {
      warnings.push("No external connection metadata is displayed. Fill connection credentials later if needed.");
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

  async function generateTableauReport() {
    if (!qvfFile) {
      setError("Upload a QVF file before generating the Tableau report.");
      return;
    }
    if (!metadataValidated && !validateMetadataJob()) {
      return;
    }
    setGenerating(true);
    setLoading(false);
    setError("");
    try {
      const contentBase64 = await readFileAsBase64(qvfFile);
      const response = await runQlikConversionJob({
        fileName: qvfFile.name,
        contentBase64,
        jobsRoot,
        jobId: result?.job_id || result?.result?.job_id || jobId,
        configPath,
        qlikEndpoint,
        qlikAppsDir,
        dataprepCacheDir,
      });
      setResult(response);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="page-stack conversion-page qlik-conversion-page">
      <div className="qlik-board">
        <div className="qlik-left-column">
          <Card
            className="qlik-source-card"
            title="Qlik metadata extraction"
            eyebrow="Real QIX pipeline"
            actions={<Badge tone={statusTone(result)}>{runStatus}</Badge>}
          >
            <div className="qlik-upload-panel">
              <div>
                <span>Source QVF</span>
                <strong>{selectedFileLabel}</strong>
              </div>
              <input ref={inputRef} type="file" accept=".qvf" hidden onChange={(event) => setQvfFile(event.target.files?.[0] || null)} />
              <Button onClick={() => inputRef.current?.click()}>Browse QVF</Button>
            </div>

            <div className="qlik-settings-grid">
              <label>
                QIX endpoint
                <input
                  value={qlikEndpoint}
                  onChange={(event) => setQlikEndpoint(event.target.value)}
                  placeholder="ws://localhost:4848/app"
                />
              </label>
              <label>
                Jobs folder
                <input value={jobsRoot} onChange={(event) => setJobsRoot(event.target.value)} placeholder="output/qlik_jobs" />
              </label>
              <label>
                Job ID
                <input value={jobId} onChange={(event) => setJobId(event.target.value)} placeholder="Generated automatically" />
              </label>
              <label>
                Qlik Apps folder
                <input
                  value={qlikAppsDir}
                  onChange={(event) => setQlikAppsDir(event.target.value)}
                  placeholder="C:\\Users\\Roua\\Documents\\Qlik\\Sense\\Apps"
                />
              </label>
              <label>
                DataPrep cache folder
                <input
                  value={dataprepCacheDir}
                  onChange={(event) => setDataprepCacheDir(event.target.value)}
                  placeholder="C:\\Users\\Roua\\Documents\\Qlik\\Sense\\Apps\\DataPrepAppCache"
                />
              </label>
              <label>
                LLM config path
                <input
                  value={configPath}
                  onChange={(event) => setConfigPath(event.target.value)}
                  placeholder="backend/config/llm_config.json"
                />
              </label>
            </div>

            {error && <div className="loading-banner error-banner">{error}</div>}
            {loading && <div className="loading-banner">Qlik metadata job in progress...</div>}
            {generating && <div className="loading-banner">Generating Tableau report from validated metadata...</div>}
            {metadataValidation && (
              <div className={`loading-banner ${metadataValidation.status === "failed" ? "error-banner" : ""}`}>
                Metadata validation: {metadataValidation.status}
                {metadataValidation.warnings?.length ? ` (${metadataValidation.warnings.length} warning(s))` : ""}
              </div>
            )}

            <div className="button-row qlik-actions">
              <Button variant="primary" onClick={runMetadataJob} disabled={loading || generating || !qvfFile}>
                Extract metadata
              </Button>
              <Button onClick={validateMetadataJob} disabled={loading || generating || !result || failed}>
                Validate metadata
              </Button>
              <Button variant="primary" onClick={generateTableauReport} disabled={loading || generating || !qvfFile || !metadataValidated}>
                Generate Tableau report
              </Button>
              <Button
                onClick={() => {
                  setResult(null);
                  setMetadataValidation(null);
                  setConnectionUsers({});
                  setConnectionPasswords({});
                }}
                disabled={loading || generating || !result}
              >
                Clear result
              </Button>
            </div>
          </Card>
        </div>

        <div className="qlik-metadata-column">
          {result ? (
            <>
              <Card className="qlik-visual-card" title="Visual metadata" eyebrow={`${visualMetadata.length} objects`}>
                {result.error && <div className="loading-banner error-banner">{result.error}</div>}

                <div className="metric-grid qlik-metric-grid">
                  <div className="metric-card">
                    <strong>{metadataValidated ? "Ready" : "Pending"}</strong>
                    <span>Validation</span>
                  </div>
                  <div className="metric-card">
                    <strong>{summary.sheet_count || 0}</strong>
                    <span>Sheets</span>
                  </div>
                  <div className="metric-card">
                    <strong>{visualMetadata.length || summary.visual_count || 0}</strong>
                    <span>Visuals</span>
                  </div>
                  <div className="metric-card">
                    <strong>{qvdTables.length}</strong>
                    <span>QVD tables</span>
                  </div>
                  <div className="metric-card">
                    <strong>{matchedVisualCount}</strong>
                    <span>Linked visuals</span>
                  </div>
                </div>

                {visualMetadata.length ? (
                  <div className="qlik-metadata-list">
                    {visualMetadata.map((visual, index) => (
                      <div className="qlik-metadata-row" key={visual.id || `${visual.title}-${index}`}>
                        <div className="qlik-metadata-main">
                          <strong>{displayValue(visual.title || visual.id, `Visual ${index + 1}`)}</strong>
                          <span className="sheet-label">{sheetLabel(visual, index)}</span>
                        </div>
                        <div className="qlik-count-pills">
                          <Badge tone="indigo">{toArray(visual.dimensions).length} dims</Badge>
                          <Badge tone="green">{toArray(visual.measures).length} measures</Badge>
                        </div>
                        <p>Dimensions: {fieldList(visual.dimensions, "field")}</p>
                        <p>Measures: {fieldList(visual.measures, "expression")}</p>
                        {visual.best_data_cache_match?.table_name && (
                          <p>
                            QVD match: {visual.best_data_cache_match.table_name} ({visual.best_data_cache_match.matched_fields?.join(", ")})
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="qlik-empty-state compact">
                    <strong>No visual metadata</strong>
                    <span>The QIX extraction did not return sheet visual objects.</span>
                  </div>
                )}
              </Card>

              {(connectionMetadata.length > 0 || connectionWarnings.length > 0) && (
                <Card className="qlik-connection-card" title="Connection metadata" eyebrow={`${connectionMetadata.length} external`}>
                  {connectionWarnings.length > 0 && (
                    <div className="warning-list qlik-warning-list">
                      {connectionWarnings.map((warning, index) => (
                        <div className="warning-card warning-warning" key={`${warning}-${index}`}>
                          <strong>Connection warning</strong>
                          <span>{warning}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {connectionMetadata.length > 0 && (
                    <div className="qlik-metadata-list">
                      {connectionMetadata.map((connection, index) => {
                        const key = connectionKey(connection, index);
                        const connectionPreview = connectionStringPreview(connection.connection_string);
                        return (
                          <div className="qlik-metadata-row qlik-connection-row" key={key}>
                            <div className="qlik-metadata-main">
                              <strong>{displayValue(connection.name || connection.id, `Connection ${index + 1}`)}</strong>
                              <span>{connectionSubtitle(connection)}</span>
                            </div>
                            <div className="qlik-connection-fields">
                              <div>
                                <span>Server</span>
                                <strong>{displayValue(connection.server)}</strong>
                              </div>
                              <div>
                                <span>Database</span>
                                <strong>{displayValue(connection.database)}</strong>
                              </div>
                              <div className="qlik-secret-field">
                                <span>User</span>
                                <input
                                  value={connectionUsers[key] ?? connection.username ?? ""}
                                  onChange={(event) =>
                                    setConnectionUsers((current) => ({
                                      ...current,
                                      [key]: event.target.value,
                                    }))
                                  }
                                  autoComplete="username"
                                />
                              </div>
                              <div>
                                <span>Auth</span>
                                <strong>{displayValue(connection.authentication)}</strong>
                              </div>
                              <div className="qlik-secret-field">
                                <span>Pwd</span>
                                <input
                                  type="password"
                                  value={connectionPasswords[key] || ""}
                                  onChange={(event) =>
                                    setConnectionPasswords((current) => ({
                                      ...current,
                                      [key]: event.target.value,
                                    }))
                                  }
                                  autoComplete="new-password"
                                />
                              </div>
                            </div>
                            {connectionPreview && <code title={connectionPreview}>{connectionPreview}</code>}
                            {connection.reference_count ? <p>{connection.reference_count} load script reference(s)</p> : null}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Card>
              )}

              <Card className="qlik-dataprep-card" title="DataPrep tables" eyebrow={`${qvdTables.length} QVD tables`}>
                {qvdTables.length ? (
                  <div className="qlik-metadata-list">
                    {qvdTables.map((table, index) => (
                      <div className="qlik-metadata-row qlik-connection-row" key={table.path || table.file_name || index}>
                        <div className="qlik-metadata-main">
                          <strong>{displayValue(table.table_name, `QVD table ${index + 1}`)}</strong>
                          <span>{table.file_name}</span>
                        </div>
                        <div className="qlik-connection-fields">
                          <div>
                            <span>Rows</span>
                            <strong>{formatNumber(table.record_count)}</strong>
                          </div>
                          <div>
                            <span>Fields</span>
                            <strong>{formatNumber(table.field_count)}</strong>
                          </div>
                          <div>
                            <span>Visuals</span>
                            <strong>{formatNumber(toArray(table.used_by_visuals).length)}</strong>
                          </div>
                        </div>
                        <p>Fields: {toArray(table.field_names).slice(0, 8).join(", ") || "No fields detected"}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="qlik-empty-state compact">
                    <strong>No QVD cache table</strong>
                    <span>DataPrepAppCache was not found for this app or Qlik has not generated prepared QVDs yet.</span>
                  </div>
                )}
              </Card>
            </>
          ) : (
            <Card className="qlik-empty-card" title="Metadata output" eyebrow="Waiting">
              <div className="qlik-empty-state">
                <strong>No metadata job yet</strong>
                <span>Visual metadata and connection metadata will appear here as two separate sections.</span>
              </div>
            </Card>
          )}
        </div>

        <div className="qlik-pipeline-column">
          {result ? (
            <Card className="qlik-pipeline-card" title="Complete pipeline" eyebrow={`${result.trace_steps?.length || 0} steps`}>
              <div className="qlik-detail-grid">
                {detailRows.map(([label, value]) => (
                  <div className="qlik-detail-row" key={label}>
                    <span>{label}</span>
                    <strong>{displayValue(value)}</strong>
                  </div>
                ))}
              </div>

              <div className="trace-list qlik-pipeline-list">
                {(result.trace_steps || []).map((step, index) => (
                  <div key={`${step}-${index}`} className="trace-row">
                    <span>{index + 1}</span>
                    <p>{step}</p>
                  </div>
                ))}
              </div>

              <div className="artifact-list qlik-pipeline-artifacts">
                {artifacts.map(([key, artifact]) => (
                  <div className="artifact-row" key={key}>
                    <div>
                      <strong>{artifactLabel(key)}</strong>
                      <span>{artifact.name}</span>
                    </div>
                    <a className="btn btn-secondary btn-sm" href={artifact.download_url}>
                      Download
                    </a>
                  </div>
                ))}
              </div>
            </Card>
          ) : (
            <Card className="qlik-empty-card" title="Complete pipeline" eyebrow="Waiting">
              <div className="qlik-empty-state">
                <strong>Pipeline not started</strong>
                <span>The full Qlik extraction sequence will be shown here after the run.</span>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
