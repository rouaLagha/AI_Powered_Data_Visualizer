import React, { useEffect, useRef, useState } from "react";
import { readFileAsText, runRdlConversion } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";

function formatSize(size) {
  if (!size) return "0 KB";
  return `${(size / 1024).toFixed(1)} KB`;
}

function artifactRows(artifacts) {
  return Object.entries(artifacts || {}).filter(([, artifact]) => artifact?.exists);
}

export default function RdlConversionPage({ defaultConfigPath = "" }) {
  const inputRef = useRef(null);
  const [fileState, setFileState] = useState(null);
  const [configPath, setConfigPath] = useState(defaultConfigPath);
  const [outputDir, setOutputDir] = useState("");
  const [publishEnabled, setPublishEnabled] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!configPath && defaultConfigPath) {
      setConfigPath(defaultConfigPath);
    }
  }, [configPath, defaultConfigPath]);

  function acceptFile(file) {
    if (!file) return;
    setFileState({
      name: file.name,
      size: file.size,
      file,
      status: file.name.toLowerCase().endsWith(".rdl") ? "ready" : "invalid_format",
    });
    setResult(null);
    setError("");
  }

  async function runConversion() {
    if (!fileState?.file || fileState.status !== "ready") {
      setError("Select an RDL file before conversion.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const content = await readFileAsText(fileState.file);
      const response = await runRdlConversion({
        fileName: fileState.name,
        content,
        configPath,
        outputDir,
        publishEnabled,
      });
      setResult(response);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  const artifacts = artifactRows(result?.artifacts);

  return (
    <div className="page-stack conversion-page">
      <Card
        title="RDL to TWB conversion"
        eyebrow="Direct converter"
        actions={<Badge tone={result?.ok ? "green" : "indigo"}>{result?.ok ? "Completed" : "REST API"}</Badge>}
      >
        <div className="form-grid">
          <label>
            RDL source
            <div className="compact-upload">
              <input ref={inputRef} type="file" accept=".rdl" hidden onChange={(event) => acceptFile(event.target.files?.[0])} />
              <Button onClick={() => inputRef.current?.click()}>Browse RDL</Button>
              <span>{fileState ? `${fileState.name} (${formatSize(fileState.size)})` : "No file selected"}</span>
            </div>
          </label>
          <label>
            LLM config path
            <input value={configPath} onChange={(event) => setConfigPath(event.target.value)} placeholder="backend/config/llm_config.json" />
          </label>
          <label>
            Output folder
            <input value={outputDir} onChange={(event) => setOutputDir(event.target.value)} placeholder="Auto-generated under outputs/react_conversions" />
          </label>
          <label className="check-row">
            <input type="checkbox" checked={publishEnabled} onChange={(event) => setPublishEnabled(event.target.checked)} />
            Enable publish stage
          </label>
        </div>

        {error && <div className="loading-banner error-banner">{error}</div>}
        {loading && <div className="loading-banner">Conversion in progress...</div>}

        <div className="button-row">
          <Button variant="primary" onClick={runConversion} disabled={loading || fileState?.status !== "ready"}>
            Convert to TWB
          </Button>
          <Button onClick={() => setResult(null)} disabled={loading || !result}>
            Clear result
          </Button>
        </div>
      </Card>

      {result && (
        <Card title="Generated artifacts" eyebrow={result.output_dir || "Output"}>
          <div className="metric-grid">
            <div className="metric-card">
              <strong>{artifacts.length}</strong>
              <span>Files ready</span>
            </div>
            <div className="metric-card">
              <strong>{result.trace_steps?.length || 0}</strong>
              <span>Pipeline steps</span>
            </div>
            <div className="metric-card">
              <strong>{result.status || "completed"}</strong>
              <span>Status</span>
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
