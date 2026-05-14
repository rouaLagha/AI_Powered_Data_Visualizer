const API_BASE = import.meta.env.VITE_SQL_MODEL_API_BASE || "";

export const STATUS = {
  pending: "pending",
  inProgress: "in_progress",
  completed: "completed",
  needsValidation: "needs_validation",
  error: "error",
};

export const pipelineDefinitions = [
  {
    id: "rdl-upload",
    title: "RDL upload",
    description: "Upload the SSRS report definition file.",
  },
  {
    id: "rdl-parsing",
    title: "Parse RDL",
    description: "Extract datasets, data sources, parameters, and SQL.",
  },
  {
    id: "dataset-selection",
    title: "Dataset and SQL",
    description: "Select the dataset and inspect the SQL query.",
  },
  {
    id: "sql-analysis",
    title: "SQL analysis",
    description: "Detect tables, joins, measures, and reasoning hints.",
  },
  {
    id: "dimensional-model",
    title: "Model",
    description: "Review the proposed star or snowflake schema.",
  },
  {
    id: "human-validation",
    title: "Validation",
    description: "Approve model changes before generation.",
  },
  {
    id: "visual-mapping",
    title: "Visual mapping",
    description: "Map validated report visuals before final TWB generation.",
  },
  {
    id: "twb-generation",
    title: "Generate TWB",
    description: "Generate Tableau workbook XML from the model.",
  },
  {
    id: "tableau-datasource",
    title: "Datasource",
    description: "Configure the published Tableau datasource.",
  },
  {
    id: "datasource-publication",
    title: "Publish",
    description: "Publish the datasource to Tableau Cloud.",
  },
  {
    id: "consumer-workbook",
    title: "Connected workbook",
    description: "Create the downloadable workbook connected to the published datasource.",
  },
  {
    id: "quality-comparison",
    title: "Quality",
    description: "Compare the source RDL with the generated Tableau output.",
  },
];

export const emptyParsingSummary = {
  datasetsFound: 0,
  dataSourcesFound: 0,
  parametersFound: 0,
  sqlQueriesExtracted: 0,
};

export const emptyAnalysisResult = {
  timeline: [
    { label: "SQL parsed", status: STATUS.pending },
    { label: "Tables detected", status: STATUS.pending },
    { label: "Joins extracted", status: STATUS.pending },
    { label: "Measures detected", status: STATUS.pending },
    { label: "LLM reasoning completed", status: STATUS.pending },
  ],
  detectedTables: [],
  detectedJoins: [],
  detectedMeasures: [],
  confidenceScore: 0,
  warnings: [],
};

export const emptyModel = {
  schemaType: "Unknown",
  tables: [],
  relationships: [],
  measures: [],
  warnings: [],
};

export const emptyQualityComparison = {
  executed: false,
  status: "not_started",
  globalScore: 0,
  summary: "",
  metrics: [],
  details: {},
};

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const text = await response.text();
  let payload = {};
  if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new Error(`Backend returned non-JSON response from ${path}: ${text.slice(0, 200)}`);
    }
  }

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}

export function getState() {
  return requestJson("/api/state");
}

export function resetBackendState() {
  return requestJson("/api/reset", { method: "POST", body: "{}" });
}

export function parseRdl({ fileName, content }) {
  return requestJson("/api/rdl/parse", {
    method: "POST",
    body: JSON.stringify({ file_name: fileName, content }),
  });
}

export function selectDataset(datasetName) {
  return requestJson("/api/dataset/select", {
    method: "POST",
    body: JSON.stringify({ dataset_name: datasetName }),
  });
}

export function analyzeModel({ sqlQuery, followUp = "", configPath = "" }) {
  return requestJson("/api/model/analyze", {
    method: "POST",
    body: JSON.stringify({
      sql_query: sqlQuery,
      follow_up: followUp,
      config_path: configPath,
    }),
  });
}

export function applyRelationshipOperation({ model = null, operation, relationship = null, match = null, previewOnly = false }) {
  return requestJson("/api/model/relationships/apply", {
    method: "POST",
    body: JSON.stringify({
      model,
      operation,
      relationship,
      match,
      preview_only: previewOnly,
    }),
  });
}

