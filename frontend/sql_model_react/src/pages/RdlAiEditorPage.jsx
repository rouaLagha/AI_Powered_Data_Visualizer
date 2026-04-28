import React, { useEffect, useRef, useState } from "react";
import { applyRdlAiEdit, readFileAsText } from "../api/pipelineApi.js";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";
import CodePreview from "../components/shared/CodePreview.jsx";

function artifactRows(artifacts) {
  return Object.entries(artifacts || {}).filter(([, artifact]) => artifact?.exists);
}

export default function RdlAiEditorPage({ defaultConfigPath = "" }) {
  const inputRef = useRef(null);
  const [fileName, setFileName] = useState("");
  const [sourceXml, setSourceXml] = useState("");
  const [instruction, setInstruction] = useState("");
  const [configPath, setConfigPath] = useState(defaultConfigPath);
  const [useLlmConfig, setUseLlmConfig] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!configPath && defaultConfigPath) {
      setConfigPath(defaultConfigPath);
    }
  }, [configPath, defaultConfigPath]);

  async function acceptFile(file) {
    if (!file) return;
    setFileName(file.name);
    setSourceXml(await readFileAsText(file));
    setResult(null);
    setError("");
  }

  async function applyEdit() {
    if (!sourceXml.trim()) {
      setError("Load an RDL file before applying an edit.");
      return;
    }
    if (!instruction.trim()) {
      setError("Enter an edit instruction first.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await applyRdlAiEdit({
        fileName: fileName || "uploaded_report.rdl",
        content: sourceXml,
        instruction,
        configPath,
        useLlmConfig,
      });
      setResult(response);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  const artifacts = artifactRows(result?.artifacts);
  const patchPreview = result?.patch_validated ? JSON.stringify(result.patch_validated, null, 2) : "";

  return (
    <div className="page-stack">
      <Card
        title="RDL AI editor"
        eyebrow="Instruction to patch"
        actions={<Badge tone={result?.ok ? "green" : "indigo"}>{result?.ok ? "Patch applied" : "REST API"}</Badge>}
      >
        <div className="form-grid">
          <label>
            RDL source
            <div className="compact-upload">
              <input ref={inputRef} type="file" accept=".rdl,.xml" hidden onChange={(event) => acceptFile(event.target.files?.[0])} />
              <Button onClick={() => inputRef.current?.click()}>Browse RDL</Button>
              <span>{fileName || "No file selected"}</span>
            </div>
          </label>
          <label>
            LLM config path
            <input value={configPath} onChange={(event) => setConfigPath(event.target.value)} disabled={!useLlmConfig} />
          </label>
          <label className="check-row">
            <input type="checkbox" checked={useLlmConfig} onChange={(event) => setUseLlmConfig(event.target.checked)} />
            Use LLM config
          </label>
        </div>

        <label>
          Edit instruction
          <textarea
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder='Example: remove CalendarYear filter completely from dsMain'
          />
        </label>

        {error && <div className="loading-banner error-banner">{error}</div>}
        {loading && <div className="loading-banner">Applying RDL edit...</div>}

        <div className="button-row">
          <Button variant="primary" onClick={applyEdit} disabled={loading || !sourceXml.trim()}>
            Apply edit
          </Button>
          <Button
            onClick={() => {
              setResult(null);
              setInstruction("");
            }}
            disabled={loading}
          >
            Clear
          </Button>
        </div>
      </Card>

      <div className="editor-grid">
        <Card title="Source RDL" eyebrow={fileName || "Input"}>
          <textarea className="xml-editor" value={sourceXml} onChange={(event) => setSourceXml(event.target.value)} />
        </Card>
        <Card title="Modified RDL" eyebrow="Backend output">
          {result?.modified_xml ? <CodePreview code={result.modified_xml} language="xml" /> : <div className="empty-state">Run an edit to preview the modified report.</div>}
        </Card>
      </div>

      {result && (
        <Card title="Patch details" eyebrow="Validated operations">
          <div className="editor-grid compact">
            <CodePreview code={patchPreview} language="json" />
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
          </div>
          <div className="trace-list">
            {(result.operation_logs || []).map((item, index) => (
              <div className="trace-row" key={`${item}-${index}`}>
                <span>{index + 1}</span>
                <p>{item}</p>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
