import React, { useState } from "react";
import { readFileAsBase64, runQlikMetadataJob } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";

function artifactRows(artifacts) {
  return Object.entries(artifacts || {}).filter(([, artifact]) => artifact?.exists);
}

export default function QlikConversionPage() {
  const [qvfFile, setQvfFile] = useState(null);
  const [jobsRoot, setJobsRoot] = useState("output/qlik_jobs");
  const [jobId, setJobId] = useState("");
  const [qlikEndpoint, setQlikEndpoint] = useState("ws://localhost:4848/app");
  const [qlikAppsDir, setQlikAppsDir] = useState("");
  const [loading, setLoading] = useState(false);
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
      });
      setResult(response);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  const artifacts = artifactRows(result?.artifacts);
  const summary = result?.summary || {};
  const failed = result?.status === "failed";

  return (
    <div className="page-stack conversion-page qlik-conversion-page">
      <Card
        title="Qlik metadata extraction"
        eyebrow="Real QIX pipeline"
        actions={
          <Badge tone={failed ? "red" : result?.ok ? "green" : "indigo"}>
            {failed ? "Failed" : result?.ok ? "Completed" : "QVF upload"}
          </Badge>
        }
      >
        <div className="form-grid qlik-form-grid">
          <label>
            QVF file
            <input
              type="file"
              accept=".qvf"
              onChange={(event) => setQvfFile(event.target.files?.[0] || null)}
            />
          </label>
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
        </div>

        {error && <div className="loading-banner error-banner">{error}</div>}
        {loading && <div className="loading-banner">Qlik metadata job in progress...</div>}

        <div className="button-row">
          <Button variant="primary" onClick={runMetadataJob} disabled={loading || !qvfFile}>
            Extract metadata
          </Button>
          <Button onClick={() => setResult(null)} disabled={loading || !result}>
            Clear result
          </Button>
        </div>
      </Card>

      {result && (
        <Card title="Qlik metadata job" eyebrow={result.job_id || "job"}>
          {result.error && <div className="loading-banner error-banner">{result.error}</div>}

          <div className="metric-grid">
            <div className="metric-card">
              <strong>{summary.sheet_count || 0}</strong>
              <span>Sheets</span>
            </div>
            <div className="metric-card">
              <strong>{summary.visual_count || 0}</strong>
              <span>Visual objects</span>
            </div>
            <div className="metric-card">
              <strong>{summary.master_measure_count || 0}</strong>
              <span>Master measures</span>
            </div>
            <div className="metric-card">
              <strong>{summary.variable_count || 0}</strong>
              <span>Variables</span>
            </div>
          </div>

          <div className="artifact-list">
            <div className="artifact-row">
              <div>
                <strong>app_id</strong>
                <span>{result.app_id || result.result?.app_id || "Returned by Qlik"}</span>
              </div>
              <Badge tone="green">{result.extraction_mode || "qix"}</Badge>
            </div>
          </div>

          <div className="artifact-list">
            {artifacts.map(([key, artifact]) => (
              <div className="artifact-row" key={key}>
                <div>
                  <strong>{key}</strong>
                  <span>{artifact.name}</span>
                </div>
                <a className="btn btn-secondary btn-sm" href={artifact.download_url}>
                  Download
                </a>
              </div>
            ))}
          </div>

          <div className="trace-list">
            {(result.trace_steps || []).map((step, index) => (
              <div key={`${step}-${index}`} className="trace-row">
                <span>{index + 1}</span>
                <p>{step}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