export function validateSchema() {
  return requestJson("/api/schema/validate", { method: "POST", body: "{}" });
}

export function mapVisualContent({ configPath = "" } = {}) {
  return requestJson("/api/visual/map", {
    method: "POST",
    body: JSON.stringify({ config_path: configPath }),
  });
}

export function generateTwb({ outputName = "data_model_to_publish.twb", templatePath = "" } = {}) {
  return requestJson("/api/twb/generate", {
    method: "POST",
    body: JSON.stringify({ output_name: outputName, template_path: templatePath }),
  });
}

export function publishTableau({ configPath = "", tableau = {} } = {}) {
  return requestJson("/api/tableau/publish", {
    method: "POST",
    body: JSON.stringify({ config_path: configPath, tableau }),
  });
}

export function compareQuality() {
  return requestJson("/api/quality/compare", { method: "POST", body: "{}" });
}

export function runRdlConversion({
  fileName,
  content,
  configPath = "",
  outputDir = "",
  publishEnabled = false,
}) {
  return requestJson("/api/conversion/run", {
    method: "POST",
    body: JSON.stringify({
      file_name: fileName,
      content,
      config_path: configPath,
      output_dir: outputDir,
      publish_enabled: publishEnabled,
    }),
  });
}

export function runQlikMetadataJob({
  fileName,
  contentBase64,
  jobsRoot = "",
  qlikEndpoint = "ws://localhost:4848/app",
  qlikAppsDir = "",
  dataprepCacheDir = "",
  qlikUserDirectory = "",
  qlikUserId = "",
  qlikSessionCookie = "",
  jobId = "",
  requestTimeoutSeconds = 30,
}) {
  return requestJson("/api/qlik/metadata/run", {
    method: "POST",
    body: JSON.stringify({
      file_name: fileName,
      content_base64: contentBase64,
      jobs_root: jobsRoot,
      job_id: jobId,
      qlik_endpoint: qlikEndpoint,
      qlik_apps_dir: qlikAppsDir,
      dataprep_cache_dir: dataprepCacheDir,
      qlik_user_directory: qlikUserDirectory,
      qlik_user_id: qlikUserId,
      qlik_session_cookie: qlikSessionCookie,
      request_timeout_seconds: requestTimeoutSeconds,
    }),
  });
}

export function runQlikConversionJob({
  fileName,
  contentBase64,
  jobsRoot = "",
  outputDir = "",
  configPath = "",
  qlikEndpoint = "ws://localhost:4848/app",
  qlikAppsDir = "",
  dataprepCacheDir = "",
  qlikUserDirectory = "",
  qlikUserId = "",
  qlikSessionCookie = "",
  jobId = "",
  requestTimeoutSeconds = 30,
}) {
  return requestJson("/api/qlik/convert/run", {
    method: "POST",
    body: JSON.stringify({
      file_name: fileName,
      content_base64: contentBase64,
      jobs_root: jobsRoot,
      output_dir: outputDir,
      config_path: configPath,
      job_id: jobId,
      qlik_endpoint: qlikEndpoint,
      qlik_apps_dir: qlikAppsDir,
      dataprep_cache_dir: dataprepCacheDir,
      qlik_user_directory: qlikUserDirectory,
      qlik_user_id: qlikUserId,
      qlik_session_cookie: qlikSessionCookie,
      request_timeout_seconds: requestTimeoutSeconds,
    }),
  });
}

export function applyRdlAiEdit({
  fileName,
  content,
  instruction,
  configPath = "",
  useLlmConfig = false,
}) {
  return requestJson("/api/rdl-editor/apply", {
    method: "POST",
    body: JSON.stringify({
      file_name: fileName,
      content,
      instruction,
      config_path: configPath,
      use_llm_config: useLlmConfig,
    }),
  });
}

export function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("Unable to read file."));
    reader.readAsText(file);
  });
}

export function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.onerror = () => reject(reader.error || new Error("Unable to read file."));
    reader.readAsDataURL(file);
  });
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function unique(values) {
  return [...new Set(values.filter((value) => String(value || "").trim()).map((value) => String(value).trim()))];
}

function slug(value, fallback) {
  const text = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return text || fallback;
}

function confidenceScore(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "high") return 0.92;
  if (normalized === "medium") return 0.72;
  if (normalized === "low") return 0.45;
  return 0.5;
}

