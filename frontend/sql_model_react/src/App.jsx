import React, { useEffect, useMemo, useState } from "react";
import Header from "./components/layout/Header.jsx";
import PipelineLayout from "./components/layout/PipelineLayout.jsx";
import RdlUploadStep from "./components/steps/RdlUploadStep.jsx";
import RdlParsingStep from "./components/steps/RdlParsingStep.jsx";
import DatasetSelectionStep from "./components/steps/DatasetSelectionStep.jsx";
import SqlAnalysisStep from "./components/steps/SqlAnalysisStep.jsx";
import DimensionalModelStep from "./components/steps/DimensionalModelStep.jsx";
import HumanValidationStep from "./components/steps/HumanValidationStep.jsx";
import VisualMappingStep from "./components/steps/VisualMappingStep.jsx";
import TwbGenerationStep from "./components/steps/TwbGenerationStep.jsx";
import TableauDatasourceStep from "./components/steps/TableauDatasourceStep.jsx";
import PublicationStep from "./components/steps/PublicationStep.jsx";
import ConsumerWorkbookStep from "./components/steps/ConsumerWorkbookStep.jsx";
import QualityComparisonStep from "./components/steps/QualityComparisonStep.jsx";
import RdlAiEditorPage from "./pages/RdlAiEditorPage.jsx";
import QlikConversionPage from "./pages/QlikConversionPage.jsx";
import TableauReportCreatorPage from "./pages/TableauReportCreatorPage.jsx";
import {
  STATUS,
  analysisFromBackend,
  analyzeModel,
  chatMessagesFromState,
  compareQuality,
  consumerWorkbookFromState,
  datasourceConfigFromState,
  datasetsFromState,
  emptyAnalysisResult,
  emptyModel,
  emptyParsingSummary,
  emptyQualityComparison,
  generateTwb as generateTwbApi,
  getState,
  mapVisualContent,
  modelFromBackend,
  parseRdl,
  parsingSummaryFromState,
  pipelineDefinitions,
  publicationResultFromState,
  qualityComparisonFromState,
  publishTableau,
  readFileAsText,
  resetBackendState,
  selectDataset,
  twbStateFromState,
  validateSchema,
} from "./api/pipelineApi.js";

function initialSteps() {
  return pipelineDefinitions.map((step, index) => ({
    ...step,
    status: index === 0 ? STATUS.inProgress : STATUS.pending,
  }));
}

function nextSteps(updates) {
  return (steps) =>
    steps.map((step, index) => {
      if (!Object.prototype.hasOwnProperty.call(updates, index)) return step;
      return { ...step, status: updates[index] };
    });
}

function suggestedPipelineFromState(state) {
  const hasReport = Boolean(state?.report_name);
  const hasSql = Boolean(String(state?.sql_query || "").trim());
  const hasModel = Boolean(state?.latest_model && Object.keys(state.latest_model).length);
  const schemaValidated = Boolean(state?.schema_validated);
  const hasVisualMapping = Boolean(state?.visual_conversion?.exists);
  const visualMappingStatus = state?.visual_conversion?.status || "";
  const hasTwb = Boolean(state?.generated_twb?.exists);
  const hasPublishResult = Boolean(state?.publish_error || state?.publish_report?.status);
  const hasConsumerWorkbook = Boolean(state?.consumer_workbook?.exists);
  const hasQualityComparison = Boolean(
    state?.quality_comparison?.executed ||
      state?.quality_comparison?.status ||
      state?.quality_comparison?.global_score ||
      state?.quality_comparison?.score,
  );

  const statuses = pipelineDefinitions.map((step, index) => ({
    ...step,
    status: index === 0 ? STATUS.inProgress : STATUS.pending,
  }));
  let active = 0;

  if (hasReport) {
    statuses[0].status = STATUS.completed;
    statuses[1].status = STATUS.completed;
    statuses[2].status = hasSql ? STATUS.inProgress : STATUS.pending;
    active = hasSql ? 2 : 1;
  }
  if (hasModel) {
    statuses[2].status = STATUS.completed;
    statuses[3].status = STATUS.completed;
    statuses[4].status = STATUS.inProgress;
    statuses[5].status = STATUS.needsValidation;
    active = 4;
  }
  if (schemaValidated) {
    statuses[4].status = STATUS.completed;
    statuses[5].status = STATUS.completed;
    statuses[6].status = visualMappingStatus === "failed" ? STATUS.error : STATUS.inProgress;
    active = 6;
  }
  if (hasVisualMapping) {
    statuses[6].status = STATUS.completed;
    statuses[7].status = STATUS.inProgress;
    active = 7;
  }
  if (hasTwb) {
    statuses[7].status = STATUS.completed;
    statuses[8].status = STATUS.inProgress;
    active = 8;
  }
  if (hasPublishResult) {
    statuses[8].status = STATUS.completed;
    statuses[9].status = state.publish_error ? STATUS.error : STATUS.completed;
    active = 9;
  }
  if (hasConsumerWorkbook) {
    statuses[10].status = STATUS.completed;
    statuses[11].status = hasQualityComparison ? STATUS.completed : STATUS.inProgress;
    active = 11;
  }
  if (hasQualityComparison) {
    statuses[11].status = STATUS.completed;
    active = 11;
  }

  return { statuses, active };
}