function confidenceLabel(score) {
  if (score >= 0.85) return "High";
  if (score >= 0.6) return "Medium";
  return "Low";
}

function warningObjects(model) {
  return toArray(model?.warnings).map((warning, index) => {
    if (typeof warning === "object" && warning !== null) {
      return {
        type: warning.type || "pipeline_warning",
        severity: warning.severity || "medium",
        message: warning.message || JSON.stringify(warning),
      };
    }
    return {
      type: "pipeline_warning",
      severity: index === 0 ? "medium" : "low",
      message: String(warning),
    };
  });
}

function parseJoinColumns(joinCondition) {
  const text = String(joinCondition || "").trim();
  const match = text.match(/([A-Za-z0-9_[\].]+)\s*=\s*([A-Za-z0-9_[\].]+)/);
  if (!match) {
    return { fromColumn: text || "join_condition", toColumn: text || "join_condition" };
  }
  const left = match[1].split(".").pop().replace(/[\[\]]/g, "");
  const right = match[2].split(".").pop().replace(/[\[\]]/g, "");
  return { fromColumn: left, toColumn: right };
}

function inferAggregation(measureName) {
  const text = String(measureName || "").toUpperCase();
  if (text.includes("COUNT")) return "COUNT";
  if (text.includes("AVG")) return "AVG";
  if (text.includes("MIN")) return "MIN";
  if (text.includes("MAX")) return "MAX";
  return "SUM";
}

export function datasetsFromState(state) {
  const summary = state?.report_summary || {};
  const selectedName = state?.selected_dataset_name || "";
  return toArray(summary.data_sets).map((dataset, index) => {
    const name = dataset.name || `Dataset ${index + 1}`;
    return {
      id: slug(name, `dataset_${index + 1}`),
      name,
      queryType: dataset.has_query ? "Text SQL" : "No SQL",
      dataSource: dataset.data_source_name || state?.selected_datasource_name || "",
      status: dataset.has_query ? "Ready" : "Review",
      sql: name === selectedName ? state?.sql_query || "" : "",
      fieldCount: dataset.field_count || 0,
    };
  });
}

export function parsingSummaryFromState(state) {
  const summary = state?.report_summary || {};
  const datasets = toArray(summary.data_sets);
  return {
    datasetsFound: datasets.length,
    dataSourcesFound: toArray(summary.data_sources).length,
    parametersFound: summary.parameter_count || 0,
    sqlQueriesExtracted: datasets.filter((dataset) => dataset.has_query).length,
  };
}

export function modelFromBackend(model) {
  if (!model || typeof model !== "object" || !Object.keys(model).length) {
    return emptyModel;
  }

  const score = confidenceScore(model.schema_confidence);
  const facts = toArray(model.fact_tables).map((fact, index) => ({
    id: slug(fact.name, `fact_${index + 1}`),
    name: fact.name || `Fact ${index + 1}`,
    type: "fact",
    confidence: score,
    columns: unique([...toArray(fact.foreign_keys), ...toArray(fact.measures)]),
    measures: toArray(fact.measures),
  }));

  const directDimensions = toArray(model.direct_dimensions).map((dimension, index) => ({
    id: slug(dimension.name, `dimension_${index + 1}`),
    name: dimension.name || `Dimension ${index + 1}`,
    type: "dimension",
    confidence: score,
    columns: unique([dimension.natural_key, ...toArray(dimension.attributes)]),
  }));

  const snowflakeDimensions = toArray(model.snowflake_dimensions).map((dimension, index) => ({
    id: slug(dimension.name, `snowflake_dimension_${index + 1}`),
    name: dimension.name || `Snowflake Dimension ${index + 1}`,
    type: "dimension",
    confidence: Math.max(score - 0.05, 0.35),
    columns: unique([dimension.natural_key, ...toArray(dimension.attributes)]),
  }));

  const relationships = toArray(model.relationships).map((relationship, index) => {
    const columns = parseJoinColumns(relationship.join_condition);
    return {
      id: slug(`${relationship.from_table}_${relationship.to_table}_${index}`, `relationship_${index + 1}`),
      fromTable: relationship.from_table || "",
      fromColumn: columns.fromColumn,
      toTable: relationship.to_table || "",
      toColumn: columns.toColumn,
      cardinality: relationship.cardinality || relationship.relationship_type || "unknown",
      confidence: score,
    };
  });

  const measures = facts.flatMap((fact) =>
    toArray(fact.measures).map((measure) => ({
      name: String(measure),
      expression: String(measure),
      sourceTable: fact.name,
      aggregation: inferAggregation(measure),
      status: "valid",
    })),
  );

  return {
    schemaType: model.model_type || "Unknown",
    tables: [...facts, ...directDimensions, ...snowflakeDimensions],
    relationships,
    measures,
    warnings: warningObjects(model),
  };
}

export function analysisFromBackend(model, loading = false) {
  if (!model || typeof model !== "object" || !Object.keys(model).length) {
    return emptyAnalysisResult;
  }
  const uiModel = modelFromBackend(model);
  const completedStatus = loading ? STATUS.inProgress : STATUS.completed;
  const score = confidenceScore(model.schema_confidence);
  return {
    timeline: [
      { label: "SQL parsed", status: completedStatus },
      { label: "Tables detected", status: completedStatus },
      { label: "Joins extracted", status: completedStatus },
      { label: "Measures detected", status: completedStatus },
      { label: "LLM reasoning completed", status: completedStatus },
    ],
    detectedTables: uiModel.tables.map((table) => table.name),
    detectedJoins: toArray(model.relationships).map(
      (relationship) =>
        relationship.join_condition ||
        `${relationship.from_table || "source"} -> ${relationship.to_table || "target"}`,
    ),
    detectedMeasures: uiModel.measures.map((measure) => measure.name),
    confidenceScore: score,
    confidenceLabel: confidenceLabel(score),
    warnings: warningObjects(model).map((warning) => warning.message),
  };
}

export function chatMessagesFromState(state) {
  return toArray(state?.conversation)
    .filter((message) => ["user", "assistant"].includes(message.role))
    .map((message, index) => ({
      id: `${message.role}_${index}`,
      role: message.role,
      content: message.content || "",
    }));
}

export function datasourceConfigFromState(state, previous = {}) {
  const defaults = state?.defaults?.tableau || {};
  const source = state?.publish_context_summary?.data_source || {};
  const connection = source.connection_info || {};
  return {
    name: previous.name || defaults.source_datasource_name || state?.selected_datasource_name || source.name || "",
    project: previous.project || defaults.project_name || "Default",
    datasourceProject: previous.datasourceProject || defaults.datasource_project_name || "published_datasources",
    workbookProject: previous.workbookProject || defaults.workbook_project_name || "published_reports",
    connectionType: "live_tds",
    tableauServerUrl: previous.tableauServerUrl || defaults.server_url || "",
    siteContentUrl: previous.siteContentUrl || defaults.site_content_url || "",
    authMode: previous.authMode || defaults.auth_method || "username_password",
    username: previous.username || defaults.username || "",
    password: previous.password || "",
    patName: previous.patName || defaults.pat_name || "",
    patSecret: previous.patSecret || "",
    sourceDatasourceName: previous.sourceDatasourceName || defaults.source_datasource_name || source.name || "",
    sourceServer: previous.sourceServer || connection.server || defaults.source_server || "",
    sourceDatabase: previous.sourceDatabase || connection.database || defaults.source_database || "",
    configPath: state?.defaults?.config_path || previous.configPath || "",
    credentialsReady: Boolean(defaults.credentials_ready),
    patReady: Boolean(defaults.pat_secret_ready),
  };
}

export function publicationResultFromState(state) {
  const report = state?.publish_report || {};
  const error = state?.publish_error || "";
  if (error) {
    return {
      status: "error",
      message: error,
      url: "",
      id: "",
    };
  }
  if (report.status) {
    const sourcePublishStatus = report.source_rdl_publish_status || report.source_rdl_powerbi_publish?.status || "";
    const baseMessage = report.message || report.reason || "Tableau publish workflow completed.";
    return {
      status: report.status,
      message: sourcePublishStatus ? `${baseMessage} Power BI source RDL: ${sourcePublishStatus}.` : baseMessage,
      url: report.datasource_url || report.published_datasource_url || report.datasource_content_url || "",
      id: report.datasource_id || report.published_datasource_id || "",
      datasourceProject: report.datasource_project_name || report.project_name || "",
      workbookProject: report.workbook_project_name || "",
      workbookUrl: report.workbook_webpage_url || report.workbook_content_url || "",
      workbookId: report.workbook_id || "",
      powerbiStatus: sourcePublishStatus,
    };
  }
  return {
    status: "pending",
    message: "Datasource has not been published yet.",
    url: "",
    id: "",
  };
}