function tableauPayload(config) {
  return {
    server_url: config.tableauServerUrl,
    site_content_url: config.siteContentUrl,
    project_name: config.project,
    datasource_project_name: config.datasourceProject || config.project,
    workbook_project_name: config.workbookProject || config.project,
    username: config.username,
    password: config.password,
    pat_name: config.patName,
    pat_secret: config.patSecret,
    source_datasource_name: config.sourceDatasourceName || config.name,
    datasource_publish_mode: config.connectionType,
    build_hyper_extract: config.connectionType === "extract",
    auth_method: config.authMode,
  };
}

const navItems = [
  { id: "pipeline", label: "RDL -> Tableau Conversion" },
  { id: "qlik", label: "Qlik -> Tableau conversion" },
  { id: "editor", label: "AI RDL report Editor" },
  { id: "tableauCreator", label: "AI Tableau report creator" },
];

const PANEL_WIDTH_LIMITS = {
  pipeline: { min: 220, max: 500, default: 320 },
  chat: { min: 280, max: 520, default: 360 },
};

const PANEL_WIDTH_STORAGE_KEY = "sql-model-assistant-panel-widths";

function clampPanelWidth(name, value) {
  const limits = PANEL_WIDTH_LIMITS[name];
  const numericValue = Number(value);
  if (!limits || !Number.isFinite(numericValue)) return limits?.default || 0;
  return Math.min(limits.max, Math.max(limits.min, Math.round(numericValue)));
}

function readPanelWidths() {
  const fallback = {
    pipeline: PANEL_WIDTH_LIMITS.pipeline.default,
    chat: PANEL_WIDTH_LIMITS.chat.default,
  };

  if (typeof window === "undefined") return fallback;

  try {
    const stored = JSON.parse(window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY) || "{}");
    return {
      pipeline: clampPanelWidth("pipeline", stored.pipeline ?? fallback.pipeline),
      chat: clampPanelWidth("chat", stored.chat ?? fallback.chat),
    };
  } catch {
    return fallback;
  }
}

export default function App() {
  const [activePage, setActivePage] = useState("pipeline");
  const [panelWidths, setPanelWidths] = useState(readPanelWidths);
  const [activeStep, setActiveStep] = useState(0);
  const [pipelineSteps, setPipelineSteps] = useState(initialSteps);
  const [backendState, setBackendState] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [parsingSummary, setParsingSummary] = useState(emptyParsingSummary);
  const [selectedDataset, setSelectedDataset] = useState(null);
  const [sqlText, setSqlText] = useState("");
  const [analysisResult, setAnalysisResult] = useState(emptyAnalysisResult);
  const [dimensionalModel, setDimensionalModel] = useState(emptyModel);
  const [validationStatus, setValidationStatus] = useState("not_started");
  const [chatMessages, setChatMessages] = useState([]);
  const [changeProposals, setChangeProposals] = useState([]);
  const [changeHistory, setChangeHistory] = useState([]);
  const [loading, setLoading] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [showXml, setShowXml] = useState(false);
  const [twbState, setTwbState] = useState({ generated: false, progress: 0 });
  const [datasourcePrepared, setDatasourcePrepared] = useState(false);
  const [datasourceConfig, setDatasourceConfig] = useState({
    name: "",
    project: "Default",
    datasourceProject: "published_datasources",
    workbookProject: "published_reports",
    connectionType: "live_tds",
    tableauServerUrl: "",
    siteContentUrl: "",
    authMode: "username_password",
    username: "",
    password: "",
    patName: "",
    patSecret: "",
    sourceDatasourceName: "",
    sourceServer: "",
    sourceDatabase: "",
    configPath: "",
    credentialsReady: false,
  });
  const [publicationResult, setPublicationResult] = useState(publicationResultFromState({}));
  const [consumerWorkbook, setConsumerWorkbook] = useState({ generated: false, name: "", downloadUrl: "" });
  const [qualityComparison, setQualityComparison] = useState(emptyQualityComparison);
  const [publishedReportTest, setPublishedReportTest] = useState({});

  function applyBackendState(state) {
    setBackendState(state);
    const nextDatasets = datasetsFromState(state);
    const selected =
      nextDatasets.find((dataset) => dataset.name === state.selected_dataset_name) ||
      nextDatasets[0] ||
      null;
    if (selected && state.sql_query) {
      selected.sql = state.sql_query;
    }

    setDatasets(nextDatasets);
    setParsingSummary(parsingSummaryFromState(state));
    setSelectedDataset(selected);
    setSqlText(state.sql_query || selected?.sql || "");
    setAnalysisResult(analysisFromBackend(state.latest_model));
    setDimensionalModel(modelFromBackend(state.latest_model));
    setValidationStatus(state.schema_validated ? "approved" : state.latest_model && Object.keys(state.latest_model).length ? "needs_validation" : "not_started");
    setChatMessages(chatMessagesFromState(state));
    setTwbState(twbStateFromState(state));
    setDatasourceConfig((previous) => ({
      ...datasourceConfigFromState(state),
      password: previous.password,
      patSecret: previous.patSecret,
    }));
    setPublicationResult(publicationResultFromState(state));
    setConsumerWorkbook(consumerWorkbookFromState(state));
    setQualityComparison(qualityComparisonFromState(state));
    setPublishedReportTest(state.published_report_test_report || {});
  }

  function syncPipelineToState(state) {
    const { statuses, active } = suggestedPipelineFromState(state);
    setPipelineSteps(statuses);
    setActiveStep(active);
  }

  async function runBackendAction(label, action) {
    setLoading(label);
    setErrorMessage("");
    try {
      const state = await action();
      applyBackendState(state);
      return state;
    } catch (error) {
      setErrorMessage(error.message || String(error));
      throw error;
    } finally {
      setLoading("");
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function loadState() {
      setLoading("Loading backend state");
      try {
        const state = await getState();
        if (cancelled) return;
        applyBackendState(state);
        syncPipelineToState(state);
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(`Unable to reach the backend API: ${error.message || error}`);
        }
      } finally {
        if (!cancelled) setLoading("");
      }
    }
    loadState();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, JSON.stringify(panelWidths));
    } catch {
      // Layout preferences are optional; ignore private browsing/storage failures.
    }
  }, [panelWidths]);

  function updatePanelWidth(name, value) {
    setPanelWidths((previous) => ({
      ...previous,
      [name]: clampPanelWidth(name, value),
    }));
  }

  function activateStep(index) {
    setActiveStep(index);
    setPipelineSteps((steps) =>
      steps.map((step, stepIndex) => {
        if (stepIndex !== index || step.status === STATUS.completed || step.status === STATUS.error) return step;
        return { ...step, status: STATUS.inProgress };
      }),
    );
  }

  async function resetPipeline() {
    try {
      const state = await runBackendAction("Resetting pipeline", resetBackendState);
      syncPipelineToState(state);
    } catch {
      setPipelineSteps(initialSteps());
      setActiveStep(0);
    }
    setUploadedFile(null);
    setDatasets([]);
    setParsingSummary(emptyParsingSummary);
    setSelectedDataset(null);
    setSqlText("");
    setAnalysisResult(emptyAnalysisResult);
    setDimensionalModel(emptyModel);
    setValidationStatus("not_started");
    setChatMessages([]);
    setChangeProposals([]);
    setChangeHistory([]);
    setTwbState({ generated: false, progress: 0 });
    setDatasourcePrepared(false);
    setPublicationResult(publicationResultFromState({}));
    setConsumerWorkbook({ generated: false, name: "", downloadUrl: "" });
    setQualityComparison(emptyQualityComparison);
    setPublishedReportTest({});
  }

  async function parseUploadedRdl() {
    if (!uploadedFile?.file) {
      setErrorMessage("Select an RDL file before loading it into the pipeline.");
      return;
    }
    const content = await readFileAsText(uploadedFile.file);
    const state = await runBackendAction("Parsing RDL", () =>
      parseRdl({ fileName: uploadedFile.name, content }),
    );
    setPipelineSteps(nextSteps({ 0: STATUS.completed, 1: STATUS.completed, 2: STATUS.inProgress }));
    setActiveStep(1);
    setDatasourcePrepared(false);
    return state;
  }

  async function handleDatasetSelect(dataset) {
    if (!dataset?.name) return;
    setSelectedDataset(dataset);
    setSqlText(dataset.sql || "");
    const state = await runBackendAction("Selecting dataset", () => selectDataset(dataset.name));
    setPipelineSteps(nextSteps({ 2: STATUS.inProgress }));
    return state;
  }

  async function runAnalysis(followUp = "") {
    if (!String(sqlText || "").trim()) {
      setErrorMessage("Select a dataset or enter SQL before running analysis.");
      return;
    }
    setPipelineSteps(nextSteps({ 2: STATUS.completed, 3: STATUS.inProgress }));
    setActiveStep(3);
    const state = await runBackendAction(followUp ? "Applying AI correction" : "SQL analysis and LLM reasoning", () =>
      analyzeModel({
        sqlQuery: sqlText,
        followUp,
        configPath: backendState?.defaults?.config_path || "",
      }),
    );
    setPipelineSteps(nextSteps({ 3: STATUS.completed, 4: STATUS.inProgress, 5: STATUS.needsValidation }));
    setActiveStep(4);
    setDatasourcePrepared(false);
    setShowXml(false);
    return state;
  }

  async function approveModel() {
    setPipelineSteps(nextSteps({ 5: STATUS.inProgress }));
    const state = await runBackendAction("Validating schema", validateSchema);
    setPipelineSteps(nextSteps({ 4: STATUS.completed, 5: STATUS.completed, 6: STATUS.inProgress }));
    setActiveStep(6);
    return state;
  }

  async function mapVisuals() {
    setPipelineSteps(nextSteps({ 6: STATUS.inProgress }));
    const state = await runBackendAction("Mapping report visuals", () =>
      mapVisualContent({ configPath: backendState?.defaults?.config_path || "" }),
    );
    setPipelineSteps(nextSteps({ 6: STATUS.completed, 7: STATUS.inProgress }));
    setActiveStep(7);
    setShowXml(false);
    return state;
  }

  async function generateTwb() {
    setPipelineSteps(nextSteps({ 7: STATUS.inProgress }));
    setTwbState((state) => ({ ...state, generated: false, progress: 35 }));
    const state = await runBackendAction("TWB generation", () =>
      generateTwbApi({ outputName: backendState?.defaults?.output_name || "data_model_to_publish.twb" }),
    );
    setPipelineSteps(nextSteps({ 7: STATUS.completed, 8: STATUS.inProgress }));
    setActiveStep(8);
    return state;
  }

  async function testConnection() {
    await runBackendAction("Refreshing Tableau configuration", getState);
  }

  function prepareDatasource() {
    if (!twbState.generated) {
      setErrorMessage("Generate the TWB before preparing the Tableau datasource.");
      return;
    }
    setDatasourcePrepared(true);
    setPipelineSteps(nextSteps({ 8: STATUS.completed, 9: STATUS.inProgress }));
    setActiveStep(9);
  }

  async function publishDatasource() {
    setPipelineSteps(nextSteps({ 9: STATUS.inProgress }));
    const state = await runBackendAction("Publishing datasource", () =>
      publishTableau({
        configPath: datasourceConfig.configPath || backendState?.defaults?.config_path || "",
        tableau: tableauPayload(datasourceConfig),
      }),
    );
    if (state.publish_error) {
      setPipelineSteps(nextSteps({ 9: STATUS.error }));
    } else {
      setPipelineSteps(nextSteps({ 9: STATUS.completed, 10: STATUS.inProgress }));
      setActiveStep(10);
    }
    return state;
  }

  async function generateConsumerWorkbook() {
    const state = await runBackendAction("Checking consumer workbook artifact", getState);
    const workbook = consumerWorkbookFromState(state);
    if (!workbook.generated) {
      setErrorMessage("No consumer workbook artifact is available yet. Complete Tableau publication first.");
      return;
    }
    setConsumerWorkbook(workbook);
    setPipelineSteps(nextSteps({ 10: STATUS.completed, 11: STATUS.inProgress }));
    setActiveStep(11);
  }

  function continueToQualityComparison() {
    setPipelineSteps(nextSteps({ 10: STATUS.completed, 11: STATUS.inProgress }));
    setActiveStep(11);
  }

  async function runQualityComparison() {
    setPipelineSteps(nextSteps({ 11: STATUS.inProgress }));
    const state = await runBackendAction("Quality comparison", compareQuality);
    setPipelineSteps(nextSteps({ 11: STATUS.completed }));
    setActiveStep(11);
    return state;
  }

  async function sendCorrection(text) {
    await runAnalysis(text);
  }

  function applyProposal() {
    setChangeProposals([]);
  }

  function rejectProposal(proposalId) {
    setChangeProposals((items) => items.filter((item) => item.id !== proposalId));
  }

  function addRelationshipProposal() {
    sendCorrection("Add the missing relationship using the fact and dimension key columns that best match the current SQL.");
  }

  function deleteRelationshipProposal(relationshipId) {
    const relationship = dimensionalModel.relationships.find((item) => item.id === relationshipId);
    if (!relationship) return;
    sendCorrection(`Remove the relationship from ${relationship.fromTable} to ${relationship.toTable}.`);
  }

  const currentStep = useMemo(() => {
    const props = {
      uploadedFile,
      setUploadedFile,
      markStepComplete: parseUploadedRdl,
      resetPipeline,
      parsingSummary,
      datasets,
      selectedDataset,
      setSelectedDataset: handleDatasetSelect,
      sqlText,
      setSqlText,
      analysisResult,
      loading: Boolean(loading),
      loadingText: loading,
      model: dimensionalModel,
      changeHistory,
      validationStatus,
    };

    switch (activeStep) {
      case 0:
        return <RdlUploadStep {...props} />;
      case 1:
        return <RdlParsingStep {...props} onParse={parseUploadedRdl} onContinue={() => activateStep(2)} />;
      case 2:
        return <DatasetSelectionStep {...props} onAnalyze={() => runAnalysis()} />;
      case 3:
        return <SqlAnalysisStep {...props} onRunAnalysis={() => runAnalysis()} />;
      case 4:
        return <DimensionalModelStep model={dimensionalModel} backendModel={backendState?.latest_model} />;
      case 5:
        return (
          <HumanValidationStep
            model={dimensionalModel}
            backendModel={backendState?.latest_model}
            changeHistory={changeHistory}
            validationStatus={validationStatus}
            onApprove={approveModel}
            onRequestReanalysis={() => runAnalysis()}
            onAddRelationship={addRelationshipProposal}
            onDeleteRelationship={deleteRelationshipProposal}
            databaseContext={backendState?.database_context}
          />
        );
      case 6:
        return <VisualMappingStep twbState={twbState} onMapVisuals={mapVisuals} loading={Boolean(loading)} />;
      case 7:
        return <TwbGenerationStep twbState={twbState} onGenerate={generateTwb} showXml={showXml} setShowXml={setShowXml} />;
      case 8:
        return (
          <TableauDatasourceStep
            datasourceConfig={datasourceConfig}
            setDatasourceConfig={setDatasourceConfig}
            datasourcePrepared={datasourcePrepared}
            onTest={testConnection}
            onPrepare={prepareDatasource}
          />
        );
      case 9:
        return <PublicationStep datasourceConfig={datasourceConfig} publicationResult={publicationResult} onPublish={publishDatasource} />;
      case 10:
        return (
          <ConsumerWorkbookStep
            consumerWorkbook={consumerWorkbook}
            datasourceConfig={datasourceConfig}
            onGenerateConsumer={generateConsumerWorkbook}
            onContinueQuality={continueToQualityComparison}
            onStartNew={resetPipeline}
          />
        );
      case 11:
        return (
          <QualityComparisonStep
            qualityComparison={qualityComparison}
            publishedReportTest={publishedReportTest}
            onRunComparison={runQualityComparison}
            loading={Boolean(loading)}
            loadingText={loading}
          />
        );
      default:
        return null;
    }
  }, [
    activeStep,
    uploadedFile,
    datasets,
    parsingSummary,
    selectedDataset,
    sqlText,
    analysisResult,
    loading,
    dimensionalModel,
    backendState,
    changeHistory,
    validationStatus,
    twbState,
    showXml,
    datasourceConfig,
    datasourcePrepared,
    publicationResult,
    consumerWorkbook,
    qualityComparison,
    publishedReportTest,
  ]);

  const headerContext = {
    backendReady: Boolean(backendState?.ok),
    reportName: backendState?.report_name || uploadedFile?.name || "",
    datasetName: selectedDataset?.name || backendState?.selected_dataset_name || "",
  };
  const defaultConfigPath = backendState?.defaults?.config_path || "";

  if (activePage === "editor") {
    return (
      <div className="app-shell">
        <Header
          activeStepTitle="AI RDL report Editor"
          validationStatus={validationStatus}
          context={headerContext}
          navItems={navItems}
          activePage={activePage}
          onNavigate={setActivePage}
          showValidationBadge={false}
        />
        <main className="page-workspace">
          {loading && <div className="loading-banner">{loading} in progress...</div>}
          {errorMessage && <div className="loading-banner error-banner">{errorMessage}</div>}
          <RdlAiEditorPage defaultConfigPath={defaultConfigPath} />
        </main>
      </div>
    );
  }

  if (activePage === "qlik") {
    return (
      <div className="app-shell">
        <Header
          activeStepTitle="Qlik -> Tableau conversion"
          validationStatus={validationStatus}
          context={headerContext}
          navItems={navItems}
          activePage={activePage}
          onNavigate={setActivePage}
          showValidationBadge={false}
        />
        <main className="page-workspace">
          {loading && <div className="loading-banner">{loading} in progress...</div>}
          {errorMessage && <div className="loading-banner error-banner">{errorMessage}</div>}
          <QlikConversionPage />
        </main>
      </div>
    );
  }

  if (activePage === "tableauCreator") {
    return (
      <div className="app-shell">
        <Header
          activeStepTitle="AI Tableau report creator"
          validationStatus={validationStatus}
          context={headerContext}
          navItems={navItems}
          activePage={activePage}
          onNavigate={setActivePage}
          showValidationBadge={false}
        />
        <main className="page-workspace">
          {loading && <div className="loading-banner">{loading} in progress...</div>}
          {errorMessage && <div className="loading-banner error-banner">{errorMessage}</div>}
          <TableauReportCreatorPage />
        </main>
      </div>
    );
  }

  return (
    <PipelineLayout
      steps={pipelineSteps}
      activeStep={activeStep}
      onSelectStep={activateStep}
      validationStatus={validationStatus}
      headerContext={headerContext}
      navItems={navItems}
      activePage={activePage}
      onNavigate={setActivePage}
      panelWidths={panelWidths}
      panelWidthLimits={PANEL_WIDTH_LIMITS}
      onPanelWidthChange={updatePanelWidth}
      chatProps={{
        chatMessages,
        changeProposals,
        onSendCorrection: sendCorrection,
        onApplyProposal: applyProposal,
        onRejectProposal: rejectProposal,
      }}
    >
      {loading && <div className="loading-banner">{loading} in progress...</div>}
      {errorMessage && <div className="loading-banner error-banner">{errorMessage}</div>}
      {currentStep}
    </PipelineLayout>
  );
}