export function consumerWorkbookFromState(state) {
  const workbook = state?.consumer_workbook || {};
  return {
    generated: Boolean(workbook.exists),
    name: workbook.name || "",
    downloadUrl: workbook.download_url || "",
    path: workbook.path || "",
  };
}

export function twbStateFromState(state) {
  const twb = state?.generated_twb || {};
  const visualConversion = state?.visual_conversion || {};
  const visualModelTwb = state?.visual_model_twb || {};
  const artifactWorkspace = state?.artifact_workspace || {};
  const artifactRoot = artifactWorkspace.root || {};
  const artifactManifest = artifactWorkspace.manifest || {};
  return {
    generated: Boolean(twb.exists),
    progress: twb.exists ? 100 : 0,
    name: twb.name || state?.generated_twb_name || "",
    downloadUrl: twb.download_url || "",
    path: twb.path || "",
    sizeBytes: twb.size_bytes || 0,
    artifactWorkspace: {
      path: artifactRoot.path || "",
      exists: Boolean(artifactRoot.exists),
      manifestPath: artifactManifest.path || "",
      manifestDownloadUrl: artifactManifest.download_url || "",
    },
    visualConversion: {
      exists: Boolean(visualConversion.exists),
      status: visualConversion.status || "not_started",
      name: visualConversion.name || "",
      path: visualConversion.path || "",
      outputDir: visualConversion.output_dir || "",
      downloadUrl: visualConversion.download_url || "",
      error: visualConversion.error || "",
    },
    visualModel: {
      generated: Boolean(visualModelTwb.exists),
      exists: Boolean(visualModelTwb.exists),
      status: visualModelTwb.status || "not_started",
      name: visualModelTwb.name || "",
      path: visualModelTwb.path || "",
      downloadUrl: visualModelTwb.download_url || "",
      error: visualModelTwb.error || "",
    },
  };
}

function normalizeQualityScore(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return 0;
  const percentValue = numericValue <= 1 ? numericValue * 100 : numericValue;
  return Math.max(0, Math.min(100, Math.round(percentValue)));
}

function normalizeQualityMetrics(metrics) {
  if (Array.isArray(metrics)) {
    return metrics
      .filter((metric) => metric && typeof metric === "object")
      .map((metric, index) => {
        const label = metric.label || metric.name || `Metric ${index + 1}`;
        return {
          id: slug(label, `metric_${index + 1}`),
          label,
          score: normalizeQualityScore(metric.score ?? metric.value ?? metric.percent),
          detail: metric.detail || metric.description || "",
          status: metric.status || "",
        };
      });
  }

  if (metrics && typeof metrics === "object") {
    return Object.entries(metrics).map(([label, value], index) => ({
      id: slug(label, `metric_${index + 1}`),
      label,
      score: normalizeQualityScore(typeof value === "object" ? value.score : value),
      detail: typeof value === "object" ? value.detail || value.description || "" : "",
      status: typeof value === "object" ? value.status || "" : "",
    }));
  }

  return [];
}

export function qualityComparisonFromState(state) {
  const comparison = state?.quality_comparison || {};
  if (!comparison || typeof comparison !== "object" || !Object.keys(comparison).length) {
    return emptyQualityComparison;
  }

  const metrics = normalizeQualityMetrics(comparison.metrics);
  const globalScore = normalizeQualityScore(
    comparison.global_score ?? comparison.globalScore ?? comparison.score ?? comparison.overall_score,
  );
  const status = comparison.status || (globalScore ? "completed" : "not_started");

  return {
    executed: Boolean(comparison.executed || (status && status !== "not_started") || metrics.length || globalScore),
    status,
    globalScore,
    summary: comparison.summary || comparison.message || "",
    metrics,
    details: comparison.details || comparison.sections || {},
  };
}
